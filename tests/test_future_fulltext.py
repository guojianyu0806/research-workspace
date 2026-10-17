"""Offline PDF workflow fixtures; paid Pro acceptance is recorded separately."""
import asyncio
import json

import httpx
import pymupdf
import pytest

from research_workspace.discovery import DiscoveryService
from research_workspace.future_fulltext import plan_sources
from research_workspace.intelligence import IntelligenceService, ChatCompletionProvider, ModelConfig
from research_workspace.jobs import JobStore
from research_workspace.library import Library
from research_workspace.advanced_api import ResearchTask, task_markdown


def fixture_result(count=1):
    ids=[f'arxiv:2401.{10000+i}v1' for i in range(count)]
    return {'kind':'future_work','sections':[{'heading':'Encoder','proposed_work':'encoder layer scaling'}],
        'future_verification':{'directions':[{'direction_id':'direction-1','section_index':0,
            'heading':'Encoder','proposed_work':'encoder layer scaling','query_ids':['query-1'],
            'excluded':True,'status':'potentially_addressed'}],
            'queries':[{'query_id':'query-1','status':'completed','source_ids':ids}],
            'sources':[{'source_id':s,'title':'Offline fixture','url':'https://arxiv.org/abs/'+s[6:]} for s in ids]}}


def setup(tmp_path,monkeypatch,*,fail_download=False):
    library=Library(tmp_path);store=JobStore(library);calls=[];downloads=[]
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30,70),'The encoder has six layers.');data=pdf.tobytes()
    def download(service,sid):
        downloads.append(sid)
        if fail_download:raise RuntimeError('secret transport body must not leak')
        return {**library.import_document(data,'fixture.pdf'),'source':{'paper_id':sid,'url':'https://arxiv.org/pdf/'+sid[6:]}}
    monkeypatch.setattr(DiscoveryService,'import_paper',download)
    def model(request):
        payload=json.loads(json.loads(request.content)['messages'][1]['content']);calls.append(payload['task'])
        if payload['task']=='bilingual_retrieval_terms':result={'terms':[]}
        elif payload['task']=='verify_response_coverage':result={'unverified_statements':[],'missing_aspects':[]}
        else:
            block=payload['evidence'][0]
            result={'status':'answered','message':'','claims':[{'text':block['text'],'citations':[{'evidence_id':block['evidence_id'],'quote':block['text']}]}]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]})
    service=IntelligenceService(library,ChatCompletionProvider(ModelConfig('https://model.invalid','fixture','fixture-key'),transport=httpx.MockTransport(model)))
    job=store.create('future_work',{'document_ids':[],'full_text_verify':True})
    result=fixture_result()
    store.save_item(job['id'],'future_synthesis',{'stage':'future_synthesis','result':result},2)
    store.save_item(job['id'],'future_verification',{'stage':'future_verification','result':result},2)
    return library,store,service,job,calls,downloads


def advance(store,service):
    with store.library._connection() as db:db.execute("UPDATE research_jobs SET updated_at='2000-01-01' WHERE status='queued'")
    asyncio.run(store.process_next(service))


def complete(library,store,service,job):
    for _ in range(15):
        library.process_next_job(page_budget=1);advance(store,service)
        saved=store.get(job['id'])
        if saved['status'] not in {'queued','running'}:return saved
    raise AssertionError('Workflow failed to terminate')


def test_fulltext_pins_pdf_and_audits_each_own_claim_without_replay(tmp_path,monkeypatch):
    library,store,service,job,calls,downloads=setup(tmp_path,monkeypatch)
    advance(store,service);advance(store,service)
    assert calls==[] and len(downloads)==1
    advance(store,service)
    assert '等待全文解析' in store.get(job['id'])['events'][-1]['message']
    library.process_next_job();advance(store,service)
    pinned=next(i for i in store.get(job['id'])['completed_items'] if i['stage'].startswith('future_parse:'))
    library.create_run(pinned['document_id']);library.process_next_job()
    done=complete(library,store,service,job)
    assert done['status']=='completed' and done['progress']==done['total']
    full=done['result']['future_fulltext'];direction=full['directions'][0];answer=direction['answer']
    assert full['status']=='completed' and not full['novelty_proven'] and not full['automatic_exclusion']
    assert direction['section_index']==0 and direction['full_text_reviewed']
    assert {s['run_id'] for s in answer['sources']}=={pinned['run_id']}
    catalog=answer['grounding']['claim_catalog'];review=answer['grounding']['claim_reviews'][0]
    assert catalog[0]['claim_id']==review['claim_id'] and review['verdict']=='supported'
    assert answer['claims'][0]['citations'][0]['quote']==catalog[0]['citations'][0]['quote']
    assert done['result']['excluded_directions']==[] and done['result']['sections']==fixture_result()['sections']
    count=len(calls)
    with library._connection() as db:db.execute("UPDATE research_jobs SET status='queued',result=NULL WHERE id=?",(job['id'],))
    advance(store,service)
    assert len(calls)==count and len(downloads)==1
    exported=task_markdown(done)
    assert '公开论文正文核验' in exported and catalog[0]['claim_id'] in exported and pinned['run_id'] in exported
    assert '审核：supported' in exported


