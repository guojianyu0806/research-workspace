import copy
import pytest
from fastapi.testclient import TestClient
from research_workspace.api import create_app
from research_workspace.library import Library,LibraryError
from research_workspace.version_diff import compare_text_snapshots
from tests.test_library import pdf_bytes


def block(text,index=0,document='before',run='r1'):
    return {'text':text,'block_id':document+str(index),'document_id':document,'run_id':run,
        'page':index+1,'bbox':[.1,.2,.7,.3]}


def add(lib,text,name):
    doc=lib.import_document(pdf_bytes([text]),name)['document'];lib.process_next_job()
    return lib.get_document(doc['id'])


def test_whitespace_and_segmentation_changes_do_not_become_text_revisions():
    a=[block('The method\nuses 12 layers. Next result is 3.5.')]
    b=[block('The method uses 12',0,'after'),block('layers.\nNext result is 3.5.',1,'after')]
    original=copy.deepcopy((a,b));r=compare_text_snapshots(a,b)
    assert r['complete'] and r['text_equivalent'] and r['changes']==[] and r['total_changes']==0
    assert (a,b)==original


@pytest.mark.parametrize(('before','after','left','right'),[
    ('Result is 10.','Result is 20.','10','20'),
    ('BERT uses LayerNorm.','bert uses LayerNorm.','BERT','bert'),
    ('Accuracy is 0.9.','Accuracy is 0.8.','9','8'),
    ('Use α + β.','Use α - β.','+','-'),
])
def test_numbers_case_and_math_symbols_are_not_normalized_away(before,after,left,right):
    r=compare_text_snapshots([block(before)],[block(after,document='after')])
    assert r['complete'] and not r['text_equivalent'] and len(r['changes'])==1
    c=r['changes'][0];assert (c['before_text'],c['after_text'])==(left,right)
    assert c['kind']=='replace'
    for side,original in [('before',before),('after',after)]:
        item=c[side];s=item['change_span']
        assert item['text']==original and original[s['start']:s['end']]==s['text']


def test_cross_block_replacement_preserves_every_source_region_without_offset_pairing():
    a=[block('Start keep red',0),block('green blue end.',1)]
    b=[block('Start keep orange end.',0,'after')]
    c=compare_text_snapshots(a,b)['changes'][0]
    assert c['before_text']=='red\ngreen blue' and c['after_text']=='orange'
    assert len(c['before_regions'])==2 and len(c['after_regions'])==1
    assert c['before_regions'][1]['page']==2
    assert compare_text_snapshots(a,b)['changes'][0]['change_id']==c['change_id']


@pytest.mark.parametrize('before,after,kind',[('A B.','A extra B.','insert'),('A extra B.','A B.','delete')])
def test_insert_and_delete_have_explicit_empty_sides(before,after,kind):
    c=compare_text_snapshots([block(before)],[block(after,document='after')])['changes'][0]
    assert c['kind']==kind
    empty='before' if kind=='insert' else 'after'
    assert c[empty] is None and c[empty+'_regions']==[] and c[empty+'_text']==''


@pytest.mark.parametrize('limit,reason',[('MAX_CHARS','character_budget_exceeded'),('MAX_TOKENS','token_budget_exceeded'),
    ('MAX_MATCH_PAIRS','alignment_budget_exceeded')])
def test_resource_limit_is_not_reported_as_no_difference(monkeypatch,limit,reason):
    from research_workspace import version_diff
    monkeypatch.setattr(version_diff,limit,1)
    r=compare_text_snapshots([block('A B C.')],[block('A B changed.')])
    assert r['status']=='resource_limit' and r['reason']==reason
    assert not r['complete'] and r['text_equivalent'] is None and r['total_changes'] is None


def test_output_limit_preserves_total_count_and_marks_incomplete(monkeypatch):
    from research_workspace import version_diff
    monkeypatch.setattr(version_diff,'MAX_CHANGES',1)
    r=compare_text_snapshots([block('A red B green C blue D.')],[block('A orange B white C black D.')])
    assert r['status']=='partial_output' and not r['complete'] and r['changes_truncated']
    assert r['total_changes']==3 and len(r['changes'])==1


