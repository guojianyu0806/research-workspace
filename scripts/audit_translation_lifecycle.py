"""Independently audit saved checkpoints, actual PDF pixels and DOM positions."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlparse, parse_qs

import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory, regression):
    directory, regression = Path(directory).resolve(), Path(regression).resolve()
    assert directory.is_relative_to(ROOT/'artifacts/translation-lifecycle')
    assert regression.is_relative_to(ROOT/'artifacts/regression-local')
    report = json.loads((directory/'report.json').read_text(encoding='utf-8'))
    fixture = json.loads((ROOT/'tests/fixtures/translation_lifecycle/20261020-v1/manifest.json').read_text(encoding='utf-8'))
    checks = []
    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)
    check('complete actual lifecycle run', report['passed'] and len(report['checks']) == 44
          and not report['errors'] and not report['external'] and all(c['passed'] for c in report['checks']))
    completion = report['completion']
    check('owned service stopped, no model calls, original rows retained', completion['stopped'] and completion['main_unchanged']
          and completion['original_source_rows_and_pdfs_unchanged'] and completion['model_calls'] == 0 and completion['runtime_unchanged'])
    for name, sha in completion['runtime_sha256'].items():
        check('exact tested runtime '+name, digest(ROOT/name) == sha)
    check('exact browser driver', digest(ROOT/'scripts/translation_lifecycle_smoke.cjs') == report['driver_sha256'])
    owner = (directory/completion['owner']).resolve()
    assert owner.is_relative_to(directory/'workspace/spaces')
    frozen = fixture['source_records']
    jobs = {}
    with sqlite3.connect((owner/'library.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for table in ('documents', 'parse_runs', 'pages', 'blocks'):
            rows = [tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
            actual = hashlib.sha256(json.dumps(rows, ensure_ascii=False, default=str, separators=(',', ':')).encode()).hexdigest()
            check('independent original table '+table, actual == fixture['raw_snapshot']['tables'][table]['sha256'])
        for name, sha in fixture['raw_snapshot']['originals'].items():
            check('independent original PDF '+name, digest(owner/'originals'/name) == sha)
        for row in db.execute('SELECT * FROM research_jobs'):
            value = json.loads(row['result']) if row['result'] else None
            if value is None:
                case = 'progress'
            else:
                bodies = value['blocks']
                ids = [b.get('block_id') for b in bodies]
                case = 'legacy' if all(v is None for v in ids) else 'duplicate' if len(set(ids)) < len(ids) else 'valid'
            check('unique lifecycle task '+case, case not in jobs)
            jobs[case] = dict(row)
            if case == 'progress':
                bodies = [json.loads(r[0]) for r in db.execute('SELECT payload FROM research_job_items WHERE job_id=? ORDER BY rowid', (row['id'],))]
                expected_ordinals = list(range(8, 50))+list(range(51, 168))+[50, 168]
                check('cancelled checkpoints and late saved response retained', row['status'] == 'cancelled'
                      and row['cancel_requested'] == 1 and row['progress'] == len(bodies) == 161 and row['total'] == 240)
            else:
                expected_ordinals = [0, 1, 2]+list(range(8, 208))
                check('all completed case records retained '+case, row['status'] == 'completed'
                      and row['progress'] == row['total'] == len(bodies) == 203)
                prior = [json.loads(r[0]) for r in db.execute('SELECT payload FROM research_job_items WHERE job_id=? ORDER BY rowid', (row['id'],))]
                check('all prior checkpoint records kept '+case, len(prior) == 203)
                for i, (body, ordinal) in enumerate(zip(prior, list(range(8, 208))+[0, 1, 2])):
                    check('prior source text '+case+' '+str(i), hashlib.sha256(body['source_text'].encode()).hexdigest() == frozen[ordinal]['source_text_sha256'])
                    check('prior target text '+case+' '+str(i), hashlib.sha256(body['translated_text'].encode()).hexdigest() == frozen[ordinal]['target_text_sha256'])
                    gold = frozen[ordinal]
                    expected_id = None if case == 'legacy' else frozen[18]['block_id'] if case == 'duplicate' and ordinal == 145 else gold['block_id']
                    check('prior complete source identity '+case+' '+str(i), body.get('block_id') == expected_id
                          and body['document_id'] == fixture['document_id'] and body['run_id'] == fixture['run_id']
                          and body['page'] == gold['page'] and body['bbox'] == gold['bbox'])
            check('exact body sequence '+case, len(bodies) == len(expected_ordinals))
            for position, (body, ordinal) in enumerate(zip(bodies, expected_ordinals)):
                gold = frozen[ordinal]
                check('saved original text '+case+' '+str(position), hashlib.sha256(body['source_text'].encode()).hexdigest() == gold['source_text_sha256'])
                expected_id = None if case == 'legacy' else frozen[18]['block_id'] if case == 'duplicate' and ordinal == 145 else gold['block_id']
                check('saved original identity and position '+case+' '+str(position), body.get('block_id') == expected_id
                      and body['document_id'] == fixture['document_id'] and body['run_id'] == fixture['run_id']
                      and body['page'] == gold['page'] and body['bbox'] == gold['bbox'])
                target = body['translated_text']
                if case == 'progress' and ordinal == 50:
                    suffix = '\n【受控替换；保留同一原文来源】'+'尺寸变化联动。'*100
                    check('explicit changed target retained', target.endswith(suffix))
                    target = target[:-len(suffix)]
                check('saved target exact frozen literal '+case+' '+str(position), hashlib.sha256(target.encode()).hexdigest() == gold['target_text_sha256'])
            meta = db.execute('SELECT total FROM translation_page_meta WHERE job_id=?', (row['id'],)).fetchone()
            check('derived paging total '+case, meta and meta['total'] == len(bodies))
        check('all four declared cases present', set(jobs) == {'valid', 'legacy', 'duplicate', 'progress'})
        check('zero provider receipts', db.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0)
        original = report['original_view']
        block = db.execute('SELECT * FROM blocks WHERE id=?', (original['block_id'],)).fetchone()
        run = db.execute('SELECT document_id FROM parse_runs WHERE id=?', (original['run_id'],)).fetchone()
        doc = db.execute('SELECT * FROM documents WHERE id=?', (original['document_id'],)).fetchone()
        check('reader evidence resolves to exact original page and area', run['document_id'] == doc['id'] == fixture['document_id']
              and block['run_id'] == original['run_id'] == fixture['run_id'] and block['page'] == original['page']
              and json.loads(block['bbox']) == original['bbox'])
        image_url = urlparse(original['image'])
        check('actual image URL bound to physical page', image_url.path == f'/api/documents/{doc["id"]}/pages/{original["page"]}/image')
        image = directory/original['file']
        check('actual PNG byte checksum', digest(image) == original['png_sha256'])
        pixels = pymupdf.Pixmap(str(image))
        rgba = pymupdf.Pixmap(pixels, 1)
        check('actual browser canvas pixel hash', hashlib.sha256(rgba.samples).hexdigest() == original['rgba_sha256']
              and pixels.width == original['width'] and pixels.height == original['height'])
        with pymupdf.open(owner/'originals'/doc['storage_name']) as pdf:
            scale = float(parse_qs(image_url.query)['scale'][0])
            direct = pdf[original['page']-1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            check('direct original PDF physical pixels', hashlib.sha256(pymupdf.Pixmap(direct, 1).samples).hexdigest() == original['rgba_sha256'])
    samples = {value['label']: value for value in report['positions']}
    check('unique reading position follows inserted prefix', samples['unique-before']['anchor']['block_id'] == samples['unique-after']['anchor']['block_id']
          and samples['unique-before']['anchor']['ordinal'] == 137 and samples['unique-after']['anchor']['ordinal'] == 140)
    check('anonymous old record cannot restore wrong ordinal', samples['legacy-before']['anchor']['ambiguous_block_id']
          and samples['legacy-before']['anchor']['ordinal'] == 137 and samples['legacy-after']['anchor']['ordinal'] == 0)
    check('duplicate old record cannot select another occurrence', samples['duplicate-after']['anchor']['ordinal'] == 0)
    check('cancel preserves moved checkpoint source', samples['progress-before']['anchor']['block_id'] == samples['progress-cancelled']['anchor']['block_id']
          and samples['progress-before']['anchor']['ordinal'] == 42 and samples['progress-cancelled']['anchor']['ordinal'] == 159)
    for sample in report['positions']:
        for side, measured in sample['panes'].items():
            check('independent DOM bound '+sample['label']+' '+side, measured['dom'] <= 120)
            check('independent DOM settled position '+sample['label']+' '+side,
                  measured['error'] is not None and measured['error'] <= measured['height']*.1)
    check('cancel response is bounded', report['cancel_response']['items_deferred'] is True
          and report['cancel_response']['completed_items'] == 0
          and parse_qs(report['cancel_response']['search'].lstrip('?'))['include_result'] == ['false'])
    for request in report['requests']:
        if request['method'] == 'GET' and request['path'].startswith('/api/research/tasks/') and '/' not in request['path'][len('/api/research/tasks/'):]:
            check('no full automatic GET '+request['path'], parse_qs(request['search'].lstrip('?'))['include_result'] == ['false'])
    full = json.loads((regression/'summary.json').read_text(encoding='utf-8'))
    sources = json.loads((regression/'manifest.json').read_text(encoding='utf-8'))
    check('full 1214-test regression', full['passed'] and full['tests'] == 1214 and not(full['failures'] or full['errors'] or full['skipped']))
    for name, sha in sources['source_sha256'].items():
        check('current regression source '+name, digest(ROOT/name) == sha)
    output = directory/('independent-lifecycle-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump({'passed': True, 'checks': checks, 'driver_sha256': digest(Path(__file__)),
                   'scope': 'Actual saved records, original PDF pixels, positions and local lifecycle; not model translation quality or every malformed legacy shape.'},
                  stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'passed': True, 'checks': len(checks)}), flush=True)


if __name__ == '__main__':
    audit(*sys.argv[1:])
