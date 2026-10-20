"""Development-only experiment: smaller original spans, no new model or tuning on BERT.

Stores all inputs and scores in a new artifact; does not alter any live index.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import sqlite3
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.intelligence import Evidence, IntelligenceService
from research_workspace.multilingual_model import LocalMultilingualEncoder


def main():
    previous = Path(sys.argv[1]).resolve()
    assert previous.is_relative_to(ROOT/'artifacts/multilingual-index')
    output = ROOT/'artifacts/multilingual-sentences'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    print(json.dumps({'output': str(output)}), flush=True)
    fixture_path = ROOT/'tests/fixtures/multilingual/20261020-v1/manifest.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    accepted = json.loads((previous/'report.json').read_text(encoding='utf-8'))
    assert accepted['passed'] and accepted['split'] == 'development' and len(accepted['queries']) == 96
    expected = {q['id']: q['expected_block_ids'] for q in accepted['queries']}
    report = {'scope': 'Development-only segmentation experiment. No production index or held-out scores.',
              'queries': [], 'errors': [], 'connections': 0, 'paid_model_calls': 0,
              'fixture_sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
              'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    database = previous/'workspace/library.sqlite3'
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute('''SELECT b.id,b.run_id,r.document_id,b.page,b.text,b.bbox,d.filename,d.title
            FROM blocks b JOIN parse_runs r ON b.run_id=r.id JOIN documents d ON r.document_id=d.id
            ORDER BY r.document_id,b.page,b.ordinal''').fetchall()
    blocks = [Evidence(evidence_id='E_'+r[0], block_id=r[0], run_id=r[1], document_id=r[2], page=r[3],
                       text=r[4], bbox=json.loads(r[5]), filename=r[6], document_title=r[7]) for r in rows]
    def denied(*args, **kwargs):
        report['connections'] += 1
        raise RuntimeError('External inference connections forbidden')
    started = time.monotonic()
    try:
        with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied):
            encoder = LocalMultilingualEncoder()
            report['model'] = encoder.status()
            entries = []
            for bi, block in enumerate(blocks):
                boundaries = [0] + [m.end() for m in re.finditer(r'(?<=[.!?。！？])\s+|\n\s*\n', block.text)] + [len(block.text)]
                for left, right in zip(boundaries, boundaries[1:]):
                    for span in encoder.split(block.text[left:right]):
                        start, end = left+span['start'], left+span['end']
                        text = block.text[start:end]
                        if text.strip():
                            entries.append({'block_index': bi, 'start': start, 'end': end, 'text': text})
            # Every vector input remains an exact original substring; no guessed
            # heading, English translation or expected answer is inserted.
            unique = sorted({e['text'] for e in entries}, key=lambda text:(len(text), text))
            values = {}
            for offset in range(0, len(unique), 24):
                batch = unique[offset:offset+24]
                values.update(zip(batch, encoder.encode(batch)))
                if offset % 480 == 0:
                    print(json.dumps({'encoded': min(offset+24, len(unique)), 'total': len(unique)}), flush=True)
            matrix = np.stack([values[e['text']] for e in entries])
            np.save(output/'sentence-vectors.npy', matrix, allow_pickle=False)
            with (output/'inputs.json').open('x', encoding='utf-8') as stream:
                json.dump({'blocks': [b.model_dump() for b in blocks], 'entries': entries}, stream, ensure_ascii=False)
            references = IntelligenceService._lexical_index(blocks)[3]
            for query in [q for q in fixture['queries'] if q['split'] == 'development']:
                vector = encoder.encode([query['query']], True)[0]
                scores = matrix @ vector
                best = {}
                for i, score in enumerate(scores):
                    bi = entries[i]['block_index']
                    if bi not in best or float(score) > best[bi][0]:
                        best[bi] = (float(score), i)
                raw = sorted(best, key=lambda bi:-best[bi][0])
                weighted = sorted(best, key=lambda bi:-best[bi][0]*IntelligenceService._evidence_quality(blocks[bi], query['query'], references))
                report['queries'].append({'id': query['id'], 'language': query['language'],
                    'raw_hit': any(blocks[bi].block_id in expected[query['id']] for bi in raw[:5]),
                    'quality_hit': any(blocks[bi].block_id in expected[query['id']] for bi in weighted[:5]),
                    'top20_raw': [{'block_id': blocks[bi].block_id, 'score': best[bi][0], 'entry': best[bi][1]} for bi in raw[:20]],
                    'top20_quality': [{'block_id': blocks[bi].block_id, 'score': best[bi][0], 'entry': best[bi][1]} for bi in weighted[:20]]})
            report['sentence_chunks'] = len(entries)
            report['unique_inputs'] = len(unique)
    except (Exception, KeyboardInterrupt) as error:
        report['errors'].append(type(error).__name__+': '+str(error))
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    report['experiment_completed'] = not report['errors'] and len(report['queries']) == 96 and report['connections'] == 0
    report['recall_counts'] = {kind: sum(q[kind] for q in report['queries']) for kind in ('raw_hit', 'quality_hit')}
    report['quality_gate_passed'] = (report['experiment_completed'] and report['recall_counts']['quality_hit']/96 >= .9
        and all(sum(q['quality_hit'] for q in report['queries'] if q['language'] == lang)/12 >= .8
                for lang in {q['language'] for q in report['queries']}))
    with (output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'completed': report['experiment_completed'],
                      'recall_counts': report['recall_counts'], 'quality_gate_passed': report['quality_gate_passed'],
                      'errors': report['errors']}), flush=True)


if __name__ == '__main__':
    main()
