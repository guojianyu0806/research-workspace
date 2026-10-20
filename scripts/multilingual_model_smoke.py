"""Actual approved weights, with all Python socket connections denied.

Small synthetic semantic probes are startup checks, not the planned 160-query
paper-grounded retrieval or 80-pair document identity acceptance.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.multilingual_model import LocalMultilingualEncoder, MANIFEST, DIRECTORY


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def english_snapshot():
    path = ROOT / 'data/models/semantic-model.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    directory = ROOT / manifest['model_directory']
    return {'manifest': sha(path), 'files': {f['path']: sha(directory / f['path']) for f in manifest['files']}}


def main():
    output = ROOT / 'artifacts/multilingual-model' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'Actual offline load, token coverage, no silent truncation, finite normalized vectors and small synthetic language probes only.',
              'checks': [], 'errors': [], 'socket_attempts': 0, 'paid_model_calls': 0,
              'driver_sha256': sha(Path(__file__)), 'adapter_sha256': sha(ROOT / 'research_workspace/multilingual_model.py')}
    def check(name, passed):
        report['checks'].append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)
    def denied(*args, **kwargs):
        report['socket_attempts'] += 1
        raise RuntimeError('Networking forbidden in offline model acceptance')
    before = english_snapshot()
    started = time.monotonic()
    try:
        with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied):
            import numpy as np
            encoder = LocalMultilingualEncoder()
            report['model'] = encoder.status()
            check('separate multilingual model prepared', report['model']['status'] == 'ready')
            text = ('The model predicts hidden input tokens from their surrounding context. 模型利用上下文恢复被遮蔽的词。 ' * 180) + ' TAIL-SOURCE-FACT-271828'
            spans = encoder.split(text)
            check('actual tokenizer covers full original tail', spans[0]['start'] == 0 and spans[-1]['end'] == len(text)
                  and any('TAIL-SOURCE-FACT-271828' in s['text'] for s in spans))
            covered = 0
            for span in spans:
                check('actual substring and token limit ' + str(span['start']),
                      span['start'] <= covered < span['end'] and span['text'] == text[span['start']:span['end']]
                      and span['tokens'] == len(encoder.tokenizer.encode(span['text']).ids) <= 128)
                covered = span['end']
            try:
                encoder.encode([text])
            except ValueError:
                rejected = True
            else:
                rejected = False
            check('unbounded input rejected rather than truncated', rejected)
            documents = [
                'Masked language modeling predicts hidden input tokens from their surrounding context.',
                'Scaled dot-product attention divides query-key dot products by the square root of key dimension.',
                'Training adjusts neural network weights with gradient descent.',
                'Bar charts display values using rectangular bars.']
            probes = {
                'en': 'How does masked language modeling predict hidden words using context?',
                'zh': '掩码语言模型怎样利用上下文预测被遮蔽的词？',
                'de': 'Wie sagt ein maskiertes Sprachmodell verborgene Wörter anhand des Kontexts voraus?',
                'fr': 'Comment un modèle de langage masqué prédit-il les mots cachés à partir du contexte ?',
                'es': '¿Cómo predice un modelo de lenguaje enmascarado las palabras ocultas usando el contexto?',
                'ja': 'マスク言語モデルは文脈から隠された単語をどのように予測しますか？',
                'ko': '마스크 언어 모델은 문맥을 사용하여 가려진 단어를 어떻게 예측합니까?',
                'ar': 'كيف يتنبأ نموذج اللغة المقنع بالكلمات المخفية باستخدام السياق؟'}
            source_vectors = np.stack(encoder.encode(documents))
            query_vectors = np.stack(encoder.encode(list(probes.values()), query=True))
            values = np.concatenate([source_vectors, query_vectors])
            check('all actual vectors finite, normalized and 384-dimensional', values.shape == (12, 384)
                  and np.isfinite(values).all() and np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-5))
            scores = query_vectors @ source_vectors.T
            report['synthetic_probes'] = []
            for language, query, row in zip(probes, probes.values(), scores):
                ranking = np.argsort(-row).tolist()
                report['synthetic_probes'].append({'language': language, 'query': query,
                    'scores': [float(v) for v in row], 'ranked_document_ordinals': ranking})
                check('synthetic masked-token probe ' + language, ranking[0] == 0)
            report['source_strings'] = documents
            report['source_spans'] = [{k: s[k] for k in ('start', 'end', 'tokens')} for s in spans]
            report['source_text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            check('zero socket attempts', report['socket_attempts'] == 0)
    except Exception as error:
        report['errors'].append(type(error).__name__ + ': ' + str(error))
    after = english_snapshot()
    report['english_before'] = before
    report['english_after'] = after
    report['checks'].append({'name': 'existing English model byte hashes unchanged', 'passed': before == after})
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    report['checks'].append({'name': 'all multilingual bytes unchanged by inference', 'passed': all(
        sha(DIRECTORY / item['path']) == item['sha256'] for item in manifest['files'])})
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    report['passed'] = not report['errors'] and all(c['passed'] for c in report['checks'])
    with (output / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'passed': report['passed'], 'checks': len(report['checks']),
                      'errors': report['errors']}, ensure_ascii=True), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
