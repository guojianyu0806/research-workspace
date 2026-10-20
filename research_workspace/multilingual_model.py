"""Explicitly prepared local multilingual encoder; never downloads at runtime.

This adapter does not change the English index or enable a new search channel.
Every encoded string must fit the actual tokenizer, including special tokens.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
MANIFEST = ROOT / 'data/models/multilingual-model.json'
DIRECTORY = ROOT / 'data/models/multilingual-minilm-l12-v2-onnx-q-faf4aa4'
MAX_TOKENS = 128
CHUNK_POLICY = 'original-character-spans-token-budget-overlap-v1'
REVISION = 'faf4aa4225822f3bc6376869cb1164e8e3feedd0'
FILES = {'config.json', 'ort_config.json', 'special_tokens_map.json',
         'tokenizer_config.json', 'tokenizer.json', 'model_optimized.onnx'}


def fingerprint(data):
    return hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != 'fingerprint'},
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def chunks(text, tokenizer, *, max_tokens=MAX_TOKENS, overlap_tokens=24):
    """Return overlapping, lossless source substrings, checked after re-tokenizing.

    Token offsets propose boundaries only. BPE at a new boundary can change the
    count, so every final substring is encoded again without truncation/padding.
    The supplied tokenizer must have truncation and padding disabled.
    """
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise ValueError('Chunking requires an untruncated, unpadded tokenizer')
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 4:
        raise ValueError('Invalid token budget')
    if isinstance(overlap_tokens, bool) or not isinstance(overlap_tokens, int) or not 0 <= overlap_tokens < max_tokens - 2:
        raise ValueError('Invalid overlap')
    if not isinstance(text, str):
        raise ValueError('Source text must be a string')
    start = 0
    while start < len(text):
        end = min(len(text), start + 4096)
        observed = tokenizer.encode(text[start:end], add_special_tokens=True)
        if len(observed.ids) > max_tokens:
            # Leave room for both boundary special tokens. Zero-width special
            # offsets must not be mistaken for original character positions.
            offsets = tokenizer.encode(text[start:end], add_special_tokens=False).offsets
            useful = [right for left, right in offsets[:max_tokens - 2] if right > left]
            end = start + max(useful, default=1)
            observed = tokenizer.encode(text[start:end], add_special_tokens=True)
            while len(observed.ids) > max_tokens and end > start + 1:
                end -= 1
                observed = tokenizer.encode(text[start:end], add_special_tokens=True)
            if len(observed.ids) > max_tokens:
                raise ValueError('One source character exceeds the token budget')
        yield {'start': start, 'end': end, 'text': text[start:end], 'tokens': len(observed.ids)}
        if end == len(text):
            break
        offsets = tokenizer.encode(text[start:end], add_special_tokens=False).offsets
        useful = [(left, right) for left, right in offsets if right > left]
        # Require forward progress even for repeated offsets (e.g. emoji byte
        # fallback tokens). All original whitespace remains in source spans.
        next_start = start + useful[-overlap_tokens][0] if overlap_tokens and len(useful) > overlap_tokens else end
        start = max(start + 1, min(end, next_start))


class LocalMultilingualEncoder:
    def __init__(self, manifest=MANIFEST):
        self.manifest = Path(manifest)
        self._lock = threading.RLock()
        self._tokenizer = None
        self._model = None
        self._verified = None

    def _read_manifest(self):
        data = json.loads(self.manifest.read_text(encoding='utf-8'))
        if (data.get('model') != MODEL or data.get('dimensions') != 384 or data.get('revision') != REVISION
                or data.get('max_input_tokens') != MAX_TOKENS or data.get('chunk_policy') != CHUNK_POLICY
                or data.get('fingerprint') != fingerprint(data)
                or (ROOT / data['model_directory']).resolve() != DIRECTORY.absolute()):
            raise ValueError('Invalid multilingual model manifest')
        if len(data['files']) != 6 or {f['path'] for f in data['files']} != FILES:
            raise ValueError('Invalid multilingual model files')
        for item in data['files']:
            path = DIRECTORY / item['path']
            if (path.resolve().parent != DIRECTORY.absolute() or not path.is_file()
                    or path.stat().st_size != item['size']):
                raise ValueError('Missing or altered multilingual model file')
        return data

    def status(self):
        if not self.manifest.is_file():
            return {'status': 'model_not_prepared', 'model': MODEL}
        try:
            data = self._read_manifest()
            return {k: data[k] for k in ('model', 'fingerprint', 'dimensions', 'max_input_tokens', 'chunk_policy')} | {'status': 'ready'}
        except (OSError, ValueError, KeyError, TypeError):
            return {'status': 'invalid_model_manifest', 'model': MODEL}

    def _verify(self):
        data = self._read_manifest()
        if self._verified is None:
            for item in data['files']:
                digest = hashlib.sha256()
                with (DIRECTORY / item['path']).open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                if digest.hexdigest() != item['sha256']:
                    raise ValueError('Multilingual model checksum mismatch')
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
                self._tokenizer = Tokenizer.from_file(str(DIRECTORY / 'tokenizer.json'))
                self._tokenizer.no_truncation()
                self._tokenizer.no_padding()
            return self._tokenizer

    def split(self, text):
        with self._lock:
            return list(chunks(text, self.tokenizer))

    def encode(self, texts, query=False):
        with self._lock:
            # Validate every input before invoking the potentially truncating
            # model API. Callers split documents and long queries explicitly.
            if isinstance(texts, str):
                raise ValueError('Pass a sequence of strings, not a single string')
            texts = list(texts)
            tokenizer = self.tokenizer
            if tokenizer.truncation is not None or tokenizer.padding is not None:
                raise ValueError('Input validation requires an untruncated tokenizer')
            if any(not isinstance(t, str) or not t.strip() or len(tokenizer.encode(t).ids) > MAX_TOKENS for t in texts):
                raise ValueError('Encoder inputs must be nonempty and fit the token budget')
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(model_name=MODEL, specific_model_path=str(DIRECTORY),
                    cache_dir=str(ROOT / 'data/models'), local_files_only=True,
                    threads=2, providers=['CPUExecutionProvider'])
                if self._model.model.tokenizer.truncation['max_length'] != MAX_TOKENS:
                    raise ValueError('Runtime tokenizer limit differs from the prepared model')
            values = list(self._model.query_embed(texts) if query else self._model.passage_embed(texts, batch_size=24))
            if len(values) != len(texts):
                raise ValueError('Embedding count mismatch')
            from .semantic import SemanticIndex
            return [SemanticIndex._vector(v, 384) for v in values]
