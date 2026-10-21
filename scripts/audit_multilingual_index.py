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
            failed = directory/('failed-independent-index-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
            with failed.open('x', encoding='utf-8') as stream:
                json.dump({'invariants_passed': False, 'checks': checks, 'driver_sha256': digest(Path(__file__))}, stream, ensure_ascii=False, indent=2)
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
    kind = report.get('encoder_kind', 'minilm')
    check('known explicit encoder kind', kind in {'minilm', 'e5'})
    plan_path = ROOT/('docs/development/145-e5-model-download-plan-20261020.json' if kind == 'e5'
                      else 'docs/development/142-multilingual-model-download-plan-20261020.json')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    model_directory = ROOT/plan['destination']
    runtime_path = ROOT/('data/models/e5-small-model.json' if kind == 'e5' else 'data/models/multilingual-model.json')
    model = json.loads(runtime_path.read_text(encoding='utf-8'))
    expected_fingerprint = hashlib.sha256(json.dumps({k: v for k, v in model.items() if k != 'fingerprint'},
                                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    check('approved independent model manifest', model['fingerprint'] == fingerprint == expected_fingerprint
          and model['model'] == plan['model'] and model['revision'] == plan['revision']
          and model['source_manifest_sha256'] == digest(plan_path))
    for item in plan['files']:
        path = model_directory/item['path']
        check('exact authorized local bytes '+item['path'], path.stat().st_size == item['size'] and digest(path) == item['sha256'])
    tokenizer = Tokenizer.from_file(str(model_directory/'tokenizer.json'))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    intervals = {}
    with sqlite3.connect((directory/'workspace/multilingual.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT payload FROM generations WHERE snapshot=?', (snapshot,)).fetchone()
        check('ready generation persisted', row is not None)
        generation = json.loads(row[0])
        rows = db.execute('SELECT cache_key,model,source,vector FROM chunk_vectors WHERE model=?', (fingerprint,)).fetchall()
        check('exact complete generation membership', len(rows) == generation['chunks']
              and (kind != 'minilm' or len(rows) == 1484)
              and set(generation['keys']) == {r[0] for r in rows})
        # Row insertion follows length-sorted batches, whereas search uses the
        # immutable generation order. Align those actual keys before BLAS;
        # row tiling can otherwise change float32 ties by one ULP.
        by_key = {r[0]: r for r in rows}
        rows = [by_key[key] for key in generation['keys']]
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
            prefixes = ['query: ', 'passage: '] if kind == 'e5' else ['']
            check('actual untruncated token budget '+key, max(len(tokenizer.encode(prefix+text).ids) for prefix in prefixes) <= plan['max_input_tokens'])
            intervals.setdefault(bid, []).append((start, end))
        if report.get('reused_public_workspace'):
            previous = (ROOT/report['reused_public_workspace']).resolve()
            check('prior public workspace inside isolated artifacts', previous.is_relative_to(ROOT/'artifacts/multilingual-index') and previous != directory)
            with sqlite3.connect((previous/'workspace/multilingual.sqlite3').as_uri()+'?mode=ro', uri=True) as old:
                for table, key_name in [('chunk_vectors', 'cache_key'), ('generations', 'snapshot'), ('active_scopes', 'scope')]:
                    columns = [r[1] for r in old.execute('PRAGMA table_info('+table+')')]
                    key_index = columns.index(key_name)
                    for prior in old.execute('SELECT * FROM '+table):
                        current = db.execute('SELECT * FROM '+table+' WHERE '+key_name+'=?', (prior[key_index],)).fetchone()
                        check('previous model/history retained '+table+' '+str(prior[key_index]), current == prior)
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
    raw_e5 = None
    if kind == 'e5':
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 2
        raw_e5 = ort.InferenceSession(str(model_directory/'model_qint8_avx512_vnni.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
        matrix = np.stack([np.frombuffer(blob, dtype='<f4')/np.linalg.norm(np.frombuffer(blob, dtype='<f4')) for _, _, _, blob in rows])
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
        if raw_e5 is not None:
            encoded = tokenizer.encode('query: '+q['query'])
            check('actual query prefix budget '+q['id'], len(encoded.ids) <= 512)
            ids = np.asarray([encoded.ids], dtype=np.int64)
            feed = {'input_ids': ids, 'attention_mask': np.ones_like(ids), 'token_type_ids': np.zeros_like(ids)}
            hidden = raw_e5.run(['last_hidden_state'], {i.name: feed[i.name] for i in raw_e5.get_inputs()})[0]
            vector = np.asarray((hidden.sum(axis=1)/np.asarray([len(encoded.ids)], dtype=np.int64)[:, None])[0], dtype='<f4')
            # Stored retrieval additionally validates/normalizes the adapter's
            # already unit-length query. Reproduce both documented boundaries;
            # otherwise one-ULP changes can reorder near-tied source records.
            vector /= np.linalg.norm(vector)
            vector /= np.linalg.norm(vector)
            scores = (matrix@vector[:, None]).max(axis=1)
            grouped = {}
            for (_, _, source, _), score in zip(rows, scores):
                bid = json.loads(source)[2]
                grouped[bid] = max(grouped.get(bid, -float('inf')), float(score))
            ordered = sorted(grouped, key=lambda bid: (-grouped[bid], blocks[bid]['document_id'], blocks[bid]['page'], bid))[:20]
            if ordered != [v['block_id'] for v in result['top20']]:
                diagnostic = directory/('rank-mismatch-'+q['id']+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
                with diagnostic.open('x', encoding='utf-8') as stream:
                    json.dump({'query': q['query'], 'independent': [{'block_id': bid, 'score': grouped[bid]} for bid in ordered],
                               'reported': [{'block_id': v['block_id'], 'score': v['score'], 'recomputed_score': grouped[v['block_id']]} for v in result['top20']]}, stream, ensure_ascii=False, indent=2)
            check('independent complete-corpus E5 rank '+q['id'], ordered == [v['block_id'] for v in result['top20']])
            check('independent complete-corpus E5 scores '+q['id'], all(abs(grouped[v['block_id']]-v['score']) < 3e-6 for v in result['top20']))
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
