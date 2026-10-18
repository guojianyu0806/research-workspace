import hashlib

import pymupdf
import pytest
from fastapi.testclient import TestClient

from research_workspace.api import create_app
from research_workspace.dedup import DedupService, DedupConflict
from research_workspace.library import Library, LibraryError


BODY=' '.join(f'The research method measures condition {i} and reports independent results with documented evidence.' for i in range(35))


def add(lib, *, own='1706.03762v1', body=BODY, filename='paper.pdf', title='A controlled research method', extra=''):
    with pymupdf.open() as pdf:
        p=pdf.new_page()
        if own:p.insert_text((40,35),'arXiv: '+own)
        p.insert_text((40,65),title)
        for start in range(0,len(body),1800):
            p=pdf.new_page();p.insert_textbox((40,40,550,800),body[start:start+1800],fontsize=10)
        if extra:pdf.new_page().insert_text((40,100),extra)
        doc=lib.import_document(pdf.tobytes(),filename)['document']
    lib.process_next_job()
    return lib.get_document(doc['id'])


def encoder(texts,query):return [[1.,.1] for _ in texts]


def service(lib):return DedupService(lib,encoder=encoder)


def pair_for(report,a,b):
    return next(p for p in report['pairs'] if {p['left']['document_id'],p['right']['document_id']}=={a['id'],b['id']})


def test_same_identifier_and_body_candidate_requires_explicit_action(tmp_path):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1706.03762v7',filename='revision.pdf',extra='Additional measured result.')
    s=service(lib);r=s.scan();p=pair_for(r,a,b)
    assert r['semantic']['status']=='applied' and r['automatic_merges']==0 and r['model_calls']==0
    assert p['classification']=='same_work_candidate' and p['can_group']
    assert all(lib.get_document(x['id'])['version_family_id'] is None for x in [a,b])
    assert p['identities']['left']['evidence'] and p['source_samples']['left']


def test_high_semantic_similarity_cannot_override_different_own_ids(tmp_path):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1810.04805v2',filename='different.pdf')
    s=service(lib);r=s.scan();p=pair_for(r,a,b)
    assert p['semantic_similarity']>.99 and p['classification']=='identity_conflict' and not p['can_group']
    with pytest.raises(DedupConflict):s.decide(r['id'],p['pair_id'],'group',a['id'])
    assert lib.get_document(a['id'])['version_family_id'] is None
    with pytest.raises(LibraryError):lib.confirm_version_relation(a['id'],b['id'])


def test_topic_similar_works_without_identifiers_are_not_groupable(tmp_path):
    lib=Library(tmp_path);a=add(lib,own=None,title='A research method for image classification')
    b=add(lib,own=None,title='A different method for language retrieval',body=BODY.replace('condition','context'),filename='related.pdf')
    s=service(lib);r=s.scan();p=pair_for(r,a,b)
    assert p['classification']=='related_only' and not p['can_group']
    with pytest.raises(DedupConflict):s.decide(r['id'],p['pair_id'],'group',a['id'])


def test_exact_readable_copy_but_not_case_changed_math_is_groupable(tmp_path):
    lib=Library(tmp_path);a=add(lib,own=None,body=BODY+' A = b + C.')
    b=add(lib,own=None,body=BODY+' A = b + C.',filename='renamed.pdf')
    c=add(lib,own=None,body=BODY+' a = b + C.',filename='case-different.pdf')
    s=service(lib);r=s.scan()
    assert pair_for(r,a,b)['classification']=='text_copy_candidate'
    assert not pair_for(r,a,c)['can_group']


def test_filename_claim_and_body_label_conflict_blocks_even_identical_text(tmp_path):
    lib=Library(tmp_path);a=add(lib);b=add(lib,filename='1810.04805v2.pdf')
    p=pair_for(service(lib).scan(),a,b)
    assert p['classification']=='identity_conflict' and not p['can_group']


def test_references_do_not_supply_own_identity_or_duplicate_body(tmp_path):
    lib=Library(tmp_path);a=add(lib,own=None,body=BODY)
    b=add(lib,own=None,body=BODY.replace('method','technique'),extra='References\narXiv: 1706.03762v1',filename='other.pdf')
    p=pair_for(service(lib).scan(),a,b)
    assert not p['own_identifier_agrees'] and not p['can_group']


