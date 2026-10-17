"""Local raster math tests with independent labels and existing-paper pixels."""
from pathlib import Path
import pymupdf
import pytest
from research_workspace.library import Library
from research_workspace.scan_math import header_scripts,header_scales,repeated_script


@pytest.fixture
def configuration(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'data/ocr/tessdata'
    monkeypatch.setenv('RESEARCH_OCR_TESSDATA',str(path))
    monkeypatch.setenv('RESEARCH_OCR_LANG','eng')
    value=Library(tmp_path)._ocr_configuration()
    if not value['available']:pytest.skip('Project local OCR data absent')
    return value


def test_separate_scripts_do_not_turn_actual_ft_into_ff(configuration):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=400,height=200)
        for x,small in [(50,'ff'),(150,'ft'),(250,'model')]:
            p.insert_text((x,100),'d',fontsize=20,fontname='tiit')
            p.insert_text((x+11,105),small,fontsize=12,fontname='tiro')
        result=header_scripts(p,pymupdf.Rect(40,70,350,120),configuration,{'remaining':30})
    assert [(m['base'],m['subscript']) for m in result]==[('d','ff'),('d','ft'),('d','model')]
    assert all(m['needs_review'] and m['latex'] for m in result)


def test_general_numeric_exponent_is_not_hardcoded_to_the_example(configuration):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=240,height=200)
        p.insert_text((40,70),'values',fontsize=14)
        p.insert_text((40,130),'x100',fontsize=14,fontname='tiro')
        x=40+pymupdf.get_text_length('x100',fontname='tiro',fontsize=14)
        p.insert_text((x,122),'3',fontsize=8,fontname='tiro')
        result=header_scales(p,pymupdf.Rect(30,40,160,140),configuration,{'remaining':20})
    assert len(result)==1 and result[0]['text']=='×100^3'
    assert result[0]['superscript']=='3' and result[0]['needs_review']


def test_plain_header_and_exhausted_budget_do_not_invent_scripts(configuration):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=300,height=200)
        p.insert_text((40,90),'Name Count 106',fontsize=13)
        rect=pymupdf.Rect(30,60,250,120)
        assert header_scripts(p,rect,configuration,{'remaining':20})==[]
        assert header_scales(p,rect,configuration,{'remaining':20})==[]
        assert header_scripts(p,rect,configuration,{'remaining':0})==[]
        assert header_scales(p,rect,configuration,{'remaining':0})==[]


def test_existing_paper_header_recovery_uses_pixels_only(configuration):
    root=Path(__file__).resolve().parents[1]
    source=root/'data/samples/1706.03762v1.pdf'
    if not source.exists() or not (root/'data/ocr/tessdata/ell.traineddata').is_file():
        pytest.skip('Optional sample and authorized Greek data absent')
    with pymupdf.open(source) as native:
        page=native[8]
        boxes=page.find_tables(strategy='lines_strict').tables[0].rows[0].cells
        image=page.get_pixmap(dpi=144)
        with pymupdf.open() as pdf:
            p=pdf.new_page(width=612,height=792);p.insert_image(p.rect,stream=image.tobytes('png'))
            assert p.get_text()==''
            box=pymupdf.Rect(boxes[1])+(1.5,1.5,-1,-1)
            values=header_scripts(p,box,configuration,{'remaining':40})
            scale=header_scales(p,pymupdf.Rect(boxes[2])+(1.5,1.5,-1,-1),configuration,{'remaining':20})
    assert [(m['base'],m['subscript']) for m in values]==[('d','model'),('d','ff'),('d','k'),('d','v'),('P','drop'),('ε','ls')]
    assert scale[0]['text']=='×10^6'
    assert values[1]['repeated_script_audit']['observations']
