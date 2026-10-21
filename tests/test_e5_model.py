"""Prefix-inclusive budgets, masked pooling, immutable local weights and modes."""
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, processors, trainers

from research_workspace import e5_model as module
from research_workspace.multilingual_model import fingerprint


@pytest.fixture
def tokenizer():
    result = Tokenizer(models.BPE(unk_token='[UNK]'))
    result.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    trainer = trainers.BpeTrainer(vocab_size=300, special_tokens=['[UNK]', '<pad>', '[CLS]', '[SEP]'],
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    result.train_from_iterator(['query: passage: The neural model recovers masked words using context.'], trainer)
    result.post_processor = processors.TemplateProcessing(single='[CLS] $A [SEP]',
        special_tokens=[('[CLS]', result.token_to_id('[CLS]')), ('[SEP]', result.token_to_id('[SEP]'))])
    return result


def prepared(tmp_path, monkeypatch, tokenizer):
    directory = tmp_path/'model'
    directory.mkdir()
    monkeypatch.setattr(module, 'DIRECTORY', directory)
    files = []
    for name in sorted(module.FILES):
        content = name.encode()
        (directory/name).write_bytes(content)
        files.append({'path': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    data = {'model': module.MODEL, 'revision': module.REVISION, 'dimensions': 384,
            'max_input_tokens': 512, 'chunk_policy': module.CHUNK_POLICY, 'query_prefix': module.QUERY_PREFIX,
            'document_prefix': module.DOCUMENT_PREFIX, 'pooling_policy': module.POOLING,
            'files': files, 'model_directory': str(directory)}
    data['fingerprint'] = fingerprint(data)
    path = tmp_path/'manifest.json'
    path.write_text(json.dumps(data))
    encoder = module.LocalE5Encoder(path)
    encoder._tokenizer = tokenizer
    return encoder, path, data, directory


@pytest.mark.parametrize('text', [
    'How does the model attend to earlier and later tokens? ',
    '模型怎样利用上下文预测被遮蔽的词？',
    'Wie werden verborgene Wörter vorhergesagt? ',
    'Comment prédire les mots cachés ? ',
    '¿Cómo se predicen las palabras ocultas? ',
    '隠された単語をどのように予測しますか？',
    '모델은 가려진 단어를 어떻게 예측합니까? ',
    'كيف يتنبأ النموذج بالكلمات المخفية؟ ',
    '🧪🧑🏽‍🔬αβγ\t\n\r\n',
])
def test_exact_source_spans_fit_both_prefixed_modes(tokenizer, text):
    original = '  '+text*90+' END-SOURCE-271828'
    spans = list(module.chunks(original, tokenizer))
    assert len(spans) > 1 and spans[0]['start'] == 0 and spans[-1]['end'] == len(original)
    covered = 0
    for span in spans:
        assert span['start'] <= covered < span['end']
        assert span['text'] == original[span['start']:span['end']]
        counts = [len(tokenizer.encode(prefix+span['text']).ids) for prefix in [module.QUERY_PREFIX, module.DOCUMENT_PREFIX]]
        assert max(counts) == span['tokens'] <= 512
        covered = span['end']
    assert covered == len(original) and any('END-SOURCE-271828' in s['text'] for s in spans)


def test_prefix_budget_is_checked_after_actual_boundary_encoding(tokenizer):
    spans = list(module.chunks('word '*100, tokenizer, max_tokens=36, overlap_tokens=8))
    assert len(spans) > 1
    assert all(max(len(tokenizer.encode(p+s['text']).ids) for p in [module.QUERY_PREFIX, module.DOCUMENT_PREFIX]) <= 36 for s in spans)
    assert list(module.chunks('', tokenizer)) == []
    assert ''.join(s['text'] for s in module.chunks('   ', tokenizer)) == '   '
    tokenizer.enable_truncation(128)
    with pytest.raises(ValueError, match='untruncated'):
        list(module.chunks('x '*1000, tokenizer))


@pytest.mark.parametrize('options', [{'max_tokens': True}, {'max_tokens': 3}, {'overlap_tokens': -1}, {'overlap_tokens': 512}])
def test_invalid_chunk_limits_fail_explicitly(tokenizer, options):
    with pytest.raises(ValueError):
        list(module.chunks('words', tokenizer, **options))


def test_missing_corrupt_escaped_and_changed_manifest_cannot_load(tmp_path, monkeypatch, tokenizer):
    assert module.LocalE5Encoder(tmp_path/'absent').status()['status'] == 'model_not_prepared'
    encoder, path, data, directory = prepared(tmp_path, monkeypatch, tokenizer)
    encoder._verify()
    data['query_prefix'] = 'different: '
    data['fingerprint'] = fingerprint(data)
    path.write_text(json.dumps(data))
    assert encoder.status()['status'] == 'invalid_model_manifest'
    with pytest.raises(ValueError):
        encoder.encode(['text'])
    data['query_prefix'] = module.QUERY_PREFIX
    data['extra'] = 'changed after load'
    data['fingerprint'] = fingerprint(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='changed'):
        encoder._verify()
    data.pop('extra')
    data['fingerprint'] = fingerprint(data)
    path.write_text(json.dumps(data))
    target = directory/'model_qint8_avx512_vnni.onnx'
    target.write_bytes(b'X'*target.stat().st_size)
    with pytest.raises(ValueError, match='checksum'):
        module.LocalE5Encoder(path)._verify()
    data['files'][0]['path'] = '../outside'
    data['fingerprint'] = fingerprint(data)
    path.write_text(json.dumps(data))
    assert module.LocalE5Encoder(path).status()['status'] == 'invalid_model_manifest'


def test_invalid_inputs_cannot_reach_session(tmp_path, monkeypatch, tokenizer):
    encoder, _, _, _ = prepared(tmp_path, monkeypatch, tokenizer)
    for texts in ['single string', ['word '*1000], [''], [None]]:
        with pytest.raises(ValueError):
            encoder.encode(texts)
    assert encoder._session is None
    tokenizer.enable_padding(pad_id=1, pad_token='<pad>')
    with pytest.raises(ValueError, match='unpadded'):
        encoder.encode(['short'])


class Session:
    def __init__(self):
        self.calls = []

    def get_inputs(self):
        return [SimpleNamespace(name=n) for n in ['input_ids', 'attention_mask', 'token_type_ids']]

    def run(self, names, feed):
        self.calls.append(feed)
        ids, mask = feed['input_ids'], feed['attention_mask']
        hidden = np.zeros((*ids.shape, 384), dtype=np.float32)
        hidden[..., 0] = ids
        hidden[..., 1] = np.arange(ids.shape[1])+1
        hidden[mask == 0] = 10000
        return [hidden]


@pytest.mark.parametrize('query', [False, True])
def test_actual_prefix_and_pad_mask_mean_are_used(tmp_path, monkeypatch, tokenizer, query):
    encoder, _, _, _ = prepared(tmp_path, monkeypatch, tokenizer)
    encoder._session = Session()
    texts = ['neural word', 'masked words '*5]
    values = encoder.encode(texts, query)
    feed = encoder._session.calls[0]
    prefix = module.QUERY_PREFIX if query else module.DOCUMENT_PREFIX
    assert len(values) == 2 and not feed['token_type_ids'].any()
    for i, text in enumerate(texts):
        tokens = tokenizer.encode(prefix+text).ids
        assert feed['input_ids'][i, :len(tokens)].tolist() == tokens
        assert not feed['attention_mask'][i, len(tokens):].any()
        assert all(feed['input_ids'][i, len(tokens):] == tokenizer.token_to_id('<pad>'))
        expected = np.zeros(384)
        expected[:2] = [np.mean(tokens), (len(tokens)+1)/2]
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(values[i], expected, atol=1e-6)
    assert tokenizer.padding is None and tokenizer.truncation is None


def test_zero_or_nonfinite_model_output_is_rejected(tmp_path, monkeypatch, tokenizer):
    encoder, _, _, _ = prepared(tmp_path, monkeypatch, tokenizer)
    session = Session()
    session.run = lambda names, feed: [np.zeros((*feed['input_ids'].shape, 384))]
    encoder._session = session
    with pytest.raises(ValueError):
        encoder.encode(['source'])
    session.run = lambda names, feed: [np.full((*feed['input_ids'].shape, 384), np.nan)]
    with pytest.raises(ValueError, match='hidden-state'):
        encoder.encode(['source'])
