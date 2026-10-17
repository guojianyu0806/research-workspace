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
    formulas = [f for f in result['formulas'] if f.get('method') == 'native-math-geometry-v2']
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


def test_aligned_rows_recover_completely_while_mixed_prose_remains_unsupported():
    source = ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf'
    if not source.exists():
        pytest.skip('Optional existing textbook sample unavailable')
    with pymupdf.open(source) as pdf:
        values = extract_display_formulas(pdf[31])[0]
        continued = [f for f in values if f['conversion'] == 'complete_aligned_rows_with_glyph_coverage']
        assert len(continued) == 1 and len(continued[0]['source_rows']) == 2
        assert compact(continued[0]['latex']) == r'\begin{aligned}y&=f[x,\boldsymbol{\varphi}]\\&=\varphi_{0}+\varphi_{1}x.\end{aligned}'
        values = extract_display_formulas(pdf[38])[0]
        prose = [f for f in values if f['conversion'] == 'prose_mixed_with_math']
        assert prose and all(f['latex'] is None for f in prose)
        values = extract_display_formulas(pdf[32])[0]
        assert len(values)==1
        assert compact(values[0]['latex']) == r'\begin{aligned}L[\boldsymbol{\varphi}]&=\sum_{i=1}^{I}(f[x_{i},\boldsymbol{\varphi}]-y_{i})^{2}\\&=\sum_{i=1}^{I}(\varphi_{0}+\varphi_{1}x_{i}-y_{i})^{2}.\end{aligned}'
        mappings=[g['glyph_mapping'] for g in values[0]['glyphs'] if g.get('glyph_mapping')]
        assert len(mappings)==2 and all(m['glyph_name']=='summationdisplay' and m['native_text']=='X' and m['mapped_text']=='∑' for m in mappings)
        assert not any(compact(f['latex'])=='i=1' for f in values)
        values,audit=extract_display_formulas(pdf[71])
        product=next(f for f in values if f['latex'] and r'\prod' in f['latex'])
        assert compact(product['latex'])==r'Pr(\mathbf{y}_{1},\mathbf{y}_{2},...,\mathbf{y}_{I}\vert\mathbf{x}_{1},\mathbf{x}_{2},...,\mathbf{x}_{I})=\prod_{i=1}^{I}Pr(\mathbf{y}_{i}\vert\mathbf{x}_{i}).'
        assert any(m['glyph_name']=='productdisplay' and m['mapped_text']=='∏' for m in audit['glyph_mapping_audit'])


def test_orphan_continuations_and_intervening_prose_are_not_joined():
    with pymupdf.open() as pdf:
        p=pdf.new_page()
        p.insert_text((40,80),'x = a + b',fontsize=14,fontname='tiit')
        p.insert_text((40,105),'A paragraph separates the equations.',fontsize=12)
        p.insert_text((49,130),'= c + d',fontsize=14,fontname='tiit')
        p.insert_text((40,210),'z = a + c',fontsize=14,fontname='tiit')
        values=extract_display_formulas(p)[0]
    assert len(values)==3
    assert values[0]['latex'] and values[1]['latex'] is None and values[2]['latex']
    assert values[1]['conversion']=='orphan_or_ambiguous_continuation'
    assert not any(f.get('source_rows') for f in values)


@pytest.mark.parametrize('rotation',[0,90])
def test_named_summations_and_each_aligned_row_have_versioned_anchors(tmp_path,monkeypatch,rotation):
    source=ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf'
    if not source.exists():pytest.skip('Optional existing textbook absent')
    monkeypatch.setenv('RESEARCH_OCR_ENABLED','0')
    lib=Library(tmp_path)
    with pymupdf.open(source) as original,pymupdf.open() as one:
        one.insert_pdf(original,from_page=32,to_page=32);one[0].set_rotation(rotation)
        data=one.tobytes()
    doc=lib.import_document(data,'loss-equations.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];saved=lib.get_page(doc['id'],1,run)
    result=LayoutService(lib).analyze_page(doc['id'],1,run)
    formula=next(f for f in result['formulas'] if f.get('source_rows'))
    assert len(formula['source_rows'])==2 and formula['latex'].count(r'\sum')==2
    for row in formula['source_rows']:
        assert row['evidence']['document_id']==doc['id'] and row['evidence']['run_id']==run
        assert row['evidence']['page']==1 and row['bbox']==row['evidence']['bbox']
        assert all(0<=v<=1 for v in row['bbox']) and 'raw_bbox' not in row
    assert any(g['native_text']=='X' and g['text']=='∑' for g in formula['glyph_evidence'])
    assert lib._source_path(doc['id']).read_bytes()==data and lib.get_page(doc['id'],1,run)==saved
