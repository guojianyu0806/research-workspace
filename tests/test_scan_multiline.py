"""Alignment is a spatial audit, never a license to complete a derivation."""
import numpy as np
import pymupdf
from tests.test_raster_formulas import raster_page,configuration
from research_workspace.scan_multiline import aligned_row_fences,within_row_fence,audit_aligned_rows


def pixels(page):
    im=page.get_pixmap(dpi=144,colorspace=pymupdf.csGRAY,alpha=False)
    return np.frombuffer(im.samples,dtype='uint8').reshape(im.height,im.stride)[:,:im.width]


def test_real_multiline_derivation_has_seven_independent_rows_without_invented_lhs():
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        groups=aligned_row_fences(pixels(pdf[0]))
    assert [len(g) for g in groups]==[2,5]
    for rows in groups:
        for a,b in zip(rows,rows[1:]):assert a['fence_box'][3]<=b['fence_box'][1]
        assert within_row_fence(rows[0]['raw_bbox'],[rows])
        assert not within_row_fence([rows[0]['raw_bbox'][0],rows[0]['raw_bbox'][1],rows[-1]['raw_bbox'][2],rows[-1]['raw_bbox'][3]],[rows])
    results=audit_aligned_rows(groups,[])
    assert all(r['latex'] is None and not r['multiline_audit']['initial_expression_confirmed'] for r in results)
    assert all(r['latex'] is None for g in results for r in g['operands'])


def test_missing_middle_equals_does_not_join_across_the_unobserved_row():
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',376,288) as pdf:
        pdf[0].draw_rect(pymupdf.Rect(221,377,230,383),color=None,fill=(1,1,1))
        groups=aligned_row_fences(pixels(pdf[0]))
    assert [len(g) for g in groups]==[2,2,2]
    assert not any(g[0]['raw_bbox'][1]<380<g[-1]['raw_bbox'][3] for g in groups)


def test_crossing_observation_is_retained_as_rejected_not_assigned_to_adjacent_rows():
    group=[[{'raw_bbox':[20,20,60,40],'equality_box':[20,28,26,30],'fence_box':[20,18,60,42]},
        {'raw_bbox':[20,45,60,65],'equality_box':[20,53,26,55],'fence_box':[20,42,60,68]}]]
    crossing={'raw_bbox':[30,30,50,55],'scope':'subexpression','latex':None,'original_text':'?'}
    result=audit_aligned_rows(group,[crossing])[0]
    assert all(not r['operands'] and r['rejected_regions'][0]['reason']=='crosses_observed_row_fence' for r in result['operands'])


def test_repeated_sum_limit_equalities_cannot_be_grouped_as_derivation_rows():
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',33,288) as pdf:
        groups=aligned_row_fences(pixels(pdf[0]))
    assert len(groups)==1
    assert all(r['equality_box'][0]<250 for r in groups[0])


def test_glyph_references_greek_rechecks_and_aligned_rows_have_rotated_versioned_sources(tmp_path,configuration):
    from research_workspace.library import Library
    from research_workspace.layout import LayoutService
    lib=Library(tmp_path)
    for number in (33,376,387):
        with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',number,288) as pdf:
            pdf[0].set_rotation(90);data=pdf.tobytes()
        doc=lib.import_document(data,f'real-{number}.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
        before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
        count={'source_greek_recheck':0,'glyph_reference':0,'equality_audit':0}
        def walk(n):
            if not isinstance(n,dict):return
            if 'raw_bbox' in n:raise AssertionError('unconverted source region')
            if 'evidence' in n:
                e=n['evidence'];assert e['document_id']==doc['id'] and e['run_id']==run and e['page']==1
                assert e['bbox']==n['bbox'] and all(0<=v<=1 for v in n['bbox'])
            for k,v in n.items():
                if k in count:count[k]+=1
                if isinstance(v,dict):walk(v)
                elif isinstance(v,list):
                    for c in v:walk(c)
        walk(result)
        assert (count['glyph_reference']>0 if number==33 else count['equality_audit']==7 if number==376 else count['source_greek_recheck']>=0)
        assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data
