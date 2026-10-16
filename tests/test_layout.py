"""Real native PDF layout fixtures. No model, OCR, or external network calls."""
import hashlib
import json

import pymupdf
import pytest

from research_workspace.layout import LayoutService
from research_workspace.library import Library, LibraryError


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setenv('RESEARCH_OCR_ENABLED','0')
    return Library(tmp_path)


def import_pdf(library,pdf,name='layout.pdf'):
    document = library.import_document(pdf.tobytes(),name)['document']
    library.process_next_job()
    return document


def table_page(pdf,rotation=0):
    page = pdf.new_page(width=600,height=800)
    for x in (40,150,260):
        page.draw_line((x,200),(x,290))
    for y in (200,230,260,290):
        page.draw_line((40,y),(260,y))
    for y,values in ((220,('Name','Score')),(250,('Alpha','10')),(280,('Beta','20'))):
        for x,text in zip((50,160),values):
            page.insert_text((x,y),text,fontsize=10)
    page.insert_text((40,190),'Table 1. Measured scores',fontsize=10)
    page.set_rotation(rotation)
    return page


def test_two_column_order_uses_gutter_not_pdf_object_order(library):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600,height=800)
        page.insert_text((60,45),'Reliable Layout Evidence',fontsize=22)
        page.insert_text((80,76),'Alice Smith, Bob Jones',fontsize=11)
        page.insert_text((80,97),'Department of Research, Example University',fontsize=10)
        page.insert_text((40,140),'Abstract',fontsize=12,fontname='hebo')
        for y,text in ((200,'RIGHT first paragraph'),(225,'RIGHT second paragraph'),(250,'RIGHT final paragraph')):
            page.insert_text((330,y),text,fontsize=10)
        for y,text in ((200,'LEFT first paragraph'),(225,'LEFT second paragraph'),(250,'LEFT final paragraph')):
            page.insert_text((40,y),text,fontsize=10)
        document = import_pdf(library,pdf)
    result = LayoutService(library).analyze_page(document['id'],1)
    by_id = {region['id']:region['text'] for region in result['text_regions']}
    order = [by_id[identifier] for identifier in result['reading_order']['region_ids']]
    body = [text for text in order if text.startswith(('LEFT','RIGHT'))]
    assert body == ['LEFT first paragraph','LEFT second paragraph','LEFT final paragraph',
                    'RIGHT first paragraph','RIGHT second paragraph','RIGHT final paragraph']
    assert result['reading_order']['columns'] == 2
    candidates = result['metadata_candidates']
    assert candidates['title'][0]['text'] == 'Reliable Layout Evidence'
    assert candidates['authors'][0]['text'] == 'Alice Smith, Bob Jones'
    assert 'Example University' in candidates['affiliations'][0]['text']
    for items in candidates.values():
        for item in items:
            assert item['needs_review'] and item['status'] == 'candidate'
            assert item['evidence']['run_id'] == document['current_run']['id']
            assert item['evidence']['bbox'] == item['bbox']


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_table_cells_retain_values_and_rendered_coordinates(library,rotation):
    with pymupdf.open() as pdf:
        page = table_page(pdf,rotation)
        expected = pymupdf.Rect(150,230,260,260)*page.rotation_matrix
        expected = [expected.x0/page.rect.width,expected.y0/page.rect.height,
                    expected.x1/page.rect.width,expected.y1/page.rect.height]
        document = import_pdf(library,pdf)
    path = library._source_path(document['id'])
    original = path.read_bytes()
    result = LayoutService(library).analyze_page(document['id'],1)
    assert len(result['tables']) == 1
    table = result['tables'][0]
    assert table['row_count'] == 3 and table['column_count'] == 2
    cell = next(cell for cell in table['cells'] if cell['row']==1 and cell['column']==1)
    assert cell['text'] == '10'
    assert cell['bbox'] == pytest.approx(expected)
    assert cell['evidence']['text'] == '10'
    assert cell['evidence']['page'] == 1
    assert path.read_bytes() == original
    assert hashlib.sha256(original).hexdigest() == result['source_sha256']


def test_native_image_and_caption_have_evidence_without_binary_payload(library):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600,height=800)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,8,8),False)
        pixmap.clear_with(120)
        page.insert_image(pymupdf.Rect(40,200,180,340),stream=pixmap.tobytes('png'))
        page.insert_text((40,360),'Figure 1. Fixture image',fontsize=10)
        document = import_pdf(library,pdf)
    result = LayoutService(library).analyze_page(document['id'],1)
    assert len(result['figures']) == 1
    figure = result['figures'][0]
    assert figure['bbox'] == pytest.approx([40/600,200/800,180/600,340/800])
    assert figure['evidence']['text'] == ''
    assert figure['semantic_role'] == 'unverified_image_or_figure'
    caption = result['captions'][0]
    assert caption['text'] == 'Figure 1. Fixture image'
    assert caption['target_id'] == figure['id']
    assert figure['caption_ids'] == [caption['id']]
    assert 'image' not in figure and 'digest' not in figure
    encoded = LayoutService(library).export_page_structure(document['id'],1)
    assert json.loads(encoded)['figures'][0]['bbox'] == figure['bbox']
    assert 'base64' not in encoded


