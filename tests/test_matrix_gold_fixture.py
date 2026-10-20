"""Prevent gold edits, work leakage and incorrect original-page anchors."""
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'tests/fixtures/matrix_gold/20261020-v2'
MANIFEST_SHA = 'e41a21f47df490173e26d7c47a975fba6e29f9aa95d40766f865171a75d6f40b'


def load():
    data = (FIXTURE/'manifest.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == MANIFEST_SHA
    return json.loads(data)


def test_frozen_gold_and_visual_review_are_bound_to_same_regions():
    gold = load()
    review = json.loads((FIXTURE/'visual-review.json').read_text(encoding='utf-8'))
    assert review['manifest_sha256'] == MANIFEST_SHA
    assert review['reviewer'] == 'Agent visual inspection; not independent human review'
    assert set(review['reviewed_ids']) == {c['id'] for c in gold['cases']}
    assert len(review['reviewed_ids']) == len(gold['cases']) == 24
    assert gold['status'] == 'clear_native_foundation_not_full_scan_acceptance'
    assert not review['parser_used_to_define_gold']


def test_whole_source_split_and_rectangular_cell_labels():
    gold = load()
    assert len(gold['sources']) == 4
    assert sum(c['split'] == 'held_out' for c in gold['cases']) == 11
    for c in gold['cases']:
        assert c['split'] == gold['sources'][c['source']]['split']
        assert c['expected_rows'] == len(c['expected_cells'])
        assert all(len(row) == c['expected_columns'] and all(isinstance(v, str) and v for v in row)
                   for row in c['expected_cells'])
        x0, y0, x1, y1 = c['bbox']
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert {'square', 'round'} == {c['delimiter'] for c in gold['cases']}
    assert any(c['expected_rows'] == 1 for c in gold['cases'])
    assert any(c['expected_columns'] == 3 and c['expected_rows'] == 3 for c in gold['cases'])


def test_available_originals_match_frozen_physical_pages_and_crops():
    gold = load()
    missing = [s['path'] for s in gold['sources'].values() if not (ROOT/s['path']).exists()]
    if missing:
        pytest.skip('Optional public PDFs absent: '+', '.join(missing))
    for key, source in gold['sources'].items():
        path = ROOT/source['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
        with pymupdf.open(path) as pdf:
            assert len(pdf) == source['pages']
            for c in gold['cases']:
                if c['source'] != key:
                    continue
                page = pdf[c['physical_page']-1]
                expected = [v/(page.rect.width if i%2 == 0 else page.rect.height)
                            for i, v in enumerate(c['bbox_points'])]
                assert c['bbox'] == pytest.approx(expected)
                crop = page.get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=pymupdf.Rect(c['bbox_points']), alpha=False)
                assert hashlib.sha256(crop.tobytes('png')).hexdigest() == c['crop']['sha256']
