"""Whole equations must be composed from actual public-paper scan pixels."""
import hashlib
import pymupdf
import pytest
from research_workspace.raster_formulas import extract_raster_math, recheck_letter_entry
from research_workspace.scan_composition import valid_sequence
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from tests.test_raster_formulas import raster_page, configuration

ATTENTION=r'Attention(Q,K,V)=softmax(\frac{QK^{T}}{\sqrt{d_{k}}})V'
SIGMOID=r'sig[z]=\frac{1}{1+\operatorname{exp}[-z]}.'


def walk(node):
    yield node
    for child in node.get('operands',[]):yield from walk(child)


@pytest.mark.parametrize('filename,page,expected',[
    ('1706.03762v1.pdf',3,ATTENTION),
    ('UnderstandingDeepLearning_02_09_26_C.pdf',80,SIGMOID)])
def test_whole_equations_have_resolved_independent_nested_observations(configuration,filename,page,expected):
    with raster_page(filename,page,288) as pdf:
        formulas,_,audit=extract_raster_math(pdf[0],configuration)
    equation=next(f for f in formulas if f.get('scope')=='whole_equation' and f['latex']==expected)
    assert equation['coverage_audit']['unassigned_ink_pixels']==0
    nodes=list(walk(equation));assert all(n['latex'] is not None for n in nodes)
    assert all(n.get('needs_review') or n.get('ocr_audit',{}).get('needs_review') for n in nodes)
    assert audit['composition_audit']['ocr_calls']<=96 and audit['composition_audit']['model_calls']==0
    if page==3:
        assert any(n['latex']==r'\sqrt{d_{k}}' for n in nodes)
        assert any(n.get('role')=='subscript' and n['latex']=='k' for n in nodes)
        assert any(n.get('role')=='supscript' and n['latex']=='T' for n in nodes)


@pytest.mark.parametrize('erased',[pymupdf.Rect(380,707,384,719),pymupdf.Rect(366,717,377,728)])
def test_erased_closing_bracket_or_root_operand_cannot_be_completed(configuration,erased):
    with raster_page('1706.03762v1.pdf',3,288) as pdf:
        p=pdf[0];p.draw_rect(erased,color=None,fill=(1,1,1),overlay=True)
        formulas,_,_=extract_raster_math(p,configuration)
    assert not any(f.get('scope')=='whole_equation' for f in formulas)
    assert any(f.get('scope')=='incomplete_equation' and f['latex'] is None for f in formulas)


@pytest.mark.parametrize('rotation',[0,90])
def test_every_nested_result_and_audit_is_versioned_and_rotated(tmp_path,configuration,rotation):
    with raster_page('1706.03762v1.pdf',3,288) as pdf:
        pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'nested-scan.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];before=lib.get_page(doc['id'],1,run)
    result=LayoutService(lib).analyze_page(doc['id'],1,run)
    whole=next(f for f in result['formulas'] if f.get('scope')=='whole_equation')
    assert whole['latex']==ATTENTION
    for n in walk(whole):
        for item in [n,*([n['ocr_audit']] if n.get('ocr_audit') else [])]:
            assert 'raw_bbox' not in item
            assert item['evidence']['run_id']==run and item['evidence']['document_id']==doc['id']
            assert item['evidence']['bbox']==item['bbox'] and all(0<=x<=1 for x in item['bbox'])
    assert lib.get_page(doc['id'],1,run)==before and hashlib.sha256(lib._source_path(doc['id']).read_bytes()).digest()==hashlib.sha256(data).digest()


@pytest.mark.parametrize('text',['a=','a=(@','a=()','a=[x)','a=+b','a=b+','a==b','a=someprose@','a=(@))'])
def test_incomplete_or_prose_sequences_fail_closed(text):
    assert not valid_sequence(text,equation=True)


def test_low_resolution_letter_recheck_retains_conflict_and_height_evidence(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',461) as pdf:
        _,matrices,_=extract_raster_math(pdf[0],configuration)
    vector=next(m for m in matrices if [c['latex'] for c in m['cells']]==['b','c','a'])
    target=vector['cells'][1];audit=target['ocr_audit']
    assert [v['text'] for v in audit['views']]==['C','Cc']
    assert [v['text'] for v in audit['letter_recheck_views']]==['c','c']
    assert audit['case_geometry']['target_height']<audit['case_geometry']['ascender_height']*.8
    assert len(audit['case_geometry']['reference_regions'])==2


def test_blank_letter_or_missing_case_reference_stays_unknown(configuration):
    target={'latex':None,'column':0,'raw_bbox':[30,30,34,34.5],
        'ocr_audit':{'status':'unresolved','views':[{'text':'C'},{'text':'Cc'}]}}
    refs=[{'latex':'a','column':0,'raw_bbox':[30,50,34,54.5]}, {'latex':'b','column':0,'raw_bbox':[30,60,34,67]}]
    with pymupdf.open() as pdf:
        p=pdf.new_page()
        assert recheck_letter_entry(p,target,refs,configuration,{'remaining':2}) is None
        assert target['ocr_audit']['status']=='unresolved'
        budget={'remaining':2}
        assert recheck_letter_entry(p,target,[],configuration,budget) is None and budget['remaining']==2


def test_letter_recheck_reference_anchors_survive_rotation(tmp_path,configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',461,144) as pdf:
        pdf[0].set_rotation(90);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'letters.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];result=LayoutService(lib).analyze_page(doc['id'],1,run)
    cell=next(c for m in result['matrices'] for c in m['cells'] if c['ocr_audit'].get('case_geometry'))
    assert cell['latex']=='c'
    for reference in cell['ocr_audit']['case_geometry']['reference_regions']:
        assert 'raw_bbox' not in reference and reference['evidence']['run_id']==run
        assert reference['evidence']['bbox']==reference['bbox'] and all(0<=x<=1 for x in reference['bbox'])


def test_erasing_letter_cannot_turn_three_row_vector_into_complete_two_rows(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',461,144) as pdf:
        pdf[0].draw_rect(pymupdf.Rect(322,393,329,400),color=None,fill=(1,1,1),overlay=True)
        _,matrices,_=extract_raster_math(pdf[0],configuration)
    right=next(m for m in matrices if m['raw_bbox'][0]>310)
    assert right['latex'] is None and right['conversion']=='matrix_row_spacing_ambiguous'
    assert [c['latex'] for c in right['cells']]==['b','a']