def test_formula_original_text_is_never_replaced_by_guessed_latex(library):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40,80),'y = x^2 + 1',fontsize=12)
        page.insert_text((40,110),'loss = alpha + beta',fontsize=12)
        page.insert_text((40,140),'Some ordinary prose is not an equation.',fontsize=10)
        document = import_pdf(library,pdf)
    formulas = LayoutService(library).analyze_page(document['id'],1)['formulas']
    assert len(formulas) == 2
    simple,unsupported = formulas
    assert simple['original_text'] == simple['evidence']['text'] == 'y = x^2 + 1'
    assert simple['latex'] == 'y = x^{2} + 1'
    assert simple['needs_review'] and simple['status'] == 'candidate'
    assert unsupported['original_text'] == 'loss = alpha + beta'
    assert unsupported['latex'] is None
    assert unsupported['conversion'] == 'unsupported_formula_notation'


def test_run_and_document_ownership_and_old_evidence_are_enforced(library,tmp_path):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,80),'Stable original text')
        document = import_pdf(library,pdf)
    service = LayoutService(library)
    old = document['current_run']['id']
    first = service.analyze_page(document['id'],1,old)
    replacement = library.create_run(document['id'])
    library.process_next_job()
    latest = service.analyze_page(document['id'],1)
    assert latest['run_id'] == replacement['id']
    assert service.analyze_page(document['id'],1,old) == first
    assert first['text_regions'][0]['evidence']['block_id'] != latest['text_regions'][0]['evidence']['block_id']
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,80),'Another original')
        other = import_pdf(library,pdf,'other.pdf')
    with pytest.raises(LibraryError,match='不属于'):
        service.analyze_page(document['id'],1,other['current_run']['id'])
    with pytest.raises(KeyError):
        LayoutService(Library(tmp_path/'another-account')).analyze_page(document['id'],1)
    for page in (0,2,True):
        with pytest.raises(LibraryError):
            service.analyze_page(document['id'],page)


def test_summary_only_analyzes_first_native_page_and_uses_saved_headings(library,monkeypatch):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40,40),'Native Title',fontsize=22)
        page.insert_text((40,100),'1 Introduction',fontsize=12)
        page.insert_text((40,120),'Body text in the actual source.',fontsize=10)
        page = pdf.new_page()
        page.insert_text((40,80),'Appendix A',fontsize=14)
        page.insert_text((40,120),'Supporting evidence.',fontsize=10)
        pdf.new_page().insert_text((40,80),'Final page.',fontsize=10)
        document = import_pdf(library,pdf)
    service = LayoutService(library)
    calls = []
    analyze = service.analyze_page

    def observed(document_id,page,run_id=None):
        calls.append(page)
        return analyze(document_id,page,run_id)

    monkeypatch.setattr(service,'analyze_page',observed)
    summary = service.get_document_summary(document['id'])
    assert calls == [1] and summary['layout_analyzed_pages'] == [1]
    assert summary['appendix_markers'][0]['title'] == 'Appendix A'
    assert summary['appendix_markers'][0]['evidence']['page'] == 2
    assert summary['run_id'] == document['current_run']['id']


def test_pending_or_image_only_page_does_not_invent_saved_text(library):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,80),'Native but not yet parsed')
        document = library.import_document(pdf.tobytes(),'pending.pdf')['document']
    result = LayoutService(library).analyze_page(document['id'],1)
    assert result['processing_status'] == 'queued'
    assert result['text_regions'][0]['evidence']['block_id'] is None
    with pymupdf.open() as pdf:
        pdf.new_page()
        blank = library.import_document(pdf.tobytes(),'blank.pdf')['document']
    result = LayoutService(library).analyze_page(blank['id'],1)
    assert result['status'] == 'no_native_text'
    assert result['metadata_candidates'] == {'title':[],'authors':[],'affiliations':[]}
    assert result['formulas'] == []
    assert result['reading_order']['columns'] is None


def test_malformed_or_implicit_formula_is_retained_without_conversion(library):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for y,text in ((70,'x = = y'),(100,'y = 2x'),(130,'x = (y + 1)')):
            page.insert_text((40,y),text,fontsize=12)
        document = import_pdf(library,pdf)
    formulas = LayoutService(library).analyze_page(document['id'],1)['formulas']
    assert [formula['original_text'] for formula in formulas] == ['x = = y','y = 2x','x = (y + 1)']
    assert all(formula['latex'] is None and formula['needs_review'] for formula in formulas)


def test_title_candidates_exclude_rotated_identifier_and_smaller_author_lines(library):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=600,height=800)
        p.insert_text((40,60),'Actual Main Research Title',fontsize=22)
        p.insert_text((40,90),'Alice Smith and Bob Jones',fontsize=16)
        p.insert_text((40,120),'contact@example.org',fontsize=16)
        p.insert_text((20,300),'arXiv:2401.12345v1',fontsize=22,rotate=90)
        for y in range(200,500,30):p.insert_text((40,y),'A paragraph with regular body font size.',fontsize=11)
        doc=library.import_document(pdf.tobytes(),'realistic-title.pdf')['document']
    library.process_next_job()
    result=LayoutService(library).analyze_page(doc['id'],1)
    assert [r['text'] for r in result['metadata_candidates']['title']]==['Actual Main Research Title']
    assert any('Alice Smith' in r['text'] for r in result['metadata_candidates']['authors'])
