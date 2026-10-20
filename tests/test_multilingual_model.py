import hashlib
import json

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, processors, trainers

from research_workspace import multilingual_model as module


@pytest.fixture
def tokenizer():
    # Contract-only tokenizer, not an approximation of the real model's quality.
    tokenizer = Tokenizer(models.BPE(unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    trainer = trainers.BpeTrainer(vocab_size=300, special_tokens=['[UNK]', '[CLS]', '[SEP]'],
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    tokenizer.train_from_iterator(['The model masks input tokens and predicts the hidden word.'], trainer)
    tokenizer.post_processor = processors.TemplateProcessing(single='[CLS] $A [SEP]',
        special_tokens=[('[CLS]', tokenizer.token_to_id('[CLS]')), ('[SEP]', tokenizer.token_to_id('[SEP]'))])
    return tokenizer


@pytest.mark.parametrize('sentence', [
    'How does the encoder attend to all input tokens? ',
    '模型怎样根据上下文预测被遮蔽的词？',
    'Wie berücksichtigt der Encoder alle Eingabetoken? ',
    'Comment le modèle prédit-il les mots masqués ? ',
    '¿Cómo predice el modelo las palabras ocultas? ',
    'モデルは隠された単語をどのように予測しますか？',
    '모델은 가려진 단어를 어떻게 예측합니까? ',
    'كيف يتنبأ النموذج بالكلمات المخفية؟ ',
    '🧪🧑🏽‍🔬αβγ\t\n\r\n'
])
def test_source_spans_cover_full_original_with_no_token_overflow(tokenizer, sentence):
    text = '  ' + sentence * 120 + '\nEND-OF-ORIGINAL'
    spans = list(module.chunks(text, tokenizer))
    assert len(spans) > 1 and spans[0]['start'] == 0 and spans[-1]['end'] == len(text)
    covered = 0
    for span in spans:
        assert span['start'] <= covered < span['end']
        assert span['text'] == text[span['start']:span['end']]
        assert span['tokens'] == len(tokenizer.encode(span['text']).ids) <= 128
        covered = span['end']
    assert covered == len(text)
    assert any('END-OF-ORIGINAL' in span['text'] for span in spans)


def test_whitespace_and_empty_inputs_are_not_invented(tokenizer):
    assert list(module.chunks('', tokenizer)) == []
    text = ' \n\t' * 5000
    spans = list(module.chunks(text, tokenizer))
    assert spans[0]['start'] == 0 and spans[-1]['end'] == len(text)
    assert all(s['text'] == text[s['start']:s['end']] and s['tokens'] <= 128 for s in spans)


@pytest.mark.parametrize('setting', ['padding', 'truncation'])
def test_reject_silently_truncating_or_padded_tokenizer(tokenizer, setting):
    if setting == 'padding':
        tokenizer.enable_padding(length=128)
    else:
        tokenizer.enable_truncation(128)
    with pytest.raises(ValueError):
        list(module.chunks('source text', tokenizer))


@pytest.mark.parametrize('budget,overlap', [(True, 0), (3, 0), (128, True), (128, 127), (128, -1)])
def test_invalid_chunk_limits_rejected(tokenizer, budget, overlap):
    with pytest.raises(ValueError):
        list(module.chunks('text', tokenizer, max_tokens=budget, overlap_tokens=overlap))


def make_manifest(tmp_path, monkeypatch):
    directory = tmp_path / 'weights'
    directory.mkdir()
    monkeypatch.setattr(module, 'DIRECTORY', directory)
    files = []
    for name in sorted(module.FILES):
        content = ('fixture:' + name).encode()
        (directory / name).write_bytes(content)
        files.append({'path': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    data = {'model': module.MODEL, 'dimensions': 384, 'max_input_tokens': 128,
            'revision': module.REVISION, 'chunk_policy': module.CHUNK_POLICY,
            'model_directory': str(directory), 'files': files}
    data['fingerprint'] = module.fingerprint(data)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    return path, data, directory


def test_missing_manifest_does_not_attempt_download(tmp_path):
    encoder = module.LocalMultilingualEncoder(tmp_path / 'absent.json')
    assert encoder.status()['status'] == 'model_not_prepared'
    with pytest.raises(FileNotFoundError):
        encoder.split('original')


def test_model_bytes_validated_before_loading_any_runtime(tmp_path, monkeypatch):
    path, data, directory = make_manifest(tmp_path, monkeypatch)
    encoder = module.LocalMultilingualEncoder(path)
    assert encoder.status()['status'] == 'ready'
    target = directory / 'model_optimized.onnx'
    target.write_bytes(b'X' * target.stat().st_size)
    with pytest.raises(ValueError, match='checksum'):
        encoder._verify()
    assert encoder._model is None


def test_manifest_change_cannot_reuse_loaded_adapter(tmp_path, monkeypatch):
    path, data, directory = make_manifest(tmp_path, monkeypatch)
    encoder = module.LocalMultilingualEncoder(path)
    encoder._verify()
    data['extra_configuration'] = 'different'
    data['fingerprint'] = module.fingerprint(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='changed'):
        encoder._verify()


def test_model_file_cannot_escape_model_directory(tmp_path, monkeypatch):
    path, data, directory = make_manifest(tmp_path, monkeypatch)
    data['files'][0]['path'] = '../outside'
    data['fingerprint'] = module.fingerprint(data)
    path.write_text(json.dumps(data))
    assert module.LocalMultilingualEncoder(path).status()['status'] == 'invalid_model_manifest'


def test_encoder_rejects_long_or_mutated_inputs_before_model_call(tmp_path, monkeypatch, tokenizer):
    path, _, _ = make_manifest(tmp_path, monkeypatch)
    encoder = module.LocalMultilingualEncoder(path)
    encoder._tokenizer = tokenizer
    with pytest.raises(ValueError, match='token budget'):
        encoder.encode(['word ' * 1000])
    with pytest.raises(ValueError, match='sequence'):
        encoder.encode('one string')
    tokenizer.enable_truncation(128)
    with pytest.raises(ValueError, match='untruncated'):
        encoder.encode(['word ' * 1000])
    assert encoder._model is None
