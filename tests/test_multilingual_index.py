import asyncio
import hashlib
import sqlite3
import threading
import time

import pytest

from research_workspace.intelligence import Evidence
from research_workspace.library import Library
from research_workspace import multilingual_index as module


def block(name, text, run='run', **changes):
    return Evidence(evidence_id='E_'+name, document_id='doc', run_id=run, block_id=name,
        document_title='Original', filename='source.pdf', page=1, bbox=[.1, .2, .8, .3], text=text).model_copy(update=changes)


class Encoder:
    def __init__(self, fingerprint='test-multilingual'):
        self.fingerprint = fingerprint
        self.calls = []
        self.fail_after = None
    def status(self):
        return {'status': 'ready', 'model': 'contract-only', 'fingerprint': self.fingerprint, 'dimensions': 2}
    def split(self, text):
        spans = []
        for i in range(0, len(text), 48):
            spans.append({'start': i, 'end': min(i+64, len(text)), 'text': text[i:i+64], 'tokens': len(text[i:i+64])+2})
            if i+64 >= len(text):
                break
        return spans
    def encode(self, texts, query=False):
        self.calls.append((list(texts), query))
        if not query and self.fail_after is not None and sum(not q for _, q in self.calls) > self.fail_after:
            raise ValueError('Controlled local encoding failure')
        return [[1., 0.] if any(w in text for w in ('canine', 'dog', '狗')) else [0., 1.] for text in texts]


def rank(index, blocks, query='dog'):
    return asyncio.run(index.rank([query], blocks))


def test_explicit_preparation_keeps_full_original_tail_and_source_positions(tmp_path):
    lib = Library(tmp_path)
    encoder = Encoder()
    index = module.MultilingualIndex(lib, encoder=encoder)
    source = block('long', 'noise ' * 1400 + 'canine TAIL')
    assert rank(index, [source])[1]['status'] == 'index_not_built'
    prepared = index.prepare([source])
    hits, info = rank(index, [source])
    assert prepared['status'] == 'ready' and prepared['truncated_blocks'] == 0
    assert hits[0].model_dump(exclude={'score'}) == source.model_dump(exclude={'score'})
    match = info['matches'][source.block_id]
    assert 'canine' in match['chunk_text']
    assert source.text[match['start']:match['end']] == match['chunk_text']
    assert match['text_sha256'] == hashlib.sha256(match['chunk_text'].encode()).hexdigest()
    assert info['source_blocks'] == 1 and info['chunks'] > 100


def test_exact_text_computation_reused_without_coalescing_source_identity(tmp_path):
    encoder = Encoder()
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    blocks = [block(str(i), 'canine') for i in range(50)]
    first = index.prepare(blocks)
    assert first['new_chunks'] == 50 and first['encoded_texts'] == 1
    with sqlite3.connect(index.path) as db:
        assert db.execute('SELECT count(*) FROM chunk_vectors').fetchone()[0] == 50
    again = index.prepare(blocks)
    assert again['cache_reused'] and again['cache_hits'] == 50 and again['new_chunks'] == again['encoded_texts'] == 0
    assert {b.block_id for b in asyncio.run(index.rank(['dog'], blocks, limit=100))[0]} == {b.block_id for b in blocks}


def test_private_plan_cache_avoids_retokenization_but_rebinds_current_metadata(tmp_path):
    encoder = Encoder()
    calls = []
    original = encoder.split
    def observed(text):
        calls.append(text)
        return original(text)
    encoder.split = observed
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    source = block('same', 'canine source')
    index.prepare([source])
    calls.clear()
    current = source.model_copy(update={'document_title': 'Current title', 'filename': 'renamed.pdf'})
    hits, _ = rank(index, [current])
    assert hits[0].document_title == 'Current title' and hits[0].filename == 'renamed.pdf'
    assert calls == ['dog']
    calls.clear()
    altered = source.model_copy(update={'text': 'changed canine source'})
    assert rank(index, [altered])[1]['status'] == 'index_not_built'
    assert altered.text in calls


def test_cache_is_workspace_model_run_text_and_geometry_bound(tmp_path):
    lib = Library(tmp_path/'alice')
    encoder = Encoder()
    index = module.MultilingualIndex(lib, encoder=encoder)
    a = block('same', 'canine')
    original = index.prepare([a])
    for modified in [a.model_copy(update={'run_id': 'new'}), a.model_copy(update={'text': 'new canine'}),
                     a.model_copy(update={'page': 2}), a.model_copy(update={'bbox': [.2, .3, .9, .4]})]:
        assert rank(index, [modified])[1]['status'] == 'index_not_built'
        assert index.prepare([modified])['snapshot'] != original['snapshot']
        assert rank(index, [a])[0][0].page == 1
    changed = module.MultilingualIndex(lib, encoder=Encoder('another-model'))
    assert rank(changed, [a])[1]['status'] == 'index_not_built'
    assert changed.prepare([a])['cache_hits'] == 0
    other = module.MultilingualIndex(Library(tmp_path/'bob'), encoder=encoder)
    assert rank(other, [a])[1]['status'] == 'index_not_built'
    assert other.prepare([a])['cache_hits'] == 0


