"""Independent stored source, offset, tokenizer and recall audit of the dev run."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unicodedata

import numpy as np
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold()


def audit(directory):
    directory = Path(directory).resolve()
    assert directory.is_relative_to(ROOT/'artifacts/multilingual-index')
    report = json.loads((directory/'report.json').read_text(encoding='utf-8'))
    fixture_path = ROOT/'tests/fixtures/multilingual/20261020-v1/manifest.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    checks = []
    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)
    check('complete development invariant run', report['passed'] and report['split'] == 'development'
          and len(report['queries']) == 96 and not report['errors'])
    check('exact frozen fixture and driver', digest(fixture_path) == report['fixture_sha256']
          and digest(ROOT/'scripts/multilingual_index_acceptance.py') == report['driver_sha256'])
    for name, sha in report['runtime_sha256'].items():
        check('current runtime '+name, digest(ROOT/name) == sha)
    check('builder finished and main data retained', report['owned_builder_stopped'] and report['main_unchanged'] and report['runtime_unchanged'])
    check('no paid or external requests', report['paid_model_calls'] == report['external_connection_attempts'] == 0)
    blocks = {}
    with sqlite3.connect((directory/'workspace/library.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for doc in db.execute('SELECT * FROM documents'):
            source = fixture['sources'][doc['filename']]
            original = directory/'workspace/originals'/doc['storage_name']
            check('stored and frozen original '+doc['id'], digest(original) == doc['sha256'] == source['sha256'] == digest(ROOT/source['path']))
        for row in db.execute('''SELECT b.*,r.document_id,d.filename,d.title FROM blocks b
            JOIN parse_runs r ON b.run_id=r.id JOIN documents d ON r.document_id=d.id'''):
            blocks[row['id']] = dict(row) | {'bbox': json.loads(row['bbox'])}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check('no model-call receipts', 'model_calls' not in tables or db.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0)
    fingerprint = report['build']['detail']['fingerprint']
    snapshot = report['build']['detail']['snapshot']
    tokenizer = Tokenizer.from_file(str(ROOT/'data/models/multilingual-minilm-l12-v2-onnx-q-faf4aa4/tokenizer.json'))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    intervals = {}
    with sqlite3.connect((directory/'workspace/multilingual.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT payload FROM generations WHERE snapshot=?', (snapshot,)).fetchone()
        check('ready generation persisted', row is not None)
        generation = json.loads(row[0])
        rows = db.execute('SELECT cache_key,model,source,vector FROM chunk_vectors').fetchall()
        check('exact complete generation membership', len(rows) == generation['chunks'] == 1484
              and set(generation['keys']) == {r[0] for r in rows})
        check('active scope atomically points to ready snapshot', db.execute('SELECT snapshot FROM active_scopes WHERE scope=?',
              (generation['scope'],)).fetchone()[0] == snapshot)
        for key, model, encoded_source, blob in rows:
            source = json.loads(encoded_source)
            did, rid, bid, start, end, text_sha = source
            block = blocks[bid]
            check('canonical vector source '+key, block['document_id'] == did and block['run_id'] == rid and 0 <= start < end <= len(block['text']))
            text = block['text'][start:end]
            check('exact original span '+key, hashlib.sha256(text.encode()).hexdigest() == text_sha)
            payload = ['multilingual-vector-v1', fingerprint, source]
            expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            check('model and version key '+key, model == fingerprint and key == expected)
            values = np.frombuffer(blob, dtype='<f4')
            check('finite normalized actual vector '+key, values.shape == (384,) and np.isfinite(values).all() and abs(float(values@values)-1) < .0001)
            check('actual untruncated token budget '+key, len(tokenizer.encode(text).ids) <= 128)
            intervals.setdefault(bid, []).append((start, end))
    for bid, block in blocks.items():
        covered = 0
        for start, end in sorted(intervals.get(bid, [])):
            check('source coverage '+bid+' '+str(start), start <= covered < end)
            covered = end
        check('source tail covered '+bid, covered == len(block['text']) or not block['text'].strip())
    frozen_queries = {q['id']: q for q in fixture['queries'] if q['split'] == 'development'}
    facts = {f['id']: f for f in fixture['facts'] if f['split'] == 'development'}
    check('all and only development questions', len({q['id'] for q in report['queries']}) == 96
          and {q['id'] for q in report['queries']} == set(frozen_queries))
    recomputed = {}
    for result in report['queries']:
        q = frozen_queries[result['id']]
        fact = facts[q['fact_id']]
        expected = {bid for bid, b in blocks.items() if b['filename'] == fact['source']
            and b['page'] == fact['evidence']['physical_page'] and normalized(fact['evidence']['anchor']) in normalized(b['text'])}
        check('exact expected paragraph '+q['id'], expected == set(result['expected_block_ids']) and bool(expected))
        check('frozen native text and original page box '+q['id'], any(
            normalized(blocks[bid]['text']) == normalized(fact['evidence']['native_text'])
            and max(abs(a-b) for a, b in zip(blocks[bid]['bbox'], fact['evidence']['bbox'])) < 1e-9
            for bid in expected))
        check('unchanged frozen question '+q['id'], result['query'] == q['query'] and result['language'] == q['language'])
        hit = any(h['block_id'] in expected for h in result['top20'][:5])
        check('independent recall '+q['id'], hit == result['recall_at_5'])
        totals = recomputed.setdefault(q['language'], {'hits': 0, 'total': 0})
        totals['hits'] += hit
        totals['total'] += 1
        for value in result['top20']:
            original = blocks[value['block_id']]
            check('returned canonical fields '+q['id']+' '+value['block_id'], all(
                value[field] == original[field] for field in ('document_id', 'run_id', 'page', 'text', 'bbox', 'filename')))
            span = result['matches'][value['block_id']]
            check('returned original character range '+q['id']+' '+value['block_id'],
                  original['text'][span['start']:span['end']] == span['chunk_text']
                  and hashlib.sha256(span['chunk_text'].encode()).hexdigest() == span['text_sha256'])
    check('all measured language totals exact', report['recall_at_5'] == recomputed)
    total_hits = sum(v['hits'] for v in recomputed.values())
    quality = total_hits/96 >= .9 and all(v['hits']/v['total'] >= .8 for v in recomputed.values())
    output = directory/('independent-index-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump({'invariants_passed': True, 'candidate_quality_passed': quality, 'candidate_hits': total_hits,
                   'candidate_total': 96, 'language_results': recomputed, 'checks': checks,
                   'driver_sha256': digest(Path(__file__)), 'scope': 'Stored origins and development recall; no held-out or end-to-end S3 quality acceptance.'},
                  stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'invariants_passed': True, 'checks': len(checks),
                      'candidate_hits': total_hits, 'candidate_quality_passed': quality}), flush=True)


if __name__ == '__main__':
    audit(sys.argv[1])
