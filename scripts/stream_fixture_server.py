"""Isolated HTTP acceptance fixture. No paid provider, production data or keys."""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
import uvicorn

parser=argparse.ArgumentParser()
parser.add_argument('--data-dir',type=Path,required=True)
args=parser.parse_args()
args.data_dir.mkdir(parents=True,exist_ok=False)
receipts=[]


class Model(BaseHTTPRequestHandler):
    def log_message(self,*_): pass

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        payload=json.loads(body['messages'][1]['content'])
        task=payload.get('task','')
        broken='fixture_disconnect' in payload.get('question','')
        receipt={'task':task,'stream':body.get('stream') is True,'content_sent':False,'completed':False}
        receipts.append(receipt)
        if task=='verify_response_coverage':
            result={'unverified_statements':[],'missing_aspects':[]}
        elif task=='verify_single_claim':
            result={'verdict':'supported','reason':'Local protocol fixture; literal source checks remain enabled.'}
        elif 'target_language' in payload:
            result={'translated_text':'向量检索改善搜索。<b>这是纯文本。</b>' * 3}
        else:
            claims=[{'text':e['text'],'citations':[{'evidence_id':e['evidence_id'],'quote':e['text']}]} for e in payload['evidence']]
            result={'status':'answered','claims':claims,'message':''}
        text=json.dumps(result,ensure_ascii=False)
        if not body.get('stream'):
            raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':text}}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            receipt['completed']=True
            return
        self.send_response(200);self.send_header('Content-Type','text/event-stream')
        self.send_header('Cache-Control','no-cache');self.end_headers()
        def emit(data):
            raw=('data: '+(data if isinstance(data,str) else json.dumps(data,ensure_ascii=False))+'\n\n').encode()
            self.wfile.write(raw);self.wfile.flush()
        try:
            emit({'choices':[{'index':0,'delta':{'reasoning_content':'PRIVATE_FIXTURE_REASONING'},'finish_reason':None}]})
            for index in range(0,len(text),8):
                emit({'choices':[{'index':0,'delta':{'content':text[index:index+8]},'finish_reason':None}]})
                receipt['content_sent']=True
                time.sleep(.12)
                if broken and index>90:
                    return
            emit({'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})
            emit({'choices':[],'usage':{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50}})
            emit('[DONE]');receipt['completed']=True
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass


model_server=ThreadingHTTPServer(('127.0.0.1',0),Model)
threading.Thread(target=model_server.serve_forever,daemon=True).start()
os.environ['RESEARCH_MODEL_BASE_URL']=f'http://127.0.0.1:{model_server.server_port}/v1'
os.environ['RESEARCH_MODEL']='fixture-model'
os.environ['RESEARCH_MODEL_API_KEY']='local-fixture-not-a-secret'
os.environ['RESEARCH_SEMANTIC_ENABLED']='off'

from research_workspace.api import create_app
from research_workspace.jobs import JobStore
app=create_app(args.data_dir,start_worker=False)
library=app.state.library
documents=[]
for name,text in [('Encoder fixture','The model uses an encoder and a decoder.'),
                  ('Retrieval fixture','The retrieval model uses local vectors.')]:
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,80),text)
        doc=library.import_document(pdf.tobytes(),name+'.pdf')['document']
    library.process_next_job();documents.append(doc['id'])


@app.get('/fixture/receipts')
def fixture_receipts():
    return {'calls':receipts,'documents':documents,'paid_calls':0}


stop=threading.Event()
def worker():
    while not stop.is_set():
        try:
            if not asyncio.run(JobStore(library).process_next()):stop.wait(.05)
        except Exception:
            # Persist only the failure type; acceptance must fail visibly.
            stop.set()
            raise


threading.Thread(target=worker,daemon=True).start()
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
deadline=time.monotonic()+10
while not server.started:
    if time.monotonic()>deadline:raise RuntimeError('Fixture server did not start')
    time.sleep(.02)
print(json.dumps({'origin':f'http://127.0.0.1:{sock.getsockname()[1]}','documents':documents}),flush=True)
try:
    for line in sys.stdin:
        if line.strip()=='stop':break
finally:
    stop.set();server.should_exit=True;thread.join(timeout=5);model_server.shutdown()
    (args.data_dir.parent/'http-receipts.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2),encoding='utf-8')