def test_failure_preserves_previous_atomic_generation_and_partial_progress(tmp_path):
    encoder = Encoder()
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    old = [block('old', 'canine')]
    initial = index.prepare(old)
    new = [block('new'+str(i), 'canine '+str(i), run='new') for i in range(60)]
    encoder.calls.clear()
    encoder.fail_after = 1
    with pytest.raises(ValueError):
        index.prepare(new)
    encoder.fail_after = None
    assert rank(index, old)[1]['snapshot'] == initial['snapshot']
    assert rank(index, new)[1]['status'] == 'index_not_built'
    with sqlite3.connect(index.path) as db:
        assert db.execute('SELECT snapshot FROM active_scopes').fetchone()[0] == initial['snapshot']
        assert db.execute('SELECT count(*) FROM generations').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM chunk_vectors').fetchone()[0] == 25
    final = index.prepare(new)
    assert final['cache_hits'] == 24 and final['new_chunks'] == 36
    assert rank(index, new)[1]['snapshot'] == final['snapshot']
    assert rank(index, old)[1]['snapshot'] == initial['snapshot']


def test_cancel_keeps_old_publication_and_resumes_completed_chunks(tmp_path):
    index = module.MultilingualIndex(Library(tmp_path), encoder=Encoder())
    old = [block('old', 'canine')]
    initial = index.prepare(old)
    new = [block(str(i), 'canine '+str(i), run='new') for i in range(60)]
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise module.IndexCancelled()
    with pytest.raises(module.IndexCancelled):
        index.prepare(new, check_cancel=cancel)
    assert rank(index, old)[1]['snapshot'] == initial['snapshot']
    assert index.prepare(new)['cache_hits'] == 24


@pytest.mark.parametrize('vector', [[0, 0], [float('nan'), 1], [1, 0, 0]])
def test_bad_vectors_never_publish_a_generation(tmp_path, vector):
    encoder = Encoder()
    encoder.encode = lambda texts, query=False: [vector for _ in texts]
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    with pytest.raises(ValueError):
        index.prepare([block('one', 'canine')])
    with sqlite3.connect(index.path) as db:
        assert db.execute('SELECT count(*) FROM generations').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM active_scopes').fetchone()[0] == 0


def test_unprepared_model_does_not_create_an_index(tmp_path):
    encoder = Encoder()
    encoder.status = lambda: {'status': 'model_not_prepared'}
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    assert rank(index, [block('a', 'source')])[1]['status'] == 'model_not_prepared'
    assert not index.path.exists()


def test_duplicate_ids_and_partial_chunk_coverage_are_rejected(tmp_path):
    encoder = Encoder()
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    with pytest.raises(ValueError, match='identity'):
        index.prepare([block('same', 'one'), block('same', 'two', document_id='other')])
    encoder.split = lambda text: [{'start': 0, 'end': 3, 'text': text[:3]}]
    with pytest.raises(ValueError, match='tail'):
        index.prepare([block('one', 'canine tail')])


def test_source_and_query_budgets_fail_explicitly(tmp_path, monkeypatch):
    index = module.MultilingualIndex(Library(tmp_path), encoder=Encoder())
    with monkeypatch.context() as change:
        change.setattr(module, 'MAX_CHARS', 2)
        with pytest.raises(ValueError, match='budget'):
            index.prepare([block('a', 'canine')])
    assert asyncio.run(index.rank(['x']*14, []))[1]['status'] == 'query_budget_exceeded'
    assert asyncio.run(index.rank(['x'*10001], []))[1]['status'] == 'query_budget_exceeded'
    index.prepare([block('a', 'canine')])
    hits, result = rank(index, [block('a', 'canine')], 'noise '*40 + 'dog')
    assert result['query_chunks'] > 1 and hits[0].score == 1


