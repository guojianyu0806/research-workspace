"""Import source-backed demonstration assets through the running local API.

This command never configures a model, creates research tasks, or changes accounts.
Cached originals and earlier reports are retained; failed downloads are retryable.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
MAX_PDF_BYTES = 50 * 1024 * 1024


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        local = False
    try:
        parsed.port
    except ValueError:
        local = False
    if (parsed.scheme != 'http' or not local or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Base URL 必须是不含凭据、查询或子路径的本机 HTTP 地址。')
    return value.rstrip('/')


def validate_pdf_url(value: str, arxiv_id: str) -> str:
    if not re.fullmatch(r'\d{4}\.\d{4,5}v[1-9]\d*', arxiv_id):
        raise ValueError('示例论文必须指定完整 arXiv 版本号。')
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (parsed.scheme != 'https' or parsed.hostname not in {'arxiv.org', 'export.arxiv.org'}
            or port not in (None, 443) or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {f'/pdf/{arxiv_id}', f'/pdf/{arxiv_id}.pdf'}):
        raise ValueError('PDF 来源必须是与指定版本匹配的 arXiv 官方 HTTPS 地址。')
    return value


def csv_path(root: Path, value: str) -> Path:
    prefix = '/static/examples/'
    if not isinstance(value, str) or not value.startswith(prefix) or '?' in value or '#' in value or '\\' in value:
        raise ValueError('CSV 必须位于 static/examples 目录。')
    directory = (root / 'research_workspace' / 'static' / 'examples').resolve()
    candidate = (directory / value[len(prefix):]).resolve()
    if not candidate.is_relative_to(directory) or candidate.suffix.lower() != '.csv':
        raise ValueError('CSV 路径不得越出示例目录。')
    return candidate


def inspect_pdf(content: bytes, expected_sha: str | None = None) -> dict:
    if not content or len(content) > MAX_PDF_BYTES or not content.lstrip().startswith(b'%PDF-'):
        raise ValueError('示例文件必须是非空 PDF，且不超过 50 MiB。')
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha and digest != expected_sha.lower():
        raise ValueError('PDF 的 SHA256 与来源清单不符；原文件保持不变。')
    try:
        with pymupdf.open(stream=content, filetype='pdf') as document:
            if not document.is_pdf or document.needs_pass or document.is_encrypted or document.page_count < 1:
                raise ValueError('PDF 没有可直接读取的页面。')
            for page in document:
                page.rect
            return {'sha256': digest, 'page_count': document.page_count}
    except Exception as exc:
        raise ValueError('PDF 无法打开或含有不可读取的页面；原文件保持不变。') from exc


def cached_pdf(root: Path, paper: dict, client: httpx.Client, *, no_download: bool = False) -> tuple[bytes, dict]:
    filename = paper['filename']
    if (not isinstance(filename, str) or Path(filename).name != filename or filename in ('', '.', '..')
            or any(character in filename for character in '/\\:') or not filename.lower().endswith('.pdf')):
        raise ValueError('PDF 缓存文件名无效。')
    directory = (root / 'data' / 'samples').resolve()
    path = directory / filename
    if not path.resolve().is_relative_to(directory):
        raise ValueError('PDF 缓存路径不得越出项目目录。')
    url = validate_pdf_url(paper['pdf_url'], paper['arxiv_id'])
    expected = paper.get('sha256')
    if expected and not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
        raise ValueError('来源清单的 SHA256 格式无效。')
    if path.exists():
        if path.stat().st_size > MAX_PDF_BYTES:
            raise ValueError('已有 PDF 超过 50 MiB；原文件保持不变。')
        content = path.read_bytes()
        return content, {**inspect_pdf(content, expected), 'cache': 'reused', 'cache_path': str(path)}
    if no_download:
        raise ValueError('本地尚无此 PDF；移除 --no-download 后可重试下载。')
    for redirect in range(4):
        with client.stream('GET', url, timeout=60, follow_redirects=False) as response:
            if response.is_redirect:
                if redirect == 3 or not response.headers.get('location'):
                    raise ValueError('PDF 下载重定向过多，可稍后重试。')
                url = validate_pdf_url(urljoin(url, response.headers['location']), paper['arxiv_id'])
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > MAX_PDF_BYTES:
                    raise ValueError('下载超过 50 MiB，未创建或覆盖缓存文件。')
                content.extend(chunk)
            content = bytes(content)
            inspection = inspect_pdf(content, expected)
            directory.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('xb') as stream:
                    stream.write(content)
            except FileExistsError:
                content = path.read_bytes()
                return content, {**inspect_pdf(content, expected), 'cache': 'reused', 'cache_path': str(path)}
            return content, {**inspection, 'cache': 'created', 'cache_path': str(path), 'download_url': url}
    raise ValueError('PDF 下载未完成，可稍后重试。')


def _document_record(document: dict) -> dict:
    run = document.get('current_run') or {}
    return {'document_id': document['id'], 'run_id': run.get('id'), 'status': run.get('status', 'not_started'),
            'archived': bool(document.get('archived_at')), 'available_run_id': (document.get('available_run') or {}).get('id')}


def prepare(root: Path, catalog: dict, api: httpx.Client, download: httpx.Client, *,
            base_url: str = 'http://127.0.0.1:8765', no_download: bool = False,
            wait_seconds: float = 60, sleep=time.sleep, monotonic=time.monotonic) -> tuple[dict, int]:
    base = validate_base_url(base_url)
    if not 0 <= wait_seconds <= 60:
        raise ValueError('解析等待时间必须在 0 至 60 秒之间。')
    dataset_config = catalog['dataset']
    source_csv = csv_path(root, dataset_config['csv_path'])
    dataset_bytes = source_csv.read_bytes()
    if not dataset_bytes or len(dataset_bytes) > 5 * 1024 * 1024:
        raise ValueError('示例 CSV 必须非空且不超过 5 MiB。')
    report = {'prepared_at': datetime.now(timezone.utc).isoformat(), 'base_url': base,
              'catalog_version': catalog.get('version'),
              'workspace': '本机默认空间', 'papers': [], 'dataset': None, 'errors': [],
              'notice': '只导入真实资料；未运行模型任务，未修改账户或重启服务。'}

    def request(method, path, *, timeout=60, **kwargs):
        response = api.request(method, base + path, timeout=timeout, follow_redirects=False, **kwargs)
        response.raise_for_status()
        return response.json()

    documents = request('GET', '/api/documents?include_archived=true')['documents']
    by_sha = {document['sha256']: document for document in documents}
    for paper in catalog['papers']:
        item = {key: paper[key] for key in ('key', 'arxiv_id', 'title', 'source_url', 'pdf_url', 'filename')}
        report['papers'].append(item)
        try:
            content, inspection = cached_pdf(root, paper, download, no_download=no_download)
            item.update(inspection)
            document = by_sha.get(inspection['sha256'])
            created = False
            if document is None:
                result = request('POST', '/api/documents', files={'file': (paper['filename'], content, 'application/pdf')})
                document = result['document']
                created = not result['duplicate']
                by_sha[inspection['sha256']] = document
            item['action'] = 'created' if created else 'reused'
            item.update(_document_record(document))
            if document.get('archived_at'):
                item['notice'] = '匹配的原文已归档；未自动恢复或覆盖。'
                continue
            placeholder = document['title'] == Path(document['filename']).stem and not document.get('authors')
            desired = {'title': paper['title'], 'authors': paper['authors']}
            changed = any(document.get(key) != value for key, value in desired.items())
            if changed and (created or placeholder):
                document = request('PATCH', '/api/documents/' + document['id'], json=desired)['document']
                by_sha[inspection['sha256']] = document
                item['metadata'] = 'official_metadata_applied'
            else:
                item['metadata'] = 'preserved'
            item.update(_document_record(document))
        except (ValueError, OSError, KeyError, httpx.HTTPError) as exc:
            item['status'] = 'error'
            item['error'] = str(exc)
            report['errors'].append({'asset': paper.get('key', 'paper'), 'error': str(exc), 'retryable': True})

    digest = hashlib.sha256(dataset_bytes).hexdigest()
    try:
        datasets = request('GET', '/api/datasets')['datasets']
        dataset = next((item for item in datasets if item['sha256'] == digest), None)
        created = dataset is None
        if created:
            dataset = request('POST', '/api/datasets', files={'file': (dataset_config['filename'], dataset_bytes, 'text/csv')})['dataset']
        report['dataset'] = {'dataset_id': dataset['id'], 'filename': dataset['filename'], 'sha256': digest,
                             'action': 'created' if created else 'reused', 'csv_path': dataset_config['csv_path'],
                             'source_url': dataset_config.get('source_url'), 'source_label': dataset_config.get('source_label')}
    except (ValueError, KeyError, httpx.HTTPError) as exc:
        report['errors'].append({'asset': 'dataset', 'error': str(exc), 'retryable': True})

    deadline = monotonic() + wait_seconds
    pending = [item for item in report['papers'] if item.get('status') in {'queued', 'running'} and not item.get('archived')]
    while pending and monotonic() < deadline:
        sleep(min(2, max(0, deadline - monotonic())))
        for item in pending[:]:
            if monotonic() >= deadline:
                break
            try:
                document = request('GET', '/api/documents/' + item['document_id'],
                                   timeout=min(5, max(.1, deadline - monotonic())))['document']
                item.update(_document_record(document))
                if item['status'] not in {'queued', 'running'}:
                    pending.remove(item)
            except (ValueError, KeyError, httpx.HTTPError) as exc:
                report['errors'].append({'asset': item['key'], 'error': str(exc), 'retryable': True})
                pending.remove(item)
    incomplete = any(item.get('status') != 'completed' or item.get('archived') for item in report['papers'])
    code = 1 if report['errors'] else 2 if incomplete else 0
    report['result'] = 'error' if code == 1 else 'needs_attention' if code == 2 else 'completed'
    report['exit_code'] = code
    return report, code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--no-download', action='store_true', help='仅复用已经验证的项目内 PDF 缓存。')
    parser.add_argument('--wait-seconds', type=float, default=60)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex[:8]
    destination = ROOT / 'artifacts' / 'demo' / stamp / 'report.json'
    try:
        catalog_path = ROOT / 'research_workspace' / 'static' / 'examples' / 'catalog.json'
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        with httpx.Client(trust_env=False) as api, httpx.Client(trust_env=False) as download:
            report, code = prepare(ROOT, catalog, api, download, base_url=args.base_url,
                                   no_download=args.no_download, wait_seconds=args.wait_seconds)
    except (ValueError, OSError, KeyError, httpx.HTTPError) as exc:
        report, code = {'result': 'error', 'exit_code': 1, 'error': str(exc), 'retryable': True}, 1
    destination.parent.mkdir(parents=True, exist_ok=False)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'result': report['result'], 'exit_code': code, 'report': str(destination)}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
