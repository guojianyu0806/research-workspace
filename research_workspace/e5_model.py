"""Explicit, offline E5 candidate with prefix-aware lossless source spans.

Not selected by the production reader. Old MiniLM and English indexes remain
unchanged; the fingerprint includes prefixes, weights and chunk/pooling policy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading

from .multilingual_model import fingerprint

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'intfloat/multilingual-e5-small'
REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
DIRECTORY = ROOT/'data/models/multilingual-e5-small-onnx-int8-614241f'
MANIFEST = ROOT/'data/models/e5-small-model.json'
MAX_TOKENS = 512
QUERY_PREFIX = 'query: '
DOCUMENT_PREFIX = 'passage: '
CHUNK_POLICY = 'exact-source-spans-both-prefix-token-budgets-64-overlap-v1'
POOLING = 'attention-mask-mean-l2-v1'
FILES = {'config.json', 'special_tokens_map.json', 'tokenizer_config.json',
         'tokenizer.json', 'model_qint8_avx512_vnni.onnx'}


def chunks(text, tokenizer, *, max_tokens=MAX_TOKENS, overlap_tokens=64):
    """Both modes fit after prefixing. Returned offsets describe source only."""
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise ValueError('Chunking requires an untruncated, unpadded tokenizer')
    if not isinstance(text, str):
        raise ValueError('Source text must be a string')
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 8:
        raise ValueError('Invalid token budget')
    if isinstance(overlap_tokens, bool) or not isinstance(overlap_tokens, int) or not 0 <= overlap_tokens < max_tokens-2:
        raise ValueError('Invalid overlap')
    prefixes = (QUERY_PREFIX, DOCUMENT_PREFIX)
    if any(len(tokenizer.encode(prefix).ids) >= max_tokens for prefix in prefixes):
        raise ValueError('Prefix exceeds the token budget')
    start = 0
    while start < len(text):
        end = min(len(text), start+8192)
        observed = [tokenizer.encode(prefix+text[start:end]) for prefix in prefixes]
        if max(len(e.ids) for e in observed) > max_tokens:
            candidates = []
            for prefix, encoding in zip(prefixes, observed):
                if len(encoding.ids) <= max_tokens:
                    continue
                useful = [right-len(prefix) for left, right in encoding.offsets[:max_tokens-1] if right > len(prefix)]
                candidates.append(max(useful, default=1))
            end = min(end, start+min(candidates))
            observed = [tokenizer.encode(prefix+text[start:end]) for prefix in prefixes]
            # BPE boundaries can change: proposal offsets never replace actual
            # final encoding checks, and nothing is silently truncated.
            while max(len(e.ids) for e in observed) > max_tokens and end > start+1:
                end -= 1
                observed = [tokenizer.encode(prefix+text[start:end]) for prefix in prefixes]
            if max(len(e.ids) for e in observed) > max_tokens:
                raise ValueError('One source character exceeds the token budget')
        yield {'start': start, 'end': end, 'text': text[start:end], 'tokens': max(len(e.ids) for e in observed)}
        if end == len(text):
            break
        offsets = tokenizer.encode(text[start:end], add_special_tokens=False).offsets
        useful = [(left, right) for left, right in offsets if right > left]
        next_start = start+useful[-overlap_tokens][0] if overlap_tokens and len(useful) > overlap_tokens else end
        start = max(start+1, min(end, next_start))


class LocalE5Encoder:
    def __init__(self, manifest=MANIFEST):
        self.manifest = Path(manifest)
        self._lock = threading.RLock()
        self._tokenizer = self._session = self._verified = None

    def _read_manifest(self):
        data = json.loads(self.manifest.read_text(encoding='utf-8'))
        expected = {'model': MODEL, 'revision': REVISION, 'dimensions': 384, 'max_input_tokens': MAX_TOKENS,
                    'chunk_policy': CHUNK_POLICY, 'query_prefix': QUERY_PREFIX, 'document_prefix': DOCUMENT_PREFIX,
                    'pooling_policy': POOLING}
        if (any(data.get(k) != v for k, v in expected.items()) or data.get('fingerprint') != fingerprint(data)
                or (ROOT/data['model_directory']).resolve() != DIRECTORY.absolute()):
            raise ValueError('Invalid E5 model manifest')
        if len(data['files']) != 5 or {f['path'] for f in data['files']} != FILES:
            raise ValueError('Invalid E5 model files')
        for item in data['files']:
            path = DIRECTORY/item['path']
            if path.resolve().parent != DIRECTORY.absolute() or not path.is_file() or path.stat().st_size != item['size']:
                raise ValueError('Missing or altered E5 model file')
        return data

    def status(self):
        if not self.manifest.is_file():
            return {'status': 'model_not_prepared', 'model': MODEL}
        try:
            data = self._read_manifest()
            return {k: data[k] for k in ('model', 'fingerprint', 'dimensions', 'max_input_tokens', 'chunk_policy',
                                         'query_prefix', 'document_prefix', 'pooling_policy')} | {'status': 'ready'}
        except (OSError, ValueError, KeyError, TypeError):
            return {'status': 'invalid_model_manifest', 'model': MODEL}

    def _verify(self):
        data = self._read_manifest()
        if self._verified is None:
            for item in data['files']:
                digest = hashlib.sha256()
                with (DIRECTORY/item['path']).open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b''):
                        digest.update(chunk)
                if digest.hexdigest() != item['sha256']:
                    raise ValueError('E5 model checksum mismatch')
            self._verified = data['fingerprint']
        elif self._verified != data['fingerprint']:
            raise ValueError('Model manifest changed after loading; create a new adapter')
        return data

    @property
    def tokenizer(self):
        with self._lock:
            self._verify()
            if self._tokenizer is None:
                from tokenizers import Tokenizer
                self._tokenizer = Tokenizer.from_file(str(DIRECTORY/'tokenizer.json'))
                self._tokenizer.no_truncation()
                self._tokenizer.no_padding()
            return self._tokenizer

    def split(self, text):
        with self._lock:
            return list(chunks(text, self.tokenizer))

    def encode(self, texts, query=False):
        with self._lock:
            if isinstance(texts, str):
                raise ValueError('Pass a sequence of strings, not a single string')
            texts = list(texts)
            tokenizer = self.tokenizer
            if tokenizer.truncation is not None or tokenizer.padding is not None:
                raise ValueError('Input validation requires an untruncated, unpadded tokenizer')
            if any(not isinstance(t, str) or not t.strip() for t in texts):
                raise ValueError('Encoder inputs must be nonempty strings')
            prefix = QUERY_PREFIX if query else DOCUMENT_PREFIX
            encoded = [tokenizer.encode(prefix+t) for t in texts]
            if any(len(e.ids) > MAX_TOKENS for e in encoded):
                raise ValueError('Encoder inputs must fit the prefix-inclusive token budget')
            if not encoded:
                return []
            if self._session is None:
                import onnxruntime as ort
                options = ort.SessionOptions()
                options.intra_op_num_threads = options.inter_op_num_threads = 2
                session = ort.InferenceSession(str(DIRECTORY/'model_qint8_avx512_vnni.onnx'), sess_options=options,
                                              providers=['CPUExecutionProvider'])
                if ('last_hidden_state' not in {o.name for o in session.get_outputs()}
                        or not {i.name for i in session.get_inputs()} <= {'input_ids', 'attention_mask', 'token_type_ids'}):
                    raise ValueError('Unsupported prepared E5 graph contract')
                self._session = session
            import numpy as np
            from .semantic import SemanticIndex
            pad = tokenizer.token_to_id('<pad>')
            if pad is None:
                raise ValueError('Prepared tokenizer has no actual pad token')
            result = []
            for offset in range(0, len(encoded), 24):
                batch = encoded[offset:offset+24]
                width = max(len(e.ids) for e in batch)
                ids = np.full((len(batch), width), pad, dtype=np.int64)
                mask = np.zeros_like(ids)
                for i, e in enumerate(batch):
                    ids[i, :len(e.ids)] = e.ids
                    mask[i, :len(e.ids)] = e.attention_mask
                values = {'input_ids': ids, 'attention_mask': mask, 'token_type_ids': np.zeros_like(ids)}
                hidden = np.asarray(self._session.run(['last_hidden_state'], {i.name: values[i.name] for i in self._session.get_inputs()})[0])
                if hidden.shape != (*ids.shape, 384) or not np.isfinite(hidden).all() or np.any(mask.sum(axis=1) == 0):
                    raise ValueError('Invalid E5 hidden-state output')
                pooled = np.where(mask[..., None] != 0, hidden, 0).sum(axis=1)/mask.sum(axis=1)[:, None]
                result.extend(SemanticIndex._vector(v, 384) for v in pooled)
            return result
