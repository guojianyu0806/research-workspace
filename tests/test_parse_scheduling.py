"""Real PDF fixtures for bounded parsing and account-worker scheduling."""

import pymupdf
import pytest

from research_workspace.library import Library, LibraryError


def import_pages(library,label,count=3):
    with pymupdf.open() as pdf:
        for page in range(1,count+1):
            pdf.new_page().insert_text((40,80),f'{label} page {page}')
        return library.import_document(pdf.tobytes(),label+'.pdf')['document']


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setenv('RESEARCH_OCR_ENABLED','0')
    return Library(tmp_path)


def test_page_budget_rotates_between_documents_and_keeps_evidence(library):
    first = import_pages(library,'first')
    second = import_pages(library,'second',2)
    first_id,second_id = first['current_run']['id'],second['current_run']['id']
    assert library.process_next_job(page_budget=1)
    saved = library.get_page(first['id'],1,first_id)
    started = library.get_run(first_id)['started_at']
    assert library.get_run(first_id)['status'] == 'queued'
    assert library.get_run(first_id)['pages_done'] == 1
    assert library.get_run(first_id)['completed_at'] is None
    assert library.process_next_job(page_budget=1)
    assert library.get_run(second_id)['pages_done'] == 1
    assert library.get_run(first_id)['pages_done'] == 1
    assert library.process_next_job(page_budget=1)
    assert library.get_run(first_id)['pages_done'] == 2
    assert library.process_next_job(page_budget=1)
    assert library.get_run(second_id)['status'] == 'completed'
    assert library.process_next_job(page_budget=1)
    assert library.get_run(first_id)['status'] == 'completed'
    assert library.get_run(first_id)['started_at'] == started
    assert library.get_page(first['id'],1,first_id) == saved
    assert not library.process_next_job(page_budget=1)


def test_failed_page_consumes_budget_without_repeating_forever(library,monkeypatch):
    document = import_pages(library,'partial')
    run_id = document['current_run']['id']
    original = library._page_blocks
    calls = []

    def extract(page):
        calls.append(page.number+1)
        if page.number == 0:
            raise ValueError('offline extraction failure')
        return original(page)

    monkeypatch.setattr(library,'_page_blocks',extract)
    for _ in range(3):
        assert library.process_next_job(page_budget=1)
    assert calls == [1,2,3]
    run = library.get_run(run_id)
    assert (run['status'],run['pages_done'],run['failed_pages']) == ('partial',3,1)
    assert not library.process_next_job(page_budget=1)


@pytest.mark.parametrize('cancel_during_page',[False,True])
def test_budgeted_cancel_keeps_saved_page_and_does_not_requeue(library,monkeypatch,cancel_during_page):
    document = import_pages(library,'cancel')
    run_id = document['current_run']['id']
    original = library._save_page

    if cancel_during_page:
        def save(*args,**kwargs):
            original(*args,**kwargs)
            library.cancel_run(run_id)
        monkeypatch.setattr(library,'_save_page',save)
    library.process_next_job(page_budget=1)
    if not cancel_during_page:
        library.cancel_run(run_id)
    saved = library.get_page(document['id'],1,run_id)
    assert library.get_run(run_id)['status'] == 'cancelled'
    assert library.get_run(run_id)['pages_done'] == 1
    assert not library.process_next_job(page_budget=1)
    assert library.get_page(document['id'],1,run_id) == saved
    assert library.recover_interrupted_runs() == 0


