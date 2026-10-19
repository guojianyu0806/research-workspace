"""Create a new immutable local acceptance data version; never overwrite one.

Public PDFs stay in data/. Only project-authored small fixtures and hashes are
tracked. Raw PDF text observations verify coverage, not semantic OCR accuracy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main_snapshot(root=ROOT/'data'):
    """Read only content hashes, never account passwords/sessions or env values."""
    database = root/'library.sqlite3'
    result = {'tables':{}, 'originals':{}}
    if database.exists():
        with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ['documents','parse_runs','pages','blocks','research_jobs','research_job_items','research_job_events','model_calls']:
                if table in tables:
                    rows = db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
                    value = json.dumps(rows,ensure_ascii=False,default=str,separators=(',',':')).encode()
                    result['tables'][table] = {'count':len(rows),'sha256':hashlib.sha256(value).hexdigest()}
    for path in sorted((root/'originals').glob('*.pdf')):
        result['originals'][path.name] = sha256(path)
    return result


def synthetic_pdf(path, marker, pages=8):
    with pymupdf.open() as pdf:
        for page_number in range(1,pages+1):
            page = pdf.new_page()
            for y,text in [(65,'Local concurrency evidence'),(110,marker),
                           (135,'Stable historical source value = 42.'),
                           (160,f'Physical page {page_number} of {pages}.')]:
                page.insert_text((50,y),text,fontsize=11)
        pdf.set_metadata({'title':'Local concurrency source','author':'Synthetic fixture'})
        path.write_bytes(pdf.tobytes())


def freeze(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    before = main_snapshot()
    files = []
    for folder in ('samples','corpus'):
        for path in sorted((ROOT/'data'/folder).glob('*.pdf')):
            with pymupdf.open(path) as pdf:
                files.append({'path':path.relative_to(ROOT).as_posix(),'sha256':sha256(path),
                    'bytes':path.stat().st_size,'pages':len(pdf),'kind':'existing_public_pdf'})
    book = next(item for item in files if Path(item['path']).name=='UnderstandingDeepLearning_02_09_26_C.pdf')
    assert book['pages']==541,book
    with pymupdf.open(ROOT/book['path']) as pdf:
        book['native_text_observations'] = [{'page':i+1,'has_text':bool(page.get_text().strip())} for i,page in enumerate(pdf)]
    blank = [v['page'] for v in book['native_text_observations'] if not v['has_text']]
    assert len(blank)==4,blank
    fixtures = []
    for index in range(10):
        marker = f'PRIVATE_ACCOUNT_{index+1:02}_SENTINEL_7AC2'
        path = directory/f'account-{index+1:02}.pdf'
        synthetic_pdf(path,marker)
        csv = directory/f'account-{index+1:02}.csv'
        csv.write_text(f'label,value\n{marker},{index+1}\nsecond,{index+2}\n',encoding='utf-8')
        fixtures.append({'name':f'LocalActor{index+1:02}','marker':marker,'pdf':path.name,
            'pdf_sha256':sha256(path),'pages':8,'csv':csv.name,'csv_sha256':sha256(csv),
            'expected_values':[index+1,index+2],'historical_quote':'Stable historical source value = 42.'})
    shared = directory/'shared.pdf';synthetic_pdf(shared,'COMMON_PUBLIC_FIXTURE_92D7')
    (directory/'broken.pdf').write_bytes(b'%PDF-1.7\nDeliberately invalid local acceptance fixture.')
    contract = {'version':'local-concurrency-v1','warmup_seconds':300,'mixed_seconds':1800,
        'extended_seconds':900,'initial_accounts':5,'extended_accounts':10,'browser_contexts':5,
        'actor_pause_seconds':1.0,'light_p95_ms':1000,'indexed_search_p95_ms':3000,
        'cancel_deadline_seconds':5,'peak_rss_bytes':8*1024**3,'idle_growth_bytes':512*1024**2,
        'memory_rounds':3,'memory_load_seconds':60,'memory_idle_seconds':30,
        'nonexpected_5xx':0,'foreign_source_leaks':0,'missing_or_duplicate_accepted_runs':0,
        'network_policy':'loopback_only; no provider config; no subscriptions',
        'scope':'Real HTTP, workers, PDFs and browser concurrency. No model-answer quality or external acceptance.'}
    manifest = {'version':'local-acceptance-20261019-v1','created_at':datetime.now(timezone.utc).isoformat(),
        'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'public_files':files,'fixtures':fixtures,'shared':{'pdf':shared.name,'sha256':sha256(shared)},
        'book':{'path':book['path'],'sha256':book['sha256'],'pages':541,'native_blank_pages':blank},
        'source_observation_scope':'Raw native-text presence is page coverage evidence, not OCR/content accuracy gold.',
        'future_annotations':{'formula_regions':{'required':60,'status':'not_yet_frozen'},
            'matrix_regions':{'required':24,'status':'not_yet_frozen'},
            'table_regions':{'required':24,'status':'not_yet_frozen'},
            'chart_regions':{'required':24,'status':'not_yet_frozen'},
            'affiliation_groups':{'required':30,'status':'not_yet_frozen'}},
        'fixture_scope':'Frozen input/expected facts for concurrency S0/S1; future parsing-quality gold remains separate.'}
    for name,value in [('manifest.json',manifest),('contract.json',contract)]:
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    after = main_snapshot()
    assert before==after,'User originals or records changed during freeze'
    print(json.dumps({'directory':str(directory),'public_pdfs':len(files),'synthetic_accounts':10,
        'manifest_sha256':sha256(directory/'manifest.json'),'main_unchanged':True,'native_blank_pages':blank}),flush=True)


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    for source in manifest['public_files']:
        assert sha256(ROOT/source['path'])==source['sha256'],source['path']
    for actor in manifest['fixtures']:
        assert sha256(directory/actor['pdf'])==actor['pdf_sha256'],actor['pdf']
        assert sha256(directory/actor['csv'])==actor['csv_sha256'],actor['csv']
    assert sha256(directory/manifest['shared']['pdf'])==manifest['shared']['sha256']
    return manifest


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'tests/fixtures/local_acceptance/20261019-v1')
    parser.add_argument('--verify',action='store_true')
    args = parser.parse_args()
    if args.verify:
        result = verify(args.output); print(json.dumps({'verified':True,'public_pdfs':len(result['public_files'])}))
    else:
        freeze(args.output)
