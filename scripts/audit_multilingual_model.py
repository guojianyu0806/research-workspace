"""Recompute model probe scores with raw ONNX Runtime, independent of FastEmbed."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(receipt_dir, smoke_dir):
    receipt_dir, smoke_dir = Path(receipt_dir).resolve(), Path(smoke_dir).resolve()
    assert receipt_dir.is_relative_to(ROOT / 'artifacts/model-download')
    assert smoke_dir.is_relative_to(ROOT / 'artifacts/multilingual-model')
    plan_path = ROOT / 'docs/development/142-multilingual-model-download-plan-20261020.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    receipt = json.loads((receipt_dir / 'receipt.json').read_text(encoding='utf-8'))
    smoke = json.loads((smoke_dir / 'report.json').read_text(encoding='utf-8'))
    directory = ROOT / plan['destination']
    checks = []
    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)
    check('successful scoped six-file receipt', receipt['passed'] and len(receipt['files']) == 6
          and not receipt['errors'] and receipt['packages_installed'] == receipt['pro_calls'] == 0)
    check('approved frozen manifest fingerprint', receipt['manifest_sha256'] == digest(plan_path))
    check('exact downloader', receipt['driver_sha256'] == digest(ROOT / 'scripts/download_multilingual_model.py'))
    for item in plan['files']:
        path = directory / item['path']
        check('exact local file ' + item['path'], path.stat().st_size == item['size'] and digest(path) == item['sha256'])
    check('whole smoke passed with sockets forbidden', smoke['passed'] and not smoke['errors']
          and smoke['socket_attempts'] == smoke['paid_model_calls'] == 0 and all(c['passed'] for c in smoke['checks']))
    check('exact smoke adapter and driver', smoke['adapter_sha256'] == digest(ROOT / 'research_workspace/multilingual_model.py')
          and smoke['driver_sha256'] == digest(ROOT / 'scripts/multilingual_model_smoke.py'))
    english_path = ROOT / 'data/models/semantic-model.json'
    english = json.loads(english_path.read_text(encoding='utf-8'))
    before = smoke['english_before']
    check('English manifest retained', before == smoke['english_after'] and digest(english_path) == before['manifest'])
    for name, value in before['files'].items():
        check('English model retained ' + name, digest(ROOT / english['model_directory'] / name) == value)
    # Reconstruct padding/attention inputs, pooling and normalization directly.
    # No import of the production encoder or FastEmbed is used for these scores.
    blocked = []
    def denied(*args, **kwargs):
        blocked.append(True)
        raise RuntimeError('Offline independent inference forbids connections')
    with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied):
        tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        tokenizer.no_truncation()
        tokenizer.enable_padding(pad_id=1, pad_token='<pad>')
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 2
        session = ort.InferenceSession(str(directory / 'model_optimized.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
        def infer(texts):
            encodings = tokenizer.encode_batch(texts)
            check('independent inputs fit context ' + str(len(texts)), all(sum(e.attention_mask) <= 128 for e in encodings))
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            attention = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            available = {'input_ids': ids, 'attention_mask': attention, 'token_type_ids': np.zeros_like(ids)}
            raw = session.run(None, {item.name: available[item.name] for item in session.get_inputs()})[0]
            mask = attention[:, :, None].astype(bool)
            pooled = (np.sum(raw, axis=1, where=mask, dtype=np.float64) / attention.sum(axis=1, keepdims=True)).astype(np.float32)
            return pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
        docs = infer(smoke['source_strings'])
        queries = infer([p['query'] for p in smoke['synthetic_probes']])
        scores = queries @ docs.T
        for item, row in zip(smoke['synthetic_probes'], scores):
            check('raw ONNX scores match saved probe ' + item['language'], np.allclose(row, item['scores'], atol=1e-6))
            check('raw ONNX rank matches saved probe ' + item['language'], np.argsort(-row).tolist() == item['ranked_document_ordinals'])
    check('no independent socket attempts', not blocked)
    output = smoke_dir / ('independent-model-audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump({'passed': True, 'checks': checks, 'driver_sha256': digest(Path(__file__)),
                   'scope': 'Approved model files, unchanged English bytes and independent ONNX startup probes; no paper-grounded retrieval or dedup quality claim.'},
                  stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'passed': True, 'checks': len(checks)}), flush=True)


if __name__ == '__main__':
    audit(*sys.argv[1:])
