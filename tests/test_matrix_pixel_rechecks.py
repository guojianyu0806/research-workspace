"""Numeric fallback must use the same cell pixels and retain old readings."""
import pymupdf
from research_workspace.raster_formulas import recheck_numeric_entry
from tests.test_raster_formulas import raster_page,configuration


def test_actual_low_resolution_one_recheck_preserves_initial_conflict(configuration):
    audit={'status':'unresolved','views':[{'text':'1'},{'text':'L'}]}
    with raster_page('UnderstandingDeepLearning_02_09_26_C.pdf',461) as pdf:
        value=recheck_numeric_entry(pdf[0],[242.25,404.5,244.75,411],configuration,{'remaining':4},audit)
    assert value=='1' and audit['status']=='pixel_rechecked_candidate'
    assert audit['views']==[{'text':'1'},{'text':'L'}] and audit['initial_status']=='unresolved'
    assert len(audit['recheck_views'])>=2


def test_blank_pixels_and_no_numeric_observation_never_supply_a_zero(configuration):
    with pymupdf.open() as pdf:
        p=pdf.new_page()
        audit={'status':'unresolved','views':[{'text':'0'},{'text':'O'}]}
        assert recheck_numeric_entry(p,[30,30,40,40],configuration,{'remaining':4},audit) is None
        assert audit['status']=='unresolved'
        unknown={'status':'unresolved','views':[{'text':'?'},{'text':''}]}
        budget={'remaining':4};assert recheck_numeric_entry(p,[30,30,40,40],configuration,budget,unknown) is None
        assert budget['remaining']==4
