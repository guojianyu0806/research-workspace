"""Controlled decisions; no network or paid model calls in these fixtures."""
import asyncio

import pymupdf
import pytest

from research_workspace.advanced_api import ResearchTask,task_markdown
from research_workspace.future_filter import checked_plan,restored_plan,exclusion_allowed,condition_decision
from research_workspace.intelligence import IntelligenceService
from research_workspace.jobs import JobStore
from research_workspace.library import Library


POSITIVE='报告模型组合的困惑度为46.8。'
PARTIAL='报告模型组合的困惑度为46.8，并且无需训练。'
UNKNOWN='在跨句长上下文任务上报告可靠的定量结果。'


def setup(tmp_path,*,proposals=None,joint_pass=True,bad_quote=False,bad_plan=False,cancel_review=False):
    library=Library(tmp_path);store=JobStore(library)
    with pymupdf.open() as pdf:
        page=pdf.new_page();page.insert_text((30,70),'The combination of models has perplexity 46.8.');content=pdf.tobytes()
    document=library.import_document(content,'fixture.pdf')['document'];library.process_next_job()
    document=library.get_document(document['id']);run=document['available_run'];calls=[]
    proposals=proposals or [POSITIVE,PARTIAL,UNKNOWN]
    directions=[{'direction_id':f'direction-{i+1}','section_index':i,'heading':'Fixture',
        'proposed_work':p,'source_ids':['arxiv:2401.10001v1'],'documents':[{'document_id':document['id'],
        'run_id':run['id'],'parse_status':run['status'],'pages_done':1,'total_pages':1}]} for i,p in enumerate(proposals)]
    source_result={'kind':'future_work','sections':[{'heading':'Fixture','proposed_work':p} for p in proposals],
        'future_fulltext':{'directions':directions,'sources':[],'notice':'offline PDF fixture'}}
    source=store.create('future_work',{'fixture':True});store.finish(source['id'],'completed',result=source_result)
    job=store.create('future_filter',{'source_task_id':source['id']})
    class Provider:
        configured=True
        async def generate(self,prompt,payload,schema):
            calls.append(schema.__name__)
            if schema.__name__=='ConditionPlan':
                proposal=payload['proposal']
                parts=[proposal.split('，')[0]+'，',proposal.split('，')[1]] if proposal==PARTIAL else [proposal]
                return {'conditions':[POSITIVE] if bad_plan else parts}
            if schema.__name__ in {'ConditionFindings','JointFinding'}:
                evidence=payload['evidence'][0]
                finding={'text':'The combination of models has perplexity 46.8.',
                    'citations':[{'evidence_id':'E_forged' if bad_quote else evidence['evidence_id'],'quote_id':'Q1','quote':''}]}
                if schema.__name__=='JointFinding':return {'finding':finding}
                return {'conditions':[{'condition_id':c['condition_id'],
                    'coverage':'fully_addressed' if '46.8' in c['source_text'] else 'evidence_insufficient',
                    'finding':finding if '46.8' in c['source_text'] else None} for c in payload['conditions']]}
            if schema.__name__=='ConditionReview':
                if cancel_review:store.cancel(job['id'])
                return {'verdict':'supported','finding_supported_as_written':True,'coverage':'fully_addressed','scope_matched':True,
                    'qualifiers_preserved':True,'explicitly_reported':True,'reason':'Fixture own quotation supports the complete condition.'}
            if schema.__name__=='ConditionRepair':return {'coverage':'evidence_insufficient','finding':None}
            if schema.__name__=='JointReview':return {'verdict':'supported','fully_covers_proposal':True,
                'jointly_satisfied':joint_pass,'explicitly_reported':True,'reason':'Fixture joint scope.'}
            raise AssertionError(schema.__name__)
    service=IntelligenceService(library,Provider())
    async def retrieve(question,docs,*,run_ids,**kwargs):
        assert docs==[document['id']] and run_ids=={document['id']:run['id']}
        blocks=service._blocks(docs,run_ids=run_ids)
        return {'evidence':blocks,'_snapshot_blocks':blocks,'mode':'offline_fixture'}
    service.retrieve_for_question=retrieve
    return store,service,job,source_result,calls


def complete(store,service,job):
    for _ in range(90):
        asyncio.run(store.process_next(service));saved=store.get(job['id'])
        if saved['status'] not in {'queued','running'}:return saved
    raise AssertionError('Workflow did not terminate')


