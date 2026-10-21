"""Reuse the actual reader's quality/BM25/RRF policies on E5, dev split only.

Does not enable E5 in the app, tune on held-out queries, or change raw results.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.e5_model import LocalE5Encoder
from research_workspace.library import Library
from research_workspace.multilingual_index import MultilingualIndex
from research_workspace.reader_search import ReaderSearch
from scripts.freeze_local_acceptance import main_snapshot


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(previous):
    previous = Path(previous).resolve()
    assert previous.is_relative_to(ROOT/'artifacts/multilingual-index')
    raw = json.loads((previous/'report.json').read_text(encoding='utf-8'))
    assert raw['passed'] and raw['encoder_kind'] == 'e5' and raw['split'] == 'development' and len(raw['queries']) == 96
    output = ROOT/'artifacts/e5-reader-development'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    print(json.dumps({'output': str(output)}), flush=True)
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = os.environ['RESEARCH_OCR_ENABLED'] = '0'
    shutil.copytree(previous/'workspace', output/'workspace')
    before, main_before = main_snapshot(output/'workspace'), main_snapshot()
    fixture_path = ROOT/'tests/fixtures/multilingual/20261020-v1/manifest.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    questions = [q for q in fixture['queries'] if q['split'] == 'development']
    expected = {q['id']: q['expected_block_ids'] for q in raw['queries']}
    report = {'scope': 'Existing reader policy on E5 in a new isolated public workspace; not production enablement or held-out acceptance.',
              'fixture_sha256': sha(fixture_path), 'driver_sha256': sha(Path(__file__)), 'queries': [], 'errors': [],
              'paid_model_calls': 0, 'external_attempts': 0, 'previous': str(previous.relative_to(ROOT)),
              'code_sha256': {p: sha(ROOT/p) for p in ['research_workspace/e5_model.py', 'research_workspace/multilingual_index.py',
                                                    'research_workspace/reader_search.py', 'research_workspace/intelligence.py']}}
    original_connect = socket.socket.connect

    def blocked(sock, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', 'localhost', '::1'}:
            return original_connect(sock, address, *args, **kwargs)
        report['external_attempts'] += 1
        raise RuntimeError('External connections forbidden during reader development')

    started = time.monotonic()
    try:
        with patch.object(socket.socket, 'connect', blocked):
            library = Library(output/'workspace')
            index = MultilingualIndex(library, encoder=LocalE5Encoder())
            reader = ReaderSearch(library, semantic=index)
            blocks, _, state = reader.snapshot()
            assert state == 'ready'
            canonical = {b.block_id: b.model_dump(exclude={'score'}) for b in blocks}
            assert index.prepare(blocks)['cache_reused']
            for q in questions:
                row = {'id': q['id'], 'language': q['language'], 'query': q['query'], 'expected_block_ids': expected[q['id']], 'modes': {}}
                for mode in ['semantic', 'hybrid']:
                    result = reader.search(q['query'], mode=mode, limit=20)
                    assert result['status'] == 'searched' and result['complete']
                    for h in result['hits']:
                        assert all(h[k] == value for k, value in canonical[h['block_id']].items())
                    row['modes'][mode] = {'hit_at_5': any(h['block_id'] in expected[q['id']] for h in result['hits'][:5]),
                                          'search_id': result['search_id'], 'ranking_queries': result['ranking_queries'],
                                          'top20': result['hits']}
                report['queries'].append(row)
                with (output/'queries.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                if len(report['queries']) % 16 == 0:
                    print(json.dumps({'queries': len(report['queries']), **{m: sum(q['modes'][m]['hit_at_5'] for q in report['queries']) for m in ['semantic', 'hybrid']}}), flush=True)
    except (Exception, KeyboardInterrupt) as error:
        report['errors'].append(type(error).__name__+': '+str(error))
    report.update({'workspace_raw_unchanged': before == main_snapshot(output/'workspace'), 'main_unchanged': main_before == main_snapshot(),
                   'code_unchanged': all(sha(ROOT/p) == h for p, h in report['code_sha256'].items()), 'elapsed_seconds': round(time.monotonic()-started, 3)})
    report['language_results'] = {m: {lang: {'hits': sum(q['modes'][m]['hit_at_5'] for q in report['queries'] if q['language'] == lang),
                                          'total': sum(q['language'] == lang for q in report['queries'])}
                                       for lang in sorted({q['language'] for q in report['queries']})} for m in ['semantic', 'hybrid']}
    report['invariants_passed'] = not report['errors'] and len(report['queries']) == 96 and report['external_attempts'] == 0 and all(report[k] for k in ['workspace_raw_unchanged', 'main_unchanged', 'code_unchanged'])
    report['quality_passed'] = {m: report['invariants_passed'] and sum(v['hits'] for v in scores.values())/96 >= .9
                               and all(v['hits']/v['total'] >= .8 for v in scores.values()) for m, scores in report['language_results'].items()}
    with (output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'invariants_passed': report['invariants_passed'], 'quality_passed': report['quality_passed'],
                      'language_results': report['language_results'], 'errors': report['errors']}), flush=True)
    return 0 if report['invariants_passed'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
