"""Seed an explicitly controlled historical-paper case, or audit a real result.

The seed reuses an actual stored discovery response, never fabricates a live
search or model-generated synthesis. All subsequent PDF and model stages run
through the normal worker and usage ledger. No model credentials are accessed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research_workspace.discovery import DiscoveryService
from research_workspace.future_verification import _source
from research_workspace.jobs import JobStore
from research_workspace.library import Library


def seed(recent=False):
    library=Library(ROOT/'data');discovery=DiscoveryService(library)
    with discovery._db() as db:
        responses=[json.loads(r[0]) for r in db.execute('SELECT response FROM searches')]
    query='rag retrieval-augmented' if recent else '1312.3005v1'
    response=next(r for r in responses if r['query']==query and r['items'])
    paper=discovery.get_paper('arxiv:2610.18567v1' if recent else 'arxiv:1312.3005v1')
    assert paper.get('document_id'), 'This acceptance case requires the already imported real PDF.'
    source=_source(paper,response['retrieved_at']);assert source
    proposals=[('性能与计算成本','核对 One Billion Word Benchmark 中语言模型的性能与计算成本权衡，说明已有模型组合效果、至少两项原表训练时间及对应困惑度，以及训练时间可比性的限制。'),
               ('跨句上下文条件','核对 One Billion Word Benchmark 能否评估跨句边界的长上下文依赖，说明原论文的相关数据处理、实际结果及明确限制。')]
    if recent:
        proposals=[('RAG配置故障诊断','核对 RAG 配置问题的经验研究：说明其真实样本来源与规模、故障症状和根因所处阶段的关系，以及论文明确说明的研究限制。')]
    directions=[{'direction_id':f'direction-{i+1}','section_index':i,'heading':h,'proposed_work':p,
                 'query_ids':['query-1'],'status':'evidence_insufficient','excluded':False,'citations':[],
                 'reason':'受控验收输入；本任务未运行摘要分类模型。'} for i,(h,p) in enumerate(proposals)]
    synthesis={'kind':'future_work','status':'controlled_acceptance_input',
        'candidate_origin':'human-authored acceptance fixture; not model-generated research proposals',
        'sections':[{'heading':h,'proposed_work':p,'claims':[]} for h,p in proposals],
        'limitations':['受控输入的公开论文正文回归；来源发表和检索时间据实保留，不代表实时完整检索或候选生成验收。']}
    screened={**synthesis,'future_verification':{'status':'evidence_insufficient','scope':'reused_actual_discovery_response_no_abstract_classification',
        'notice':'复用既有真实检索记录与已导入公开论文；候选为受控输入，未冒充实时检索或模型生成。',
        'queries':[{'query_id':'query-1','query':response['query'],'field':response['field'],
                    'retrieved_at':response['retrieved_at'],'status':'completed','cached':True,
                    'reuse_kind':'stored_actual_response','source_ids':[source['source_id']]}],
        'sources':[source],'directions':directions,'errors':[]},'retained_directions':directions,'excluded_directions':[]}
    store=JobStore(library)
    job=store.create('future_work',{'document_ids':[paper['document_id']],'full_text_verify':True,
        'instruction':'Controlled historical-PDF full-text acceptance; candidate synthesis and abstract classification not run.'})
    store.save_item(job['id'],'future_synthesis',{'stage':'future_synthesis','result':synthesis},2)
    store.save_item(job['id'],'future_verification',{'stage':'future_verification','result':screened},2)
    print('REAL_PRO_FULLTEXT_JOB',job['id'])


def verify(job_id,require_training_table=False,allow_auth_recovery=False,source_commit=None):
    output=ROOT/'artifacts/future-fulltext'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False);checks=[];library=Library(ROOT/'data')
    source_commit=subprocess.check_output(['git','rev-parse',source_commit or 'HEAD'],cwd=ROOT,text=True).strip()
    (output/'manifest.json').write_text(json.dumps({'job_id':job_id,
        'software_commit':source_commit,
        'scope':'controlled public-PDF fulltext stage; not a live exhaustive novelty search',
        'require_training_table':require_training_table,'allow_auth_recovery':allow_auth_recovery,
        'source_hashes':{name:hashlib.sha256(subprocess.check_output(['git','show',source_commit+':'+name],cwd=ROOT)).hexdigest() for name in (
            'research_workspace/future_fulltext.py','research_workspace/fulltext_context.py',
            'research_workspace/intelligence.py','research_workspace/jobs.py')}},ensure_ascii=False,indent=2),encoding='utf-8')
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        assert value,name
    # Independent normalization only for PDF whitespace and printing ligatures;
    # do not normalize mathematical superscripts or subscripts.
    def norm(value):
        for old,new in {'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}.items():value=value.replace(old,new)
        return ' '.join(value.split())
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as client:
        job=client.get('/api/research/tasks/'+job_id).json()['job']
        (output/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        check('completed_checkpoint_totals',job['status']=='completed' and job['progress']==job['total'])
        result=job['result'];full=result['future_fulltext']
        check('all_directions_have_passage_audit',full['status']=='completed' and bool(full['directions']))
        check('no_false_novelty_or_automatic_exclusion',not full['novelty_proven'] and not full['automatic_exclusion'] and not result['excluded_directions'])
        originals={};source_docs={}
        for source in full['sources']:
            parsed=source['parse'];download=source['download'];doc=library.get_document(parsed['document_id'])
            check('pdf_identity_'+source['source_id'],download['sha256']==parsed['sha256']==doc['sha256']==hashlib.sha256(library._source_path(doc['id']).read_bytes()).hexdigest())
            check('complete_parse_'+source['source_id'],parsed['parse_status']=='completed' and parsed['pages_done']==parsed['total_pages'])
            originals.update({b['block_id']:b for b in library._evidence_blocks(doc['id'],parsed['run_id'])})
            source_docs[source['source_id']]=(doc['id'],parsed['run_id'])
        for direction in full['directions']:
            did=direction['direction_id'];index=direction['section_index'];answer=direction.get('answer',{})
            check(did+'_original_section',result['sections'][index]['proposed_work']==direction['proposed_work'])
            check(did+'_reviewed',direction['full_text_reviewed'] and answer.get('grounding',{}).get('status')=='passed')
            grounding=answer['grounding'];catalog=grounding['claim_catalog'];reviews=grounding['claim_reviews']
            check(did+'_one_to_one_lengths',len(answer['claims'])==len(catalog)==len(reviews)>0)
            check(did+'_complete_answer',not grounding['response_coverage'][-1]['missing_aspects'] and not grounding['response_coverage'][-1]['unverified_statements'])
            allowed={source_docs[sid] for sid in direction['source_ids'] if sid in source_docs}
            sources={s['evidence_id']:s for s in answer['sources']}
            check(did+'_body_evidence_beyond_abstract_page',any(s['page']>1 for s in sources.values()))
            for ci,claim in enumerate(answer['claims']):
                entry=catalog[ci];own=[r for r in reviews if r['claim_id']==entry['claim_id']]
                check(f'{did}_claim_{ci}_identity',len(own)==1 and own[0]['claim_index']==entry['claim_index']==ci and entry['text']==claim['text'] and own[0]['verdict']=='supported')
                check(f'{did}_claim_{ci}_owns_quotes',bool(claim['citations']) and len(claim['citations'])==len(entry['citations']))
                for qi,(cite,record) in enumerate(zip(claim['citations'],entry['citations'])):
                    evidence=sources[cite['evidence_id']];block=originals[evidence['block_id']]
                    check(f'{did}_{ci}_{qi}_source',all(record[k]==evidence[k]==block[k] for k in ('document_id','run_id','block_id','page','bbox')) and (evidence['document_id'],evidence['run_id']) in allowed)
                    check(f'{did}_{ci}_{qi}_quote',cite['evidence_id']==record['evidence_id'] and cite['quote']==record['quote'] and norm(cite['quote']) in norm(block['text']))
        calls=job['model_calls'];usage=job['usage']
        successful=[c for c in calls if c['http_status']==200]
        allowed_statuses={200,401} if allow_auth_recovery else {200}
        check('actual_pro_responses_and_known_failures',bool(successful) and all(c['model']=='deepseek-v4-pro' and c['state']=='response_received' and c['http_status'] in allowed_statuses for c in calls) and usage['calls']==len(calls))
        check('successful_calls_have_reported_receipts',usage['usage_reported_calls']==len(successful))
        check('reported_usage_totals_exclude_unreported_failures',all(usage[k]==sum(c['usage'][k] for c in successful) for k in ('prompt_tokens','completion_tokens','total_tokens')))
        export=client.get('/api/research/tasks/'+job_id+'/export').text
        check('export_claim_ids_and_run_ids',all(c['claim_id'] in export for d in full['directions'] for c in d['answer']['grounding']['claim_catalog']) and all(run in export for _,run in source_docs.values()))
        if require_training_table:
            positive=full['directions'][0]['answer']
            quotes=[q['quote'] for c in positive['claims'] for q in c['citations']]
            check('training_table_quoted_with_headers',any('Training Time' in norm(q) and 'Perplexity' in q and '74.4' in q for q in quotes))
            check('structural_recall_is_audited',bool(positive['retrieval'].get('fulltext_context',{}).get('table_evidence_ids')))
        (output/'export.md').write_text(export,encoding='utf-8')
    print('CHECKS',len(checks),'OUTPUT',output)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('job_id',nargs='?');parser.add_argument('--seed-controlled',action='store_true');parser.add_argument('--recent',action='store_true');parser.add_argument('--require-training-table',action='store_true');parser.add_argument('--allow-auth-recovery',action='store_true');parser.add_argument('--source-commit');args=parser.parse_args()
    if args.seed_controlled:seed(args.recent)
    elif args.job_id:verify(args.job_id,args.require_training_table,args.allow_auth_recovery,args.source_commit)
    else:parser.error('Specify a job ID or --seed-controlled.')