def test_full_positive_partial_and_insufficient_are_separated_with_own_audits(tmp_path):
    store,service,job,original,calls=setup(tmp_path)
    done=complete(store,service,job)
    assert done['status']=='completed',done['error']
    assert done['progress']==done['total']
    result=done['result'];audit=result['future_direction_filter'];rows=audit['directions']
    assert [d['status'] for d in rows]==['fully_addressed','partially_addressed','evidence_insufficient']
    assert [d['direction_id'] for d in result['excluded_directions']]==['direction-1']
    assert [d['direction_id'] for d in result['retained_directions']]==['direction-2','direction-3']
    assert result['sections']==original['sections'] and original.get('future_direction_filter') is None
    positive=rows[0];condition=positive['conditions'][0]
    assert condition['review']['claim_id']==condition['finding']['identity']['claim_id']
    assert positive['joint_review']['claim_id']==positive['joint_finding']['identity']['claim_id']
    export=task_markdown(done)
    assert positive['decision_id'] in export and positive['joint_review']['claim_id'] in export
    assert 'future_filter:direction-3:evidence' in export
    assert 'future_filter:direction-2:repair_finding:condition-2' in export
    assert '未形成可审核引句，未执行支持度审核' in export
    count=len(calls)
    with store.library._connection() as db:db.execute("UPDATE research_jobs SET status='queued',result=NULL WHERE id=?",(job['id'],))
    assert complete(store,service,job)['status']=='completed' and len(calls)==count


def test_all_conditions_positive_but_incompatible_experiment_is_retained(tmp_path):
    store,service,job,original,calls=setup(tmp_path,proposals=[POSITIVE],joint_pass=False)
    done=complete(store,service,job);assert done['status']=='completed',done['error']
    decision=done['result']['future_direction_filter']['directions'][0]
    assert decision['conditions'][0]['status']=='fully_addressed'
    assert decision['status']=='partially_addressed' and not decision['excluded']


@pytest.mark.parametrize('variant',['bad_quote','bad_plan'])
def test_forged_quote_and_omitted_conditions_never_exclude(tmp_path,variant):
    store,service,job,original,calls=setup(tmp_path,proposals=[PARTIAL],**{variant:True})
    done=complete(store,service,job);assert done['status']=='completed',done['error']
    assert not done['result']['excluded_directions']
    assert done['result']['future_direction_filter']['directions'][0]['status']=='evidence_insufficient'
    assert 'JointReview' not in calls


def test_paid_review_checkpoint_survives_cancel_and_resume_without_replay(tmp_path):
    store,service,job,original,calls=setup(tmp_path,proposals=[POSITIVE],cancel_review=True)
    done=complete(store,service,job)
    assert done['status']=='cancelled'
    assert calls.count('ConditionReview')==1
    assert any(i['stage'].endswith('review:condition-1') for i in done['completed_items'])
    store.retry(job['id']);done=complete(store,service,job)
    assert done['status']=='completed' and done['progress']==done['total']
    assert calls.count('ConditionReview')==1 and done['result']['excluded_directions']


def test_partition_preserves_qualifiers_numbers_order_and_scope():
    assert ''.join(c['source_text'] for c in checked_plan(PARTIAL,[PARTIAL]))==PARTIAL
    for parts in ([POSITIVE],['报告模型组合的困惑度为46.8'],['并且无需训练。','报告模型组合的困惑度为46.8，']):
        with pytest.raises(ValueError):checked_plan(PARTIAL,parts)
    with pytest.raises(ValueError):checked_plan('保持温度为-5℃。',['保持温度为','5℃。'])
    with pytest.raises(ValueError):checked_plan('困惑度46.8。',['困惑度46','8。'])


def test_joint_proof_cannot_mix_papers_or_partial_parses():
    conditions=[{'status':'fully_addressed','finding':{'claim':'fixture'},
                 'review':{'verdict':'supported','finding_supported_as_written':True}}]
    finding={'identity':{'citations':[{'document_id':'a'},{'document_id':'b'}]}}
    review={'verdict':'supported','fully_covers_proposal':True,'jointly_satisfied':True,'explicitly_reported':True}
    docs=[{'document_id':d,'parse_status':'completed','pages_done':2,'total_pages':2} for d in ('a','b')]
    assert not exclusion_allowed(conditions,finding,review,docs)
    finding['identity']['citations'].pop()
    assert exclusion_allowed(conditions,finding,review,docs)
    docs[0]['parse_status']='partial'
    assert not exclusion_allowed(conditions,finding,review,docs)
    docs[0]['parse_status']='completed'
    conditions[0]['review'].pop('finding_supported_as_written')
    assert not exclusion_allowed(conditions,finding,review,docs)


def test_new_api_tasks_enable_filter_without_changing_legacy_payloads():
    assert ResearchTask(kind='future_work').assess_solved


