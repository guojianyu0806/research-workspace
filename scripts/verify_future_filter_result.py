"""Controlled real-PDF classifier cases and independent audit, without secrets.

Seed creates a clearly labelled input-selection record, not simulated model
answers. Every subsequent classification, quotation and review runs normally
through the real worker and its model-call ledger.
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
from research_workspace.jobs import JobStore
from research_workspace.library import Library

PROPOSALS=[
    ('已报告的模型组合','通过线性插值组合语言模型，在 One Billion Word Benchmark 测试集上将困惑度降至46.8。'),
    ('含额外训练限制的模型组合','通过线性插值组合语言模型，在 One Billion Word Benchmark 测试集上将困惑度降至46.8，并且无需训练任何模型。'),
    ('跨句长上下文实验','在 One Billion Word Benchmark 上验证跨句长上下文建模，报告跨句依赖任务的定量改进。'),
]


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def seed(reuse_evidence_from=None,reuse_findings=False):
    library=Library(ROOT/'data');store=JobStore(library)
    doc=library.get_document('99cdc2449b9844bb9873ca3658d90fd9')
    run=doc['available_run'];assert run['status']=='completed' and run['pages_done']==run['total_pages']==6
    documents=[{'document_id':doc['id'],'run_id':run['id'],'parse_status':run['status'],
                'pages_done':run['pages_done'],'total_pages':run['total_pages']}]
    directions=[{'direction_id':f'direction-{i+1}','section_index':i,'heading':heading,
        'proposed_work':proposal,'source_ids':['arxiv:1312.3005v1'],'documents':documents,
        'full_text_reviewed':False,'status':'controlled_evidence_selection'} for i,(heading,proposal) in enumerate(PROPOSALS)]
    result={'kind':'future_work','status':'controlled_classifier_input',
        'candidate_origin':'human-authored controlled classifier cases; no simulated model generation or prior coverage review',
        'sections':[{'heading':h,'proposed_work':p,'claims':[]} for h,p in PROPOSALS],
        'future_fulltext':{'status':'controlled_evidence_selection','directions':directions,'sources':[],
            'automatic_exclusion':False,'novelty_proven':False,
            'notice':'受控输入，仅选择真实已完整解析的公开论文；此前未运行方向生成或正文结论审核。本次新任务独立执行真实全文条件筛选。'},
        'limitations':['受控判定用例，不代表实时检索或完整未来方向生成验收。']}
    source=store.create('future_work',{'controlled_classifier_input':True,'document_ids':[doc['id']]})
    store.finish(source['id'],'completed',result=result)
    # Explicit acceptance-only reuse of actual immutable planning/retrieval
    # checkpoints. Candidate findings may be explicitly reused as unreviewed
    # input. No support or joint reviews are reused or simulated.
    if reuse_evidence_from:
        prior=store.get(reuse_evidence_from)
        assert prior['status']=='completed'
        old=next(i['result'] for i in prior['completed_items'] if i['stage']=='future_filter_start')
        assert [d['proposed_work'] for d in old['directions']]==[p for _,p in PROPOSALS]
        assert all(d['documents']==documents for d in old['directions'])
        selected=[i for i in prior['completed_items'] if i['stage'].endswith(':plan') or i['stage'].endswith(':evidence')
                  or (reuse_findings and i['stage'].endswith(':findings'))]
        assert len(selected)==(9 if reuse_findings else 6)
        total=2+sum(6+3*len(i['result']['conditions']) for i in selected if i['stage'].endswith(':plan'))
        # A short transaction prevents the worker seeing half-seeded checkpoints.
        with library._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            from uuid import uuid4
            jid=uuid4().hex;now=datetime.now(timezone.utc).isoformat()
            payload={'source_task_id':source['id'],'acceptance_reuses_actual_evidence_from':reuse_evidence_from,
                     'acceptance_reuses_unreviewed_findings':reuse_findings}
            db.execute('INSERT INTO research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (jid,'future_filter','queued',json.dumps(payload),None,None,1+len(selected),total,0,now,now))
            start={'stage':'future_filter_start','result':{'directions':old['directions'],'base_count':0,'started_at':now,
                'acceptance_reuses_actual_evidence_from':reuse_evidence_from}}
            for item in [start,*selected]:
                db.execute('INSERT INTO research_job_items VALUES(?,?,?)',(jid,item['stage'],json.dumps(item,ensure_ascii=False)))
        store.event(jid,'queued','受控复测复用既有真实固定条件与检索；可显式复用未审核结论候选，全部支持度审核和联合审核重新调用 Pro。')
        job=store.get(jid)
    else:
        with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as client:
            response=client.post('/api/research/tasks',json={'kind':'future_filter','source_task_id':source['id']})
            response.raise_for_status();job=response.json()['job']
    print('SOURCE',source['id'],'REAL_PRO_FILTER_JOB',job['id'])


def verify(job_id,source_commit=None):
    output=ROOT/'artifacts/future-filter'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False);checks=[];library=Library(ROOT/'data')
    commit=subprocess.check_output(['git','rev-parse',source_commit or 'HEAD'],cwd=ROOT,text=True).strip()
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        (output/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        assert value,name
    def norm(value):
        for old,new in {'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}.items():value=value.replace(old,new)
        return ' '.join(value.split())
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as client:
        job=client.get('/api/research/tasks/'+job_id).json()['job']
        (output/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        check('completed_exact_progress',job['status']=='completed' and job['progress']==job['total'])
        result=job['result'];rows=result['future_direction_filter']['directions']
        source=JobStore(library).get(job['payload']['source_task_id'])['result']
        check('original_sections_preserved',result['sections']==source['sections'])
        check('source_not_overwritten','future_direction_filter' not in source)
        check('three_expected_classifications',[r['status'] for r in rows]==['fully_addressed','partially_addressed','evidence_insufficient'])
        check('only_positive_excluded',[r['direction_id'] for r in result['excluded_directions']]==['direction-1'])
        check('uncertain_and_partial_retained',[r['direction_id'] for r in result['retained_directions']]==['direction-2','direction-3'])
        check('condition_review_v4',result['future_direction_filter']['version']=='future-filter-v4')
        export=client.get('/api/research/tasks/'+job_id+'/export').text
        (output/'export.md').write_text(export,encoding='utf-8')
        originals={};allowed=set();pdf_hashes={}
        for row in rows:
            for parsed in row['documents']:
                did=parsed['document_id'];rid=parsed['run_id'];allowed.add((did,rid))
                if did not in pdf_hashes:
                    doc=library.get_document(did)
                    sha=hashlib.sha256(library._source_path(did).read_bytes()).hexdigest();pdf_hashes[did]=sha
                    check('pdf_identity_'+did,sha==doc['sha256'])
                    originals.update({b['block_id']:b for b in library._evidence_blocks(did,rid)})
        def finding_checks(label,finding,review):
            claim=finding['claim'];identity=finding['identity'];sources={s['evidence_id']:s for s in finding['sources']}
            expected={'text':claim['text'],'citations':[{'citation_id':f'R{i+1}',**q,
                **{k:sources[q['evidence_id']][k] for k in ('document_id','run_id','block_id','page','bbox')}} for i,q in enumerate(claim['citations'])]}
            check(label+'_content_identity',identity['text']==claim['text'] and identity['citations']==expected['citations'] and identity['claim_id']=='C_'+digest(expected))
            check(label+'_own_review',review['claim_id']==identity['claim_id'] and review['claim_index']==identity['claim_index']==0 and review['verdict']=='supported')
            if 'condition_id' in review:check(label+'_entire_finding_supported',review.get('finding_supported_as_written') is True)
            check(label+'_export',identity['claim_id'] in export and claim['text'] in export)
            check(label+'_nonempty_quotes',bool(claim['citations']))
            for i,q in enumerate(claim['citations']):
                s=sources[q['evidence_id']];block=originals[s['block_id']]
                check(label+f'_quote_{i}',bool(q['quote']) and norm(q['quote']) in norm(block['text']))
                check(label+f'_source_{i}',(s['document_id'],s['run_id']) in allowed and all(s[k]==block[k] for k in ('document_id','run_id','block_id','page','bbox')))
        for row in rows:
            did=row['direction_id'];conditions=row['conditions'];proposal=row['proposed_work'];cursor=0
            check(did+'_original_proposal',proposal==source['future_fulltext']['directions'][row['section_index']]['proposed_work'])
            stages={i['stage']:i['result'] for i in job['completed_items'] if i['stage'].startswith('future_filter:'+did+':')}
            retrieval=stages['future_filter:'+did+':evidence']
            check(did+'_actual_frozen_retrieval',retrieval['status']=='retrieved' and bool(retrieval['evidence']) and
                all((e['document_id'],e['run_id']) in allowed for e in retrieval['evidence']))
            check(did+'_export_has_own_retrieval','future_filter:'+did+':evidence' in export)
            for c in conditions:
                start,end=c['start'],c['end']
                check(did+c['condition_id']+'_literal_partition',not proposal[cursor:start].strip() and proposal[start:end]==c['source_text'] and start>=cursor)
                cursor=end
                checkpoint=stages['future_filter:'+did+':repair_review:'+c['condition_id']]
                check(did+c['condition_id']+'_final_checkpoint',checkpoint==c)
                if c['finding']:
                    finding_checks(did+c['condition_id'],c['finding'],c['review'])
                    check(did+c['condition_id']+'_condition_binding',c['review']['condition_id']==c['condition_id'] and c['review']['index_space']=='this condition finding only')
                if c['status'] in {'fully_addressed','partially_addressed'}:
                    check(did+c['condition_id']+'_positive_requirement_gate',c['review'].get('positive_requirement_satisfied') is True)
                if c['status']=='fully_addressed':
                    check(did+c['condition_id']+'_full_guards',bool(c['finding']) and all(c['review'][k] for k in ('scope_matched','qualifiers_preserved','explicitly_reported','positive_requirement_satisfied')) and c['review']['coverage']=='fully_addressed')
            check(did+'_all_characters_covered',not proposal[cursor:].strip())
            check(did+'_decision_identity',row['decision_id']=='F_'+digest({k:v for k,v in row.items() if k not in {'decision_id','checked_at'}}) and row['decision_id'] in export)
            if row['excluded']:
                joint=row['joint_finding'];review=row['joint_review'];finding_checks(did+'_joint',joint,review)
                check(did+'_joint_guards',all(c['status']=='fully_addressed' for c in conditions) and all(review[k] for k in ('fully_covers_proposal','jointly_satisfied','explicitly_reported')) and review['index_space']=='joint finding only')
                docs={q['document_id'] for q in joint['identity']['citations']}
                check(did+'_single_complete_pdf',len(docs)==1 and all(d['parse_status']=='completed' and d['pages_done']==d['total_pages']>0 for d in row['documents'] if d['document_id'] in docs))
                quotes=[norm(q['quote']) for q in joint['claim']['citations']]
                check(did+'_method_result_and_benchmark_together',
                    any('linearly interpolating together probabilities from all models.' in q for q in quotes)
                    and any('All models 46.8' in q for q in quotes)
                    and any('Model combination on the 1B Word Benchmark test set.' in q for q in quotes))
        negative=[c for c in rows[2]['conditions'] if c['finding']]
        check('negative_limitation_has_own_reviewed_body_quote',bool(negative) and any(
            'the benchmark is not useful' in norm(q['quote']) and s['page']==2
            for c in negative for q in c['finding']['claim']['citations']
            for s in c['finding']['sources'] if s['evidence_id']==q['evidence_id']))
        calls=job['model_calls'];success=[c for c in calls if c['http_status']==200];usage=job['usage']
        check('real_pro_only',bool(calls) and len(success)==len(calls) and all(c['model']=='deepseek-v4-pro' and c['state']=='response_received' for c in calls))
        check('usage_receipts',usage['calls']==usage['usage_reported_calls']==len(calls))
        check('usage_sums',all(usage[k]==sum(c['usage'][k] for c in success) for k in ('prompt_tokens','completion_tokens','total_tokens')))
        manifest={'job_id':job_id,'source_task_id':job['payload']['source_task_id'],'software_commit':commit,
            'pdf_sha256':pdf_hashes,'checks':len(checks),'usage':usage,'scope':'controlled real-PDF classifier; no live exhaustive search',
            'source_hashes':{p:hashlib.sha256(subprocess.check_output(['git','show',commit+':'+p],cwd=ROOT)).hexdigest() for p in ('research_workspace/future_filter.py','research_workspace/jobs.py','research_workspace/intelligence.py')}}
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('CHECKS',len(checks),'OUTPUT',output)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('job_id',nargs='?');parser.add_argument('--seed-controlled',action='store_true');parser.add_argument('--reuse-evidence-from');parser.add_argument('--reuse-findings',action='store_true');parser.add_argument('--source-commit');args=parser.parse_args()
    if args.seed_controlled:seed(args.reuse_evidence_from,args.reuse_findings)
    elif args.job_id:verify(args.job_id,args.source_commit)
    else:parser.error('Specify job ID or --seed-controlled.')
