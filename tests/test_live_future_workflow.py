"""Offline freshness receipts and conservative live-workflow contracts."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json

import httpx
import pytest

from research_workspace.advanced_api import ResearchTask
from research_workspace.discovery import DiscoveryService,DiscoveryError
from research_workspace.future_verification import verify_future_work
from research_workspace.library import Library


def feed(published):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>http://arxiv.org/abs/2610.10001v1</id><title>Causal Attention Graph Prediction</title>
    <summary>We introduce a causal attention method for graph prediction.</summary>
    <published>{published}</published><updated>{published}</updated>
    <link title="pdf" href="https://arxiv.org/pdf/2610.10001v1"/></entry></feed>'''.encode()


def setup(tmp_path):
    calls=[];fail=[]
    published=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    def handler(request):
        calls.append(request)
        return httpx.Response(503) if fail else httpx.Response(200,content=feed(published))
    service=DiscoveryService(Library(tmp_path),httpx.Client(transport=httpx.MockTransport(handler)),request_interval=0)
    return service,calls,fail


def test_forced_refresh_bypasses_cache_retains_each_raw_receipt_and_fails_closed(tmp_path):
    service,calls,fail=setup(tmp_path)
    a=service.search('causal attention')
    assert service.search('causal attention')['cached'] and len(calls)==1
    b=service.search('causal attention',force_refresh=True)
    assert not b['cached'] and b['force_refresh'] and len(calls)==2
    assert a['receipt']['id']!=b['receipt']['id']
    for response in (a,b):
        receipt=response['receipt'];content=(service.library.data_dir/receipt['path']).read_bytes()
        assert hashlib.sha256(content).hexdigest()==receipt['sha256'] and len(content)==receipt['bytes']
    fail.append(True)
    with pytest.raises(DiscoveryError):service.search('causal attention',force_refresh=True)
    assert service.search('causal attention')['cached'] and len(calls)==3


def test_time_filter_rejects_out_of_window_feed_and_uses_distinct_cache(tmp_path):
    service,calls,fail=setup(tmp_path)
    service.search('causal attention')
    end=datetime.now(timezone.utc)-timedelta(days=10);start=end-timedelta(days=5)
    result=service.search('causal attention',published_from=start.isoformat(),published_to=end.isoformat())
    assert result['items']==[] and result['excluded_by_date']==1 and len(calls)==2
    assert 'AND submittedDate:[' in calls[-1].url.params['search_query']
    assert result['published_from']==start.isoformat() and result['published_to']==end.isoformat()
    assert service.search('causal attention')['items']
    with pytest.raises(DiscoveryError):service.search('x',published_from=start.isoformat())
    with pytest.raises(DiscoveryError):service.search('x',published_from='2026-01-01',published_to=end.isoformat())


class Provider:
    configured=True
    async def generate(self,prompt,payload,schema):
        return {'directions':[{'direction_id':d['direction_id'],'status':'evidence_insufficient',
            'coverage':'unclear','citations':[]} for d in payload['directions']]}


def proposal():
    return {'kind':'future_work','sections':[{'heading':'Causal attention','proposed_work':'Causal attention graph prediction'}]}


def test_live_abstract_workflow_records_actual_receipts_window_and_preserves_original(tmp_path):
    service,calls,fail=setup(tmp_path);original=proposal()
    result=asyncio.run(verify_future_work(service.library,Provider(),original,discovery=service,
        force_refresh=True,lookback_days=90))
    audit=result['future_verification'];query=audit['queries'][0]
    assert audit['live_refresh_requested'] and audit['date_filter']['lookback_days']==90
    assert query['status']=='completed' and query['cached'] is False and query['receipt']['sha256']
    assert audit['sources'][0]['discovery_receipt_ids']==[query['receipt']['id']]
    assert result['sections']==original['sections'] and not result['excluded_directions']


def test_cached_or_backdated_receipt_cannot_impersonate_live_search(tmp_path):
    service,calls,fail=setup(tmp_path);old=service.search('causal attention')
    class Stale:
        def search(self,query,**kwargs):return {**old,'query':query,'cached':True}
    result=asyncio.run(verify_future_work(service.library,Provider(),proposal(),discovery=Stale(),
        force_refresh=True,lookback_days=90))
    assert result['future_verification']['queries'][0]['status']=='failed'
    assert not result['future_verification']['sources'] and not result['excluded_directions']


def test_new_future_tasks_default_to_live_recent_search():
    task=ResearchTask(kind='future_work')
    assert task.refresh_search is True and task.research_days==90


def test_unverified_live_search_cannot_filter_even_with_a_complete_pdf(tmp_path):
    from research_workspace.future_filter import filter_step
    from research_workspace.jobs import JobStore
    library=Library(tmp_path);store=JobStore(library);job=store.create('future_filter',{})
    class Forbidden:
        async def generate(self,*a,**k):raise AssertionError('No paid classification for incomplete live search')
    class Service:provider=Forbidden()
    original={'kind':'future_work','sections':[{'proposed_work':'Report a result.'}],
        'future_fulltext':{'directions':[{'direction_id':'direction-1','section_index':0,'heading':'Fixture',
            'proposed_work':'Report a result.','documents':[{'document_id':'fixture','run_id':'fixture-run',
            'parse_status':'completed','pages_done':1,'total_pages':1}],
            'search_audit':{'live_required':True,'verified':False}}]}}
    result=None
    for _ in range(20):
        result=asyncio.run(filter_step(store,Service(),store.get(job['id']),original))
        if result:break
    assert result and not result['excluded_directions']
    assert result['retained_directions'][0]['status']=='evidence_insufficient'
    assert result['retained_directions'][0]['search_audit']['verified'] is False


def test_future_candidate_repair_preserves_good_claims_and_both_proposals(tmp_path):
    import pymupdf
    from research_workspace.intelligence import IntelligenceService,SynthesisPayload,ClaimSupportPayload,ClaimRepairPayload,CoveragePayload
    library=Library(tmp_path)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30,70),'ORION models use attention. ORION predicts removed words.')
        doc=library.import_document(pdf.tobytes(),'orion.pdf')['document']
    library.process_next_job();calls=[];plans=['Test random projection.','Test a formal privacy guarantee.']
    class Provider:
        configured=True
        async def generate(self,prompt,payload,schema):
            calls.append(schema.__name__)
            if schema is SynthesisPayload:
                assert payload['allow_isolated_claim_repair'] is True
                eid=payload['evidence'][0]['evidence_id']
                return SynthesisPayload(status='generated',sections=[{'heading':str(i),'proposed_work':p,
                    'claims':[{'text':'ORION uses attention.' if i==0 else 'ORION predicts 99 words.',
                        'citations':[{'evidence_id':eid,'quote_id':'Q1' if i==0 else 'Q2'}]}]} for i,p in enumerate(plans)])
            if schema is ClaimSupportPayload:return ClaimSupportPayload(
                verdict='unsupported' if '99' in payload['claim']['text'] else 'supported',reason='Fixture own quote check.')
            if schema is ClaimRepairPayload:
                assert payload['repair_scope']=='background_fact_only' and payload['proposals_held_out']
                assert 'Two directions.' not in payload['request'] and all(p not in payload['request'] for p in plans)
                assert prompt.startswith('Prompt version: future-background-repair-v1')
                assert 'paper-scoped wording' in prompt and 'name-only quotation' in prompt
                return ClaimRepairPayload(claim={'text':'ORION predicts removed words.',
                    'citations':[{'evidence_id':payload['evidence'][0]['evidence_id'],'quote_id':'Q2'}]})
            if schema is CoveragePayload:return CoveragePayload(unverified_statements=[],missing_aspects=[])
            raise AssertionError(schema)
    service=IntelligenceService(library,Provider())
    async def retrieve(*a,**kw):
        blocks=service._blocks([doc['id']]);return {'evidence':blocks,'trace':[],'queries':[],'_snapshot_blocks':blocks}
    service.retrieve_for_question=retrieve
    result=asyncio.run(service.synthesize('future_work',[doc['id']],instruction='Two directions.'))
    assert [s['proposed_work'] for s in result['sections']]==plans
    assert result['sections'][0]['claims'][0]['text']=='ORION uses attention.'
    assert result['sections'][1]['claims'][0]['text']=='ORION predicts removed words.'
    assert calls.count('SynthesisPayload')==1 and calls.count('ClaimRepairPayload')==1
    assert 'optional_prune' not in result['grounding']


def test_retries_preserve_failed_review_history_without_advancing_progress(tmp_path):
    from research_workspace.jobs import JobStore
    store=JobStore(Library(tmp_path));job=store.create('future_work',{})
    for attempt in (1,2):
        with store.library._connection() as db:
            db.execute('INSERT OR REPLACE INTO research_job_items VALUES(?,?,?)',(job['id'],'grounding_validation',
                json.dumps({'stage':'grounding_validation','result':{'attempt':attempt}})))
        store.finish(job['id'],'failed',error='fixture')
        store.retry(job['id'])
    store.save_item(job['id'],'future_synthesis',{'stage':'future_synthesis','result':{}},4)
    saved=store.get(job['id']);archives=[i for i in saved['completed_items'] if i['stage'].startswith('diagnostic:')]
    assert [i['result']['attempt'] for i in archives]==[1,2]
    assert saved['progress']==1 and saved['total']==4
