"""Source-local initial expressions, with explicit erasure and separation fixtures."""
import json
import numpy as np
import pymupdf
import pytest
from tests.test_raster_formulas import raster_page,configuration
from tests.test_scan_row_geometry import INITIAL,read_row
from research_workspace.raster_formulas import _pixel_crop
from research_workspace.scan_initial_equations import initial_candidates
from research_workspace.scan_multiline import aligned_row_fences
from research_workspace.scan_row_geometry import components,crossing_diagonals,join_operator_specks
from scripts.verify_real_greek_scan import records


@pytest.mark.parametrize('index',[0,1])
def test_real_initial_is_independently_read_without_native_text_or_following_answers(configuration,index):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        page=pdf[0];pixels,_=_pixel_crop(page,page.rect,dpi=144)
        rows=aligned_row_fences(pixels)[index];candidates,audit=initial_candidates(pixels,rows)
        assert audit['candidate_count']==1
        node,reader=read_row(page,candidates[0]['raw_bbox'],configuration)
        assert not page.get_text() and node['latex']==INITIAL and '=' not in node['latex']
        assert node['coverage_audit']['all_children_resolved'] and node['coverage_audit']['unassigned_ink_pixels']==0
        assert reader.calls<=384 and reader.nodes<=128
        crossing=[n['ocr_audit']['latin_greek_crossing_audit'] for n in records(node)
            if n.get('ocr_audit',{}).get('latin_greek_crossing_audit')]
        assert len(crossing)==1 and crossing[0]['latin_candidate']=='x'
        assert all(v['crossing_observed'] for v in crossing[0]['geometry_views'])
        assert crossing[0]['raw_bbox'][0]>(290 if index==0 else 280)
        assert any(n.get('latex')==r'\sqrt{1-\beta_{t}}' for n in records(node))
        if index==1:
            assert any(n.get('geometry_audit',{}).get('diagonally_connected_operator_speck_pixels')==2 for n in records(node))
        json.dumps(node) # audits must not leak NumPy scalar types


@pytest.mark.parametrize('erase',[
    [293,164.5,300,172], # final Latin x
    [150,168,196,179], # first denominator
    [199,160,234,162], # first radical roof
])
def test_removed_initial_ink_cannot_be_completed_from_the_identical_other_group(configuration,erase):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1))
        node,_=read_row(pdf[0],[150.5,155.5,299.5,178.5],configuration)
    assert node['latex']!=INITIAL


@pytest.mark.parametrize('fixture',['erase_initial','corridor_ink'])
def test_absent_initial_or_intervening_ink_does_not_attach_next_available_expression(fixture):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        # Keep continuation geometry from the unmodified page, then mutate only
        # the initial band/corridor; there is no equation template in production.
        pixels,_=_pixel_crop(pdf[0],pdf[0].rect,dpi=144);rows=aligned_row_fences(pixels)
        if fixture=='erase_initial':pdf[0].draw_rect(pymupdf.Rect(150,155,301,179),color=None,fill=(1,1,1))
        else:pdf[0].draw_rect(pymupdf.Rect(400,180,401,181),color=None,fill=(0,0,0))
        changed,_=_pixel_crop(pdf[0],pdf[0].rect,dpi=144)
        assert initial_candidates(changed,rows[0])[0]==[]
        assert len(initial_candidates(changed,rows[1])[0])==1


@pytest.mark.parametrize('rotation',[0,90])
def test_erased_initial_x_cannot_create_false_table_and_hide_the_seven_continuations(tmp_path,configuration,rotation):
    from research_workspace.library import Library
    from research_workspace.layout import LayoutService
    from tests.test_scan_row_geometry import EXPECTED
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        pdf[0].draw_rect(pymupdf.Rect(293,164.5,300,172),color=None,fill=(1,1,1));pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'erased-first-x.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];result=LayoutService(lib).analyze_page(doc['id'],1,run)
    groups=sorted([n for n in result['formulas'] if n.get('method')=='raster-aligned-rows-v1'],key=lambda g:g['operands'][0]['source_row_number'])
    assert not result['tables'] and len(groups)==2
    assert groups[0]['initial_expression']['latex']!=INITIAL and groups[1]['initial_expression']['latex']==INITIAL
    assert [r['latex'] for g in groups for r in g['operands']]==EXPECTED
    rejected=result['scan_table_audit']['rejected_candidates']
    assert rejected and all(n['evidence']['run_id']==run and n['bbox']==n['evidence']['bbox'] for n in rejected)
    assert all('raw_bbox' not in n for n in records(result))


def test_crossing_requires_four_arms_and_no_vertical_stem():
    mask=np.zeros((24,28),dtype=bool)
    for y in range(24):
        x=round(y*25/23);mask[y,x:x+3]=True;mask[y,25-x:28-x]=True
    assert crossing_diagonals(mask)[0]
    assert not crossing_diagonals(np.zeros_like(mask))[0]
    stem=mask.copy();stem[:,0:3]=True
    assert not crossing_diagonals(stem)[0]
    missing=mask.copy();missing[:8,:12]=False
    assert not crossing_diagonals(missing)[0]


@pytest.mark.parametrize('gap',[0,1])
def test_operator_speck_join_preserves_exact_pixels_and_requires_actual_diagonal_neighbour(gap):
    mask=np.zeros((32,45),dtype=bool);mask[1,5:40]=True;mask[1:26,5]=True
    mask[26+gap,4]=True
    before=set(map(tuple,np.argwhere(mask)))
    joined=join_operator_specks(components(mask))
    assert set(p for c in joined for p in c['points'])==before
    assert len(joined)==(1 if gap==0 else 2)
    assert sum(c.get('joined_operator_speck_pixels',0) for c in joined)==(1 if gap==0 else 0)
