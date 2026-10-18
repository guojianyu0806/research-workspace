"""Whole sum equations retain observed LHS, equality and every row source."""
import pymupdf
import pytest
from research_workspace.raster_formulas import extract_raster_math
from tests.test_raster_formulas import raster_page,configuration

FIRST=r'L[\varphi]=\sum_{i=1}^{I}(f[x_{i},\varphi]-y_{i})^{2}'
SECOND=r'=\sum_{i=1}^{I}(\varphi_{0}+\varphi_{1}x_{i}-y_{i})^{2}.'
ALIGNED=r'\begin{aligned}'+FIRST.replace('=','&=',1)+r' \\ '+SECOND.replace('=','&=',1)+r'\end{aligned}'


def values(configuration,erased=None,extra_ink=False):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        if erased:pdf[0].draw_rect(pymupdf.Rect(erased),color=None,fill=(1,1,1))
        if extra_ink:
            for y in (606,608):pdf[0].draw_rect(pymupdf.Rect(238,y,242,y+.6),color=None,fill=(0,0,0))
        f,_,a=extract_raster_math(pdf[0],configuration)
    return [n for n in f if n['method']=='raster-sum-equations-v1'],a


def test_real_sum_recovers_the_complete_function_equation_and_two_observed_rows(configuration):
    f,a=values(configuration)
    first=next(n for n in f if n['latex']==FIRST);assert first['scope']=='whole_equation'
    assert [n['role'] for n in first['operands']]==['left_hand_side','equality','right_hand_side']
    assert first['operands'][0]['latex']==r'L[\varphi]'
    assert first['operands'][1]['ocr_audit']['method']=='two_aligned_horizontal_ink_strokes'
    full=next(n for n in f if n['latex']==ALIGNED)
    assert [r['latex'] for r in full['operands']]==[FIRST,SECOND]
    assert full['alignment_audit']['all_rows_resolved'] and full['alignment_audit']['initial_lhs_observed']
    assert full['alignment_audit']['mathematical_equivalence_verified'] is False
    assert all(r['coverage_audit']['all_children_resolved'] and r['coverage_audit']['unassigned_ink_pixels']==0 for r in full['operands'])
    assert a['composition_audit']['sum_equation_audit']['ocr_calls']<=96


@pytest.mark.parametrize('erased',[
    [216,602,223,613], # function L
    [226,602,232.5,613], # LHS phi
    [232.5,602,235,613], # closing bracket
    [246,605,253,610], # first equality
    [246,640,253,645], # continuation equality
    [267,590,274,599], # first sum upper limit
    [279,597,341,619]]) # first summand
def test_missing_original_ink_is_not_completed_from_the_other_row(configuration,erased):
    f,_=values(configuration,erased)
    assert not any(n['latex']==ALIGNED for n in f)
    if erased[1]<630:assert not any(n['latex']==FIRST for n in f)


def test_extra_equality_between_lhs_and_relation_blocks_a_complete_equation(configuration):
    f,_=values(configuration,extra_ink=True)
    assert not any(n['latex'] in (FIRST,ALIGNED) for n in f)
    assert any(n['scope']=='incomplete_equation' for n in f)


@pytest.mark.parametrize('rotation',[0,90])
def test_whole_sum_rows_and_all_audits_share_rotated_immutable_source(tmp_path,configuration,rotation):
    from research_workspace.library import Library
    from research_workspace.layout import LayoutService
    from scripts.verify_real_greek_scan import records
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'real-complete-sum.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];before=lib.get_page(doc['id'],1,run)
    result=LayoutService(lib).analyze_page(doc['id'],1,run)
    full=next(n for n in result['formulas'] if n['latex']==ALIGNED)
    for node in records(full):
        assert 'raw_bbox' not in node
        if isinstance(node.get('evidence'),dict):
            e=node['evidence'];assert e['document_id']==doc['id'] and e['run_id']==run and e['page']==1
            assert e['bbox']==node['bbox'] and all(0<=v<=1 for v in node['bbox'])
    assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data


def test_closing_bracket_requires_two_observed_arms_not_balance_inference(configuration):
    from research_workspace.scan_glyphs import bracket_recheck
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        bracket=bracket_recheck(pdf[0],[233,602.5,234.5,612.5]);assert bracket['latex']==']'
        assert len(bracket['ocr_audit']['bracket_geometry_views'])==2
        pdf[0].draw_rect(pymupdf.Rect(232.5,602,235,604),color=None,fill=(1,1,1))
        assert bracket_recheck(pdf[0],[233,602.5,234.5,612.5]) is None


def test_beta_requires_own_greek_observations_and_visible_descender(configuration):
    from research_workspace.scan_glyphs import beta_recheck
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        value,audit,_=beta_recheck(pdf[0],[418.25,383,423.625,392],configuration,{'remaining':2})
    assert value==r'\beta' and all(v['normalized_text']=='β' for v in audit['views'])
    assert all(v['descender_observed'] for v in audit['descender_views'])


def test_complex_derivation_recovers_each_row_without_inventing_initial_expression(configuration):
    from scripts.verify_real_greek_scan import records
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        formulas,_,audit=extract_raster_math(pdf[0],configuration)
    groups=[f for f in formulas if f['method']=='raster-aligned-rows-v1']
    assert [len(g['operands']) for g in groups]==[2,5]
    rows=[r for g in groups for r in g['operands']]
    assert all(r['coverage_audit']['all_children_resolved'] and r['latex'] and r['row_audit']['full_row_resolved'] for r in rows)
    assert all(g['scope']=='transcribed_aligned_continuations' and not g['multiline_audit']['initial_expression_confirmed'] for g in groups)
    assert any(r['row_recursive_audit']['parsed_structure_count'] for r in rows)
    nodes=list(records(groups));assert any(n.get('latex')==r'\sqrt{1-\beta_{t}}' for n in nodes)
    assert not any(n.get('latex')==r'\sqrt{1-B_{t}}' for n in nodes)
    assert audit['composition_audit']['multiline_audit']['ocr_calls']<=256