def test_crash_recovery_retries_failed_page_and_preserves_successful_ids(library,monkeypatch):
    document = import_pages(library,'recover')
    run_id = document['current_run']['id']
    extract,save = library._page_blocks,library._save_page

    def fail_first(page):
        if page.number == 0:
            raise ValueError('offline first-page failure')
        return extract(page)

    monkeypatch.setattr(library,'_page_blocks',fail_first)
    library.process_next_job(page_budget=1)

    def interrupted_save(*args,**kwargs):
        save(*args,**kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(library,'_save_page',interrupted_save)
    with pytest.raises(KeyboardInterrupt):
        library.process_next_job(page_budget=1)
    second = library.get_page(document['id'],2,run_id)
    assert library.get_run(run_id)['status'] == 'running'
    reopened = Library(library.data_dir)
    assert reopened.recover_interrupted_runs() == 1
    assert reopened.process_next_job(page_budget=1)
    assert reopened.get_page(document['id'],1,run_id)['status'] == 'parsed'
    assert reopened.get_run(run_id)['pages_done'] == 2
    assert reopened.process_next_job(page_budget=1)
    assert reopened.get_run(run_id)['status'] == 'completed'
    assert reopened.get_page(document['id'],2,run_id) == second


def test_single_page_retry_with_budget_does_not_reextract_copied_pages(library,monkeypatch):
    document = import_pages(library,'retry')
    library.process_next_job()
    source_id = document['current_run']['id']
    before = [library.get_page(document['id'],p,source_id) for p in (1,2,3)]
    retry = library.retry_page(document['id'],2,source_id)
    original = library._page_blocks
    calls = []

    def extract(page):
        calls.append(page.number+1)
        return original(page)

    monkeypatch.setattr(library,'_page_blocks',extract)
    assert library.process_next_job(page_budget=1)
    assert calls == [2]
    assert library.get_run(retry['id'])['status'] == 'completed'
    assert [library.get_page(document['id'],p,source_id) for p in (1,2,3)] == before
    assert library.get_page(document['id'],1,retry['id'])['blocks'][0]['id'] != before[0]['blocks'][0]['id']


def test_budgeted_reparse_keeps_prior_available_run_until_finished(library):
    document = import_pages(library,'stable')
    library.process_next_job()
    source_id = document['current_run']['id']
    saved = library.get_page(document['id'],1,source_id)
    replacement = library.create_run(document['id'])
    library.process_next_job(page_budget=1)
    assert library.get_document(document['id'])['available_run']['id'] == source_id
    assert library.get_page(document['id'],1) == saved
    # The default call retains its existing whole-document behavior after a step.
    library.process_next_job()
    assert library.get_document(document['id'])['available_run']['id'] == replacement['id']
    assert library.get_page(document['id'],1,source_id) == saved


def test_account_tick_runs_tasks_and_subscriptions_after_only_one_pdf_page(tmp_path,monkeypatch):
    from research_workspace import activity_worker

    monkeypatch.setenv('RESEARCH_OCR_ENABLED','0')
    events = []
    libraries = [Library(tmp_path/name) for name in ('alice','bob')]

    class Jobs:
        def __init__(self,library):
            self.name = library.data_dir.name

        async def process_next(self):
            events.append((self.name,'research'))
            return False

    class Discovery:
        def __init__(self,library):
            self.name = library.data_dir.name

        def due_checks(self,*,limit):
            events.append((self.name,'subscription'))
            return []

    monkeypatch.setattr(activity_worker,'JobStore',Jobs)
    monkeypatch.setattr(activity_worker,'DiscoveryService',Discovery)
    for library in libraries:
        document = import_pages(library,library.data_dir.name,20)
        assert activity_worker.workspace_tick(library,parse_documents=True)
        run = library.get_run(document['current_run']['id'])
        assert run['pages_done'] == 1 and run['status'] == 'queued'
    assert events == [('alice','research'),('alice','subscription'),
                      ('bob','research'),('bob','subscription')]


@pytest.mark.parametrize('budget',[0,-1,True,1.5])
def test_invalid_budget_does_not_claim_a_run(library,budget):
    document = import_pages(library,'untouched')
    with pytest.raises(LibraryError):
        library.process_next_job(page_budget=budget)
    assert library.get_run(document['current_run']['id'])['status'] == 'queued'
