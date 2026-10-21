"""Actual E5 offline inference and independent raw-ONNX pooling comparison."""
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
from research_workspace.e5_model import LocalE5Encoder, DIRECTORY, QUERY_PREFIX, DOCUMENT_PREFIX


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def old_models():
    result = {}
    for relative in ['data/models/semantic-model.json', 'data/models/multilingual-model.json']:
        path = ROOT/relative
        data = json.loads(path.read_text(encoding='utf-8'))
        result[relative] = sha(path)
        for file in data['files']:
            p = ROOT/data['model_directory']/file['path']
            result[p.relative_to(ROOT).as_posix()] = sha(p)
    return result


def main():
    output = ROOT/'artifacts/e5-model'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    report = {'checks': [], 'errors': [], 'socket_attempts': 0, 'paid_model_calls': 0,
              'scope': 'Startup/token coverage and independent numerical inference; synthetic probes are not paper-grounded retrieval acceptance.',
              'driver_sha256': sha(Path(__file__)), 'adapter_sha256': sha(ROOT/'research_workspace/e5_model.py'), 'old_models_before': old_models()}
    started = time.monotonic()

    def check(name, passed):
        report['checks'].append({'name': name, 'passed': bool(passed)})
        assert passed, name

    def denied(*args, **kwargs):
        report['socket_attempts'] += 1
        raise RuntimeError('Networking forbidden for offline E5 acceptance')

    try:
        with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied):
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
            encoder = LocalE5Encoder()
            report['model'] = encoder.status()
            check('independent E5 manifest ready', report['model']['status'] == 'ready')
            original = ('Attention attends over the entire input. 注意力处理完整输入序列。 🧪\n'*110)+' ORIGINAL-TAIL-271828'
            spans = encoder.split(original)
            check('multiple source spans, full original tail', len(spans) > 1 and spans[0]['start'] == 0 and spans[-1]['end'] == len(original))
            covered = 0
            for span in spans:
                check('exact original prefix-inclusive span '+str(span['start']), span['start'] <= covered < span['end']
                      and span['text'] == original[span['start']:span['end']]
                      and max(len(encoder.tokenizer.encode(p+span['text']).ids) for p in [QUERY_PREFIX, DOCUMENT_PREFIX]) == span['tokens'] <= 512)
                covered = span['end']
            try:
                encoder.encode([original])
            except ValueError:
                rejected = True
            else:
                rejected = False
            check('oversized input rejected rather than silently truncated', rejected)
            check('actual tokenizer pad is one, independent of graph config zero', encoder.tokenizer.token_to_id('<pad>') == 1)
            documents = [
                'Masked language modeling predicts hidden words from surrounding context.',
                'Scaled dot-product attention divides query-key dot products by the square root of key dimension.',
                'Training adjusts neural network weights using gradient descent.',
                'Bar charts display values using rectangular bars.',
            ]
            probes = {
                'en': 'How does masked language modeling predict hidden words using context?',
                'zh': '掩码语言模型怎样利用上下文预测被遮蔽的词？',
                'de': 'Wie sagt ein maskiertes Sprachmodell verborgene Wörter anhand des Kontexts voraus?',
                'fr': 'Comment un modèle de langage masqué prédit-il les mots cachés à partir du contexte ?',
                'es': '¿Cómo predice un modelo de lenguaje enmascarado las palabras ocultas usando el contexto?',
                'ja': 'マスク言語モデルは文脈から隠された単語をどのように予測しますか？',
                'ko': '마스크 언어 모델은 문맥을 사용하여 가려진 단어를 어떻게 예측합니까?',
                'ar': 'كيف يتنبأ نموذج اللغة المقنع بالكلمات المخفية باستخدام السياق؟',
            }
            source_vectors = np.stack(encoder.encode(documents))
            query_vectors = np.stack(encoder.encode(list(probes.values()), query=True))
            values = np.concatenate([source_vectors, query_vectors])
            check('finite normalized vectors', values.shape == (12, 384) and np.isfinite(values).all()
                  and np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-5))
            tokenizer = Tokenizer.from_file(str(DIRECTORY/'tokenizer.json'))
            tokenizer.no_padding()
            tokenizer.no_truncation()
            options = ort.SessionOptions()
            options.intra_op_num_threads = options.inter_op_num_threads = 2
            session = ort.InferenceSession(str(DIRECTORY/'model_qint8_avx512_vnni.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
            # Independent manual padding/pooling, not the production adapter.
            independent = []
            for texts, prefix, expected in [(documents, 'passage: ', source_vectors), (list(probes.values()), 'query: ', query_vectors)]:
                encoded = [tokenizer.encode(prefix+t) for t in texts]
                width = max(len(e.ids) for e in encoded)
                ids = np.array([e.ids+[1]*(width-len(e.ids)) for e in encoded], dtype=np.int64)
                attention = np.array([[1]*len(e.ids)+[0]*(width-len(e.ids)) for e in encoded], dtype=np.int64)
                all_inputs = {'input_ids': ids, 'attention_mask': attention, 'token_type_ids': np.zeros_like(ids)}
                hidden = session.run(['last_hidden_state'], {i.name: all_inputs[i.name] for i in session.get_inputs()})[0]
                pooled = np.stack([row[:len(e.ids)].mean(axis=0) for row, e in zip(hidden, encoded)])
                pooled = pooled/np.linalg.norm(pooled, axis=1)[:, None]
                independent.append(pooled)
                check('independent raw ONNX mean and normalization '+prefix, np.allclose(pooled, expected, rtol=1e-5, atol=1e-6))
            scores = query_vectors@source_vectors.T
            check('independent actual cosine ordering', np.allclose(independent[1]@independent[0].T, scores, atol=1e-6))
            report['synthetic_probes'] = [{'language': language, 'query': q, 'scores': row.tolist(), 'top': int(row.argmax()), 'expected': 0}
                                           for (language, q), row in zip(probes.items(), scores)]
            report['probe_documents'] = documents
            check('runtime tokenizer remains untruncated and unpadded', encoder.tokenizer.truncation is None and encoder.tokenizer.padding is None)
    except Exception as error:
        report['errors'].append(type(error).__name__+': '+str(error))
    report['old_models_after'] = old_models()
    report['old_models_unchanged'] = report['old_models_before'] == report['old_models_after']
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    report['passed'] = not report['errors'] and all(c['passed'] for c in report['checks']) and report['old_models_unchanged'] and report['socket_attempts'] == 0
    with (output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'passed': report['passed'], 'checks': len(report['checks']), 'errors': report['errors'],
                      'synthetic_top1': sum(p['top'] == p['expected'] for p in report.get('synthetic_probes', []))}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
