"""Offline reader retrieval. Ranking signals never replace canonical originals."""
import asyncio
import hashlib
import json
import math
import re
import threading

from .intelligence import Evidence, IntelligenceService
from .library import LibraryError
from .semantic import SemanticIndex

MAX_BLOCKS = 12000
MAX_CHARACTERS = 8_000_000
MIN_SIMILARITY = 0.50
_slots = threading.BoundedSemaphore(2)
STOP = set('a an the is are was were be been of to in on for with by and or it this that how what does do can which we from'.split())


class ReaderSearch:
    def __init__(self, library, *, semantic=None):
        self.library = library
        self.semantic = semantic if semantic is not None else SemanticIndex(library)

    def snapshot(self, document_id=None, run_id=None):
        """All documents, runs and blocks share one consistent database read."""
        if run_id and not document_id:
            raise LibraryError('指定历史解析时请同时指定文献。')
        blocks, sources = [], []
        characters = 0
        with self.library._connection() as db:
            db.execute('BEGIN')
            if document_id:
                ids = [document_id]
            else:
                ids = [r[0] for r in db.execute('''SELECT d.id FROM documents d
                    WHERE d.archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM version_families v
                    WHERE v.id=d.version_family_id AND v.current_document_id IS NOT NULL
                    AND v.current_document_id!=d.id) ORDER BY d.created_at DESC,d.id''')]
            for did in ids:
                doc = self.library._document(db, did)
                run = self.library._run(db, run_id) if run_id else doc['available_run']
                if not run:
                    continue
                if run['document_id'] != did or run['status'] not in {'completed', 'partial'}:
                    raise LibraryError('请选择属于该文献、已经完成的可读解析版本。')
                rows = db.execute('''SELECT id,page,text,bbox FROM blocks WHERE run_id=?
                    ORDER BY page,ordinal LIMIT ?''', (run['id'], MAX_BLOCKS + 1 - len(blocks))).fetchall()
                digest = hashlib.sha256()
                for row in rows:
                    characters += len(row['text'])
                    if len(blocks) >= MAX_BLOCKS or characters > MAX_CHARACTERS:
                        return [], sources, 'source_budget_exceeded'
                    digest.update(json.dumps(list(row), ensure_ascii=False).encode())
                    blocks.append(Evidence(evidence_id='E_' + row['id'], document_id=did,
                        document_title=doc['title'], filename=doc['filename'], run_id=run['id'],
                        block_id=row['id'], page=row['page'], text=row['text'], bbox=json.loads(row['bbox'])))
                sources.append({'document_id':did, 'run_id':run['id'], 'parse_version':run['version'],
                    'parse_status':run['status'], 'pages_done':run['pages_done'], 'total_pages':run['total_pages'],
                    'source_sha256':doc['sha256'], 'blocks':len(rows), 'snapshot_sha256':digest.hexdigest()})
        return blocks, sources, 'ready'

    def search(self, query, *, mode='hybrid', document_id=None, run_id=None, english_query='', limit=20):
        query = query.strip()
        english_query = english_query.strip()
        if not query or len(query) > 200 or len(english_query) > 200:
            raise LibraryError('检索问题不能为空，问题和英文辅助词各不超过200个字符。')
        if mode not in {'hybrid', 'semantic'} or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise LibraryError('检索方式或结果数量无效。')
        result = {'query':query, 'mode':mode, 'english_query':english_query, 'hits':[],
            'method':'reader-local-retrieval-v1', 'status':'ready', 'complete':False,
            'model_calls':0, 'network_used':False, 'source_snapshots':[], 'warnings':[],
            'limits':{'max_blocks':MAX_BLOCKS, 'max_characters':MAX_CHARACTERS, 'min_similarity':MIN_SIMILARITY},
            'score_policy':'Ranking only; similarity is not probability or factual verification.'}
        if not _slots.acquire(blocking=False):
            result.update(status='busy')
            result['warnings'].append('本地检索正在处理其他请求，请稍后重试。')
            return self._audit(result)
        try:
            return self._audit(self._search(result, document_id, run_id, limit))
        finally:
            _slots.release()

    @staticmethod
    def _audit(result):
        result['search_id'] = 'S_' + hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return result

    def _search(self, result, document_id, run_id, limit):
        blocks, sources, status = self.snapshot(document_id, run_id)
        result['source_snapshots'] = sources
        if status != 'ready':
            result.update(status=status)
            result['warnings'].append('可读正文超过本地检索上限；本次没有完成检索，不能据此判断没有相关原文。')
            return result
        if not blocks:
            result.update(status='no_readable_text', complete=True)
            return result
        if any(s['parse_status'] == 'partial' for s in sources):
            result['warnings'].append('部分文献只有部分可读正文，结果不覆盖尚未识别的内容。')
        query = result['query']
        search_query = result['english_query'] or query
        if re.search(r'[\u3400-\u9fff]', search_query):
            result['warnings'].append('本地向量模型主要面向英文；中文检索效果有限，可补充英文检索词。不会自动调用付费模型翻译。')
        canonical = {b.block_id:b for b in blocks}
        lexical_index = IntelligenceService._lexical_index(blocks)
        lexical_query = ' '.join(t for t in IntelligenceService._search_terms(search_query) if t not in STOP)
        lexical = IntelligenceService._rank_lexical(lexical_query, blocks, lexical_index) if result['mode'] == 'hybrid' else []
        try:
            dense, info = asyncio.run(self.semantic.rank([search_query], blocks, limit=min(len(blocks), 100)))
        except Exception:
            dense, info = [], {'status':'failed', 'error':'local_embedding_unavailable'}
        result['semantic'] = info
        if info.get('status') != 'applied':
            dense = []
            result['warnings'].append('本地语义检索未启用或未完成。' + ('本次仅按关键词排序。' if result['mode'] == 'hybrid' else '本次未返回语义结果，可改用混合或字面检索。'))
            result.update(status='lexical_fallback' if result['mode'] == 'hybrid' else 'semantic_unavailable')
        else:
            result.update(status='searched')
        # Bind both identities and all displayed text/positions back to the frozen
        # snapshot. A ranker's altered text or foreign run is never trusted.
        dense_scores = {}
        for hit in dense:
            source = canonical.get(hit.block_id)
            if (source and hit.document_id == source.document_id and hit.run_id == source.run_id
                    and math.isfinite(hit.score) and MIN_SIMILARITY <= hit.score <= 1.00001):
                dense_scores[hit.block_id] = max(dense_scores.get(hit.block_id, -1), float(hit.score))
        # Titles in a bibliography often have high dense similarity but do not
        # explain the requested mechanism. Keep them, with the same explicit
        # source-quality weighting as research retrieval; reference requests
        # remain able to retrieve the bibliography itself.
        quality = {bid:IntelligenceService._evidence_quality(canonical[bid], search_query, lexical_index[3]) for bid in dense_scores}
        dense_ids = sorted(dense_scores, key=lambda bid:(-dense_scores[bid] * quality[bid], bid))
        ranks, channels = {}, {}
        for name, ids in [('semantic', dense_ids), ('keywords', [b.block_id for b in lexical[:100]])]:
            for rank, bid in enumerate(ids, 1):
                ranks[bid] = ranks.get(bid, 0) + 1 / (60 + rank)
                channels.setdefault(bid, []).append(name)
        ordered = sorted(ranks, key=lambda bid:(-ranks[bid], -dense_scores.get(bid, -1), bid))[:limit]
        for bid in ordered:
            hit = canonical[bid].model_dump()
            hit.update(score=round(ranks[bid], 8), similarity=dense_scores.get(bid),
                source_quality_weight=quality.get(bid), channels=channels[bid])
            result['hits'].append(hit)
        result['complete'] = info.get('status') == 'applied'
        if info.get('truncated_blocks'):
            result['complete'] = False
            result['warnings'].append('部分超长文本块仅索引前5800字符，返回的原文仍为完整保存文本；其余内容尚未覆盖。')
        result['retrieved_blocks'] = len(blocks)
        return result