def test_oversized_context_output_never_becomes_an_empty_success(monkeypatch):
    from research_workspace import version_diff
    monkeypatch.setattr(version_diff,'MAX_OUTPUT_CHARS',10)
    r=compare_text_snapshots([block('Result 10.')],[block('Result 20.')])
    assert not r['complete'] and r['changes_truncated'] and r['status']=='partial_output'
    assert r['total_changes']==1 and r['changes']==[] and r['text_equivalent'] is False


def test_fixed_historical_runs_survive_new_reparse_and_pdf_is_immutable(tmp_path,monkeypatch):
    lib=Library(tmp_path);a=add(lib,'Result is 10.','a.pdf');b=add(lib,'Result is 20.','b.pdf')
    rid=a['available_run']['id'];before=lib.get_page(a['id'],1,rid);source=lib._source_path(a['id']).read_bytes()
    queued=lib.create_run(a['id'])
    r=lib.compare_versions(a['id'],b['id'])
    assert r['run_id']==rid and r['other_run_id']==b['available_run']['id']
    with pytest.raises(LibraryError):lib.compare_versions(a['id'],b['id'],run_id=queued['id'])
    lib.process_next_job()
    assert lib.compare_versions(a['id'],b['id'],run_id=rid)['source_snapshots']['before']['run_id']==rid
    with pytest.raises(LibraryError):lib.compare_versions(a['id'],b['id'],run_id=b['available_run']['id'])
    assert lib.get_page(a['id'],1,rid)==before and lib._source_path(a['id']).read_bytes()==source
    from research_workspace import semantic
    monkeypatch.setattr(semantic,'_encode',lambda *a,**kw:pytest.fail('No model allowed'))
    assert lib.compare_versions(a['id'],b['id'],run_id=rid)['model_calls']==0


def test_both_run_selections_share_a_database_snapshot_during_concurrent_reparse(tmp_path,monkeypatch):
    lib=Library(tmp_path);a=add(lib,'First old text.','a.pdf');b=add(lib,'Second old text.','b.pdf')
    old=b['available_run']['id'];original=lib._document;started=False
    def observed(db,did):
        nonlocal started
        doc=original(db,did)
        if did==a['id'] and not started:
            started=True
            writer=Library(tmp_path);writer.create_run(b['id']);writer.process_next_job()
        return doc
    monkeypatch.setattr(lib,'_document',observed)
    result=lib.compare_versions(a['id'],b['id'])
    assert result['other_run_id']==old and lib.get_document(b['id'])['available_run']['id']!=old


def test_partial_parse_has_explicit_coverage_warning(tmp_path):
    lib=Library(tmp_path);a=lib.import_document(pdf_bytes(['Text available.',None]),'partial.pdf')['document'];lib.process_next_job()
    b=add(lib,'Other text.','b.pdf');r=lib.compare_versions(a['id'],b['id'])
    assert r['source_snapshots']['before']['parse_status']=='partial'
    assert any('部分可读' in x for x in r['limitations'])


def test_diff_api_binds_runs_and_rejects_cross_workspace_access(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as guest,TestClient(app,base_url='http://127.0.0.1:8765') as other:
        a=add(app.state.library,'Result is 10.','a.pdf');b=add(app.state.library,'Result is 20.','b.pdf')
        url=f"/api/documents/{a['id']}/diff"
        params={'other_id':b['id'],'run_id':a['available_run']['id'],'other_run_id':b['available_run']['id']}
        r=guest.get(url,params=params);assert r.status_code==200
        result=r.json();assert result['changes'][0]['before_text']=='10' and result['complete']
        other.post('/api/account/register',json={'name':'DiffOther','password':'test-only-password-678'})
        assert other.get(url,params=params).status_code==404
        assert guest.get(url,params={**params,'run_id':b['available_run']['id']}).status_code==400
