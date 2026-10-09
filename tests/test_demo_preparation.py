"""Offline checks for the importer; generated PDFs are test fixtures only."""
import hashlib
from pathlib import Path

import httpx
import pymupdf
import pytest

from scripts.prepare_demo import cached_pdf, csv_path, prepare, validate_base_url, validate_pdf_url


@pytest.fixture
def assets(tmp_path):
    directory = tmp_path / 'research_workspace/static/examples'
    directory.mkdir(parents=True)
    (directory / 'sample.csv').write_bytes(b'model,score\nexample,1\n')
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), 'Synthetic importer test fixture, not a research sample.')
        content = pdf.tobytes()
    paper = {'key': 'fixture', 'arxiv_id': '1706.03762v7', 'title': 'Official title fixture',
             'authors': ['Example Author'], 'filename': '1706.03762v7.pdf',
             'source_url': 'https://arxiv.org/abs/1706.03762v7',
             'pdf_url': 'https://arxiv.org/pdf/1706.03762v7', 'sha256': hashlib.sha256(content).hexdigest()}
    catalog = {'papers': [paper], 'dataset': {'filename': 'sample.csv', 'csv_path': '/static/examples/sample.csv'}}
    return tmp_path, content, catalog


def local_api(content, existing=None, *, status='completed'):
    state = {'documents': [existing] if existing else [], 'datasets': [], 'writes': []}

    def response(request):
        path = request.url.path
        assert request.url.host == '127.0.0.1'
        assert not request.headers.get('cookie') and not request.headers.get('authorization')
        if request.method != 'GET':
            state['writes'].append((request.method, path))
        if path == '/api/documents':
            if request.method == 'GET':
                assert request.url.params['include_archived'] == 'true'
                return httpx.Response(200, json={'documents': state['documents']})
            document = {'id': 'document1', 'title': '1706.03762v7', 'filename': '1706.03762v7.pdf',
                        'sha256': hashlib.sha256(content).hexdigest(), 'authors': [],
                        'current_run': {'id': 'run1', 'status': status}, 'archived_at': None}
            state['documents'].append(document)
            return httpx.Response(202, json={'document': document, 'duplicate': False})
        if path == '/api/documents/document1':
            if request.method == 'PATCH':
                import json
                state['documents'][0].update(json.loads(request.content))
            return httpx.Response(200, json={'document': state['documents'][0]})
        if path == '/api/datasets':
            if request.method == 'GET':
                return httpx.Response(200, json={'datasets': state['datasets']})
            dataset = {'id': 'dataset1', 'filename': 'sample.csv',
                       'sha256': hashlib.sha256(b'model,score\nexample,1\n').hexdigest()}
            state['datasets'].append(dataset)
            return httpx.Response(201, json={'dataset': dataset})
        pytest.fail(f'Unexpected request: {request.method} {path}')
    return httpx.Client(transport=httpx.MockTransport(response)), state


def test_repeated_import_preserves_assets_and_makes_no_second_write(assets):
    root, content, catalog = assets
    api, state = local_api(content)
    downloads = []
    def download(request):
        downloads.append(str(request.url))
        return httpx.Response(200, content=content)
    with api, httpx.Client(transport=httpx.MockTransport(download)) as source:
        first, code = prepare(root, catalog, api, source, wait_seconds=0)
        assert code == 0 and first['papers'][0]['action'] == 'created'
        assert state['documents'][0]['title'] == catalog['papers'][0]['title']
        writes = list(state['writes'])
        second, code = prepare(root, catalog, api, source, wait_seconds=0)
    assert code == 0 and second['papers'][0]['action'] == 'reused'
    assert second['dataset']['action'] == 'reused'
    assert state['writes'] == writes and len(downloads) == 1


@pytest.mark.parametrize('archived', [False, True])
def test_existing_user_metadata_and_archive_state_are_preserved(assets, archived):
    root, content, catalog = assets
    paper = catalog['papers'][0]
    existing = {'id': 'document1', 'title': 'My annotated paper title', 'authors': ['User supplied author'],
                'filename': paper['filename'], 'sha256': paper['sha256'],
                'current_run': {'id': 'run1', 'status': 'completed'}, 'archived_at': '2026-01-01' if archived else None}
    api, state = local_api(content, existing)
    with api, httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as source:
        report, code = prepare(root, catalog, api, source, wait_seconds=0)
    assert code == (2 if archived else 0)
    assert existing['title'] == 'My annotated paper title'
    assert existing['authors'] == ['User supplied author']
    assert state['writes'] == [('POST', '/api/datasets')]
    assert report['papers'][0]['archived'] == archived


def test_invalid_cache_and_failed_download_never_replace_originals(assets):
    root, content, catalog = assets
    paper = catalog['papers'][0]
    path = root / 'data/samples' / paper['filename']
    path.parent.mkdir(parents=True)
    original = b'%PDF-invalid retained file'
    path.write_bytes(original)
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail('Cache must not be redownloaded'))) as source:
        with pytest.raises(ValueError):
            cached_pdf(root, paper, source)
    assert path.read_bytes() == original
    other = dict(paper, filename='other.pdf')
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as source:
        with pytest.raises(httpx.HTTPStatusError):
            cached_pdf(root, other, source)
    assert not (path.parent / 'other.pdf').exists()
    assert path.read_bytes() == original


@pytest.mark.parametrize('body', [b'<html>not a PDF</html>', b'%PDF-1.7\ninvalid structure'])
def test_invalid_download_is_rejected_before_cache_creation(assets, body):
    root, _, catalog = assets
    paper = dict(catalog['papers'][0])
    paper.pop('sha256')
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as source:
        with pytest.raises(ValueError):
            cached_pdf(root, paper, source)
    assert not (root / 'data/samples' / paper['filename']).exists()


def test_url_and_path_boundaries_and_redirect_validation(assets):
    root, _, catalog = assets
    assert validate_base_url('http://127.0.0.1:8765/') == 'http://127.0.0.1:8765'
    for value in ['https://127.0.0.1', 'http://example.com', 'http://user@localhost',
                  'http://localhost/api', 'http://localhost?x=1', 'http://localhost#x']:
        with pytest.raises(ValueError):
            validate_base_url(value)
    for value in ['https://evil.test/pdf/1706.03762v7', 'https://arxiv.org/pdf/1706.03762v1',
                  'https://user@arxiv.org/pdf/1706.03762v7', 'http://arxiv.org/pdf/1706.03762v7']:
        with pytest.raises(ValueError):
            validate_pdf_url(value, '1706.03762v7')
    with pytest.raises(ValueError):
        csv_path(root, '/static/examples/../../../private.csv')
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={'location': 'https://evil.test/file.pdf'}))) as source:
        with pytest.raises(ValueError):
            cached_pdf(root, catalog['papers'][0], source)


def test_unfinished_parse_has_explicit_exit_without_reparse(assets):
    root, content, catalog = assets
    api, state = local_api(content, status='queued')
    with api, httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as source:
        report, code = prepare(root, catalog, api, source, wait_seconds=0)
    assert code == 2 and report['result'] == 'needs_attention'
    assert report['papers'][0]['status'] == 'queued'
    assert not any('parse-runs' in path or 'research' in path for _, path in state['writes'])