@pytest.mark.parametrize('partial',[True,False])
def test_partial_or_tiny_sources_cannot_confirm_identity(tmp_path,partial):
    lib=Library(tmp_path);a=add(lib,body=BODY if partial else 'Short fragment.')
    b=add(lib,body=BODY if partial else 'Another short fragment.',own='1706.03762v2',filename='other.pdf')
    if partial:
        with lib._connection() as db:db.execute("UPDATE parse_runs SET status='partial' WHERE id=?",(a['available_run']['id'],))
    report=service(lib).scan();p=pair_for(report,a,b)
    assert p['classification']=='insufficient_evidence' and not p['can_group']
    assert not report['complete']


def test_group_and_undo_preserve_originals_pages_history_and_audits(tmp_path):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1706.03762v2',filename='other.pdf')
    original={d['id']:lib._source_path(d['id']).read_bytes() for d in [a,b]}
    page=lib.get_page(a['id'],1,a['available_run']['id']);s=service(lib);r=s.scan();p=pair_for(r,a,b)
    action=s.decide(r['id'],p['pair_id'],'group',b['id'])
    assert not lib.get_document(a['id'])['is_current_version'] and lib.get_document(b['id'])['is_current_version']
    assert lib.search('research',document_id=a['id'],run_id=a['available_run']['id'])
    assert lib.get_page(a['id'],1,a['available_run']['id'])==page
    assert all(lib._source_path(d['id']).read_bytes()==original[d['id']] for d in [a,b])
    assert s.get(r['id'])['pairs']==r['pairs']
    s.undo(action['id'])
    assert all(lib.get_document(d['id'])['version_family_id'] is None for d in [a,b])
    assert s.get(r['id'])['actions'][0]['reverted_at']
    with pytest.raises(DedupConflict):s.undo(action['id'])


@pytest.mark.parametrize('change',['metadata','reparse','archive','group'])
def test_stale_scans_cannot_apply_after_source_or_group_change(tmp_path,change):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1706.03762v2',filename='other.pdf')
    s=service(lib);r=s.scan();p=pair_for(r,a,b)
    if change=='metadata':lib.update_metadata(a['id'],title='Changed title')
    elif change=='reparse':lib.create_run(a['id']);lib.process_next_job()
    elif change=='archive':lib.archive_document(a['id'])
    else:lib.confirm_version_relation(a['id'],b['id'])
    with pytest.raises(DedupConflict):s.decide(r['id'],p['pair_id'],'group',b['id'])


def test_undo_cannot_overwrite_subsequent_version_selection(tmp_path):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1706.03762v2',filename='other.pdf')
    s=service(lib);r=s.scan();p=pair_for(r,a,b);action=s.decide(r['id'],p['pair_id'],'group',b['id'])
    lib.confirm_version_relation(a['id'],b['id'],action='use_document')
    with pytest.raises(DedupConflict):s.undo(action['id'])
    assert lib.get_document(a['id'])['is_current_version']


def test_no_transitive_merge_and_separate_decision_is_reversible(tmp_path):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1706.03762v2',filename='other.pdf');c=add(lib,own='1706.03762v3',filename='third.pdf')
    s=service(lib);r=s.scan();p=pair_for(r,a,b);action=s.decide(r['id'],p['pair_id'],'keep_separate')
    reverse=s.scan(b['id']);rp=pair_for(reverse,a,b);assert rp['pair_id']==p['pair_id']
    with pytest.raises(DedupConflict):s.decide(reverse['id'],rp['pair_id'],'group',a['id'])
    s.undo(action['id']);s.decide(r['id'],p['pair_id'],'group',a['id'])
    newer=s.scan();assert not pair_for(newer,a,c)['can_group']


