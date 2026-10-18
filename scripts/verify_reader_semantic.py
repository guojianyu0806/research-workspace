"""Real offline embeddings and source-location acceptance on existing papers."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.reader_search import ReaderSearch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reuse-artifact',type=Path,help='Copy a previous isolated library/cache into a new acceptance folder')
    args=parser.parse_args()
    os.environ['RESEARCH_SEMANTIC_ENABLED']='auto'
    os.environ['RESEARCH_OCR_ENABLED']='0'
    out=ROOT/'artifacts/reader-semantic'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    print(out,flush=True)
    docs={};hashes={};checks=[];results={}
    if args.reuse_artifact:
        previous=args.reuse_artifact.resolve()
        if not previous.is_relative_to((ROOT/'artifacts/reader-semantic').resolve()):
            raise ValueError('Only an isolated reader-semantic artifact can be reused')
        old_report=json.loads((previous/'results.json').read_text(encoding='utf-8'))
        shutil.copytree(previous/'library',out/'library')
        docs=old_report['documents']
    lib=Library(out/'library')
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    for name in ['1706.03762v7.pdf','1810.04805v2.pdf','2005.11401v4.pdf']:
        data=(ROOT/'data/samples'/name).read_bytes();hashes[name]=hashlib.sha256(data).hexdigest()
        if name not in docs:
            doc=lib.import_document(data,name)['document'];lib.process_next_job();docs[name]=lib.get_document(doc['id'])
    search=ReaderSearch(lib)
    # The model and runtime are already installed. Deny socket connections during
    # acceptance so any attempted external request fails rather than costing API.
    original_connect=socket.socket.connect
    def blocked(sock,address,*args,**kwargs):
        # Windows asyncio uses a local socket pair for its event loop.
        if isinstance(address,tuple) and address[0] in {'127.0.0.1','::1','localhost'}:
            return original_connect(sock,address,*args,**kwargs)
        raise AssertionError('External network is disabled for offline acceptance')
    socket.socket.connect=blocked
    try:
        cases=[
            ('positions','1706.03762v7.pdf','How does the model know the order of words without recurrence?', '', 'semantic', ['positional encoding','position']),
            ('masked','1810.04805v2.pdf','How are missing words predicted using context from both directions?', '', 'hybrid', ['masked','mask']),
            ('retrieval','2005.11401v4.pdf','How is external knowledge retrieved from Wikipedia combined with generation?', '', 'hybrid', ['retriev','wikipedia']),
            ('chinese-hint','1706.03762v7.pdf','模型怎样知道词语的顺序？','positional encodings represent token order','semantic',['position']),
            ('unrelated','1706.03762v7.pdf','Veterinary dosage for treating a kitten with feline leukemia', '', 'semantic', []),
            ('unrelated-food','1810.04805v2.pdf','How to bake sourdough bread with rye flour in a kitchen oven?', '', 'semantic', []),
        ]
        for name,filename,query,hint,mode,terms in cases:
            doc=docs[filename];t=time.perf_counter()
            r=search.search(query,english_query=hint,mode=mode,document_id=doc['id'],run_id=doc['available_run']['id'],limit=10)
            results[name]=r;(out/(name+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
            check(name+'_real_embedding_applied',r.get('semantic',{}).get('status')=='applied')
            check(name+'_no_paid_calls',r['model_calls']==0 and r['network_used'] is False)
            blocks={b['block_id']:b for b in lib._evidence_blocks(doc['id'],doc['available_run']['id'])}
            check(name+'_all_original_sources',all(all(h[k]==blocks[h['block_id']][k] for k in ['document_id','run_id','page','bbox','text']) for h in r['hits']))
            if terms:check(name+'_relevant_top5',any(any(term in h['text'].lower() for term in terms) for h in r['hits'][:5]))
            else:check(name+'_no_unrelated_candidate',r['hits']==[])
            if terms and not hint:check(name+'_literal_question_has_no_match',not lib.search(query,document_id=doc['id'],run_id=doc['available_run']['id']))
            print(name,round(time.perf_counter()-t,2),'s',[(h['page'],round(h.get('similarity') or 0,3),h['text'][:110].replace('\n',' ')) for h in r['hits'][:3]],flush=True)
        repeat=search.search(cases[0][2],mode='semantic',document_id=docs[cases[0][1]]['id'],run_id=docs[cases[0][1]]['available_run']['id'],limit=10)
        check('repeat_uses_saved_embedding_cache',repeat['semantic']['new_chunks']==0 and repeat['semantic']['cache_hits']>0)
        check('repeat_ranking_stable',[h['block_id'] for h in repeat['hits']]==[h['block_id'] for h in results['positions']['hits']])
        doc=docs['1706.03762v7.pdf'];old=doc['available_run']['id'];lib.create_run(doc['id']);lib.process_next_job()
        historical=search.search(cases[0][2],mode='semantic',document_id=doc['id'],run_id=old,limit=10)
        check('reparse_retains_historical_hits',historical['hits']==repeat['hits'])
        (out/'historical.json').write_text(json.dumps(historical,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:socket.socket.connect=original_connect
    check('original_pdfs_unchanged',all(hashlib.sha256((ROOT/'data/samples'/name).read_bytes()).hexdigest()==sha for name,sha in hashes.items()))
    report={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'documents':docs,'checks':checks,
        'passed':sum(c['passed'] for c in checks),'total':len(checks),'source_sha256':hashes,'model_calls':0,'external_socket_connect_denied':True}
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"{report['passed']}/{report['total']}",flush=True)
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
