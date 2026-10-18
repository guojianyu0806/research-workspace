import pymupdf
import pytest
from fastapi.testclient import TestClient

from research_workspace.api import create_app
from research_workspace.library import Library, LibraryError
from research_workspace.reader_search import ReaderSearch
from research_workspace.semantic import SemanticIndex


def add(lib, text, name='paper.pdf'):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 90), text)
        doc = lib.import_document(pdf.tobytes(), name)['document']
    lib.process_next_job()
    return lib.get_document(doc['id'])


class Dense:
    async def rank(self, queries, blocks, **kwargs):
        return [b.model_copy(update={'score':.9 if 'canine' in b.text else .2}) for b in blocks], {'status':'applied'}


def test_semantic_paraphrase_has_exact_original_and_zero_provider_calls(tmp_path, monkeypatch):
    from research_workspace.intelligence import ChatCompletionProvider
    monkeypatch.setattr(ChatCompletionProvider, '__init__', lambda *a, **k:pytest.fail('No provider'))
    lib=Library(tmp_path);doc=add(lib,'A canine rests beside the window.')
    r=ReaderSearch(lib,semantic=Dense()).search('Where does the dog rest?', mode='semantic')
    assert r['model_calls']==0 and not r['network_used'] and r['complete']
    h=r['hits'][0];original=lib.get_page(doc['id'],1,h['run_id'])['blocks'][0]
    assert h['text']==original['text'] and h['bbox']==original['bbox'] and h['block_id']==original['id']
    assert h['channels']==['semantic'] and r['source_snapshots'][0]['run_id']==h['run_id']


def test_ranker_cannot_supply_fake_text_or_foreign_identity(tmp_path):
    lib=Library(tmp_path);add(lib,'A canine sleeps.')
    class Tampered:
        async def rank(self,queries,blocks,**kwargs):
            b=blocks[0]
            return [b.model_copy(update={'text':'Forged answer','bbox':[0,0,1,1],'score':.9}),
                b.model_copy(update={'block_id':'foreign','score':1}),
                b.model_copy(update={'run_id':'foreign','score':1})], {'status':'applied'}
    r=ReaderSearch(lib,semantic=Tampered()).search('dog',mode='semantic')
    assert len(r['hits'])==1 and r['hits'][0]['text']=='A canine sleeps.' and r['hits'][0]['bbox']!=[0,0,1,1]


def test_weak_similarity_never_becomes_a_semantic_hit(tmp_path):
    lib=Library(tmp_path);add(lib,'A printed table of figures.')
    r=ReaderSearch(lib,semantic=Dense()).search('canine',mode='semantic')
    assert r['complete'] and r['hits']==[]


@pytest.mark.parametrize('mode',['hybrid','semantic'])
def test_unavailable_semantic_is_explicit_and_hybrid_keeps_lexical(tmp_path,mode):
    lib=Library(tmp_path);add(lib,'A canine sleeps.')
    class Unavailable:
        async def rank(self,*a,**k):raise ValueError('Should not echo exception')
    r=ReaderSearch(lib,semantic=Unavailable()).search('canine',mode=mode)
    assert not r['complete'] and r['warnings'] and 'Should not echo' not in str(r)
    assert bool(r['hits'])==(mode=='hybrid')
    assert r['status']==('lexical_fallback' if mode=='hybrid' else 'semantic_unavailable')


def test_scope_history_pending_reparse_and_foreign_run(tmp_path):
    lib=Library(tmp_path);a=add(lib,'A canine sleeps.','a.pdf');b=add(lib,'Another canine sleeps.','b.pdf')
    s=ReaderSearch(lib,semantic=Dense());old=a['available_run']['id'];new=lib.create_run(a['id'])
    r=s.search('dog',document_id=a['id'])
    assert {h['run_id'] for h in r['hits']}=={old}
    with pytest.raises(LibraryError):s.search('dog',document_id=a['id'],run_id=new['id'])
    with pytest.raises(LibraryError):s.search('dog',document_id=a['id'],run_id=b['available_run']['id'])
    with pytest.raises(LibraryError):s.search('dog',run_id=old)
    lib.process_next_job()
    r=s.search('dog',document_id=a['id'],run_id=old)
    assert {h['run_id'] for h in r['hits']}=={old}


def test_archives_and_confirmed_version_preferences_are_respected(tmp_path):
    lib=Library(tmp_path);a=add(lib,'A canine sleeps.','a.pdf');b=add(lib,'Another canine sleeps.','b.pdf')
    lib.confirm_version_relation(a['id'],b['id'],action='use_other')
    s=ReaderSearch(lib,semantic=Dense());assert {h['document_id'] for h in s.search('dog')['hits']}=={b['id']}
    lib.archive_document(b['id'])
    assert s.search('dog')['hits']==[]
    assert s.search('dog',document_id=a['id'])['hits']


def test_budget_does_not_silently_search_a_subset(tmp_path,monkeypatch):
    import research_workspace.reader_search as module
    lib=Library(tmp_path);add(lib,'A canine sleeps.')
    monkeypatch.setattr(module,'MAX_CHARACTERS',3)
    r=ReaderSearch(lib,semantic=Dense()).search('dog')
    assert r['status']=='source_budget_exceeded' and not r['complete'] and not r['hits']


