"""Synthetic local public-paper signals; no external service or real users."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from research_workspace.accounts import Accounts
from research_workspace.api import create_app
from research_workspace.collaboration import CollaborativeRecommendations, MAX_PROFILE, canonical
from research_workspace.discovery import DiscoveryService
from research_workspace.library import Library

A='arxiv:2401.10001v1';B='arxiv:2401.10002v1';C='arxiv:2401.10003v1';D='arxiv:2401.10004v1'


def feed(ids):
    return ('<feed xmlns="http://www.w3.org/2005/Atom">'+''.join(
        f'<entry><id>https://arxiv.org/abs/{key[6:]}</id><title>Public methods {index}</title><summary>Public fixture.</summary></entry>'
        for index,key in enumerate(ids))+'</feed>').encode()


def populate(library,ids=(A,B,C,D)):
    state={'ids':ids}
    discovery=DiscoveryService(library,httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=feed(state['ids'])))),request_interval=0)
    for offset in range(0,len(ids),50):
        state['ids']=ids[offset:offset+50]
        discovery.search('public fixture '+hashlib.sha256('|'.join(state['ids']).encode()).hexdigest()[:12],limit=50)
    return discovery


@pytest.fixture
def cohort(tmp_path):
    accounts=Accounts(tmp_path);users=[]
    for index in range(5):
        _,user=accounts.register('Fixture'+str(index),'local-fixture-password')
        library=Library(accounts.directory(user));discovery=populate(library)
        shared=CollaborativeRecommendations(accounts,user,library)
        users.append((user,discovery,shared))
    return accounts,users


def ready(cohort,neighbors=3,candidate_vote=1):
    accounts,users=cohort
    for index,(_,discovery,shared) in enumerate(users[:neighbors+1]):
        discovery.feedback(A,1);discovery.feedback(B,1)
        if index:discovery.feedback(C,candidate_vote)
        shared.set_enabled(True,discovery)
    return accounts,users


def recommend(user):
    _,discovery,shared=user
    return discovery.recommendations(rerank=shared.rerank)


def scores(result):return {item['id']:item['score'] for item in result['items']}


def test_default_optout_and_default_space_do_not_create_projection(cohort):
    accounts,users=cohort
    result=recommend(users[0])
    assert result['collaboration']['enabled'] is False
    assert result['collaboration']['status']=='disabled'
    assert not (accounts.root/'collaboration.sqlite3').exists()
    default=Library(accounts.root)
    guest=CollaborativeRecommendations(accounts,{'id':None},default)
    assert guest.settings()['can_join'] is False
    with pytest.raises(ValueError,match='独立账号'):guest.set_enabled(True,populate(default))


def test_three_related_participants_rerank_without_disclosing_identities(cohort):
    _,users=ready(cohort);target=users[0]
    baseline=target[1].recommendations();result=recommend(target)
    assert result['collaboration']['status']=='applied'
    assert '群体重排' in result['method']
    assert scores(result)[C]==scores(baseline)[C]+.75
    assert scores(result)[D]==scores(baseline)[D]
    serialized=json.dumps(result,ensure_ascii=False)
    for user,_,_ in users:
        assert user['id'] not in serialized and user['name'] not in serialized
    assert result['network_used'] is False and result['collaboration']['network_used'] is False


@pytest.mark.parametrize('neighbors',[0,1,2])
def test_small_cohort_does_not_change_baseline(cohort,neighbors):
    _,users=ready(cohort,neighbors=neighbors)
    result=recommend(users[0])
    assert scores(result)==scores(users[0][1].recommendations())
    assert result['collaboration']['status']=='insufficient_cohort'


def test_two_common_likes_required_not_merely_one_common_topic(cohort):
    _,users=ready(cohort)
    for _,discovery,shared in users[1:4]:discovery.feedback(B,0);shared.sync(discovery)
    assert recommend(users[0])['collaboration']['status']=='insufficient_cohort'
    users[0][1].feedback(B,0)
    assert recommend(users[0])['collaboration']['status']=='insufficient_profile'


def test_unjoined_private_feedback_never_counts_as_a_neighbor(cohort):
    accounts,users=ready(cohort,neighbors=2)
    user,discovery,_=users[3]
    for key in (A,B,C):discovery.feedback(key,1)
    result=recommend(users[0])
    assert result['collaboration']['status']=='insufficient_cohort'
    with sqlite3.connect(accounts.root/'collaboration.sqlite3') as db:
        assert not db.execute('SELECT 1 FROM signals WHERE user_id=?',(user['id'],)).fetchone()


@pytest.mark.parametrize('own_vote',[-1,1])
def test_own_explicit_feedback_cannot_be_overridden(cohort,own_vote):
    _,users=ready(cohort)
    users[0][1].feedback(C,own_vote)
    assert scores(recommend(users[0]))[C]==scores(users[0][1].recommendations())[C]


def test_three_negative_votes_demote_candidate_without_inventing_quality_claim(cohort):
    _,users=ready(cohort,candidate_vote=-1)
    baseline=users[0][1].recommendations();result=recommend(users[0])
    assert scores(result)[C]==scores(baseline)[C]-.75
    assert any('负向反馈' in reason for item in result['items'] if item['id']==C for reason in item['reasons'])


def test_one_or_two_candidate_votes_cannot_form_group_recommendation(cohort):
    _,users=ready(cohort)
    users[1][1].feedback(C,0);users[1][2].sync(users[1][1])
    assert scores(recommend(users[0]))[C]==scores(users[0][1].recommendations())[C]


def test_withdrawal_removes_only_own_projection_and_retains_private_votes(cohort):
    accounts,users=ready(cohort)
    own=users[1];own[2].set_enabled(False,own[1])
    with sqlite3.connect(accounts.root/'collaboration.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM signals WHERE user_id=?',(own[0]['id'],)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM participants WHERE user_id=?',(own[0]['id'],)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM participants').fetchone()[0]==3
    assert own[1].get_paper(C)['feedback']==1
    assert recommend(users[0])['collaboration']['status']=='insufficient_cohort'
    own[2].sync(own[1]);assert not own[2].settings()['enabled']


def test_parallel_feedback_sync_cannot_repopulate_withdrawn_projection(cohort):
    accounts,users=ready(cohort);user,discovery,shared=users[1]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(shared.sync,discovery) for _ in range(8)]
        shared.set_enabled(False,discovery)
        for future in futures:future.result()
    shared.sync(discovery)
    with sqlite3.connect(accounts.root/'collaboration.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM signals WHERE user_id=?',(user['id'],)).fetchone()[0]==0


def test_versions_count_as_one_interest_and_latest_zero_clears_signal(cohort):
    accounts,users=ready(cohort);user,discovery,shared=users[0]
    new=A.replace('v1','v7');other=populate(discovery.library,ids=[new])
    other.feedback(new,0);shared.sync(other)
    with sqlite3.connect(accounts.root/'collaboration.sqlite3') as db:
        assert not db.execute('SELECT 1 FROM signals WHERE user_id=? AND paper_id=?',(user['id'],canonical(A))).fetchone()
    assert recommend(users[0])['collaboration']['status']=='insufficient_profile'


def test_shared_store_contains_no_titles_documents_searches_or_tasks(cohort):
    accounts,users=ready(cohort)
    with sqlite3.connect(accounts.root/'collaboration.sqlite3') as db:
        assert {row[1] for row in db.execute('PRAGMA table_info(signals)')}=={'user_id','paper_id','value'}
        serialized='\n'.join(db.iterdump())
    assert all(word not in serialized for word in ['Public methods','Public fixture','local-fixture-password','workspace','document_id','summary'])
    assert 'arxiv:' not in serialized  # Only canonical public IDs.


def test_untrusted_metadata_or_non_arxiv_identifier_not_published(cohort):
    accounts,users=ready(cohort);user,discovery,shared=users[0]
    discovery.feedback(C,1)
    with discovery._db() as db:
        row=json.loads(db.execute('SELECT record FROM papers WHERE id=?',(C,)).fetchone()[0])
        row['metadata_verified']=False
        db.execute('UPDATE papers SET record=? WHERE id=?',(json.dumps(row),C))
    shared.sync(discovery)
    with sqlite3.connect(accounts.root/'collaboration.sqlite3') as db:
        assert not db.execute('SELECT 1 FROM signals WHERE user_id=? AND paper_id=?',(user['id'],canonical(C))).fetchone()
    assert canonical('private-document-123') is None


def test_other_workspace_metadata_and_import_state_are_not_candidates(cohort):
    _,users=ready(cohort)
    foreign='arxiv:2401.99999v1'
    for _,discovery,shared in users[1:4]:
        source=populate(discovery.library,ids=[foreign]);source.feedback(foreign,1);shared.sync(source)
    result=recommend(users[0])
    assert foreign not in {paper['id'] for paper in result['items']}
    assert all(p['document_id'] is None for p in result['items'])


def test_neighbor_titles_and_summaries_never_replace_own_metadata(cohort):
    _,users=ready(cohort)
    for _,discovery,_ in users[1:4]:
        with discovery._db() as db:
            record=json.loads(db.execute('SELECT record FROM papers WHERE id=?',(C,)).fetchone()[0])
            record.update(title='FOREIGN_PRIVATE_TITLE',summary='FOREIGN_PRIVATE_SUMMARY',document_id='FOREIGN_PRIVATE_DOCUMENT')
            db.execute('UPDATE papers SET record=? WHERE id=?',(json.dumps(record),C))
    result=recommend(users[0])
    assert 'FOREIGN_PRIVATE' not in json.dumps(result)
    assert scores(result)[C]==scores(users[0][1].recommendations())[C]+.75


def test_capacity_refusal_does_not_join_or_publish(cohort,monkeypatch):
    from research_workspace import collaboration
    monkeypatch.setattr(collaboration,'MAX_PARTICIPANTS',2)
    _,users=cohort
    for _,discovery,shared in users[:2]:shared.set_enabled(True,discovery)
    with pytest.raises(ValueError,match='容量'):users[2][2].set_enabled(True,users[2][1])
    assert users[2][2].settings()['enabled'] is False


def test_collaboration_applies_before_top_twenty_or_top_hundred_cutoff(cohort):
    _,users=ready(cohort)
    many=['arxiv:2401.'+str(20000+i)+'v1' for i in range(130)]
    for index,(_,discovery,shared) in enumerate(users[:4]):
        source=populate(discovery.library,ids=many)
        if index:source.feedback(many[-1],1)
        shared.sync(source)
    result=recommend(users[0])
    assert many[-1] in {item['id'] for item in result['items']}


def test_wrong_account_library_binding_and_fake_user_rejected(cohort):
    accounts,users=cohort
    with pytest.raises(ValueError,match='不一致'):CollaborativeRecommendations(accounts,users[0][0],users[1][1].library)
    with pytest.raises(ValueError,match='不存在'):CollaborativeRecommendations(accounts,{'id':'missing'},Library(accounts.root/'spaces'/'missing'))


def test_profile_limits_are_explicit_and_bounded(cohort):
    _,users=cohort;user,discovery,shared=users[0]
    ids=['arxiv:2401.'+str(30000+i)+'v1' for i in range(MAX_PROFILE+10)]
    discovery=populate(discovery.library,ids=ids)
    for identifier in ids:discovery.feedback(identifier,1)
    settings=shared.set_enabled(True,discovery)
    assert settings['shared_feedback_count']==MAX_PROFILE and settings['profile_limit']==MAX_PROFILE


@pytest.fixture
def application(tmp_path,monkeypatch):
    for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):monkeypatch.delenv(key,raising=False)
    return create_app(tmp_path,start_worker=False)


def test_api_consent_identity_spoofing_guest_and_account_isolation(application):
    with TestClient(application,base_url='http://127.0.0.1:8765') as alice, TestClient(application,base_url='http://127.0.0.1:8765') as bob, TestClient(application,base_url='http://127.0.0.1:8765') as guest:
        a=alice.post('/api/account/register',json={'name':'Alice','password':'fixture-local-password'}).json()['user']
        b=bob.post('/api/account/register',json={'name':'Bob','password':'fixture-local-password'}).json()['user']
        alib=Library(application.state.accounts.directory(a));populate(alib)
        assert guest.put('/api/recommendations/collaboration',json={'enabled':True}).status_code==403
        for payload in [{'enabled':True,'user_id':b['id']},{'enabled':'true'},{'enabled':1}]:
            assert alice.put('/api/recommendations/collaboration',json=payload).status_code==422
        assert alice.put('/api/recommendations/collaboration',json={'enabled':True}).json()['collaboration']['enabled']
        assert not bob.get('/api/recommendations').json()['collaboration']['enabled']
        assert bob.post('/api/discover/feedback/'+A,json={'value':1}).status_code==404
        assert alice.post('/api/discover/feedback/'+A,json={'value':True}).status_code==422
        assert alice.post('/api/discover/feedback/'+A,json={'value':1}).json()['collaboration_sync']=='completed'
        body=bob.get('/api/recommendations').json()
        assert body['items']==[] and a['id'] not in json.dumps(body)
        assert alice.put('/api/recommendations/collaboration',json={'enabled':False}).json()['collaboration']['shared_feedback_count']==0
        assert DiscoveryService(alib).get_paper(A)['feedback']==1


def test_api_optional_shared_failure_preserves_private_feedback(application,monkeypatch):
    with TestClient(application,base_url='http://127.0.0.1:8765') as browser:
        user=browser.post('/api/account/register',json={'name':'Fixture','password':'fixture-local-password'}).json()['user']
        library=Library(application.state.accounts.directory(user));populate(library)
        def fail(*_):raise sqlite3.OperationalError('fixture busy')
        monkeypatch.setattr(CollaborativeRecommendations,'sync',fail)
        result=browser.post('/api/discover/feedback/'+A,json={'value':1})
        assert result.status_code==200 and result.json()['collaboration_sync']=='pending'
        assert DiscoveryService(library).get_paper(A)['feedback']==1


def test_optional_group_failure_keeps_local_recommendations(application,monkeypatch):
    with TestClient(application,base_url='http://127.0.0.1:8765') as browser:
        populate(application.state.library)
        def fail(*_):raise sqlite3.OperationalError('fixture unavailable')
        monkeypatch.setattr(CollaborativeRecommendations,'rerank',fail)
        result=browser.get('/api/recommendations')
        assert result.status_code==200 and len(result.json()['items'])==4
        assert result.json()['collaboration']['status']=='unavailable'


def test_expired_session_never_falls_back_to_default_or_cohort(application):
    with TestClient(application,base_url='http://127.0.0.1:8765') as browser:
        browser.cookies.set('research_session','synthetic-invalid-session')
        assert browser.get('/api/recommendations').status_code==401
        assert browser.put('/api/recommendations/collaboration',json={'enabled':True}).status_code==401
        assert browser.post('/api/discover/feedback/'+A,json={'value':1}).status_code==401