def test_limits_and_unavailable_embedding_are_explicit(tmp_path,monkeypatch):
    import research_workspace.dedup as module
    lib=Library(tmp_path);add(lib);add(lib,own='1706.03762v2',filename='other.pdf')
    with monkeypatch.context() as m:
        m.setattr(module,'MAX_DOCUMENT_CHARS',5)
        r=service(lib).scan();assert not r['complete'] and len(r['skipped_sources'])==2 and not r['pairs']
    monkeypatch.setattr(module.semantic,'model_status',lambda:{'status':'disabled'})
    r=DedupService(lib).scan();assert not r['complete'] and r['semantic']['status']=='disabled'
    assert r['pairs'][0]['can_group']  # Identifier/body evidence is explicit even without vectors.


def test_api_protects_workspace_and_blocked_confirm(tmp_path,monkeypatch):
    monkeypatch.setattr(DedupService,'_similarities',lambda self,items:({}, {'status':'disabled'}))
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as guest, TestClient(app,base_url='http://127.0.0.1:8765') as other:
        a=add(app.state.library);b=add(app.state.library,own='1810.04805v2',filename='other.pdf')
        r=guest.post('/api/dedup/scans',json={});assert r.status_code==201
        report=r.json();p=pair_for(report,a,b)
        assert guest.post(f"/api/dedup/scans/{report['id']}/decisions",json={'pair_id':p['pair_id'],'decision':'group','preferred_id':a['id']}).status_code==409
        other.post('/api/account/register',json={'name':'DedupOther','password':'test-only-password-789'})
        assert other.get(f"/api/dedup/scans/{report['id']}").status_code==404
        assert other.post('/api/dedup/scans',json={'document_id':a['id']}).status_code==404
        assert other.get('/api/dedup/scans').json()['scans']==[]


def test_legacy_transitive_family_cannot_bypass_verified_identity_conflict(tmp_path):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own=None,filename='unknown.pdf')
    c=add(lib,own='1810.04805v2',filename='different.pdf')
    lib.confirm_version_relation(a['id'],b['id'])
    with pytest.raises(LibraryError):lib.confirm_version_relation(b['id'],c['id'])
    assert lib.get_document(c['id'])['version_family_id'] is None


def test_scan_snapshot_is_consistent_with_concurrent_reparse(tmp_path,monkeypatch):
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1706.03762v2',filename='other.pdf')
    original=lib._document;started=False
    def observed(db,did):
        nonlocal started
        doc=original(db,did)
        if not started:
            started=True;writer=Library(tmp_path);writer.create_run(a['id']);writer.process_next_job()
        return doc
    monkeypatch.setattr(lib,'_document',observed)
    r=service(lib).scan()
    assert {s['run_id'] for s in r['sources']}=={a['available_run']['id'],b['available_run']['id']}
    p=pair_for(r,a,b)
    with pytest.raises(DedupConflict):service(lib).decide(r['id'],p['pair_id'],'group',a['id'])


def test_group_eligibility_is_rechecked_in_write_transaction(tmp_path):
    import json
    lib=Library(tmp_path);a=add(lib);b=add(lib,own='1810.04805v2',filename='different.pdf')
    s=service(lib);r=s.scan();p=pair_for(r,a,b)
    r['pairs'][0]['can_group']=True
    with lib._connection() as db:db.execute('UPDATE dedup_scans SET payload=? WHERE id=?',(json.dumps(r),r['id']))
    with pytest.raises(DedupConflict):s.decide(r['id'],p['pair_id'],'group',a['id'])
    assert lib.get_document(a['id'])['version_family_id'] is None


def test_semantic_samples_skip_license_prelude_and_remain_source_substrings(tmp_path):
    lib=Library(tmp_path)
    legal='Provided proper attribution is provided, permission to reproduce. '+'Legal attribution statement. '*20
    a=add(lib,body=legal+'\n'+BODY)
    s=service(lib)
    with lib._connection() as db:
        db.execute('BEGIN');items,_,_=s._snapshot(db,a['id'])
    samples=items[0]['samples']
    assert samples and all('Provided proper attribution' not in b['text'] for b in samples)
    originals={b['block_id']:b for b in lib._evidence_blocks(a['id'])}
    assert all(originals[b['block_id']]['text'].startswith(b['text']) for b in samples)
