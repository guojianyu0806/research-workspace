"""Summand structure stays separate from limit checks and unresolved glyphs."""
import pymupdf
import pytest
from tests.recursive_scan_fixtures import ascii_sum_fixture
from tests.test_raster_formulas import raster_page,configuration
from research_workspace.raster_formulas import extract_raster_math
from research_workspace.library import Library
from research_workspace.layout import LayoutService

EXPECTED=r'\sum_{i=1}^{I}(x_{i}-y_{i})^{2}'


def nodes(n):
    yield n
    for c in n.get('operands',[]):yield from nodes(c)


def test_explicitly_derived_ascii_squared_error_sum_has_independent_children(configuration):
    with ascii_sum_fixture() as pdf:
        f,_,audit=extract_raster_math(pdf[0],configuration)
    value=next(n for n in f if n['method']=='raster-summands-v1' and n['latex']==EXPECTED)
    parts=list(nodes(value))
    assert {r'x_{i}',r'y_{i}',r')^{2}'} <= {n['latex'] for n in parts if n['latex']}
    assert value['scope']=='summation_expression' and value['status']=='partial_formula_needs_review'
    assert audit['composition_audit']['summand_audit']['ocr_calls']<=160


def test_real_paper_sums_recover_greek_comma_and_complete_independent_summands(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        f,_,_=extract_raster_math(pdf[0],configuration)
    values=[n for n in f if n['method']=='raster-summands-v1']
    assert [n['latex'] for n in values]==[
        r'\sum_{i=1}^{I}(f[x_{i},\varphi]-y_{i})^{2}',
        r'\sum_{i=1}^{I}(\varphi_{0}+\varphi_{1}x_{i}-y_{i})^{2}.']
    for index,n in enumerate(values):
        parts=list(nodes(n));observed={p['latex'] for p in parts if p['latex']}
        assert r'y_{i}' in observed and r')^{2}' in observed
        assert (r'x_{i}' if index==0 else r'\varphi_{0}') in observed
        assert all(p['latex'] is not None for p in parts)
        assert n['scope']=='summation_expression' and n['needs_review']
        # The two original independent limits are not replaced by a whole answer.
        assert n['operands'][0]['latex']==r'\sum_{i=1}^{I}'
    first=list(nodes(values[0]));second=list(nodes(values[1]))
    comma=next(p for p in first if p['latex']==',')
    assert comma['ocr_audit']['method']=='two_threshold_punctuation_ink_geometry'
    assert len(comma['ocr_audit']['geometry_views'])==2
    matched=next(p for p in second if p.get('ocr_audit',{}).get('glyph_reference'))
    assert matched['latex']=='x'
    ref=matched['ocr_audit']['glyph_reference']
    assert ref['own_ocr_candidate_required'] and ref['cosine_similarity']>=.9
    assert ref['raw_bbox'][1]<matched['raw_bbox'][1]


@pytest.mark.parametrize('erase,reason',[
    ([267,590,274,599],'operator_limits'),
    ([279,597,364,619],'summand')])
def test_missing_upper_or_summand_cannot_borrow_from_other_row(configuration,erase,reason):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1),overlay=True)
        f,_,_=extract_raster_math(pdf[0],configuration)
    values=sorted([n for n in f if n['method']=='raster-summands-v1'],key=lambda n:n['raw_bbox'][1]);assert len(values)==2
    first=values[0];assert first['latex'] is None
    if reason=='summand':
        assert first['conversion']=='summand_not_observed' and first['operands'][1]['ocr_audit']['status']=='not_observed'
        assert first['operands'][1]['raw_bbox'][3]<values[1]['raw_bbox'][1]
    else:assert first['operands'][0]['operands'][0]['latex'] is None


@pytest.mark.parametrize('rotation',[0,90])
def test_sum_child_and_audit_anchors_remain_versioned_after_rotation(tmp_path,configuration,rotation):
    with ascii_sum_fixture() as pdf:
        pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'derived-square-sum.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];before=lib.get_page(doc['id'],1,run)
    result=LayoutService(lib).analyze_page(doc['id'],1,run)
    value=next(n for n in result['formulas'] if n['method']=='raster-summands-v1' and n['latex']==EXPECTED)
    for n in nodes(value):
        records=[n,*([n['ocr_audit']] if n.get('ocr_audit') else [])]
        for p in records:
            assert 'raw_bbox' not in p and p['evidence']['document_id']==doc['id'] and p['evidence']['run_id']==run
            assert p['evidence']['bbox']==p['bbox'] and all(0<=x<=1 for x in p['bbox'])
    assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data
