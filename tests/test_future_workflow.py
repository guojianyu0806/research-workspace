import asyncio
from types import SimpleNamespace

import pytest

from research_workspace.advanced_api import task_markdown
from research_workspace.jobs import JobStore
from research_workspace.library import Library


def test_future_work_synthesis_checkpoint_survives_inflight_cancel_and_resume(tmp_path,monkeypatch):
    calls=[]
    synthesis={'kind':'future_work','status':'generated','sections':[{'heading':'Fixture','proposed_work':'test research'}]}
    class Service:
        provider=SimpleNamespace(configured=True)
        async def synthesize(self,*args,**kwargs):
            calls.append('synthesis')
            store.cancel(task['id'])
            return synthesis
    async def verify(library,provider,saved,*,check_cancel):
        check_cancel()
        assert saved==synthesis
        calls.append('verification')
        return {**saved,'future_verification':{'status':'evidence_insufficient','notice':'offline fixture'}}
    monkeypatch.setattr('research_workspace.future_verification.verify_future_work',verify)
    store=JobStore(Library(tmp_path))
    task=store.create('future_work',{'document_ids':['fixture'],'instruction':'test'})
    assert asyncio.run(store.process_next(Service()))
    assert store.get(task['id'])['status']=='cancelled'
    assert store.get(task['id'])['progress']==1
    store.cancel(task['id'])
    store.retry(task['id'])
    assert asyncio.run(store.process_next(Service()))
    done=store.get(task['id'])
    assert done['status']=='completed' and done['progress']==2
    assert calls==['synthesis','verification']
    assert done['result']['future_verification']['status']=='evidence_insufficient'


def future_fixture():
    source = {'source_id':'arxiv:2601.00001v1','url':'https://arxiv.org/abs/2601.00001v1',
              'title':'Offline Abstract Fixture','published':'2026-01-02T00:00:00Z',
              'retrieved_at':'2026-10-07T10:00:00+00:00'}
    excluded = {'direction_id':'direction-1','section_index':0,'heading':'第一方向',
                'proposed_work':'原始方向一','status':'potentially_addressed','excluded':True,
                'reason':'摘要可能覆盖，需要阅读全文确认。',
                'citations':[{'source_id':source['source_id'],'quote':'We report an explicit method for this fixture task.',
                              'url':'https://ignored-citation.invalid','published':source['published']}]}
    retained = {'direction_id':'direction-2','section_index':1,'heading':'第二方向',
                'proposed_work':'原始方向二','status':'evidence_insufficient','excluded':False,
                'reason':'现有摘要不足，保留待核验。','citations':[]}
    return {'kind':'future_work','status':'generated',
            'sections':[{'heading':'第一方向','proposed_work':'原始方向一','claims':[]},
                        {'heading':'第二方向','proposed_work':'原始方向二','claims':[]}],
            'retained_directions':[retained],'excluded_directions':[excluded],
            'future_work_verification':'bounded_abstract_review',
            'future_verification':{'status':'partial','notice':'有限摘要核验，不是研究空白证明。',
                'queries':[{'query_id':'query-1','query':'causal attention graph','status':'completed',
                            'retrieved_at':'2026-10-07T10:00:00+00:00','attempted_at':'2026-10-08T09:00:00+00:00',
                            'cached':True,'field':'all','sort_by':'submittedDate','limit':5,
                            'source_ids':[source['source_id']]},
                           {'query_id':'query-2','query':'bayesian robustness','status':'not_attempted',
                            'reason':'本条未执行。','source_ids':[]}],
                'sources':[source],'directions':[excluded,retained],'errors':[]}}


def readable_export(result=None, *, status='completed', items=None):
    return task_markdown({'id':'offline-export-test','kind':'future_work','status':status,
                          'result':result,'completed_items':items or []}).split('## 来源与任务记录',1)[0]


def test_future_markdown_exposes_verdict_queries_citations_and_original_section_status():
    output = readable_export(future_fixture())
    assert '部分方向已形成判定（partial）' in output
    assert '有限摘要核验，不是研究空白证明。' in output
    assert '查询词：causal attention graph' in output
    assert '2026-10-07T10:00:00+00:00；使用缓存' in output
    assert '本次尝试时间：2026-10-08T09:00:00+00:00' in output
    assert 'query-2 · 未执行' in output
    assert '### 保留为待验证方向 · 1' in output
    assert '### 暂移出重点候选（需全文确认） · 1' in output
    assert '现有摘要不足，保留待核验。' in output
    assert '> We report an explicit method for this fixture task.' in output
    assert '[arxiv:2601.00001v1 · Offline Abstract Fixture](https://arxiv.org/abs/2601.00001v1)' in output
    assert '发表时间：2026-01-02T00:00:00Z' in output
    assert 'https://ignored-citation.invalid' not in output
    assert '## 第一方向\n\n**此方向暂移出重点候选，需全文确认。' in output
    second_section = output.split('## 原始分节内容（保留供核对）',1)[1].split('## 第二方向',1)[1]
    assert '原始方向二' in second_section and '暂移出' not in second_section


