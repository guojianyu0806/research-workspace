"""Independently inspect stored source and derived records after HTTP shutdown."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def expected_blocks(count,variant):
    # Independent transcription of the frozen input contract, not the generator import.
    blocks=[]
    for ordinal in range(count):
        text=f'SYNTHETIC original paragraph {ordinal}, value 42. '
        if ordinal%11==0:text+='E = mc^2; sum_i x_i.\n'
        text*=300 if ordinal%31==0 else 2+ordinal%4
        translated=f'合成联动资料第{ordinal}段，数值42。'*(250 if ordinal%29==0 else 2+ordinal%3)
        if variant=='missing' and ordinal%37==0:translated=''
        blocks.append(dict(document_id='synthetic-long-document',run_id='synthetic-other-run' if variant=='wrong-run' and ordinal==count-1 else 'synthetic-long-run-v1',
            block_id='synthetic-block-00001' if variant=='duplicate' and ordinal==count-1 else f'synthetic-block-{ordinal:05d}',
            page=ordinal//18+1,bbox=[.1,.1,.9,.3],source_text=text,translated_text=translated))
    return blocks


def audit(directory):
    directory=Path(directory).resolve()
    if not directory.is_relative_to(ROOT/'artifacts/translation-pages'):raise ValueError('Expected project translation paging artifacts')
    evidence=json.loads((directory/'report.json').read_text(encoding='utf-8'))
    manifest_path=ROOT/'tests/fixtures/long_reading/20261020-v1/manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    checks=[]
    def check(name,passed):
        checks.append(dict(name=name,passed=bool(passed)))
        if not passed:raise AssertionError(name)
    check('HTTP run finished and passed',evidence['passed'] and evidence['stopped'] and not evidence['errors'])
    check('301 HTTP gates passed',len(evidence['checks'])==301 and all(c['passed'] for c in evidence['checks']))
    check('same frozen manifest',hashlib.sha256(manifest_path.read_bytes()).hexdigest()==evidence['manifest_sha256'])
    for name,value in evidence['runtime_sha256'].items():check('current runtime '+name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value)
    saved_path=ROOT/manifest['saved_translation']['path']
    check('saved translation source file SHA',hashlib.sha256(saved_path.read_bytes()).hexdigest()==manifest['saved_translation']['sha256'])
    saved=json.loads(saved_path.read_text(encoding='utf-8'))
    expected={'saved-107':saved['blocks']}
    for case in manifest['cases']:
        blocks=expected_blocks(case['count'],case['variant'])
        check('independent input digest '+case['id'],digest(blocks)==case['canonical_sha256'])
        expected[case['id']]=blocks
    with sqlite3.connect((ROOT/'data/library.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT id,result FROM research_jobs WHERE kind='translate' AND json_extract(result,'$.run_id')=?",(saved['run_id'],)).fetchall()
        matching=[r for r in rows if hashlib.sha256(r[0].encode()).hexdigest()==manifest['saved_translation']['source_task_sha256']]
        check('frozen original model result still byte identical',len(matching)==1 and hashlib.sha256(matching[0][1].encode()).hexdigest()==manifest['saved_translation']['original_result_sha256'])
        for block in saved['blocks']:
            source=db.execute('SELECT b.text,b.bbox,b.page,r.document_id FROM blocks b JOIN parse_runs r ON r.id=b.run_id WHERE b.id=? AND b.run_id=?',(block['block_id'],block['run_id'])).fetchone()
            check('saved source '+block['block_id'],bool(source) and source[0]==block['source_text'] and json.loads(source[1])==block['bbox'] and source[2]==block['page'] and source[3]==block['document_id'])
    found={};model_calls=0
    for path in (directory/'workspace').rglob('library.sqlite3'):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='research_jobs'").fetchone():continue
            model_calls+=db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
            for job in db.execute("SELECT id,result FROM research_jobs WHERE kind='translate'"):
                value=json.loads(job['result']);blocks=value['blocks']
                keys=[label for label,source in expected.items() if blocks==source]
                check('persisted result matches one frozen input '+job['id'],len(keys)==1)
                label=keys[0];check('one job per frozen case '+label,label not in found);found[label]=True
                meta=db.execute('SELECT * FROM translation_page_meta WHERE job_id=?',(job['id'],)).fetchone()
                check('saved clean revision '+label,meta and meta['total']==len(blocks) and meta['dirty']==0 and meta['revision']>=1 and meta['source_kind']=='result' and not meta['error'])
                derived=db.execute('SELECT * FROM translation_page_blocks WHERE job_id=? ORDER BY ordinal',(job['id'],)).fetchall()
                check('all ordinals persisted '+label,[r['ordinal'] for r in derived]==list(range(len(blocks))))
                check('derived payloads byte-content equivalent '+label,[json.loads(r['payload']) for r in derived]==blocks)
                check('stored lengths exact '+label,all(r['source_chars']==len(b['source_text']) and r['translation_chars']==len(b['translated_text']) for r,b in zip(derived,blocks)))
                check('stored missing flags exact '+label,all(bool(r['missing_translation'])==(not b['translated_text'].strip()) for r,b in zip(derived,blocks)))
                check('source identity validity exact '+label,all(bool(r['anchor_valid'])==(b['document_id']==value['document_id'] and b['run_id']==value['run_id']) for r,b in zip(derived,blocks)))
                case=next(c for c in evidence['cases'] if c['id']==label)
                check('reported reconstructed digest '+label,case['canonical_blocks_sha256']==digest(blocks))
    check('all six cases present',set(found)==set(expected))
    check('all workspaces no model calls',model_calls==0)
    output=directory/('independent-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with output.open('x',encoding='utf-8') as stream:json.dump(dict(passed=True,checks=checks,cases=len(found),model_calls=model_calls,
        scope='Independent source/SQLite audit; not browser or translation language quality.'),stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(output),passed=True,checks=len(checks),cases=len(found))),flush=True)


if __name__=='__main__':audit(sys.argv[1])