def test_chinese_query_english_hint_and_partial_parse_warning(tmp_path):
    lib=Library(tmp_path);a=add(lib,'A canine sleeps.')
    with lib._connection() as db:db.execute("update parse_runs set status='partial' where id=?",(a['available_run']['id'],))
    s=ReaderSearch(lib,semantic=Dense());r=s.search('小狗在哪里？',english_query='dog rests')
    assert r['english_query']=='dog rests' and r['hits']
    assert any('部分' in w for w in r['warnings'])
    assert any('中文' in w for w in s.search('小狗在哪里？')['warnings'])


def test_api_mode_validation_legacy_and_account_boundary(tmp_path,monkeypatch):
    monkeypatch.setattr(SemanticIndex,'rank',Dense().rank)
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as guest, TestClient(app,base_url='http://127.0.0.1:8765') as other:
        a=add(app.state.library,'A canine sleeps.');p={'q':'dog','mode':'semantic','document_id':a['id']}
        assert guest.get('/api/search',params=p).json()['hits']
        assert guest.get('/api/search',params={'q':'canine'}).json()['mode']=='literal'
        assert guest.get('/api/search',params={**p,'mode':'unknown'}).status_code==422
        other.post('/api/account/register',json={'name':'ReaderOther','password':'test-only-password-987'})
        assert other.get('/api/search',params=p).status_code==404
        assert other.get('/api/search',params={'q':'dog','mode':'semantic'}).json()['hits']==[]


def test_snapshot_stays_consistent_during_concurrent_reparse(tmp_path,monkeypatch):
    lib=Library(tmp_path);a=add(lib,'A canine sleeps.','a.pdf');b=add(lib,'Another canine sleeps.','b.pdf')
    original=lib._document;started=False
    def observed(db,did):
        nonlocal started
        doc=original(db,did)
        if not started:
            started=True
            writer=Library(tmp_path);writer.create_run(a['id']);writer.process_next_job()
        return doc
    monkeypatch.setattr(lib,'_document',observed)
    r=ReaderSearch(lib,semantic=Dense()).search('dog',mode='semantic')
    assert {s['run_id'] for s in r['source_snapshots']}=={a['available_run']['id'],b['available_run']['id']}
    assert lib.get_document(a['id'])['available_run']['id']!=a['available_run']['id']


def test_busy_search_and_long_block_coverage_are_auditable(tmp_path,monkeypatch):
    import research_workspace.reader_search as module
    lib=Library(tmp_path);add(lib,'A canine sleeps.')
    class Busy:
        def acquire(self,**kwargs):return False
    with monkeypatch.context() as m:
        m.setattr(module,'_slots',Busy())
        r=ReaderSearch(lib,semantic=Dense()).search('dog')
        assert r['status']=='busy' and not r['complete'] and r['search_id']
    class Truncated:
        async def rank(self,queries,blocks,**kwargs):
            return [blocks[0].model_copy(update={'score':.9})],{'status':'applied','truncated_blocks':1}
    r=ReaderSearch(lib,semantic=Truncated()).search('dog')
    assert not r['complete'] and any('5800' in w for w in r['warnings'])


def test_failed_ranker_cannot_return_semantic_hits(tmp_path):
    lib=Library(tmp_path);add(lib,'A canine sleeps.')
    class Failed:
        async def rank(self,queries,blocks,**kwargs):
            return [blocks[0].model_copy(update={'score':.9})],{'status':'failed'}
    r=ReaderSearch(lib,semantic=Failed()).search('dog',mode='semantic')
    assert r['hits']==[] and not r['complete']


def test_reference_titles_do_not_outrank_explanatory_originals(tmp_path,monkeypatch):
    from research_workspace.intelligence import Evidence
    def b(name,text):
        return Evidence(evidence_id=name,document_id='doc',run_id='run',block_id=name,
            document_title='Paper',filename='paper.pdf',page=1,bbox=[.1,.1,.9,.2],text=text)
    blocks=[b('body','The system represents positions by adding ordered vectors to token embeddings.'),
        b('heading','References'), b('ref','An author. A study of token order representations. A Journal, 2020.')]
    class Ranked:
        async def rank(self,queries,source,**kwargs):
            return [blocks[2].model_copy(update={'score':.95}),blocks[0].model_copy(update={'score':.8})],{'status':'applied'}
    s=ReaderSearch(Library(tmp_path),semantic=Ranked())
    monkeypatch.setattr(s,'snapshot',lambda *a:(blocks,[],'ready'))
    assert s.search('token order',mode='semantic')['hits'][0]['block_id']=='body'
    assert s.search('references token order',mode='semantic')['hits'][0]['block_id']=='ref'


def test_explicit_local_aliases_are_recorded_without_changing_originals(tmp_path):
    lib=Library(tmp_path);add(lib,'A canine sleeps.')
    class Captured:
        async def rank(self,queries,blocks,**kwargs):
            assert queries==['missing words in both directions','masked tokens in bidirectional']
            return [],{'status':'applied'}
    r=ReaderSearch(lib,semantic=Captured()).search('missing words in both directions')
    assert r['query']=='missing words in both directions' and len(r['local_aliases'])==2
    assert r['ranking_queries'][1]=='masked tokens in bidirectional'


def test_unrelated_midrange_similarity_is_below_candidate_floor(tmp_path):
    lib=Library(tmp_path);add(lib,'A canine sleeps.')
    class Weak:
        async def rank(self,queries,blocks,**kwargs):
            return [blocks[0].model_copy(update={'score':.61})],{'status':'applied'}
    assert ReaderSearch(lib,semantic=Weak()).search('off topic',mode='semantic')['hits']==[]