def test_owned_background_job_dedup_cancel_and_foreign_account_boundary(tmp_path):
    entered, release = threading.Event(), threading.Event()
    encoder = Encoder()
    original = encoder.encode
    def paused(texts, query=False):
        if not query and any('new' in text for text in texts):
            entered.set()
            assert release.wait(5)
        return original(texts, query)
    encoder.encode = paused
    index = module.MultilingualIndex(Library(tmp_path/'alice'), encoder=encoder)
    old = [block('old', 'canine')]
    initial = index.prepare(old)
    new = [block('new'+str(i), 'new canine '+str(i), run='new') for i in range(60)]
    job = index.start_build(new)
    try:
        assert entered.wait(3)
        assert index.job_status(job['id'])['owned_live_thread']
        assert index.start_build(new)['id'] == job['id']
        other = module.MultilingualIndex(Library(tmp_path/'bob'), encoder=encoder)
        with pytest.raises(ValueError, match='workspace'):
            other.job_status(job['id'])
        with pytest.raises(ValueError, match='workspace'):
            other.cancel(job['id'])
        assert rank(index, old)[1]['snapshot'] == initial['snapshot']
        index.cancel(job['id'])
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while index.job_status(job['id'])['owned_live_thread']:
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert index.job_status(job['id'])['status'] == 'cancelled'
    assert rank(index, new)[1]['status'] == 'index_not_built'
    assert rank(index, old)[1]['snapshot'] == initial['snapshot']


def test_stored_job_state_does_not_claim_a_live_thread(tmp_path):
    index = module.MultilingualIndex(Library(tmp_path), encoder=Encoder())
    with index._connection() as db:
        db.execute('INSERT INTO builds VALUES(?,?,?,?,?)', ('stored', 'snapshot', 'building', '{}', 0))
    status = index.job_status('stored')
    assert not status['owned_live_thread'] and 'observation' in status
    with pytest.raises(ValueError, match='owned handle'):
        index.cancel('stored')


def test_english_index_and_raw_library_are_never_written(tmp_path):
    lib = Library(tmp_path)
    legacy = tmp_path/'semantic.sqlite3'
    legacy.write_bytes(b'unchanged-English-index-sentinel')
    original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [legacy, tmp_path/'library.sqlite3']}
    index = module.MultilingualIndex(lib, encoder=Encoder())
    index.prepare([block('original', 'canine')])
    rank(index, [block('original', 'canine')])
    assert original == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [legacy, tmp_path/'library.sqlite3']}


def test_public_encoder_shared_without_sharing_account_index(tmp_path):
    a = module.MultilingualIndex(Library(tmp_path/'one'))
    b = module.MultilingualIndex(Library(tmp_path/'two'))
    assert a.encoder is b.encoder and a.path != b.path


def test_live_cross_process_lease_blocks_rebuild_without_removing_old_results(tmp_path):
    encoder = Encoder()
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    old = [block('old', 'canine')]
    original = index.prepare(old)
    new = [block('new', 'new canine', run='new')]
    with index._connection() as db:
        db.execute('INSERT INTO leases VALUES(?,?,?)', (original['scope'], 'other-process-owner', time.time()+60))
    with pytest.raises(module.IndexBusy, match='Another process'):
        index.prepare(new)
    assert rank(index, old)[1]['snapshot'] == original['snapshot']
    with index._connection() as db:
        assert db.execute('SELECT owner FROM leases').fetchone()[0] == 'other-process-owner'
        db.execute('UPDATE leases SET expires_at=0')
    assert index.prepare(new)['status'] == 'ready'


def test_builder_cannot_publish_after_lease_owner_changes(tmp_path):
    encoder = Encoder()
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    old = [block('old', 'canine')]
    initial = index.prepare(old)
    original_encode = encoder.encode
    def reclaim(texts, query=False):
        if not query:
            with index._connection() as db:
                db.execute('UPDATE leases SET owner=?', ('new-owner',))
        return original_encode(texts, query)
    encoder.encode = reclaim
    with pytest.raises(module.IndexBusy, match='ownership'):
        index.prepare([block('new', 'new canine')])
    assert rank(index, old)[1]['snapshot'] == initial['snapshot']
    with index._connection() as db:
        assert db.execute('SELECT count(*) FROM generations').fetchone()[0] == 1
        assert db.execute('SELECT owner FROM leases').fetchone()[0] == 'new-owner'


@pytest.mark.parametrize('corruption', ['missing', 'invalid_vector'])
def test_corrupted_ready_generation_is_repaired_before_reuse(tmp_path, corruption):
    encoder = Encoder()
    index = module.MultilingualIndex(Library(tmp_path), encoder=encoder)
    source = [block('one', 'canine')]
    original = index.prepare(source)
    with index._connection() as db:
        if corruption == 'missing':
            db.execute('DELETE FROM chunk_vectors')
        else:
            db.execute('UPDATE chunk_vectors SET vector=?', (b'\0'*8,))
    repaired = index.prepare(source)
    assert not repaired['cache_reused'] and repaired['new_chunks'] == 1
    assert repaired['snapshot'] == original['snapshot']
    assert rank(index, source)[0][0].score == 1
