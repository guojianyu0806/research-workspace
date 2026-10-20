"""Account-local, versioned multilingual generations with atomic publication.

Builders retain verified chunk progress on cancellation/failure. Readers use an
exact ready snapshot; they never silently substitute another parse or account.
Scheduling is explicit and uses owned thread handles, never process-name kills.
"""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import threading
import time
from uuid import uuid4

from .multilingual_model import LocalMultilingualEncoder
from .semantic import SemanticIndex

MAX_BLOCKS = 12000
MAX_CHARS = 8_000_000
MAX_CHUNKS = 50000
_slots = threading.BoundedSemaphore(2)
_guard = threading.Lock()
_jobs = {}
_locks = {}
_shared_encoder = None


class IndexBusy(ValueError):
    pass


class IndexCancelled(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class MultilingualIndex:
    def __init__(self, library, *, encoder=None):
        self.library = library
        global _shared_encoder
        if encoder is None:
            # Only public weights/tokenizer are shared. Source plans, embeddings,
            # generations and jobs remain bound to each account's SQLite file.
            with _guard:
                if _shared_encoder is None:
                    _shared_encoder = LocalMultilingualEncoder()
                encoder = _shared_encoder
        self.encoder = encoder
        self.path = library.data_dir / 'multilingual.sqlite3'
        self._plan_cache = None

    @contextmanager
    def _connection(self):
        with sqlite3.connect(self.path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS chunk_vectors (
                    cache_key TEXT PRIMARY KEY, model TEXT NOT NULL,
                    source TEXT NOT NULL, vector BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS generations (
                    snapshot TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    model TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS active_scopes (
                    scope TEXT PRIMARY KEY, snapshot TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS leases (
                    scope TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS builds (
                    id TEXT PRIMARY KEY, snapshot TEXT NOT NULL,
                    status TEXT NOT NULL, detail TEXT NOT NULL, updated_at REAL NOT NULL);
            ''')
            yield db

    def status(self):
        return self.encoder.status()

    def _plan(self, blocks):
        status = self.status()
        if status['status'] != 'ready':
            raise ValueError('Multilingual model is not prepared')
        if len(blocks) > MAX_BLOCKS or sum(len(b.text) for b in blocks) > MAX_CHARS:
            raise ValueError('Multilingual source budget exceeded; no partial publication')
        if len({b.block_id for b in blocks}) != len(blocks):
            raise ValueError('Ambiguous canonical block identity')
        sources = sorted([[b.document_id, b.run_id, b.block_id, b.page, b.text, b.bbox] for b in blocks])
        scope = digest(['multilingual-scope-v1', status['fingerprint'], sorted({b.document_id for b in blocks})])
        snapshot = digest(['multilingual-snapshot-v1', status['fingerprint'], sources])
        if self._plan_cache and self._plan_cache[0] == snapshot:
            # One private snapshot per index instance. Rebind current canonical
            # objects so title/filename changes cannot return stale metadata.
            current = {block.block_id: block for block in blocks}
            entries = [(key, current[bid], text, source) for key, bid, text, source in self._plan_cache[1]]
            return status, scope, snapshot, entries
        entries = []
        for block in sorted(blocks, key=lambda b: (b.document_id, b.run_id, b.page, b.block_id)):
            covered = 0
            spans = self.encoder.split(block.text)
            for span in spans:
                start, end, text = span['start'], span['end'], span['text']
                if not (0 <= start <= covered < end <= len(block.text) and text == block.text[start:end]):
                    raise ValueError('Chunk spans do not cover the exact source')
                covered = end
                if not text.strip():
                    continue
                source = [block.document_id, block.run_id, block.block_id, start, end, hashlib.sha256(text.encode()).hexdigest()]
                key = digest(['multilingual-vector-v1', status['fingerprint'], source])
                entries.append((key, block, text, source))
                if len(entries) > MAX_CHUNKS:
                    raise ValueError('Multilingual chunk budget exceeded; no partial publication')
            if covered != len(block.text):
                raise ValueError('Uncovered source tail')
        self._plan_cache = (snapshot, [(key, block.block_id, text, source) for key, block, text, source in entries])
        return status, scope, snapshot, entries

    def _ready(self, db, snapshot, status, keys):
        row = db.execute('SELECT payload FROM generations WHERE snapshot=? AND model=?',
                         (snapshot, status['fingerprint'])).fetchone()
        if not row:
            return None
        payload = json.loads(row['payload'])
        if payload['keys'] != keys:
            raise ValueError('Saved generation does not match its exact source snapshot')
        return payload

    def prepare(self, blocks, *, check_cancel=None):
        """Synchronous build primitive, also used by the owned background job."""
        status, scope, snapshot, entries = self._plan(blocks)
        import numpy as np
        keys = [key for key, _, _, _ in entries]
        with _guard:
            lock = _locks.setdefault(str(self.path.resolve()), threading.Lock())
        if not lock.acquire(blocking=False):
            raise IndexBusy('An index build already owns this workspace')
        if not _slots.acquire(blocking=False):
            lock.release()
            raise IndexBusy('Local multilingual build capacity is busy')
        owner = uuid4().hex
        try:
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = self._ready(db, snapshot, status, keys)
                if existing:
                    healthy = True
                    for key, _, _, source in entries:
                        row = db.execute('SELECT vector,source,model FROM chunk_vectors WHERE cache_key=?', (key,)).fetchone()
                        try:
                            if not row or row['source'] != canonical(source) or row['model'] != status['fingerprint']:
                                raise ValueError('Missing original member')
                            SemanticIndex._vector(np.frombuffer(row['vector'], dtype='<f4'), status['dimensions'])
                        except ValueError:
                            healthy = False
                            break
                    if healthy:
                        return dict(existing, status='ready', cache_reused=True, cache_hits=len(keys), new_chunks=0, encoded_texts=0)
                lease = db.execute('SELECT owner,expires_at FROM leases WHERE scope=?', (scope,)).fetchone()
                if lease and lease['expires_at'] > time.time():
                    raise IndexBusy('Another process holds the build lease')
                db.execute('INSERT OR REPLACE INTO leases VALUES(?,?,?)', (scope, owner, time.time() + 600))
            pending = []
            with self._connection() as db:
                for key, block, text, source in entries:
                    row = db.execute('SELECT vector,source,model FROM chunk_vectors WHERE cache_key=?', (key,)).fetchone()
                    try:
                        if not row or row['model'] != status['fingerprint'] or row['source'] != canonical(source):
                            raise ValueError('Uncached source')
                        SemanticIndex._vector(np.frombuffer(row['vector'], dtype='<f4'), status['dimensions'])
                    except ValueError:
                        pending.append((key, text, source))
            # Reuse exact model input within this source snapshot only. Every
            # original chunk retains its own document/run/offset cache record.
            by_text = {}
            for key, text, source in pending:
                by_text.setdefault(text, []).append((key, source))
            texts = sorted(by_text, key=lambda text: (len(text), text))
            for start in range(0, len(texts), 24):
                if check_cancel:
                    check_cancel()
                batch = texts[start:start + 24]
                values = self.encoder.encode(batch)
                if len(values) != len(batch):
                    raise ValueError('Multilingual encoder count mismatch')
                vectors = [SemanticIndex._vector(value, status['dimensions']) for value in values]
                with self._connection() as db:
                    # A reclaimed/expired lease cannot publish stale progress.
                    changed = db.execute('UPDATE leases SET expires_at=? WHERE scope=? AND owner=?',
                        (time.time() + 600, scope, owner)).rowcount
                    if changed != 1:
                        raise IndexBusy('Build lease ownership changed')
                    for text, vector in zip(batch, vectors):
                        for key, source in by_text[text]:
                            db.execute('INSERT OR REPLACE INTO chunk_vectors VALUES(?,?,?,?)',
                                (key, status['fingerprint'], canonical(source), vector.astype('<f4').tobytes()))
            if check_cancel:
                check_cancel()
            payload = {'scope': scope, 'snapshot': snapshot, 'fingerprint': status['fingerprint'],
                       'keys': keys, 'source_blocks': len(blocks), 'chunks': len(entries),
                       'encoded_texts': len(texts), 'new_chunks': len(pending),
                       'cache_hits': len(entries) - len(pending), 'truncated_blocks': 0}
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                lease = db.execute('SELECT owner FROM leases WHERE scope=?', (scope,)).fetchone()
                if not lease or lease['owner'] != owner:
                    raise IndexBusy('Build no longer owns atomic publication')
                # Verify complete immutable membership before the single commit
                # that makes this generation and scope pointer visible.
                for key in keys:
                    row = db.execute('SELECT vector FROM chunk_vectors WHERE cache_key=?', (key,)).fetchone()
                    if not row:
                        raise ValueError('Missing completed vector')
                    SemanticIndex._vector(np.frombuffer(row[0], dtype='<f4'), status['dimensions'])
                db.execute('INSERT OR REPLACE INTO generations VALUES(?,?,?,?,?)',
                    (snapshot, scope, status['fingerprint'], canonical(payload), time.time()))
                db.execute('INSERT OR REPLACE INTO active_scopes VALUES(?,?)', (scope, snapshot))
            return dict(payload, status='ready', cache_reused=False)
        finally:
            try:
                with self._connection() as db:
                    db.execute('DELETE FROM leases WHERE scope=? AND owner=?', (scope, owner))
            finally:
                _slots.release()
                lock.release()

    def start_build(self, blocks):
        """Schedule an explicit local job; callers may keep reading old snapshots."""
        frozen = tuple(block.model_copy(deep=True) for block in blocks)
        _, _, snapshot, _ = self._plan(frozen)
        key = (str(self.path.resolve()), snapshot)
        with _guard:
            for (path, identity), job in list(_jobs.items()):
                if path == key[0] and job['snapshot'] == snapshot and job['thread'].is_alive():
                    return self.job_status(identity)
            # Keep owned running handles. Finished jobs are persisted in SQLite
            # and do not need unbounded global Python references.
            for old in [k for k, v in _jobs.items() if not v['thread'].is_alive()]:
                _jobs.pop(old)
            identity, cancel = uuid4().hex, threading.Event()
            def update(state, detail):
                with self._connection() as db:
                    db.execute('UPDATE builds SET status=?,detail=?,updated_at=? WHERE id=?',
                               (state, canonical(detail), time.time(), identity))
            def run():
                def check():
                    if cancel.is_set():
                        raise IndexCancelled('Local build cancelled')
                try:
                    update('building', {})
                    result = self.prepare(frozen, check_cancel=check)
                    update('completed', result)
                except IndexCancelled:
                    update('cancelled', {'reason': 'cancelled_between_local_batches'})
                except IndexBusy:
                    update('busy', {'reason': 'local_build_capacity_or_lease_busy'})
                except Exception:
                    update('failed', {'reason': 'local_index_build_failed'})
            thread = threading.Thread(target=run, name='local-multilingual-index', daemon=True)
            with self._connection() as db:
                db.execute('INSERT INTO builds VALUES(?,?,?,?,?)', (identity, snapshot, 'queued', '{}', time.time()))
            _jobs[(key[0], identity)] = {'thread': thread, 'cancel': cancel, 'snapshot': snapshot}
            thread.start()
        return self.job_status(identity)

    def job_status(self, identity):
        with self._connection() as db:
            row = db.execute('SELECT * FROM builds WHERE id=?', (identity,)).fetchone()
        if not row:
            raise ValueError('Unknown local index job in this workspace')
        job = _jobs.get((str(self.path.resolve()), identity))
        result = dict(row)
        result['detail'] = json.loads(result['detail'])
        result['owned_live_thread'] = bool(job and job['thread'].is_alive())
        if result['status'] in {'queued', 'building'} and not result['owned_live_thread']:
            result['observation'] = 'No live owned handle in this process; stored status alone does not prove running.'
        return result

    def cancel(self, identity):
        self.job_status(identity)  # Resolve only within this account's database.
        job = _jobs.get((str(self.path.resolve()), identity))
        if not job:
            raise ValueError('No owned handle to cancel in this process')
        job['cancel'].set()
        return self.job_status(identity)

    async def rank(self, queries, blocks, *, limit=48, check_cancel=None):
        status = self.status()
        if status['status'] != 'ready':
            return [], status
        if (not 1 <= len(queries) <= 13 or any(not isinstance(q, str) or len(q) > 10000 for q in queries)
                or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 300):
            return [], dict(status, status='query_budget_exceeded')
        status, scope, snapshot, entries = await asyncio.to_thread(self._plan, blocks)
        with self._connection() as db:
            payload = self._ready(db, snapshot, status, [key for key, _, _, _ in entries])
            if payload is None:
                return [], dict(status, status='index_not_built', snapshot=snapshot, scope=scope)
            import numpy as np
            vectors = []
            for key, _, _, source in entries:
                row = db.execute('SELECT vector,source,model FROM chunk_vectors WHERE cache_key=?', (key,)).fetchone()
                if not row or row['source'] != canonical(source) or row['model'] != status['fingerprint']:
                    raise ValueError('Ready generation has missing or changed source members')
                vectors.append(SemanticIndex._vector(np.frombuffer(row['vector'], dtype='<f4'), status['dimensions']))
        if not vectors:
            return [], dict(status, status='applied', snapshot=snapshot, source_blocks=len(blocks), chunks=0, matches={})
        query_parts = [span['text'] for q in queries for span in self.encoder.split(q) if span['text'].strip()]
        if not query_parts or len(query_parts) > 64:
            return [], dict(status, status='query_budget_exceeded')
        from .intelligence import IntelligenceService
        await IntelligenceService._check_cancel(check_cancel)
        values = await asyncio.to_thread(self.encoder.encode, query_parts, True)
        if len(values) != len(query_parts):
            raise ValueError('Query embedding count mismatch')
        query_vectors = np.stack([SemanticIndex._vector(v, status['dimensions']) for v in values])
        scores = (np.stack(vectors) @ query_vectors.T).max(axis=1)
        selected = {}
        for (_, block, text, source), score in zip(entries, scores):
            if block.block_id not in selected or float(score) > selected[block.block_id][1]:
                selected[block.block_id] = (block, float(score), {'start': source[3], 'end': source[4],
                    'text_sha256': source[5], 'chunk_text': text})
        ordered = sorted(selected.values(), key=lambda item: (-item[1], item[0].document_id, item[0].page, item[0].block_id))[:limit]
        return [b.model_copy(update={'score': score}) for b, score, _ in ordered], dict(status,
            status='applied', scope=scope, snapshot=snapshot, source_blocks=len(blocks), chunks=len(entries),
            truncated_blocks=0, query_chunks=len(query_parts), network_used=False,
            matches={b.block_id: span for b, _, span in ordered},
            language_policy='Multilingual original-text tokens; no paid translation',
            selection_policy='Ranking candidates only; no identity or factual confirmation')
