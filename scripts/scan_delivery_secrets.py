"""Bounded local source/history secret screening; never print matching values."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'provider_token': rb'\bsk-[A-Za-z0-9_-]{20,}',
    'github_token': rb'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{24,}',
    'hf_token': rb'\bhf_[A-Za-z0-9]{24,}',
    'private_key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'literal_api_key': rb'(?i)(?:api_key|api[-_ ]?secret|access_token)\s*[:=]\s*[\x22\x27]([^\x22\x27\r\n]{12,})[\x22\x27]',
}
SAFE = ('fixture', 'not-a-real', 'not_real', 'placeholder', 'example', 'redacted', 'dummy', 'offline', 'test-only')


def scan():
    objects = subprocess.check_output(['git', 'rev-list', '--objects', 'HEAD'], cwd=ROOT).decode().splitlines()
    paths = {line.split(' ', 1)[0]: line.split(' ', 1)[1] if ' ' in line else '' for line in objects}
    process = subprocess.run(['git', 'cat-file', '--batch'], cwd=ROOT,
                             input=('\n'.join(paths)+'\n').encode(), stdout=subprocess.PIPE, check=True)
    data, offset, blobs = process.stdout, 0, 0
    findings, allowed = [], []
    for _ in paths:
        end = data.index(b'\n', offset)
        oid, kind, size = data[offset:end].decode().split()
        size = int(size); payload = data[end+1:end+1+size]; offset = end+2+size
        if kind != 'blob':
            continue
        blobs += 1
        for name, pattern in PATTERNS.items():
            for match in re.finditer(pattern, payload):
                value = match.group(1) if name == 'literal_api_key' else match.group()
                text = value.decode('utf-8', 'ignore').lower()
                record = {'blob': oid, 'path': paths[oid], 'kind': name,
                          'line': payload[:match.start()].count(b'\n')+1,
                          'match_sha256': hashlib.sha256(value).hexdigest()}
                (allowed if any(marker in text for marker in SAFE) else findings).append(record)
    output = ROOT/'artifacts/submission-preparation-20261027'/('source-secret-scan-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'scope': 'All HEAD-reachable history blobs; heuristic screening, not a guarantee of all possible secrets.',
              'blobs': blobs, 'unresolved': findings, 'explicit_fixture_matches': allowed, 'passed': not findings}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'blobs': blobs, 'unresolved_count': len(findings), 'fixture_count': len(allowed), 'passed': report['passed']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(scan())
