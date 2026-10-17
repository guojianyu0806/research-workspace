"""Pixel-only operator coverage, missing limits and rotation provenance."""
from pathlib import Path
import hashlib
import pymupdf
import pytest
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from research_workspace.raster_formulas import extract_raster_math
from research_workspace.scan_operators import pair_regions,shape
from tests.test_raster_formulas import raster_page,configuration


def test_existing_scan_recovers_actual_radical_and_transpose(configuration):
    with raster_page('1706.03762v1.pdf',3,dpi=288) as pdf:
        values,_,audit=extract_raster_math(pdf[0],configuration)
    extended=[v for v in values if v['method']=='raster-operators-v1']
    assert {v['latex'] for v in extended if v['latex']}=={r'\sqrt{d_{k}}',r'K^{T}'}
    assert all(v['scope']=='subexpression' for v in extended)
    assert audit['operator_audit']['model_calls']==0 and audit['operator_audit']['ocr_calls']<=80


def test_existing_scanned_sums_require_operator_and_each_limit(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,dpi=288) as pdf:
        values,_,_=extract_raster_math(pdf[0],configuration)
    sums=[v for v in values if v.get('observed_operator')==r'\sum']
    assert len(sums)==2 and all(v['latex']==r'\sum_{i=1}^{I}' for v in sums)
    for value in sums:
        assert value['scope']=='operator_with_limits' and value['status']=='partial_formula_needs_review'
        assert value['operator_audit']['status']=='two_view_candidate'
        assert [o['role'] for o in value['operands']]==['upper_limit','lower_limit']
        assert all(o['ocr_audit']['status']=='two_view_candidate' for o in value['operands'])


def test_erased_upper_limit_is_not_completed_from_the_other_sum(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,dpi=288) as pdf:
        page=pdf[0];page.draw_rect(pymupdf.Rect(267,590,274,599),color=None,fill=(1,1,1),overlay=True)
        values,_,_=extract_raster_math(page,configuration)
    sums=[v for v in values if v.get('observed_operator')==r'\sum']
    assert len(sums)==2
    first=min(sums,key=lambda v:v['raw_bbox'][1]);assert first['latex'] is None
    assert first['operands'][0]['latex'] is None and first['conversion']=='unresolved_sum_limits'


@pytest.mark.parametrize('rotation',[0,90])
def test_operator_and_operand_audits_share_their_rotated_source_regions(tmp_path,configuration,rotation):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,dpi=288) as pdf:
        pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'scan-sums.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
    before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
    sums=[f for f in result['formulas'] if f.get('observed_operator')==r'\sum'];assert len(sums)==2
    for f in sums:
        records=[f,f['operator_audit'],*f['operands'],*(o['ocr_audit'] for o in f['operands'])]
        for item in records:
            assert 'raw_bbox' not in item and all(0<=v<=1 for v in item['bbox'])
            assert item['evidence']['run_id']==run and item['evidence']['document_id']==doc['id']
            assert item['evidence']['bbox']==item['bbox'] and item['evidence']['source']=='immutable_pdf_raster_local_ocr'
    assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data


def test_pairing_cannot_link_rows_or_read_an_ordinary_descender_as_script():
    def c(box):return {'box':box,'points':[]}
    assert pair_regions([c([10,10,20,24]),c([21,100,27,108])])==[]
    # Ordinary p followed by t: the ascender/descender is not a superscript.
    assert pair_regions([c([10,10,18,26]),c([19,7,25,22])])==[]
