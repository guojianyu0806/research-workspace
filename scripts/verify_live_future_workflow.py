"""Independently audit a normal live Future Work job; never seeds its stages."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit,parse_qs

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research_workspace.discovery import DiscoveryService
from research_workspace.library import Library


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def norm(value):
    for old,new in {'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}.items():value=value.replace(old,new)
    return ' '.join(value.split())


def verify(job_id,source_commit):
    output=ROOT/'artifacts/live-workflow'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False);checks=[];library=Library(ROOT/'data')
    commit=subprocess.check_output(['git','rev-parse',source_commit],cwd=ROOT,text=True).strip()
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        assert value,name
    originals={};original_pdfs={}
    def document(did,rid):
        if (did,rid) not in original_pdfs:
            doc=library.get_document(did)
            fingerprint=hashlib.sha256(library._source_path(did).read_bytes()).hexdigest()
            check('pdf_'+did,doc['sha256']==fingerprint)
            original_pdfs[did,rid]=fingerprint
            originals.update({b['block_id']:b for b in library._evidence_blocks(did,rid)})
    def finding(label,claim,identity,review,sources,index):
        smap={s['evidence_id']:s for s in sources}
        expected={'text':claim['text'],'citations':[{'citation_id':f'R{i+1}',**q,
            **{k:smap[q['evidence_id']][k] for k in ('document_id','run_id','block_id','page','bbox')}}
            for i,q in enumerate(claim['citations'])]}
        check(label+'_identity',identity['claim_id']=='C_'+sha(expected) and identity['text']==claim['text']
              and identity['citations']==expected['citations'] and identity['claim_index']==index)
        check(label+'_own_review',review['claim_id']==identity['claim_id'] and review['claim_index']==index
              and review['verdict']=='supported')
        check(label+'_nonempty_citations',bool(claim['citations']))
        for qi,q in enumerate(claim['citations']):
            s=smap[q['evidence_id']];document(s['document_id'],s['run_id']);b=originals[s['block_id']]
            check(label+f'_quote_{qi}',bool(q['quote']) and norm(q['quote']) in norm(b['text'])
                and all(s[k]==b[k] for k in ('document_id','run_id','block_id','page','bbox')))
    def answer(label,value,claims):
        grounding=value['grounding'];catalog=grounding['claim_catalog'];reviews=grounding['claim_reviews']
        check(label+'_accepted',grounding['status']=='passed' and len(claims)==len(catalog)>0)
        for i,claim in enumerate(claims):
            identity=catalog[i]
            own=[r for r in reviews if r['claim_id']==identity['claim_id'] and r['claim_index']==i]
            check(label+f'_{i}_one_review',len(own)==1)
            finding(label+f'_{i}',claim,identity,own[0],value['sources'],i)
        coverage=grounding.get('response_coverage') or []
        if coverage:
            check(label+'_coverage_index_space',coverage[-1]['claim_ids']==[x['claim_id'] for x in catalog])
            check(label+'_complete_response',not coverage[-1]['missing_aspects'] and not coverage[-1]['unverified_statements'])
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as client:
        response=client.get('/api/research/tasks/'+job_id);response.raise_for_status();job=response.json()['job']
        (output/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        check('completed_exact_steps',job['status']=='completed' and job['progress']==job['total'])
        payload=job['payload'];items={i.get('stage'):i for i in job['completed_items']}
        result=job['result'];synthesis=items['future_synthesis']['result'];audit=result['future_verification']
        check('normal_new_live_job',job['kind']=='future_work' and payload['refresh_search'] and payload['full_text_verify']
              and payload['assess_solved'] and not any(k.startswith(('acceptance_','controlled_')) for k in payload)
              and not payload.get('source_task_id') and not synthesis.get('candidate_origin'))
        check('all_real_stages',all(k in items for k in ('future_synthesis','future_verification','future_fulltext_plan',
              'future_fulltext','future_filter_start','future_filter_result')))
        diagnostics=[i for i in job['completed_items'] if str(i.get('stage','')).startswith('diagnostic:')]
        check('retry_diagnostics_preserved',len({i['stage'] for i in diagnostics})==len(diagnostics)
              and all(i.get('origin_stage') in {'grounding_validation','review_validation'}
                  and i.get('recorded_at') and i.get('model_call_count',-1)>=0 for i in diagnostics))
        check('original_sections_immutable',result['sections']==synthesis['sections'])
        claims=[c for s in synthesis['sections'] for c in s['claims']]
        answer('original_local_context',synthesis,claims)
        check('recent_live_policy',audit['live_refresh_requested'] and audit['date_filter']['lookback_days']==payload['research_days'])
        window=audit['date_filter'];feeds={};receipts={}
        check('all_queries_executed',bool(audit['queries']) and all(q['status']=='completed' for q in audit['queries']))
        for query in audit['queries']:
            qid=query['query_id'];receipt=query['receipt'];path=library.data_dir/receipt['path']
            check(qid+'_real_new_receipt',query['cached'] is False and query['force_refresh']
                and datetime.fromisoformat(receipt['request_started_at'])>=datetime.fromisoformat(audit['started_at'])
                and datetime.fromisoformat(receipt['received_at'])<=datetime.fromisoformat(audit['completed_at']))
            check(qid+'_contained_receipt_file',path.resolve().is_relative_to((library.data_dir/'discovery-responses').resolve()))
            content=path.read_bytes()
            check(qid+'_raw_fingerprint',len(content)==receipt['bytes'] and hashlib.sha256(content).hexdigest()==receipt['sha256'])
            url=urlsplit(receipt['request_url']);params=parse_qs(url.query)
            check(qid+'_official_newest_query',url.scheme=='https' and url.hostname=='export.arxiv.org' and url.path=='/api/query'
                and params['sortBy']==['submittedDate'] and params['sortOrder']==['descending']
                and 'submittedDate:[' in params['search_query'][0])
            paper_records,_=DiscoveryService._parse_feed(content,receipt['received_at'])
            feeds[qid]={p['id']:p for p in paper_records};receipts[receipt['id']]=query
            check(qid+'_source_membership',set(query['source_ids']).issubset(feeds[qid]))
        for source in audit['sources']:
            records=[feeds[q['query_id']][source['source_id']] for q in audit['queries'] if source['source_id'] in q['source_ids']]
            check(source['source_id']+'_actual_feed_content',bool(records) and any(source['title']==p['title'] and norm(source['abstract'])==norm(p['summary']) and source['published']==p['published'] for p in records))
            published=datetime.fromisoformat(source['published'].replace('Z','+00:00'))
            check(source['source_id']+'_recent_publication',datetime.fromisoformat(window['from'])<=published<=datetime.fromisoformat(window['to']))
            check(source['source_id']+'_receipt_binding',bool(source['discovery_receipt_ids']) and set(source['discovery_receipt_ids']).issubset(receipts))
        full=result['future_fulltext'];new_downloads=[]
        for source in full['sources']:
            downloaded=source['download'];parsed=source['parse']
            check('download_'+source['source_id'],downloaded['status']=='downloaded' and parsed['status']=='ready')
            document(parsed['document_id'],parsed['run_id'])
            check('fixed_parse_'+source['source_id'],parsed['sha256']==downloaded['sha256']==original_pdfs[parsed['document_id'],parsed['run_id']]
                and parsed['parse_status']=='completed' and parsed['pages_done']==parsed['total_pages']>0)
            if not downloaded.get('duplicate'):
                new_downloads.append(source['source_id'])
                check('new_pdf_time_'+source['source_id'],datetime.fromisoformat(downloaded['source']['downloaded_at'])>=datetime.fromisoformat(job['created_at']))
        check('at_least_one_verified_official_pdf',bool(full['sources']))
        # Existing immutable public PDFs may legitimately be reused. The audit
        # records duplicate flags; this never labels an old download as new.
        for direction in full['directions']:
            did=direction['direction_id'];check(did+'_fulltext_reviewed',direction['full_text_reviewed'])
            answer(did+'_fulltext',direction['answer'],direction['answer']['claims'])
        filtering=result['future_direction_filter'];rows=filtering['directions']
        check('live_filter_version',filtering['version']=='future-filter-v5')
        check('all_proposals_retained_in_audit',len(rows)==len(synthesis['sections']) and
              [d['proposed_work'] for d in rows]==[s['proposed_work'] for s in synthesis['sections']])
        excluded={d['direction_id'] for d in result['excluded_directions']};retained={d['direction_id'] for d in result['retained_directions']}
        check('no_physical_deletion_or_lost_direction',not(excluded&retained) and excluded|retained=={d['direction_id'] for d in rows})
        for d in rows:
            did=d['direction_id'];cursor=0
            check(did+'_fresh_search_scope',d['search_audit']['live_required'] and d['search_audit']['verified'])
            for c in d['conditions']:
                label=did+'_'+c['condition_id'];start,end=c['start'],c['end'];text=d['proposed_work']
                check(label+'_literal_condition',start>=cursor and not text[cursor:start].strip() and text[start:end]==c['source_text']);cursor=end
                if c['finding']:
                    f=c['finding'];r=c['review'];finding(label,f['claim'],f['identity'],r,f['sources'],0)
                    check(label+'_complete_support',r['finding_supported_as_written'] and r['condition_id']==c['condition_id'])
                if c['status'] in {'fully_addressed','partially_addressed'}:check(label+'_positive_requirement',c['review']['positive_requirement_satisfied'])
            check(did+'_no_lost_qualifier',not d['proposed_work'][cursor:].strip())
            check(did+'_decision_digest',d['decision_id']=='F_'+sha({k:v for k,v in d.items() if k not in {'decision_id','checked_at'}}))
            if d['excluded']:
                f=d['joint_finding'];r=d['joint_review'];finding(did+'_joint',f['claim'],f['identity'],r,f['sources'],0)
                check(did+'_full_joint_guards',all(c['status']=='fully_addressed' and c['review']['scope_matched'] and c['review']['qualifiers_preserved'] and c['review']['explicitly_reported'] for c in d['conditions'])
                    and all(r[k] for k in ('fully_covers_proposal','jointly_satisfied','explicitly_reported'))
                    and len({q['document_id'] for q in f['identity']['citations']})==1)
            else:check(did+'_uncertain_kept',did in retained)
        calls=job['model_calls'];usage=job['usage'];success=[c for c in calls if c['http_status']==200]
        check('actual_pro_receipts',bool(calls) and len(calls)==len(success) and all(c['model']=='deepseek-v4-pro' and c['state']=='response_received' for c in calls)
            and usage['calls']==usage['usage_reported_calls']==len(calls))
        check('usage_totals',all(usage[k]==sum(c['usage'][k] for c in calls) for k in ('prompt_tokens','completion_tokens','total_tokens')))
        export=client.get('/api/research/tasks/'+job_id+'/export').text;(output/'export.md').write_text(export,encoding='utf-8')
        check('export_receipts_and_decisions',all(q['receipt']['id'] in export for q in audit['queries']) and all(d['decision_id'] in export for d in rows))
        manifest={'job_id':job_id,'software_commit':commit,'checks':len(checks),'new_downloads':new_downloads,'usage':usage,
            'scope':'normal API live discovery + actual candidate generation + official PDFs + fulltext review + condition filtering',
            'source_hashes':{p:hashlib.sha256(subprocess.check_output(['git','show',commit+':'+p],cwd=ROOT)).hexdigest() for p in
                ('research_workspace/discovery.py','research_workspace/future_verification.py','research_workspace/future_fulltext.py',
                 'research_workspace/future_filter.py','research_workspace/jobs.py','research_workspace/intelligence.py',
                 'research_workspace/claim_repair.py','research_workspace/prompts/future_background_repair_v1.txt')}}
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('CHECKS',len(checks),'OUTPUT',output)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('job_id');parser.add_argument('--source-commit',required=True);args=parser.parse_args()
    verify(args.job_id,args.source_commit)