def test_automatic_filter_follows_newly_saved_fulltext_checkpoint_in_same_tick(tmp_path,monkeypatch):
    store,service,job,original,calls=setup(tmp_path,proposals=[POSITIVE])
    store.cancel(job['id'])
    new=store.create('future_work',{'full_text_verify':True,'assess_solved':True})
    for stage in ('future_synthesis','future_verification'):
        store.save_item(new['id'],stage,{'stage':stage,'result':original},3)
    async def fulltext(store,service,current,result):
        if not any(i['stage']=='future_fulltext' for i in current['completed_items']):
            store.save_item(current['id'],'future_fulltext',{'stage':'future_fulltext','result':original},3)
        return original
    monkeypatch.setattr('research_workspace.future_fulltext.fulltext_step',fulltext)
    done=complete(store,service,new)
    assert done['status']=='completed',done['error']
    assert done['progress']==done['total']==14 and done['result']['excluded_directions']


def test_recheck_api_rejects_unfinished_and_foreign_sources(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    monkeypatch.setattr(IntelligenceService,'capabilities',lambda self:{'configured':True})
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as alice, TestClient(app,base_url='http://127.0.0.1:8765') as bob:
        user=alice.post('/api/account/register',json={'name':'FilterAlice','password':'fixture-pass-123'}).json()['user']
        bob.post('/api/account/register',json={'name':'FilterBob','password':'fixture-pass-456'})
        assert alice.get('/api/research/tasks').status_code==200
        store=JobStore(app.state.libraries[user['id']]);source=store.create('future_work',{})
        body={'kind':'future_filter','source_task_id':source['id']}
        assert alice.post('/api/research/tasks',json=body).status_code==400
        store.finish(source['id'],'completed',result={'sections':[]})
        assert alice.post('/api/research/tasks',json=body).status_code==400
        store.finish(source['id'],'completed',result={'future_fulltext':{'status':'completed','directions':[]}})
        assert bob.post('/api/research/tasks',json=body).status_code==404
        assert alice.post('/api/research/tasks',json=body).status_code==202


def test_missing_complete_pdf_keeps_direction_without_purchasing_review(tmp_path):
    store,service,job,original,calls=setup(tmp_path,proposals=[POSITIVE])
    original['future_fulltext']['directions'][0]['documents'][0]['parse_status']='partial'
    source_id=job['payload']['source_task_id']
    store.finish(source_id,'completed',result=original)
    done=complete(store,service,job)
    assert done['status']=='completed' and done['progress']==done['total']
    assert not done['result']['excluded_directions'] and not calls


def test_separator_restoration_never_omits_qualifiers_or_numeric_symbols():
    proposal='支持模型组合，并且无需训练。'
    rows=restored_plan(proposal,['支持模型组合','并且无需训练。'])
    assert ''.join(r['source_text'] for r in rows)==proposal
    for text,parts in [('温度-5℃。',['温度','5℃。']),('数值46.8。',['数值46','8。']),
                       (proposal,['支持模型组合'])]:
        with pytest.raises(ValueError):restored_plan(text,parts)


def test_correction_is_independently_reviewed_and_preserves_rejected_attempt(tmp_path):
    store,service,job,original,calls=setup(tmp_path,proposals=[POSITIVE])
    generate=service.provider.generate;reviews=[]
    async def corrected(prompt,payload,schema):
        if schema.__name__=='ConditionReview':
            answer=await generate(prompt,payload,schema);reviews.append(payload['finding']['claim_id'])
            if len(reviews)==1:
                answer.update(verdict='unsupported',coverage='evidence_insufficient',qualifiers_preserved=False)
            return answer
        if schema.__name__=='ConditionRepair':
            calls.append('ConditionRepair')
            assert payload['previous_attempt']['review']['verdict']=='unsupported'
            return {'coverage':'fully_addressed','finding':payload['previous_attempt']['rejected_finding']['claim']}
        return await generate(prompt,payload,schema)
    service.provider.generate=corrected
    done=complete(store,service,job)
    assert done['status']=='completed' and done['progress']==done['total']
    row=done['result']['excluded_directions'][0];condition=row['conditions'][0]
    assert len(reviews)==2 and calls.count('ConditionRepair')==1
    assert condition['review']['verdict']=='supported'
    assert condition['prior_attempt']['review']['verdict']=='unsupported'
    assert row['joint_review']['claim_id']==row['joint_finding']['identity']['claim_id']


def test_inconsistent_supported_verdict_cannot_promote_unproven_finding():
    review={'verdict':'supported','finding_supported_as_written':False,'coverage':'fully_addressed',
            'scope_matched':True,'qualifiers_preserved':True,'explicitly_reported':True}
    assert condition_decision('fully_addressed',review,{'claim':'fixture'})=='evidence_insufficient'
