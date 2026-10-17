"""Recursive geometry uses explicitly derived fixtures and immutable sources."""
import pymupdf
import pytest
from tests.recursive_scan_fixtures import fixture
from tests.test_raster_formulas import configuration
from research_workspace.raster_formulas import extract_raster_math
from research_workspace.library import Library
from research_workspace.layout import LayoutService

EXPECTED={'double-root':r'\sqrt{\sqrt{d_{k}}}',
    'triple-root':r'\sqrt{\sqrt{\sqrt{d_{k}}}}',
    'fraction-in-root':r'\sqrt{\frac{1}{1+\operatorname{exp}[-z]}}',
    'nested-fraction':r'\frac{\frac{1}{1+\operatorname{exp}[-z]}}{1}'}


@pytest.mark.parametrize('kind',list(EXPECTED))
def test_explicitly_derived_nested_geometry_recovers_every_child(configuration,kind):
    with fixture(kind) as pdf:
        formulas,_,audit=extract_raster_math(pdf[0],configuration)
    item=next(f for f in formulas if f['method']=='raster-recursive-v1' and f['latex']==EXPECTED[kind])
    assert item['recursive_audit']['direct_structure_children']>=1
    assert item['scope']=='subexpression' and item['needs_review']
    a=audit['composition_audit']['recursive_audit']
    assert a['model_calls']==0 and a['ocr_calls']<=128 and a['parsed_structure_count']<=64


@pytest.mark.parametrize('kind,erased',[
    ('double-root',[91.5,66,104,68.5]),
    ('nested-fraction',[79,75,130,86])])
def test_missing_inner_roof_or_denominator_is_not_completed(configuration,kind,erased):
    with fixture(kind,erase=erased) as pdf:
        formulas,_,audit=extract_raster_math(pdf[0],configuration)
    assert not any(f['latex']==EXPECTED[kind] for f in formulas)
    assert audit['composition_audit']['recursive_audit']['candidate_regions']


@pytest.mark.parametrize('rotation',[0,90])
def test_recursive_children_candidates_and_audits_have_the_same_versioned_source(tmp_path,configuration,rotation):
    with fixture('nested-fraction') as pdf:
        pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'derived-nested-fraction.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];before=lib.get_page(doc['id'],1,run)
    result=LayoutService(lib).analyze_page(doc['id'],1,run)
    item=next(f for f in result['formulas'] if f['method']=='raster-recursive-v1' and f['latex']==EXPECTED['nested-fraction'])
    def walk(n):
        yield n
        if n.get('ocr_audit'):yield n['ocr_audit']
        for c in n.get('operands',[]):yield from walk(c)
    records=[*walk(item),*result['raster_math_audit']['composition_audit']['recursive_audit']['candidate_regions']]
    for n in records:
        assert 'raw_bbox' not in n and n['evidence']['document_id']==doc['id'] and n['evidence']['run_id']==run
        assert n['evidence']['bbox']==n['bbox'] and all(0<=v<=1 for v in n['bbox'])
    assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data
