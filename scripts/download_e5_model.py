"""Fetch only the five revision-pinned files explicitly authorized by the user.

No installed packages, credential discovery or implicit fallback downloads.
Existing English/MiniLM files and an existing E5 target are never overwritten.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT/'docs/development/145-e5-model-download-plan-20261020.json'
DESTINATION = ROOT/'data/models/multilingual-e5-small-onnx-int8-614241f'
REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
NAMES = {'config.json', 'special_tokens_map.json', 'tokenizer_config.json',
         'tokenizer.json', 'model_qint8_avx512_vnni.onnx'}


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download():
    plan = json.loads(PLAN.read_text(encoding='utf-8'))
    assert plan['model'] == plan['repository'] == 'intfloat/multilingual-e5-small'
    assert plan['revision'] == REVISION
    assert plan['destination'] == DESTINATION.relative_to(ROOT).as_posix()
    assert len(plan['files']) == 5 and {f['path'] for f in plan['files']} == NAMES
    assert sum(f['size'] for f in plan['files']) == plan['total_bytes'] == 135430817
    prefix = 'https://huggingface.co/intfloat/multilingual-e5-small/resolve/'+REVISION+'/onnx/'
    for item in plan['files']:
        assert item['source_path'] == 'onnx/'+item['path'] and item['url'] == prefix+item['path']
        assert len(item['sha256']) == 64 and int(item['sha256'], 16) >= 0
    assert DESTINATION.resolve() == DESTINATION.absolute()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    output = ROOT/'artifacts/e5-model-download'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    report = {'manifest_sha256': sha256(PLAN), 'driver_sha256': sha256(Path(__file__)),
              'revision': REVISION, 'destination': str(DESTINATION.relative_to(ROOT)),
              'authorization': 'Explicit human approval of proposal 145: five files, project-local download and acceptance only.',
              'files': [], 'errors': [], 'packages_installed': 0, 'pro_calls': 0,
              'started_at': datetime.now(timezone.utc).isoformat()}
    print(json.dumps({'output': str(output)}), flush=True)
    started = time.monotonic()
    try:
        def validate_request(request):
            if request.url.scheme != 'https' or request.url.username or request.url.password:
                raise ValueError('Only credential-free HTTPS downloads are allowed')
        with httpx.Client(timeout=httpx.Timeout(90, connect=30), follow_redirects=True,
                          event_hooks={'request': [validate_request]}) as client:
            for item in plan['files']:
                target = DESTINATION/item['path']
                assert target.resolve().parent == DESTINATION.resolve()
                if target.exists():
                    assert target.is_file() and target.stat().st_size == item['size']
                    assert sha256(target) == item['sha256'], 'Existing file differs; never overwrite'
                    report['files'].append({**item, 'action': 'existing_verified'})
                    continue
                temporary = output/(item['path']+'.part')
                digest = hashlib.sha256()
                count = 0
                with client.stream('GET', item['url']) as response:
                    response.raise_for_status()
                    with temporary.open('xb') as stream:
                        for chunk in response.iter_bytes(1024*1024):
                            count += len(chunk)
                            if count > item['size']:
                                raise ValueError('Response exceeds approved file size')
                            stream.write(chunk)
                            digest.update(chunk)
                assert count == item['size'] and digest.hexdigest() == item['sha256'], 'Size or hash mismatch'
                if os.name == 'nt':
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                report['files'].append({**item, 'action': 'downloaded_verified'})
                print(json.dumps({'file': item['path'], 'bytes': count, 'verified': True}), flush=True)
    except Exception as error:
        # Exception strings can contain signed public CDN URLs. Store the type
        # only; failed bytes remain isolated and never become runtime weights.
        report['errors'].append(type(error).__name__)
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    report['passed'] = not report['errors'] and len(report['files']) == 5
    with (output/'receipt.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'passed': report['passed'], 'verified_files': len(report['files']), 'errors': report['errors']}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(download())
