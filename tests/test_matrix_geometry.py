"""Matrix content and provenance, including fail-closed missing entries."""
from pathlib import Path
import pymupdf
import pytest
from research_workspace.matrix_geometry import extract_native_matrices
from research_workspace.layout import LayoutService
from research_workspace.library import Library

SOURCE=Path(__file__).resolve().parents[1]/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf'


def test_real_permutation_and_scripted_matrix():
    original=SOURCE.read_bytes()
    with pymupdf.open(SOURCE) as pdf:
        values,audit=extract_native_matrices(pdf[460])
        matrix=next(m for m in values if m['column_count']==3)
        assert [[c['latex'] for c in matrix['cells'] if c['row']==r] for r in range(3)]==[['0','1','0'],['0','0','1'],['1','0','0']]
        assert len(matrix['bracket_audit'])==4 and audit['model_calls']==0
        values,_=extract_native_matrices(pdf[461]);matrix=next(m for m in values if m['column_count']==3)
        assert [c['latex'].replace(' ','') for c in matrix['cells']]==[r'\phi_{'+str(r)+str(c)+'}' for r in range(1,4) for c in range(1,4)]
        assert len(matrix['bracket_audit'])==4
        for cell in matrix['cells']:
            assert cell['original_text'].startswith('ϕ')
            assert len(cell['glyph_mapping_audit'])==1
            mapping=cell['glyph_mapping_audit'][0]
            assert mapping['glyph_name']=='phi' and mapping['native_text']=='ϕ' and mapping['mapped_text']=='φ'
        nested,_=extract_native_matrices(pdf[61])
        assert any(m['latex'] is None and m['conversion']=='nested_matrix_requires_review' for m in nested)
    assert SOURCE.read_bytes()==original


def test_missing_cell_cannot_silently_become_zero():
    with pymupdf.open(SOURCE) as pdf:
        p=pdf[460];values,_=extract_native_matrices(p);m=next(m for m in values if m['column_count']==3)
        cell=next(c for c in m['cells'] if c['row']==1 and c['column']==1)
        p.add_redact_annot(pymupdf.Rect(cell['raw_bbox']));p.apply_redactions()
        revised,_=extract_native_matrices(p)
        rejected=next(x for x in revised if x['raw_bbox'][0]<m['raw_bbox'][0]+1)
        assert rejected['latex'] is None and rejected['conversion']=='ragged_matrix'


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_matrix_entries_keep_document_run_and_rotated_regions(tmp_path,monkeypatch,rotation):
    monkeypatch.setenv('RESEARCH_OCR_ENABLED','0')
    with pymupdf.open(SOURCE) as source,pymupdf.open() as pdf:
        pdf.insert_pdf(source,from_page=461,to_page=461);pdf[0].set_rotation(rotation);data=pdf.tobytes()
    lib=Library(tmp_path);doc=lib.import_document(data,'matrix.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
    before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
    assert len(result['matrices'])==4
    for matrix in result['matrices']:
        assert matrix['latex'] and matrix['needs_review']
        for item in [matrix,*matrix['cells']]:
            e=item['evidence'];assert e['document_id']==doc['id'] and e['run_id']==run and e['page']==1
            assert e['bbox']==item['bbox'] and all(0<=x<=1 for x in e['bbox'])
            if item in matrix['cells'] and r'\phi' in item['latex']:
                assert item['glyph_mapping_audit'][0]['glyph_name']=='phi'
    exported=__import__('json').loads(LayoutService(lib).export_page_structure(doc['id'],1,run))
    assert exported['matrices']==result['matrices']
    assert lib.get_page(doc['id'],1,run)==before and lib._source_path(doc['id']).read_bytes()==data
