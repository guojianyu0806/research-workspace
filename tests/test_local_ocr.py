"""Real local OCR rechecks and conservative retention of source observations."""
from pathlib import Path
import pymupdf
import pytest
from research_workspace.library import Library
from research_workspace.local_ocr import numeric_rechecks, empty_artifact_checks, refine_numeric_cell


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    monkeypatch.setenv('RESEARCH_OCR_TESSDATA', str(Path(__file__).resolve().parents[1]/'data/ocr/tessdata'))
    monkeypatch.setenv('RESEARCH_OCR_LANG','eng')
    value=Library(tmp_path)._ocr_configuration()
    if not value['available']:pytest.skip('Project-local OCR data unavailable')
    return value


def test_recheck_reads_actual_pixels_and_retains_the_suspect_reading(configuration):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=300,height=200)
        p.insert_text((60,80),'4.91',fontsize=16,fontname='tiro')
        observed=[{'text':'4.9]','bbox':[59,63,92,85]}]
        budget={'remaining':2}
        corrected,audit=numeric_rechecks(p,observed,configuration,budget)
    assert observed[0]['text']=='4.9]'
    assert corrected[0]['text']=='4.91' and audit[0]['original_text']=='4.9]'
    assert len(audit[0]['views'])==2 and {v['dpi'] for v in audit[0]['views']}=={288,432}
    assert all(v['text']=='4.91' for v in audit[0]['views'])
    assert budget['remaining']==0 and audit[0]['needs_review']


def test_brackets_and_budget_exhaustion_never_force_numeric_replacement(configuration):
    with pymupdf.open() as pdf:
        p=pdf.new_page()
        bracket=[{'text':'[4.9]','bbox':[40,40,80,70]}]
        budget={'remaining':2}
        result,audit=numeric_rechecks(p,bracket,configuration,budget)
        assert result==bracket and not audit and budget['remaining']==2
        observed=[{'text':'4.9]','bbox':[40,40,80,70]}]
        result,audit=numeric_rechecks(p,observed,configuration,{'remaining':0})
        assert result==observed and audit[0]['status']=='not_processed_budget'


def test_real_equal_sign_kept_while_border_only_observation_is_audited():
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=300,height=200)
        p.insert_text((60,80),'=',fontsize=16)
        actual={'text':'=','bbox':[59,63,75,85]}
        result,audit=empty_artifact_checks(p,[actual],pymupdf.Rect(40,40,160,110))
        assert result==[actual] and audit==[]
        p.draw_line((180,60),(220,60))
        p.draw_line((180,100),(220,100))
        artifact={'text':'=','bbox':[190,59,193,101]}
        result,audit=empty_artifact_checks(p,[artifact],pymupdf.Rect(181,62,219,98))
        assert result==[] and audit[0]['original_text']=='=' and audit[0]['minimum_gray']==255


def test_sparse_mode_cannot_delete_isolated_numbers_or_merge_two_words(monkeypatch):
    legacy=[{'text':'2','bbox':[10,10,15,20]}, {'text':'4','bbox':[10,30,15,40]},
            {'text':'433','bbox':[40,50,60,60]}, {'text':'26.4','bbox':[75,50,95,60]}]
    sparse=[{'text':'4.33','bbox':[40,50,60,60]}, {'text':'26.4','bbox':[75,50,95,60]}]
    monkeypatch.setattr('research_workspace.local_ocr.words',lambda *a,**k:sparse)
    revised,audit=refine_numeric_cell(None,legacy,{},(0,0))
    assert [w['text'] for w in revised]==['2','4','4.33','26.4']
    assert audit[0]['original_text']=='433' and legacy[2]['text']=='433'
    monkeypatch.setattr('research_workspace.local_ocr.words',lambda *a,**k:[{'text':'4.3326.4','bbox':[40,50,95,60]}])
    assert refine_numeric_cell(None,legacy,{},(0,0))[0]==legacy
