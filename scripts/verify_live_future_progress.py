"""Read-only audit of accepted checkpoints in a balance-interrupted live job.

This deliberately cannot produce a completed end-to-end acceptance manifest.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research_workspace.discovery import DiscoveryService
from research_workspace.library import Library


def normalized(text):
    for a,b in {'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}.items():text=text.replace(a,b)
    return ' '.join(text.split())


def verify(job_id):
    output=ROOT/'artifacts/live-workflow'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False)
    job=httpx.get('http://127.0.0.1:8765/api/research/tasks/'+job_id,trust_env=False).json()['job']
    (output/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
    checks=[];library=Library(ROOT/'data')
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        assert value,name
    items={i['stage']:i for i in job['completed_items']}
    check('interrupted_by_http402',job['status']=='failed' and job['result'] is None
        and job['model_calls'][-1]['http_status']==402)
    check('not_full_acceptance',job['progress']<job['total'] and 'future_filter_result' not in items)
    audit=items['future_verification']['result']['future_verification']
    check('actual_live_planning',audit['live_refresh_requested'] and audit['query_planning']['status']=='completed')
    for q in audit['queries']:
        r=q['receipt'];p=(library.data_dir/r['path']).resolve();content=p.read_bytes()
        check(q['query_id']+'_fresh',q['status']=='completed' and q['cached'] is False
            and datetime.fromisoformat(r['request_started_at'])>=datetime.fromisoformat(audit['started_at']))
        check(q['query_id']+'_raw_receipt',p.is_relative_to((library.data_dir/'discovery-responses').resolve())
            and hashlib.sha256(content).hexdigest()==r['sha256'] and len(content)==r['bytes'])
        records,_=DiscoveryService._parse_feed(content,r['received_at'])
        check(q['query_id']+'_source_ids',set(q['source_ids']).issubset({p['id'] for p in records}))
    blocks={};pdfs=[]
    for s in items['future_fulltext_plan']['result']['papers']:
        sid=s['source_id'];d=items['future_pdf:'+sid];p=items['future_parse:'+sid]
        check(sid+'_fixed_pdf',d['status']=='downloaded' and p['status']=='ready'
            and hashlib.sha256(library._source_path(p['document_id']).read_bytes()).hexdigest()==p['sha256']==d['sha256'])
        check(sid+'_complete_parse',p['parse_status']=='completed' and p['pages_done']==p['total_pages']>0)
        blocks.update({b['block_id']:b for b in library._evidence_blocks(p['document_id'],p['run_id'])})
        pdfs.append({'source_id':sid,'duplicate':d['duplicate'],'run_id':p['run_id'],'sha256':p['sha256']})
    accepted=[];claims_total=0;quotes_total=0
    for stage,item in items.items():
        if not stage.startswith('future_direction:'):continue
        direction=item['result'];a=direction['answer'];g=a['grounding'];sources={b['evidence_id']:b for b in a['sources']}
        check(stage+'_accepted',direction['full_text_reviewed'] and g['status']=='passed' and direction['search_audit']['verified'])
        for index,claim in enumerate(a['claims']):
            ident=g['claim_catalog'][index];reviews=[r for r in g['claim_reviews'] if r['claim_id']==ident['claim_id'] and r['claim_index']==index]
            expected={'text':claim['text'],'citations':[{'citation_id':f'R{i+1}',**q,
                **{k:sources[q['evidence_id']][k] for k in ('document_id','run_id','block_id','page','bbox')}} for i,q in enumerate(claim['citations'])]}
            digest=hashlib.sha256(json.dumps(expected,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            check(stage+f'_{index}_own_audit',ident['claim_id']=='C_'+digest and ident['text']==claim['text']
                and ident['claim_index']==index and ident['citations']==expected['citations']
                and len(reviews)==1 and reviews[0]['verdict']=='supported')
            claims_total+=1
            for qi,q in enumerate(claim['citations']):
                s=sources[q['evidence_id']];b=blocks[s['block_id']]
                check(stage+f'_{index}_{qi}_original_quote',bool(q['quote']) and normalized(q['quote']) in normalized(b['text'])
                    and all(s[k]==b[k] for k in ('document_id','run_id','block_id','page','bbox')))
                quotes_total+=1
        accepted.append(direction['direction_id'])
    manifest={'job_id':job_id,'status':'partial_not_end_to_end_acceptance','checks':len(checks),
        'accepted_directions':accepted,'accepted_claims':claims_total,'original_quotes':quotes_total,'pdfs':pdfs,
        'pending':['direction-2 fulltext review','automatic condition/joint filtering','final end-to-end audit','browser citation round trips'],
        'http402_call_id':job['model_calls'][-1]['id'],'usage':job['usage']}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'checks':len(checks),'claims':claims_total,'quotes':quotes_total}))


if __name__=='__main__':verify(sys.argv[1])
