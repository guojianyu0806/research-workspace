"""Real source glyph rechecks must retain their own ink and OCR evidence."""
from copy import deepcopy
import pytest
from tests.test_raster_formulas import raster_page,configuration
from research_workspace.scan_glyphs import corroborate_base,greek_recheck,punctuation
from research_workspace.raster_formulas import extract_raster_math


def base_node(box,latex=None,reading='x'):
    return {'latex':None,'geometry_audit':{'relation':'sub'},'operands':[
        {'role':'base','raw_bbox':box,'latex':latex,'original_text':'',
            'ocr_audit':{'views':[{'text':reading},{'text':reading}]}},
        {'role':'subscript','raw_bbox':[0,0,1,1],'latex':'i','original_text':'i'}]}


@pytest.mark.parametrize('target,reading',[
    ([337.75,640.5,342.5,647],'x'), # y ink cannot inherit x
    ([316.5,640.5,321.5,645],'q'), # shape alone cannot propose an alphabet candidate
    ([200,640,205,645],'x')])      # blank ink cannot borrow the reference
def test_same_page_shape_never_completes_missing_or_contradictory_ink(target,reading):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        refs=[];known=base_node([289.375,605.5,294.375,610.125],'x')
        corroborate_base(pdf[0],known,refs);assert len(refs)==1
        current=base_node(target,reading=reading);before=deepcopy(current)
        corroborate_base(pdf[0],current,refs)
    assert current==before


def test_greek_reader_cannot_discard_the_comma_beside_a_glyph(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        budget={'remaining':4}
        value,audit=greek_recheck(pdf[0],[299,603,309.5,612],configuration,budget)
        assert value is None and audit['status']=='multiple_disjoint_ink_regions'
        assert budget['remaining']==4
        comma=punctuation(pdf[0],[299,609,300,610],configuration)
        assert comma['latex']==',' and comma['raw_bbox'][3]>611


def test_real_greek_recheck_retains_two_actual_source_readings(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        value,audit=greek_recheck(pdf[0],[306.25,638,311.625,647],configuration,{'remaining':4})
    assert value==('ϕ',r'\varphi')
    assert audit['status']=='source_greek_two_view_candidate'
    assert audit['views'][-1]['normalized_text']==audit['views'][-2]['normalized_text']=='ϕ'
    assert len(audit['views'])<=4


@pytest.mark.parametrize('erase',[None,[261,383,267.5,389],[257,395,262.5,401]])
def test_real_root_fraction_uses_observed_alpha_and_script_run_not_missing_ink(configuration,erase):
    import pymupdf
    expected=r'\sqrt{\frac{\alpha_{t}}{\alpha_{t-1}}}'
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',387,288) as pdf:
        if erase:pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1))
        f,_,a=extract_raster_math(pdf[0],configuration)
    if erase:assert not any(n['latex']==expected for n in f)
    else:
        root=next(n for n in f if n['latex']==expected)
        assert root['scope']=='subexpression' and root['needs_review']
        assert root['operands'][0]['operands'][0]['operands'][1]['latex']==r'\alpha_{t-1}'
    assert a['composition_audit']['recursive_audit']['ocr_calls']<=128
