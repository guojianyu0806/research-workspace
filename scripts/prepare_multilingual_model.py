"""Verify approved local files and create a separate runtime model manifest."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.multilingual_model import CHUNK_POLICY, DIRECTORY, MANIFEST, fingerprint


def prepare():
    proposal = ROOT / 'docs/development/142-multilingual-model-download-plan-20261020.json'
    data = json.loads(proposal.read_text(encoding='utf-8'))
    assert DIRECTORY.resolve() == DIRECTORY.absolute()
    for item in data['files']:
        path = DIRECTORY / item['path']
        assert path.resolve().parent == DIRECTORY.resolve() and path.stat().st_size == item['size']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
    manifest = {key: data[key] for key in ('model', 'repository', 'revision', 'dimensions', 'max_input_tokens', 'license')}
    manifest.update(model_directory=DIRECTORY.relative_to(ROOT).as_posix(), chunk_policy=CHUNK_POLICY,
                    files=[{key: item[key] for key in ('path', 'size', 'sha256')} for item in data['files']],
                    source_manifest_sha256=hashlib.sha256(proposal.read_bytes()).hexdigest())
    manifest['fingerprint'] = fingerprint(manifest)
    if MANIFEST.exists():
        assert json.loads(MANIFEST.read_text(encoding='utf-8')) == manifest, 'Preserve the existing runtime manifest'
    else:
        with MANIFEST.open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'manifest': str(MANIFEST), 'fingerprint': manifest['fingerprint'], 'files': 6}), flush=True)


if __name__ == '__main__':
    prepare()
