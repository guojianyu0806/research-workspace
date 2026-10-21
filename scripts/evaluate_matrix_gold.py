"""Score development matrices only, retaining all failures and source anchors."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from unittest.mock import patch

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.matrix_geometry import extract_native_matrices


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iou(a, b):
    overlap = (pymupdf.Rect(a) & pymupdf.Rect(b)).get_area()
    total = pymupdf.Rect(a).get_area()+pymupdf.Rect(b).get_area()-overlap
    return overlap/total if total else 0


def compact(text):
    return re.sub(r'\s+', '', text or '')


def evaluate():
    fixture = ROOT/'tests/fixtures/matrix_gold/20261020-v2/manifest.json'
    gold = json.loads(fixture.read_text(encoding='utf-8'))
    review = json.loads(fixture.with_name('visual-review.json').read_text(encoding='utf-8'))
    assert review['manifest_sha256'] == digest(fixture)
    assert set(review['reviewed_ids']) == {c['id'] for c in gold['cases']}
    assert not review['parser_used_to_define_gold']
    selected = [c for c in gold['cases'] if c['split'] == 'development']
    output = ROOT/'artifacts/matrix-gold-development'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    report = {'fixture_sha256': digest(fixture), 'visual_review_sha256': digest(fixture.with_name('visual-review.json')),
              'driver_sha256': digest(Path(__file__)),
              'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'method': 'One-to-one descending IoU geometry assignment, threshold .35 fixed before scoring; exact row/column and cell LaTeX comparison, whitespace only ignored.',
              'scope': '13 clear native development regions; no held-out parser evaluation; unannotated page predictions not precision-scored.',
              'model_calls': 0, 'socket_attempts': 0, 'cases': [], 'pages': [], 'source_unchanged': {},
              'source_code_sha256': {p: digest(ROOT/p) for p in ['research_workspace/native_math_fonts.py', 'research_workspace/matrix_geometry.py', 'research_workspace/formula_geometry.py']}}
    started = time.monotonic()

    def denied(*args, **kwargs):
        report['socket_attempts'] += 1
        raise RuntimeError('Local matrix evaluation forbids network')

    with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied):
        for key, source in gold['sources'].items():
            if source['split'] != 'development':
                continue
            path = ROOT/source['path']
            before = digest(path)
            assert before == source['sha256'], key
            with pymupdf.open(path) as pdf:
                for number in sorted({c['physical_page'] for c in selected if c['source'] == key}):
                    predictions, audit = extract_native_matrices(pdf[number-1])
                    page_gold = [c for c in selected if c['source'] == key and c['physical_page'] == number]
                    edges = sorted([(iou(c['bbox_points'], p['raw_bbox']), i, j) for i, c in enumerate(page_gold)
                                    for j, p in enumerate(predictions)], reverse=True)
                    matches, used = {}, set()
                    for score, i, j in edges:
                        if score >= .35 and i not in matches and j not in used:
                            matches[i] = (j, score)
                            used.add(j)
                    report['pages'].append({'source': key, 'physical_page': number, 'predictions': predictions, 'audit': audit,
                                            'unmatched_predictions_not_precision_scored': [j for j in range(len(predictions)) if j not in used]})
                    for i, c in enumerate(page_gold):
                        row = {'id': c['id'], 'source': key, 'physical_page': number, 'bbox_points': c['bbox_points'],
                               'expected_cells': c['expected_cells'], 'matched': i in matches, 'correct': False}
                        if i in matches:
                            j, score = matches[i]
                            p = predictions[j]
                            actual = [[v['latex'] for v in p['cells'] if v['row'] == r] for r in range(p['row_count'] or 0)]
                            row.update({'prediction_index': j, 'iou': score, 'actual_cells': actual, 'conversion': p['conversion'],
                                        'correct': bool(p['latex']) and p['row_count'] == c['expected_rows'] and p['column_count'] == c['expected_columns']
                                        and [[compact(v) for v in r] for r in actual] == [[compact(v) for v in r] for r in c['expected_cells']]})
                        report['cases'].append(row)
            report['source_unchanged'][key] = digest(path) == before
    report.update({'elapsed_seconds': round(time.monotonic()-started, 3), 'expected_regions': len(selected),
                   'matched_regions': sum(c['matched'] for c in report['cases']), 'correct_regions': sum(c['correct'] for c in report['cases']),
                   'held_out_regions_not_scored': sum(c['split'] == 'held_out' for c in gold['cases'])})
    report['quality_passed'] = report['correct_regions']/len(selected) >= .9
    report['source_code_unchanged'] = all(digest(ROOT/p) == value for p, value in report['source_code_sha256'].items())
    report['invariants_passed'] = all(report['source_unchanged'].values()) and report['source_code_unchanged'] and report['socket_attempts'] == 0 and len(report['cases']) == len(selected)
    with (output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), **{k: report[k] for k in ['expected_regions', 'matched_regions', 'correct_regions', 'held_out_regions_not_scored', 'quality_passed', 'invariants_passed']}}), flush=True)


if __name__ == '__main__':
    evaluate()
