"""Independent transcription expectations manually compared with raster page 376.

Expected strings are test data only; production receives no answers or native text.
"""
import pymupdf
import pytest
from tests.test_raster_formulas import raster_page,configuration
from research_workspace.scan_row_geometry import RowReader
from research_workspace.scan_multiline import aligned_row_fences
from research_workspace.raster_formulas import _pixel_crop

EXPECTED=[
    r'=\frac{(1-\alpha_{t-1})}{1-\alpha_{t}}\sqrt{1-\beta_{t}}z_{t}+\frac{\sqrt{\alpha_{t-1}}\beta_{t}}{1-\alpha_{t}}(\frac{1}{\sqrt{\alpha_{t}}}z_{t}-\frac{\sqrt{1-\alpha_{t}}}{\sqrt{\alpha_{t}}}\epsilon)',
    r'=\frac{(1-\alpha_{t-1})}{1-\alpha_{t}}\sqrt{1-\beta_{t}}z_{t}+\frac{\beta_{t}}{1-\alpha_{t}}(\frac{1}{\sqrt{1-\beta_{t}}}z_{t}-\frac{\sqrt{1-\alpha_{t}}}{\sqrt{1-\beta_{t}}}\epsilon),',
    r'=(\frac{(1-\alpha_{t-1})\sqrt{1-\beta_{t}}}{1-\alpha_{t}}+\frac{\beta_{t}}{(1-\alpha_{t})\sqrt{1-\beta_{t}}})z_{t}-\frac{\beta_{t}}{\sqrt{1-\alpha_{t}}\sqrt{1-\beta_{t}}}\epsilon',
    r'=(\frac{(1-\alpha_{t-1})(1-\beta_{t})}{(1-\alpha_{t})\sqrt{1-\beta_{t}}}+\frac{\beta_{t}}{(1-\alpha_{t})\sqrt{1-\beta_{t}}})z_{t}-\frac{\beta_{t}}{\sqrt{1-\alpha_{t}}\sqrt{1-\beta_{t}}}\epsilon',
    r'=\frac{(1-\alpha_{t-1})(1-\beta_{t})+\beta_{t}}{(1-\alpha_{t})\sqrt{1-\beta_{t}}}z_{t}-\frac{\beta_{t}}{\sqrt{1-\alpha_{t}}\sqrt{1-\beta_{t}}}\epsilon',
    r'=\frac{1-\alpha_{t}}{(1-\alpha_{t})\sqrt{1-\beta_{t}}}z_{t}-\frac{\beta_{t}}{\sqrt{1-\alpha_{t}}\sqrt{1-\beta_{t}}}\epsilon',
    r'=\frac{1}{\sqrt{1-\beta_{t}}}z_{t}-\frac{\beta_{t}}{\sqrt{1-\alpha_{t}}\sqrt{1-\beta_{t}}}\epsilon,',
]


def read_row(page,box,configuration,budget=384):
    reader=RowReader(page,box,configuration=configuration,max_calls=budget)
    value=reader.parse([0,0,reader.mask.shape[1],reader.mask.shape[0]])
    return value,reader


def test_real_seven_rows_are_independently_transcribed_from_own_pixels(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        page=pdf[0];pixels,_=_pixel_crop(page,page.rect,dpi=144)
        rows=[r for g in aligned_row_fences(pixels) for r in g]
        values=[]
        for r in rows:
            node,reader=read_row(page,r['raw_bbox'],configuration);values.append(node)
            assert node['coverage_audit']['all_children_resolved'] and node['coverage_audit']['unassigned_ink_pixels']==0
            assert reader.calls<=reader.max_calls and reader.nodes<=128
        assert [n['latex'] for n in values]==EXPECTED
        assert not page.get_text()


@pytest.mark.parametrize('erase',[
    [352,437,358.25,447], # actual beta base in last denominator
    [234,436.25,268.5,437.25], # radical roof
    [274.5,434.25,277.5,439.25], # z subscript t
    [316.75,440,323,445.25], # alpha base
    [291,434.25,361.5,435.5], # fraction rule
    [321.5,423.5,330.5,433], # beta_t numerator
    [368,436.25,370,440], # comma
])
def test_erased_ink_is_not_borrowed_from_other_rows(configuration,erase):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1))
        result,_=read_row(pdf[0],[222.5,423.5,369.5,447],configuration)
    assert result['latex']!=EXPECTED[-1]
    if erase==[352,437,358.25,447]:
        assert result['latex'] is None
        from scripts.verify_real_greek_scan import records
        assert any(n.get('ocr_audit',{}).get('status')=='unresolved_orphan_lowered_glyph' for n in records(result))


def test_exhausted_ocr_budget_keeps_the_row_unknown(configuration):
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        node,reader=read_row(pdf[0],[222.5,423.5,369.5,447],configuration,budget=0)
    assert node['latex'] is None and reader.calls==0


@pytest.mark.parametrize('rotation',[0,90])
def test_seven_row_exports_preserve_new_and_prior_versioned_audits(tmp_path,configuration,rotation):
    from research_workspace.library import Library
    from research_workspace.layout import LayoutService
    from scripts.verify_real_greek_scan import records
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'rows.pdf')['document'];lib.process_next_job()
    run=doc['current_run']['id'];before=lib.get_page(doc['id'],1,run)
    result=LayoutService(lib).analyze_page(doc['id'],1,run)
    groups=[f for f in result['formulas'] if f.get('method')=='raster-aligned-rows-v1']
    rows=sorted([r for g in groups for r in g['operands']],key=lambda r:r['source_row_number'])
    assert [r['latex'] for r in rows]==EXPECTED
    assert all(g['scope']=='transcribed_aligned_continuations' and not g['multiline_audit']['initial_expression_confirmed']
        and not g['multiline_audit']['mathematical_equivalence_verified'] for g in groups)
    for r in rows:
        assert r['row_audit']['full_row_resolved'] and r['row_audit']['ocr_calls']<=384
        assert r['prior_observations'] and r['equality_audit']
    for n in records(groups):
        assert 'raw_bbox' not in n
        if isinstance(n.get('evidence'),dict):
            e=n['evidence'];assert e['document_id']==doc['id'] and e['run_id']==run and e['page']==1
            assert e['bbox']==n['bbox'] and n['bbox'] and all(0<=v<=1 for v in n['bbox'])
    assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data
