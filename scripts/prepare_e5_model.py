"""Verify approved E5 bytes, then exclusively create an independent manifest."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.e5_model import CHUNK_POLICY, DIRECTORY, MANIFEST, POOLING
from research_workspace.multilingual_model import fingerprint


def prepare():
    path = ROOT/'docs/development/145-e5-model-download-plan-20261020.json'
    plan = json.loads(path.read_text(encoding='utf-8'))
    assert DIRECTORY.resolve() == DIRECTORY.absolute()
    for item in plan['files']:
        file = DIRECTORY/item['path']
        assert file.resolve().parent == DIRECTORY.resolve() and file.stat().st_size == item['size']
        assert hashlib.sha256(file.read_bytes()).hexdigest() == item['sha256']
    manifest = {k: plan[k] for k in ('model', 'repository', 'revision', 'dimensions', 'max_input_tokens',
                                    'license', 'query_prefix', 'document_prefix')}
    manifest.update(model_directory=DIRECTORY.relative_to(ROOT).as_posix(), chunk_policy=CHUNK_POLICY, pooling_policy=POOLING,
                    files=[{k: f[k] for k in ('path', 'size', 'sha256')} for f in plan['files']],
                    source_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest['fingerprint'] = fingerprint(manifest)
    if MANIFEST.exists():
        assert json.loads(MANIFEST.read_text(encoding='utf-8')) == manifest, 'Preserve existing runtime manifest'
    else:
        with MANIFEST.open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'manifest': str(MANIFEST), 'fingerprint': manifest['fingerprint'], 'files': len(manifest['files'])}), flush=True)


if __name__ == '__main__':
    prepare()
