"""Real local Tesseract on developer-rasterized fixtures; no model/network."""
from pathlib import Path
import hashlib
import pymupdf
import pytest

from research_workspace.library import Library
from research_workspace.layout import LayoutService
from research_workspace.raster_tables import extract_scan_tables


def scan_fixture(rotation=0,merged=False):
    with pymupdf.open() as vector:
        p=vector.new_page(width=400,height=400)
        for y in (90,130,170,210):p.draw_line((40,y),(340,y),width=1)
        for x in (40,190,340):p.draw_line((x,130 if merged and x==190 else 90),(x,210),width=1)
        for row,values in enumerate([['Measured values',''] if merged else ['Item','Count'],['Alpha','12.5'],['Beta','-7.25']]):
            for column,text in enumerate(values):p.insert_text((55+150*column,116+40*row),text,fontsize=13)
        pix=p.get_pixmap(dpi=144)
        with pymupdf.open() as scanned:
            target=scanned.new_page(width=400,height=400);target.insert_image(target.rect,stream=pix.tobytes('png'));target.set_rotation(rotation)
            return scanned.tobytes()


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setenv('RESEARCH_OCR_ENABLED','1');monkeypatch.setenv('RESEARCH_OCR_LANG','eng')
    monkeypatch.setenv('RESEARCH_OCR_TESSDATA',str(Path(__file__).resolve().parents[1]/'data/ocr/tessdata'))
    lib=Library(tmp_path)
    if not lib._ocr_configuration()['available']:pytest.skip('Project-local English OCR data unavailable')
    return lib


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_scanned_cells_values_rotation_and_original_preservation(library,rotation):
    data=scan_fixture(rotation);doc=library.import_document(data,'image-only-grid.pdf')['document']
    if rotation==0:library.process_next_job()
    result=LayoutService(library).analyze_page(doc['id'],1)
    assert result['scan_table_audit']['status']=='extracted_needs_review'
    assert result['scan_table_audit']['model_calls']==0 and len(result['tables'])==1
    table=result['tables'][0];assert (table['row_count'],table['column_count'])==(3,2)
    values=[[c['text'] for c in table['cells'] if c['row']==r] for r in range(3)]
    assert values==[['Item','Count'],['Alpha','12.5'],['Beta','-7.25']]
    cell=next(c for c in table['cells'] if c['row']==1 and c['column']==1)
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        expected=pymupdf.Rect(190,130,340,170)*pdf[0].rotation_matrix
        expected=[expected.x0/pdf[0].rect.width,expected.y0/pdf[0].rect.height,expected.x1/pdf[0].rect.width,expected.y1/pdf[0].rect.height]
    assert cell['bbox']==pytest.approx(expected,abs=.004)
    assert cell['evidence']['source']=='immutable_pdf_raster_local_ocr' and cell['needs_review']
    assert cell['evidence']['run_id']==doc['current_run']['id']
    for w in cell['word_evidence']:assert w['text'] and all(0<=v<=1 for v in w['bbox'])
    assert hashlib.sha256(library._source_path(doc['id']).read_bytes()).hexdigest()==hashlib.sha256(data).hexdigest()


def test_scanned_merged_header_is_not_filled_with_invented_text(library):
    doc=library.import_document(scan_fixture(merged=True),'merged-grid.pdf')['document']
    tables=LayoutService(library).analyze_page(doc['id'],1)['tables'];assert tables
    cells=tables[0]['cells'];merged=next(c for c in cells if c['row']==0 and c['column']==1)
    assert merged['status']=='merged_or_missing_cell' and merged['text']=='' and merged['bbox'] is None
    assert next(c for c in cells if c['row']==0 and c['column']==0)['text']=='Measured values'


def test_plain_scan_has_no_invented_table_and_large_pages_stop_before_render(library,monkeypatch):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=400,height=400);p.insert_text((40,100),'A normal paragraph with no ruled table.',fontsize=12)
        pix=p.get_pixmap(dpi=144)
        with pymupdf.open() as scan:
            p=scan.new_page(width=400,height=400);p.insert_image(p.rect,stream=pix.tobytes('png'))
            tables,audit=extract_scan_tables(p,library._ocr_configuration())
            assert tables==[] and audit['status']=='no_ruled_table'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=4000,height=4000)
        tables,audit=extract_scan_tables(p,library._ocr_configuration());assert tables==[] and audit['status']=='resource_limit'


def test_horizontal_rules_without_column_grid_complete_fallback_without_crash(library):
    with pymupdf.open() as native:
        p=native.new_page(width=400,height=400)
        for y in (90,130,170,210):p.draw_line((40,y),(340,y))
        for row,values in enumerate([['Item','Count'],['Alpha','12.5'],['Beta','-7.25']]):
            for column,value in enumerate(values):p.insert_text((55+150*column,116+40*row),value,fontsize=13)
        pix=p.get_pixmap(dpi=144)
        with pymupdf.open() as scan:
            p=scan.new_page(width=400,height=400);p.insert_image(p.rect,stream=pix.tobytes('png'))
            tables,audit=extract_scan_tables(p,library._ocr_configuration())
    assert isinstance(tables,list) and audit['horizontal_rules']==4 and audit['vertical_rules']==0
    assert audit['ocr_words']==6 and audit['model_calls']==0
    # Aligned words alone do not prove a reliable cell partition.
    assert all(t['needs_review'] for t in tables)


def test_ocr_audit_has_rotated_coordinates_and_its_own_source_anchor(library,monkeypatch):
    from research_workspace import raster_tables
    original=raster_tables.extract_scan_tables
    def observed(*args,**kwargs):
        tables,audit=original(*args,**kwargs)
        cell=tables[0]['cells'][3]
        cell['ocr_audit']=[{'status':'two_view_candidate','original_text':'12,5','text':'12.5',
            'bbox':cell['raw_bbox'],'crop_bbox':cell['raw_bbox'],'needs_review':True}]
        return tables,audit
    monkeypatch.setattr(raster_tables,'extract_scan_tables',observed)
    doc=library.import_document(scan_fixture(rotation=90),'audit-rotation.pdf')['document']
    cell=LayoutService(library).analyze_page(doc['id'],1)['tables'][0]['cells'][3]
    audit=cell['ocr_audit'][0]
    assert audit['bbox']==cell['bbox']==audit['crop_bbox']
    assert audit['coordinate_system']=='normalized_rendered_page'
    assert audit['evidence']['bbox']==cell['bbox']
    assert audit['evidence']['run_id']==doc['current_run']['id']
    assert audit['evidence']['source']=='immutable_pdf_raster_local_ocr'
