"""Paged history must retain text, source identity, ordering and tenant isolation."""
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest

from research_workspace.api import create_app
from research_workspace.jobs import JobStore
from research_workspace.library import Library
from research_workspace.translation_pages import TranslationRevisionChanged
from scripts.freeze_long_reading import synthetic_blocks


def result_for(blocks):
    return dict(document_id='synthetic-long-document',run_id='synthetic-long-run-v1',
                target_language='zh',status='completed',total_blocks=len(blocks),
                total_pages=(len(blocks)+17)//18,parsed_pages=[],missing_pages=[],blocks=blocks)


def make_job(store,blocks,finished=True):
    payload={k:v for k,v in result_for(blocks).items() if k in ('document_id','run_id','target_language')}
    job=store.create('translate',payload)
    if finished:store.finish(job['id'],'completed',result=result_for(blocks))
    return job['id']


def original_records(store,job_id):
    with store.library._connection() as db:
        return {table:[tuple(row) for row in db.execute(f'SELECT * FROM {table} WHERE '+
                ('id=?' if table=='research_jobs' else 'job_id=?'),(job_id,))]
                for table in ('research_jobs','research_job_items','research_job_events','model_calls')}


@pytest.fixture
def store(tmp_path):return JobStore(Library(tmp_path))


def test_5000_blocks_all_pages_reconstruct_original_without_changing_history(store):
    blocks=synthetic_blocks(5000);job_id=make_job(store,blocks)
    # Existing checkpoints and review records remain available in the legacy response.
    store.save_item(job_id,'old-checkpoint',blocks[-1],5000)
    store.save_item(job_id,'review_validation',{'result':'synthetic audit retained'},5000)
    full=store.get(job_id);original=original_records(store,job_id)
    header=store.get(job_id,include_result=False)
    assert header['result'] is None and header['completed_items']==[] and header['model_calls']==[]
    assert header['result_deferred'] and header['items_deferred'] and header['model_calls_deferred']
    info=header['translation_summary']
    assert info['total']==5000 and info['invalid_block_count']==0 and info['source_kind']=='result'
    assert 'blocks' not in info['result_metadata']
    reconstructed=[]
    for offset in range(0,5000,200):
        page=store.translation_page(job_id,offset=offset,limit=200,revision=info['revision'],include_index=offset==0)
        assert len(page['blocks'])==200 and page['offset']==offset
        assert [s['ordinal'] for s in page['record_metadata']]==list(range(offset,offset+200))
        assert all(s['anchor_valid'] for s in page['record_metadata'])
        reconstructed+=page['blocks']
        if offset==0:
            assert len(page['index'])==5000
            assert 'source_text' not in page['index'][0]
            assert page['index'][0]['source_chars']==len(blocks[0]['source_text'])
        assert page['next_offset']==(offset+200 if offset<4800 else None)
    assert reconstructed==blocks
    assert store.translation_page(job_id,offset=5000)['blocks']==[]
    assert store.translation_page(job_id,block_id=blocks[3999]['block_id'],limit=1)['blocks']==[blocks[3999]]
    assert store.get(job_id)==full and original_records(store,job_id)==original


def test_checkpoint_replacement_recovery_and_completion_have_atomic_revisions(store):
    blocks=synthetic_blocks(3);job_id=make_job(store,blocks,False)
    store.save_item(job_id,'b2',blocks[2],3);store.save_item(job_id,'b0',blocks[0],3)
    store.save_item(job_id,'grounding_validation',{'result':'synthetic'},3)
    store.save_item(job_id,'diagnostic:old',{'result':'retained'},3)
    page=store.translation_page(job_id)
    assert page['blocks']==[blocks[2],blocks[0]] and page['source_kind']=='items'
    store.save_item(job_id,'b2',blocks[1],3)
    with pytest.raises(TranslationRevisionChanged):store.translation_page(job_id,revision=page['revision'])
    updated=store.translation_page(job_id)
    assert updated['blocks']==[blocks[0],blocks[1]]
    store.claim();store.recover()
    assert store.get(job_id)['status']=='interrupted'
    assert store.translation_page(job_id)['blocks']==updated['blocks']
    store.finish(job_id,'completed',result=result_for(blocks))
    with pytest.raises(TranslationRevisionChanged):store.translation_page(job_id,revision=updated['revision'])
    assert store.translation_page(job_id)['blocks']==blocks
    assert len(store.get(job_id)['completed_items'])==4


@pytest.mark.parametrize('variant,missing,duplicates,invalid',[('missing',28,0,0),('duplicate',0,1,0),('wrong-run',0,0,1)])
def test_invalid_anchor_records_are_preserved_without_silent_dedup(store,variant,missing,duplicates,invalid):
    blocks=synthetic_blocks(1000,variant);job_id=make_job(store,blocks)
    info=store.get(job_id,include_result=False)['translation_summary']
    assert info['missing_translation_count']==missing and info['duplicate_id_count']==duplicates
    assert info['invalid_block_count']==invalid
    assert info['ambiguous_anchor_count']==(2 if duplicates else 0)
    page=store.translation_page(job_id,offset=998)
    assert page['blocks']==blocks[998:]
    assert page['record_metadata'][-1]['anchor_valid']==(variant=='missing')
    if duplicates:
        assert page['record_metadata'][-1]['duplicate_block_id']
        assert not store.translation_page(job_id,offset=1,limit=1)['record_metadata'][0]['anchor_valid']
        with pytest.raises(ValueError,match='重复'):store.translation_page(job_id,block_id=blocks[1]['block_id'])


@pytest.mark.parametrize('offset,limit',[(-1,100),(True,100),(0,201),(0,0),(0,True),(1.5,100)])
def test_paging_bounds(store,offset,limit):
    job_id=make_job(store,[])
    with pytest.raises(ValueError):store.translation_page(job_id,offset=offset,limit=limit)


@pytest.mark.parametrize('box,valid',[([.2,.2,.2,.5],False),([.2,.2,.7,.2],False),([0,0,1,1],True),
                                   (None,False),([True,0,1,1],False),([0,0,2,1],False)])
def test_only_nonempty_normalized_source_areas_can_navigate(store,box,valid):
    blocks=synthetic_blocks(1);blocks[0]['bbox']=box;job_id=make_job(store,blocks)
    page=store.translation_page(job_id)
    assert page['blocks']==blocks and page['record_metadata'][0]['anchor_valid'] is valid
    assert page['invalid_block_count']==int(not valid)


def test_metadata_bounds_events_and_does_not_hide_legacy_records(store):
    job_id=make_job(store,synthetic_blocks(2))
    for i in range(105):store.event(job_id,'synthetic',str(i))
    original=original_records(store,job_id);full=store.get(job_id)
    header=store.header(job_id)
    assert len(header['events'])==100 and header['events'][-1]['message']=='104'
    assert header['event_count']==107 and header['events_truncated']
    assert store.get(job_id)==full and original_records(store,job_id)==original
    other=store.create('qa',{'question':'synthetic'})
    assert store.header(other['id'])['translation_summary'] is None
    with pytest.raises(ValueError):store.translation_page(other['id'])
    with pytest.raises(KeyError):store.translation_page('missing-task')
    with pytest.raises(KeyError):store.translation_page(job_id,block_id='unknown-block')


@pytest.mark.parametrize('value',[{},None,[],{'blocks':None},{'blocks':[None,True,False,123,'text',{}]}])
def test_legacy_incomplete_shapes_never_rewrite_original(store,value):
    job_id=make_job(store,[]);store.finish(job_id,'completed',result=value)
    before=original_records(store,job_id)
    header=store.header(job_id)
    if isinstance(value,dict) and isinstance(value.get('blocks'),list):
        page=store.translation_page(job_id)
        assert page['blocks']==value['blocks'] and all(not s['anchor_valid'] for s in page['record_metadata'])
    elif value is None:
        assert header['translation_summary']['source_kind']=='items'
    else:
        assert header['translation_summary']['index_error']
        with pytest.raises(ValueError):store.translation_page(job_id)
    assert original_records(store,job_id)==before


def test_nonstandard_json_reports_index_error_without_altering_saved_result(store):
    job_id=make_job(store,[])
    store.finish(job_id,'completed',result={'blocks':[{'value':float('nan')}]})
    before=original_records(store,job_id)
    assert store.header(job_id)['translation_summary']['index_error']
    with pytest.raises(ValueError):store.translation_page(job_id)
    assert original_records(store,job_id)==before


def test_parallel_first_reads_and_unrelated_updates_keep_one_consistent_revision(store):
    blocks=synthetic_blocks(5000);job_id=make_job(store,blocks)
    other=make_job(store,[])
    def read(i):
        page=store.translation_page(job_id,offset=i*100,limit=100)
        assert page['blocks']==blocks[i*100:i*100+100]
        return page['revision']
    with ThreadPoolExecutor(max_workers=10) as pool:revisions=list(pool.map(read,range(20)))
    assert len(set(revisions))==1
    store.save_item(other,'unrelated',{},1)
    assert store.translation_page(job_id)['revision']==revisions[0]


def test_api_tenants_paging_validation_revision_and_legacy_export(tmp_path,monkeypatch):
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):monkeypatch.delenv(name,raising=False)
    app=create_app(tmp_path,start_worker=False)
    base='http://127.0.0.1:8765'
    with TestClient(app,base_url=base) as alice,TestClient(app,base_url=base) as bob,TestClient(app,base_url=base) as guest:
        registration=alice.post('/api/account/register',json={'name':'Paging_Alice','password':'synthetic-pass-123'})
        assert registration.status_code==200,registration.text
        user=registration.json()['user']
        assert bob.post('/api/account/register',json={'name':'Paging_Bob','password':'synthetic-pass-456'}).status_code==200
        assert alice.get('/api/documents').status_code==200
        store=JobStore(app.state.libraries[user['id']]);blocks=synthetic_blocks(107);job_id=make_job(store,blocks)
        path='/api/research/tasks/'+job_id
        original=original_records(store,job_id)
        exported=alice.get(path+'/export');assert exported.status_code==200
        header=alice.get(path+'?include_result=false').json()['job']
        revision=header['translation_summary']['revision']
        assert header['result'] is None
        page=alice.get(path+'/translation-blocks',params={'limit':100,'include_index':True,'revision':revision})
        assert page.status_code==200 and page.json()['blocks']==blocks[:100] and len(page.json()['index'])==107
        assert alice.get(path+'/translation-blocks?offset=100').json()['blocks']==blocks[100:]
        assert alice.get(path).json()['job']['result']==result_for(blocks)
        for other in (bob,guest):
            for suffix in ('','?include_result=false','/translation-blocks?include_index=true','/export'):
                assert other.get(path+suffix).status_code==404
        for query in ('limit=201','limit=0','offset=-1','revision=0'):
            assert alice.get(path+'/translation-blocks?'+query).status_code==422
        assert alice.get(path+'/translation-blocks?block_id=absent').status_code==404
        store.finish(job_id,'completed',result=result_for(blocks))
        assert alice.get(path+'/translation-blocks',params={'revision':revision}).status_code==409
        assert alice.get(path+'/export').content==exported.content
        after=original_records(store,job_id)
        assert after['research_job_items']==original['research_job_items'] and after['model_calls']==[]
