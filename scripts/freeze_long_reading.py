"""Freeze saved translation identity and explicitly synthetic long-reader cases."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot,sha256
DEFAULT=ROOT/'tests/fixtures/long_reading/20261020-v1'


def synthetic_blocks(count,variant='mixed'):
    rows=[]
    for i in range(count):
        source=(f'SYNTHETIC original paragraph {i}, value 42. '+('E = mc^2; sum_i x_i.\n' if i%11==0 else ''))
        translated=f'合成联动资料第{i}段，数值42。'
        source=source*(300 if i%31==0 else 2+i%4)
        translated=translated*(250 if i%29==0 else 2+i%3)
        if variant=='missing' and i%37==0:translated=''
        row=dict(document_id='synthetic-long-document',run_id='synthetic-long-run-v1',
                 block_id=f'synthetic-block-{i:05d}',page=i//18+1,bbox=[.1,.1,.9,.3],
                 source_text=source,translated_text=translated)
        if variant=='duplicate' and i==count-1:row['block_id']='synthetic-block-00001'
        if variant=='wrong-run' and i==count-1:row['run_id']='synthetic-other-run'
        rows.append(row)
    return rows


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def freeze():
    DEFAULT.mkdir(parents=True,exist_ok=False)
    before=main_snapshot();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    local=ROOT/'artifacts/long-reading-source'/stamp;local.mkdir(parents=True,exist_ok=False)
    with sqlite3.connect((ROOT/'data/library.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute("SELECT id,result FROM research_jobs WHERE kind='translate' AND status='completed' AND json_array_length(json_extract(result,'$.blocks'))=107 ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row is None:raise RuntimeError('No existing 107-block completed translation available')
        result=json.loads(row['result']);assert result['total_pages']==6 and not result.get('missing_pages')
        for b in result['blocks']:
            original=db.execute('SELECT b.text,b.bbox,b.page,r.document_id FROM blocks b JOIN parse_runs r ON r.id=b.run_id WHERE b.id=? AND b.run_id=?',(b['block_id'],b['run_id'])).fetchone()
            assert original and original['document_id']==b['document_id'] and original['text']==b['source_text']
            assert json.loads(original['bbox'])==b['bbox'] and original['page']==b['page']
        doc=db.execute('SELECT filename,sha256,storage_name FROM documents WHERE id=?',(result['document_id'],)).fetchone()
    historical=local/'saved-107.json'
    with historical.open('x',encoding='utf-8',newline='\n') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    configs=[(1000,'mixed'),(5000,'mixed'),(5000,'missing'),(1000,'duplicate'),(1000,'wrong-run')]
    cases=[]
    for count,variant in configs:
        blocks=synthetic_blocks(count,variant)
        cases.append(dict(id=f'{variant}-{count}',kind='synthetic',count=count,variant=variant,
            canonical_sha256=hashlib.sha256(canonical(blocks)).hexdigest(),
            missing_translations=sum(not b['translated_text'] for b in blocks),
            duplicate_ids=count-len({b['block_id'] for b in blocks}),
            wrong_run_count=sum(b['run_id']!='synthetic-long-run-v1' for b in blocks)))
    source_manifest=json.loads((ROOT/'tests/fixtures/local_acceptance/20261019-v1/manifest.json').read_text(encoding='utf-8'))
    book=next(s for s in source_manifest['public_files'] if Path(s['path']).name=='UnderstandingDeepLearning_02_09_26_C.pdf')
    assert sha256(ROOT/book['path'])==book['sha256'] and book['pages']==541
    manifest=dict(version='long-reading-20261020-v1',cases=cases,
        generator_sha256=sha256(Path(__file__)),scope='Synthetic capacity/position cases; saved historical translation for compatibility, no new model quality claims.',
        saved_translation=dict(path=historical.relative_to(ROOT).as_posix(),sha256=sha256(historical),blocks=107,pages=6,
            original_result_sha256=hashlib.sha256(row['result'].encode()).hexdigest(),source_task_sha256=hashlib.sha256(row['id'].encode()).hexdigest(),
            pdf_filename=doc['filename'],pdf_sha256=doc['sha256']),
        book=dict(path=book['path'],sha256=book['sha256'],pages=541,reading_pages=[1,2,4,10,12,73,271,399,541],
                  blank_physical_pages=[2,4,10,12],rotated_large_image_cases='pending source review'),
        gates=dict(max_body_dom_per_column=120,per_direction_jumps=100,max_settled_position_error_viewport=.1,
                   max_scroll_long_task_ms=200,max_api_page_blocks=200,full_export_unchanged=True),
        model_calls=0,main_unchanged=before==main_snapshot())
    assert manifest['main_unchanged']
    with (DEFAULT/'manifest.json').open('x',encoding='utf-8',newline='\n') as stream:json.dump(manifest,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(DEFAULT),saved_translation=str(historical),cases=len(cases),main_unchanged=True)),flush=True)


if __name__=='__main__':freeze()
