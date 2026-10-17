"""Actual public-paper pixels, no native text accessible to the scanner."""
from pathlib import Path
import pymupdf
import pytest
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from research_workspace.raster_formulas import extract_raster_math,consensus,scan_linear

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def configuration(monkeypatch):
    monkeypatch.setenv('RESEARCH_OCR_ENABLED','1')
    monkeypatch.setenv('RESEARCH_OCR_TESSDATA',str(ROOT/'data/ocr/tessdata'))
    monkeypatch.setenv('RESEARCH_OCR_LANG','eng')
    value=Library._ocr_configuration()
    if not value['available']:pytest.skip('Existing local OCR data absent')
    return value


def raster_page(filename,number,dpi=144):
    with pymupdf.open(ROOT/'data/samples'/filename) as native:
        p=native[number-1];image=p.get_pixmap(dpi=dpi)
        pdf=pymupdf.open();scan=pdf.new_page(width=p.rect.width,height=p.rect.height)
        scan.insert_image(scan.rect,stream=image.tobytes('png'))
        assert scan.get_text()==''
        return pdf


@pytest.mark.parametrize('rotation',[0,90])
def test_scanned_matrix_accepted_entries_and_unresolved_vectors_have_separate_anchors(tmp_path,configuration,rotation):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',461,dpi=288) as pdf:
        pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'pixel-matrix.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
    before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
    accepted=[m for m in result['matrices'] if m['latex']]
    numeric=next(m for m in accepted if m['column_count']==3)
    assert [c['latex'] for c in numeric['cells']]==['0','1','0','0','0','1','1','0','0']
    assert any(m['latex'] is None for m in result['matrices'])
    for m in result['matrices']:
        for c in [m,*m['cells']]:
            e=c['evidence'];assert e['document_id']==doc['id'] and e['run_id']==run and e['page']==1 and e['block_id'] is None
            assert e['source']=='immutable_pdf_raster_local_ocr' and e['bbox']==c['bbox'] and all(0<=v<=1 for v in c['bbox'])
            if c.get('ocr_audit'):assert len(c['ocr_audit']['views'])==2
    assert result['raster_math_audit']['ocr_calls']<=129 and result['raster_math_audit']['model_calls']==0
    assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data


def test_existing_fraction_is_explicitly_a_subexpression_with_both_observations(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',80,dpi=288) as pdf:
        values,_,audit=extract_raster_math(pdf[0],configuration)
    fractions=[v for v in values if v['latex'] and v.get('scope')=='subexpression']
    assert any(f['latex']==r'\frac{1}{1+\operatorname{exp}[-z]}' for f in fractions)
    for f in fractions:
        assert f['status']=='partial_formula_needs_review' and len(f['operands'])==2
        assert all(o['ocr_audit']['status']=='two_view_candidate' for o in f['operands'])
    assert audit['model_calls']==0


def test_lower_resolution_matrix_recovers_actual_digits_and_keeps_uncertain_letters(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',461) as pdf:
        _,matrices,_=extract_raster_math(pdf[0],configuration)
    numeric=next(m for m in matrices if m['column_count']==3)
    assert [c['latex'] for c in numeric['cells']]==['0','1','0','0','0','1','1','0','0']
    assert numeric['latex'] and numeric['segmentation_audit']['threshold']==210
    assert any(m['latex'] is None and any(c['latex'] is None for c in m['cells']) for m in matrices)


def test_scalar_assignment_does_not_flatten_a_displaced_subscript(configuration):
    with raster_page('1706.03762v1.pdf',7) as pdf:
        values,_,_=extract_raster_math(pdf[0],configuration)
    assert any(v['latex']==r'warmup\_steps=4000.' for v in values)
    # The raster's P_drop must not silently become the different name Parop.
    assert not any(v['latex'] and 'Parop' in v['latex'] for v in values)


def test_disabled_and_exhausted_budget_never_invent_math(configuration):
    import numpy as np
    with pymupdf.open() as pdf:
        page=pdf.new_page()
        f,m,a=extract_raster_math(page,{'enabled':False,'available':False})
        assert f==m==[] and a['status']=='unavailable'
    value,audit=consensus(np.full((20,20),255,dtype='uint8'),configuration,{'remaining':0})
    assert value is None and audit['status']=='not_processed_budget'
