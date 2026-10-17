"""Offline usage receipts, failure preservation, recovery and account boundaries."""
import asyncio
import json

import httpx
import pytest

from research_workspace.intelligence import ChatCompletionProvider,ModelConfig,AnswerPayload,ModelProviderError,IntelligenceService
from research_workspace.jobs import JobStore
from research_workspace.library import Library
from research_workspace.model_usage import record_model_calls,sanitized_usage,estimate_cost


def adapter(handler):
    async def no_wait(_):pass
    return ChatCompletionProvider(ModelConfig('https://model.invalid/v1','fixture-model','fixture-key-private'),
        transport=httpx.MockTransport(handler),sleep=no_wait)


def response(usage=None,valid=True):
    body={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'status':'insufficient_evidence','claims':[], 'message':'need evidence'}) if valid else 'bad json'}}]}
    if usage is not None:body['usage']=usage
    return httpx.Response(200,json=body)


def test_receipts_include_invalid_attempts_but_no_prompt_key_or_raw_output(tmp_path):
    store=JobStore(Library(tmp_path));job=store.create('qa',{})
    count=0
    def handler(request):
        nonlocal count
        count+=1
        return response({'prompt_tokens':12,'completion_tokens':8,'total_tokens':20,'prompt_cache_hit_tokens':5,'secret':'raw-secret'},valid=count>1)
    async def run():
        with record_model_calls(store.usage_store,job['id']):
            await adapter(handler).generate('private prompt',{'question':'private question'},AnswerPayload)
    asyncio.run(run())
    saved=store.get(job['id']);usage=saved['usage']
    assert count==2 and usage['calls']==2 and usage['total_tokens']==40 and usage['prompt_cache_hit_tokens']==10
    assert usage['cost'] is None and usage['usage_reported_calls']==2
    assert [c['attempt'] for c in saved['model_calls']]==[1,2]
    serialized=json.dumps(saved['model_calls'])
    assert all(secret not in serialized for secret in ['private prompt','private question','fixture-key-private','raw-secret','bad json'])
    assert store.summary(job['id'])['usage']==usage
    assert JobStore(Library(tmp_path/'other')).list()==[]


def test_timeout_unknown_is_not_replayed_and_recovery_keeps_uncertainty(tmp_path):
    store=JobStore(Library(tmp_path));job=store.create('qa',{});store.claim()
    count=0
    def handler(request):
        nonlocal count
        count+=1
        raise httpx.ReadTimeout('private transport',request=request)
    async def run():
        with record_model_calls(store.usage_store,job['id']):
            with pytest.raises(ModelProviderError):await adapter(handler).generate('test',{},AnswerPayload)
    asyncio.run(run())
    assert count==1 and store.get(job['id'])['usage']['unknown_outcomes']==1
    store.usage_store.begin(job['id'],'https://model.invalid/v1','fixture','AnswerPayload','test',1)
    store.recover()
    saved=store.get(job['id'])
    assert saved['status']=='interrupted' and saved['usage']['unknown_outcomes']==2 and saved['usage']['in_flight']==0
    assert saved['usage']['total_tokens'] is None and store.claim() is None


def test_missing_or_invalid_usage_is_not_reported_as_zero(tmp_path):
    assert sanitized_usage({'usage':{'total_tokens':True,'prompt_tokens':-1,'completion_tokens':1.5}})=={}
    store=JobStore(Library(tmp_path));job=store.create('qa',{})
    async def run():
        with record_model_calls(store.usage_store,job['id']):
            await adapter(lambda r:response()).generate('test',{},AnswerPayload)
    asyncio.run(run())
    assert store.get(job['id'])['usage']['calls']==1
    assert store.get(job['id'])['usage']['total_tokens'] is None


def test_actual_job_pipeline_context_does_not_leak_to_other_concurrent_job(tmp_path):
    stores=[JobStore(Library(tmp_path/str(i))) for i in range(2)]
    jobs=[s.create('translate_text',{'text':'Vector retrieval improves search.','target_language':'zh'}) for s in stores]
    async def handler(request):
        await asyncio.sleep(.01)
        return httpx.Response(200,json={'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'translated_text':'向量检索改善搜索。'},ensure_ascii=False)}}]})
    shared=adapter(handler)
    async def run():
        await asyncio.gather(*(s.process_next(IntelligenceService(s.library,shared)) for s in stores))
        # Outside a task, calls must not accidentally enter the previous ledger.
        await shared.generate('test',{},type('TranslationSchema',(),{'model_json_schema':staticmethod(lambda:{}),'model_validate_json':staticmethod(json.loads)}))
    asyncio.run(run())
    for s,j in zip(stores,jobs):
        result=s.get(j['id'])
        assert result['status']=='completed' and result['usage']['calls']==1
        assert result['model_calls'][0]['job_id']==j['id']


def test_usage_persistence_failure_does_not_replay_paid_request(tmp_path,monkeypatch):
    store=JobStore(Library(tmp_path));job=store.create('qa',{});calls=[]
    def fail(*args,**kwargs):raise RuntimeError('fixture database unavailable')
    monkeypatch.setattr(store.usage_store,'end',fail)
    async def run():
        with record_model_calls(store.usage_store,job['id']):
            with pytest.raises(RuntimeError):await adapter(lambda r:calls.append(1) or response({'total_tokens':1})).generate('test',{},AnswerPayload)
    asyncio.run(run())
    assert len(calls)==1


def test_public_price_range_uses_reported_cache_split_and_never_claims_invoice():
    from decimal import Decimal
    usage={'prompt_tokens':1000000,'completion_tokens':1000000,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':1000000}
    cost=estimate_cost('deepseek-v4-pro','api.deepseek.com',usage)
    assert Decimal(cost['min'])==Decimal('2.64') and Decimal(cost['max'])==Decimal('5.28') and cost['actual_charge'] is None
    assert estimate_cost('deepseek-v4-pro','untrusted.invalid',usage) is None
    assert estimate_cost('unknown','api.deepseek.com',usage) is None
    unknown=estimate_cost('deepseek-v4-pro','api.deepseek.com',{'prompt_tokens':1000000,'completion_tokens':1000000})
    assert Decimal(unknown['min'])==Decimal('2.002') and Decimal(unknown['max'])==Decimal('5.28')