def test_unavailable_pdf_is_not_fulltext_review_or_automatic_exclusion(tmp_path,monkeypatch):
    library,store,service,job,calls,downloads=setup(tmp_path,monkeypatch,fail_download=True)
    done=complete(library,store,service,job)
    assert done['status']=='completed' and calls==[]
    full=done['result']['future_fulltext']
    assert full['status']=='evidence_insufficient' and not full['directions'][0]['full_text_reviewed']
    assert done['result']['excluded_directions']==[] and 'secret transport' not in json.dumps(done)


def test_cancelled_direction_checkpoint_reused_and_source_budget_is_explicit(tmp_path,monkeypatch):
    library,store,service,job,calls,downloads=setup(tmp_path,monkeypatch)
    original=store.save_item
    def save(jid,key,payload,total):
        original(jid,key,payload,total)
        if key.startswith('future_direction:'):store.cancel(jid)
    monkeypatch.setattr(store,'save_item',save)
    cancelled=complete(library,store,service,job)
    assert cancelled['status']=='cancelled'
    count=len(calls);store.retry(job['id'])
    done=complete(library,store,service,job)
    assert done['status']=='completed' and len(calls)==count
    plan=plan_sources(fixture_result(7))
    assert len(plan['papers'])==plan['paper_limit']==3


def test_api_defaults_new_tasks_to_fulltext_without_reinterpreting_old_payloads():
    assert ResearchTask(kind='future_work').model_dump()['full_text_verify'] is True
    assert ResearchTask(kind='future_work',full_text_verify=False).full_text_verify is False


def test_directions_keep_original_indices_and_do_not_borrow_other_queries():
    result=fixture_result(4)
    result['future_verification']['directions'].append({'direction_id':'direction-5','section_index':4,
        'heading':'Other','proposed_work':'unsearched condition','query_ids':[]})
    plan=plan_sources(result)
    assert plan['directions'][1]['section_index']==4 and plan['directions'][1]['query_ids']==[]


def test_each_direction_retrieves_only_its_own_query_documents(tmp_path,monkeypatch):
    library,store,service,job,calls,downloads=setup(tmp_path,monkeypatch)
    result=fixture_result(2);audit=result['future_verification']
    first,second=[s['source_id'] for s in audit['sources']]
    audit['queries']=[{'query_id':'query-1','source_ids':[first],'status':'completed'},
                      {'query_id':'query-2','source_ids':[second],'status':'completed'}]
    audit['directions'].append({'direction_id':'direction-5','section_index':4,
        'heading':'Other encoder','proposed_work':'other encoder layers','query_ids':['query-2']})
    def download(discovery,sid):
        downloads.append(sid)
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((30,70),'The encoder has '+('six' if sid==first else 'nine')+' layers.')
            imported=library.import_document(pdf.tobytes(),sid[6:]+'.pdf')
        return {**imported,'source':{'paper_id':sid}}
    monkeypatch.setattr(DiscoveryService,'import_paper',download)
    store.save_item(job['id'],'future_verification',{'stage':'future_verification','result':result},4)
    done=complete(library,store,service,job)
    assert done['status']=='completed'
    directions=done['result']['future_fulltext']['directions']
    assert directions[1]['section_index']==4
    assert directions[0]['source_ids']==[first] and directions[1]['source_ids']==[second]
    documents=[{s['document_id'] for s in d['answer']['sources']} for d in directions]
    assert documents[0].isdisjoint(documents[1])
    assert 'six' in directions[0]['answer']['claims'][0]['text']
    assert 'nine' in directions[1]['answer']['claims'][0]['text']
