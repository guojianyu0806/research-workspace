"""Read-only acceptance against existing public papers; optional isolated HTTP UI.

No model transport, network retrieval, dependency installation or main DB writes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--serve',action='store_true')
args = parser.parse_args()
output = ROOT/'artifacts'/'front-matter'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
output.mkdir(parents=True,exist_ok=False)
from research_workspace.structure import extract_structure


def snapshot():
    with sqlite3.connect('file:'+str(ROOT/'data'/'library.sqlite3').replace('\\','/')+'?mode=ro',uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = {table:db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
                for table in ['documents','parse_runs','blocks','research_jobs','research_job_items','model_calls'] if table in tables}
    files = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT/'data'/'originals').glob('*.pdf'))}
    return {'counts':{k:len(v) for k,v in rows.items()},'rows_sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,default=str).encode()).hexdigest(),'originals':files}


before = snapshot()
with sqlite3.connect('file:'+str(ROOT/'data'/'library.sqlite3').replace('\\','/')+'?mode=ro',uri=True) as db:
    db.row_factory = sqlite3.Row
    docs = [dict(r) for r in db.execute('SELECT * FROM documents ORDER BY created_at')]
    runs = [dict(r) for r in db.execute('SELECT * FROM parse_runs ORDER BY created_at')]
    cases = []
    for run in runs:
        doc = next(d for d in docs if d['id']==run['document_id'])
        blocks = [dict(r)|{'document_id':doc['id'],'bbox':json.loads(r['bbox'])} for r in db.execute(
            'SELECT id AS block_id,run_id,page,text,bbox FROM blocks WHERE run_id=? ORDER BY page,ordinal',(run['id'],))]
        front = extract_structure(blocks,doc['id'],run['id'])['front_matter']
        sources = {b['block_id']:b for b in blocks}
        for item in front['authors']+front['affiliations']:
            ev = item['evidence']; source = sources[ev['block_id']]
            assert ev['run_id']==run['id'] and ev['document_id']==doc['id']
            assert source['text'][ev['char_start']:ev['char_end']]==ev['text']
            assert ev['bbox']==source['bbox']
        for relation in front['relations']:
            author = next(a for a in front['authors'] if a['id']==relation['author_id'])
            org = next(a for a in front['affiliations'] if a['id']==relation['affiliation_id'])
            assert relation['evidence']==[author['evidence'],org['evidence']]
        assert front['audit']['source_spans_valid']
        case = {'document_id':doc['id'],'run_id':run['id'],'title':doc['title'],'filename':doc['filename'],'front_matter':front}
        cases.append(case)

# Expected facts are transcribed from existing PDF front pages, not inferred from
# model answers or test output. Scope is these named public samples only.
expected = {
    '1706.03762v7.pdf': [('Ashish Vaswani','Google Brain'),('Noam Shazeer','Google Brain'),('Niki Parmar','Google Research'),('Jakob Uszkoreit','Google Research'),('Łukasz Kaiser','Google Brain'),('Llion Jones','Google Research'),('Aidan N. Gomez','University of Toronto'),('Illia Polosukhin',None)],
    '1706.03762v1.pdf': [('Ashish Vaswani','Google Brain'),('Noam Shazeer','Google Brain'),('Niki Parmar','Google Research'),('Jakob Uszkoreit','Google Research'),('Łukasz Kaiser','Google Brain'),('Llion Jones','Google Research'),('Aidan N. Gomez','University of Toronto'),('Illia Polosukhin',None)],
}
checked = []
for doc in docs:
    case = next(c for c in cases if c['run_id']==doc['current_run_id'])
    front = case['front_matter']
    pairs = expected.get(doc['filename'])
    if doc['title'].startswith('BERT:'):
        pairs = [(name,'Google AI Language') for name in ['Jacob Devlin','Ming-Wei Chang','Kenton Lee','Kristina Toutanova']]
    if not pairs:
        continue
    assert [a['name'] for a in front['authors']]==[name for name,_ in pairs]
    for name,org_name in pairs:
        author = next(a for a in front['authors'] if a['name']==name)
        org_names = [o['text'] for o in front['affiliations'] if o['id'] in author['affiliation_ids']]
        assert org_names == ([] if org_name is None else [org_name]), (name,org_names)
        checked.append({'filename':doc['filename'],'name':name,'affiliation':org_name,'passed':True})
assert len(checked)==20, 'Both Transformer versions and BERT must actually be checked'
after = snapshot()
assert before==after
report = {'cases':cases,'manual_expected_checks':checked,'original_records_unchanged':before==after,
          'model_calls_added':after['counts']['model_calls']-before['counts']['model_calls'],
          'snapshot':before,'scope':'Existing public papers and saved parse versions; candidate extraction, not general metadata accuracy or official package acceptance.'}
(output/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

if not args.serve:
    print(json.dumps({'output':str(output),'runs':len(cases),'manual_checks':len(checked),'model_calls_added':0}),flush=True)
    raise SystemExit

# Import module-level API app only after directing it to an isolated workspace.
for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):
    os.environ.pop(key,None)
os.environ['RESEARCH_DATA_DIR'] = str(output/'workspace')
os.environ['RESEARCH_SEMANTIC_ENABLED'] = 'off'
from research_workspace.api import create_app
import uvicorn
app = create_app(output/'workspace',start_worker=False)
library = app.state.library
fixture_docs = []
for doc in docs:
    if doc['filename'] not in expected and not doc['title'].startswith('BERT:'):
        continue
    new = library.import_document((ROOT/'data'/'originals'/doc['storage_name']).read_bytes(),doc['filename'])['document']
    library.process_next_job()
    library.update_metadata(new['id'],title=doc['title'])
    fixture_docs.append({'id':new['id'],'title':doc['title'],'filename':doc['filename']})
sock = socket.socket(); sock.bind(('127.0.0.1',0)); sock.listen(128)
server = uvicorn.Server(uvicorn.Config(app,log_level='error'))
thread = threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
deadline = time.monotonic()+10
while not server.started:
    if time.monotonic()>deadline:
        raise RuntimeError('Fixture startup timed out')
    time.sleep(.02)
print(json.dumps({'output':str(output),'origin':f'http://127.0.0.1:{sock.getsockname()[1]}','documents':fixture_docs}),flush=True)
try:
    for line in sys.stdin:
        if line.strip()=='stop':break
finally:
    server.should_exit=True;thread.join(timeout=5)
    assert before==snapshot(), 'Main records changed during fixture checks'
    (output/'main-unchanged.json').write_text(json.dumps({'unchanged':True,'model_calls_added':0}),encoding='utf-8')