@pytest.mark.parametrize('url',[
    'https://arxiv.org.evil.invalid/abs/2601.00001v1',
    'https://evil.invalid@arxiv.org/abs/2601.00001v1',
    'https://arxiv.org:443/abs/2601.00001v1',
    'https://arxiv.org/abs/2601.00001v1?redirect=bad',
    'https://arxiv.org/abs/2601.00001v1#fragment',
    'https://arxiv.org/abs/2601.00002v1',
    'javascript:alert(1)',
])
def test_future_export_only_links_matching_official_arxiv_abstracts(url):
    result = future_fixture()
    result['future_verification']['sources'][0]['url'] = url
    output = readable_export(result)
    assert '官方论文链接未通过校验' in output
    assert ']('+url+')' not in output
    assert '> We report an explicit method for this fixture task.' in output


def test_future_export_escapes_metadata_and_refuses_unlisted_citation_url():
    result = future_fixture()
    result['future_verification']['sources'][0]['title'] = '[run](javascript:alert(1)) <img src=x>'
    result['retained_directions'][0]['citations'] = [
        {'source_id':'arxiv:2601.99999v1','url':'https://arxiv.org/abs/2601.99999v1',
         'quote':'A quoted sentence from a missing source.'}]
    output = readable_export(result)
    assert '<img src=x>' not in output
    assert '[run](javascript:alert(1))' not in output
    assert 'arxiv:2601.99999v1 · 不在本次核验来源清单中' in output
    assert '](https://arxiv.org/abs/2601.99999v1)' not in output


def test_cancelled_synthesis_checkpoint_can_be_read_without_claiming_verification():
    synthesis = {'kind':'future_work','status':'generated',
                 'sections':[{'heading':'已保存方向','proposed_work':'无需重新生成即可查看的内容'}]}
    output = readable_export(status='cancelled',items=[{'stage':'future_synthesis','result':synthesis}])
    assert '状态：cancelled' in output
    assert '公开摘要核验尚未完成' in output
    assert '## 已保存方向' in output
    assert '无需重新生成即可查看的内容' in output
    assert '已保存的阶段结果' in output


def test_interrupted_export_prefers_saved_verification_over_initial_synthesis():
    verified = future_fixture()
    synthesis = {'kind':'future_work','sections':[{'heading':'旧检查点标题','proposed_work':'original'}]}
    output = readable_export(status='interrupted',items=[{'stage':'future_synthesis','result':synthesis},
                                                        {'stage':'future_verification','result':verified}])
    assert '状态：interrupted' in output
    assert '部分方向已形成判定' in output and '旧检查点标题' not in output
    assert '公开摘要核验尚未完成' not in output


def test_saved_verification_is_reused_after_crash_before_finish(tmp_path,monkeypatch):
    calls=[]
    class Service:
        provider=SimpleNamespace(configured=True)
        async def synthesize(self,*args,**kwargs):
            calls.append('synthesis')
            raise AssertionError('A saved synthesis must not be billed again')
    async def verify(*args,**kwargs):
        calls.append('verification')
        raise AssertionError('A saved verification must not be billed again')
    monkeypatch.setattr('research_workspace.future_verification.verify_future_work',verify)
    store=JobStore(Library(tmp_path))
    task=store.create('future_work',{'document_ids':['fixture'],'instruction':'offline'})
    synthesis={'kind':'future_work','sections':[{'heading':'Saved','proposed_work':'saved direction'}]}
    verified=future_fixture()
    store.save_item(task['id'],'future_synthesis',{'stage':'future_synthesis','result':synthesis},2)
    store.save_item(task['id'],'future_verification',{'stage':'future_verification','result':verified},2)
    assert store.claim()['id']==task['id']
    assert store.recover()==1
    assert store.get(task['id'])['status']=='interrupted'
    store.retry(task['id'])
    assert asyncio.run(store.process_next(Service()))
    done=store.get(task['id'])
    assert done['status']=='completed' and done['progress']==2
    assert done['result']==verified
    assert calls==[]
