"""Resolve continuation from saved research snapshots in the current workspace."""
from __future__ import annotations

from .intelligence import MAX_DOCUMENTS
from .library import LibraryError


def validate_pinned_documents(library, runs):
    """Return display metadata for an exact set of readable, retained runs."""
    documents = []
    for document_id, run_id in runs.items():
        try:
            document = library.get_document(document_id)
            run = library.get_run(run_id)
        except KeyError:
            raise LibraryError('来源文献或当时的解析版本已不可用。') from None
        if (document.get('archived_at') or run['document_id'] != document_id
                or run['status'] not in {'completed', 'partial'} or not run['text_pages']):
            raise LibraryError('来源文献或当时的解析版本已不可用。')
        documents.append({'document_id': document_id, 'title': document['title'][:300], 'run_id': run_id})
    return documents


def _agent_runs(store, source):
    scope = source['payload'].get('agent_scope') or {}
    documents = scope.get('documents') or []
    runs = {item['document_id']: item['run_id'] for item in documents}
    if len(runs) != len(documents) or set(runs) != set(scope.get('document_ids') or []):
        return None
    if scope.get('import_limit'):
        state = store.load_agent_state(source['id'])
        if not state or not isinstance(state.get('run_ids'), dict):
            return None
        # An import enters the answer scope only after a specific run is ready.
        for item in state.get('imported', {}).values():
            document_id = item['document_id']
            run_id = state['run_ids'].get(document_id)
            if document_id in runs and runs[document_id] != run_id:
                return None
            if run_id:
                runs[document_id] = run_id
    return scope.get('mode'), runs


def _evidence_runs(result):
    observed = {}
    for field in ('sources', 'evidence'):
        items = result.get(field) or []
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict):
                return None
            document_id, run_id = item.get('document_id'), item.get('run_id')
            if not isinstance(document_id, str) or not isinstance(run_id, str) or not document_id or not run_id:
                return None
            if observed.setdefault(document_id, run_id) != run_id:
                return None
    return observed


def _saved_runs(payload, result, observed):
    document_ids = list(dict.fromkeys(payload.get('document_ids') or []))
    retrieval = result.get('retrieval') or {}
    # Older results can prove the scope if every selected paper has one
    # unambiguous run in the saved citations or retrieved evidence.
    runs = retrieval.get('selected_runs', observed)
    if not isinstance(runs, dict) or set(runs) != set(document_ids):
        return None
    return {document_id: runs[document_id] for document_id in document_ids}


def follow_up_preview(store, source):
    """Return a read-only description if the complete source scope is provable."""
    result = source.get('result')
    if source['status'] != 'completed' or not isinstance(result, dict):
        return None
    kind = source['kind']
    if kind not in {'agent_research', 'discover_qa', 'qa', 'compare', 'review', 'draft'}:
        return None
    observed = _evidence_runs(result)
    if observed is None:
        return None
    mode = 'qa'
    if kind == 'agent_research':
        snapshot = _agent_runs(store, source)
        if snapshot is None:
            return None
        mode, runs = snapshot
    elif kind == 'discover_qa':
        parsed = (result.get('workflow') or {}).get('parse') or {}
        runs = {parsed.get('document_id'): parsed.get('run_id')}
    else:
        mode = 'compare' if kind == 'compare' else 'qa'
        runs = _saved_runs(source['payload'], result, observed)
    if (mode not in {'qa', 'compare'} or not runs or len(runs) > MAX_DOCUMENTS
            or (mode == 'compare' and len(runs) < 2)
            or any(runs.get(doc) != run for doc, run in observed.items())
            or any(not isinstance(doc, str) or not doc or not isinstance(run, str) or not run
                   for doc, run in runs.items())):
        return None
    try:
        documents = validate_pinned_documents(store.library, runs)
    except LibraryError:
        return None
    return {'mode': mode, 'documents': documents}
