"""Native two-dimensional math: real PDF geometry, version and failure checks."""
from pathlib import Path
import hashlib
import json
import re

import pymupdf
import pytest

from research_workspace.formula_geometry import extract_display_formulas
from research_workspace.layout import LayoutService
from research_workspace.library import Library

ROOT = Path(__file__).resolve().parents[1]


def compact(value):
    return re.sub(r'\s+', '', value or '')


def native_fixture(rotation=0, unknown=False):
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=400, height=400)
        p.insert_text((40, 120), 'x =', fontsize=14, fontname='tiit')
        p.insert_text((75, 110), 'a + b', fontsize=14, fontname='tiit')
        p.draw_line((73, 116), (110, 116), width=.6)
        p.insert_text((86, 134), '@' if unknown else 'c', fontsize=14, fontname='tiit')
        p.insert_text((40, 180), 'Ordinary prose below must not become a denominator.', fontsize=10)
        p.set_rotation(rotation)
        return pdf.tobytes()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_fraction_positions_and_versioned_evidence(tmp_path, monkeypatch, rotation):
    monkeypatch.setenv('RESEARCH_OCR_ENABLED', '0')
    lib = Library(tmp_path)
    data = native_fixture(rotation)
    doc = lib.import_document(data, 'native-math.pdf')['document']
    lib.process_next_job()
    run = doc['current_run']['id']
    saved_before = lib.get_page(doc['id'], 1, run)
    result = LayoutService(lib).analyze_page(doc['id'], 1, run)
    formulas = [f for f in result['formulas'] if f.get('method') == 'native-math-geometry-v1']
    assert len(formulas) == 1
    formula = formulas[0]
    assert compact(formula['latex']) == r'x=\frac{a+b}{c}'
    assert formula['status'] == 'transcribed_needs_review' and formula['needs_review']
    assert formula['evidence']['document_id'] == doc['id']
    assert formula['evidence']['run_id'] == run and formula['evidence']['page'] == 1
    assert formula['glyph_count'] == len(formula['glyph_evidence']) == 6
    assert formula['rule_evidence'] and all(g['bbox'] for g in formula['glyph_evidence'])
    assert result['formula_audit']['model_calls'] == 0
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        p = pdf[0]
        rect = pymupdf.Rect(40, 96, 110, 138) * p.rotation_matrix
        expected = [rect.x0/p.rect.width, rect.y0/p.rect.height, rect.x1/p.rect.width, rect.y1/p.rect.height]
    assert formula['bbox'] == pytest.approx(expected, abs=.015)
    assert hashlib.sha256(lib._source_path(doc['id']).read_bytes()).digest() == hashlib.sha256(data).digest()
    assert lib.get_page(doc['id'], 1, run) == saved_before
    assert json.loads(LayoutService(lib).export_page_structure(doc['id'], 1, run))['formulas']


def test_unknown_glyph_retains_original_without_guessing():
    with pymupdf.open(stream=native_fixture(unknown=True), filetype='pdf') as pdf:
        formula = extract_display_formulas(pdf[0])[0][0]
    assert formula['latex'] is None
    assert formula['conversion'] == 'unmapped_native_glyph'
    assert '@' in formula['original_text']


def test_source_version_attention_fraction_scripts_and_nested_exponents():
    source = ROOT/'data/samples/1706.03762v1.pdf'
    if not source.exists():
        pytest.skip('Optional existing public paper sample unavailable')
    before = source.read_bytes()
    with pymupdf.open(source) as pdf:
        attention = extract_display_formulas(pdf[2])[0][0]
        assert compact(attention['latex']) == r'\operatorname{Attention}(Q,K,V)=\operatorname{softmax}(\frac{QK^{T}}{\sqrt{d_{k}}})V'
        multi, head = extract_display_formulas(pdf[3])[0]
        assert compact(multi['latex']) == r'\operatorname{MultiHead}(Q,K,V)=\operatorname{Concat}(\mathrm{head}_{1},...,\mathrm{head}_{h})'
        # This immutable v1 equation has no output projection W^O.
        assert 'W' not in multi['latex']
        assert r'W_{i}^{Q}' in compact(head['latex'])
        pe = extract_display_formulas(pdf[4])[0][1:]
        assert len(pe) == 2 and all(r'10000^{2i/d_{\mathrm{model}}}' in compact(f['latex']) for f in pe)
        rate = extract_display_formulas(pdf[6])[0][0]
        value = compact(rate['latex'])
        assert r'd_{\mathrm{model}}^{-0.5}' in value
        assert r'step\_num^{-0.5}' in value and r'warmup\_steps^{-1.5}' in value
        assert any(g['text'] == '_' for g in rate['glyphs'])
        assert sum(g['text'] in 'model' and g['font'].startswith('Nimbus') for g in rate['glyphs']) == 5
    assert source.read_bytes() == before


def test_plain_text_and_image_math_not_fabricated():
    with pymupdf.open() as pdf:
        p = pdf.new_page()
        p.insert_text((40, 90), 'A plain sentence without a mathematical expression.')
        assert extract_display_formulas(p)[0] == []
        pix = p.get_pixmap()
        with pymupdf.open() as scan:
            image = scan.new_page()
            image.insert_image(image.rect, stream=pix.tobytes('png'))
            values, audit = extract_display_formulas(image)
            assert values == [] and 'scanned_math' in audit['unsupported']


def test_multiline_and_mixed_prose_are_not_reported_as_complete_equations():
    source = ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf'
    if not source.exists():
        pytest.skip('Optional existing textbook sample unavailable')
    with pymupdf.open(source) as pdf:
        values = extract_display_formulas(pdf[31])[0]
        continued = [f for f in values if f['conversion'] == 'multiline_continuation_requires_review']
        assert len(continued) == 2 and all(f['latex'] is None for f in continued)
        values = extract_display_formulas(pdf[38])[0]
        prose = [f for f in values if f['conversion'] == 'prose_mixed_with_math']
        assert prose and all(f['latex'] is None for f in prose)
        values = extract_display_formulas(pdf[32])[0]
        limits = [f for f in values if f['conversion'] == 'large_operator_limits_require_review']
        assert limits and all(f['latex'] is None for f in limits)
