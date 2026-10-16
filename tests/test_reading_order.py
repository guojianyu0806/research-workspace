import pymupdf
import pytest
from research_workspace.library import Library


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_saved_two_column_body_reads_whole_left_column_before_right(tmp_path,rotation):
    library=Library(tmp_path/'library')
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=600,height=800)
        for x,y,text in [(40,100,'LEFT first evidence paragraph'),(330,110,'RIGHT first evidence paragraph'),
                         (40,200,'LEFT second evidence paragraph'),(330,210,'RIGHT second evidence paragraph')]:
            p.insert_text((x,y),text)
        p.set_rotation(rotation)
        doc=library.import_document(pdf.tobytes(),'columns.pdf')['document']
    library.process_next_job()
    page=library.get_page(doc['id'],1)
    assert [b['text'] for b in page['blocks']]==['LEFT first evidence paragraph','LEFT second evidence paragraph',
        'RIGHT first evidence paragraph','RIGHT second evidence paragraph']
    assert all(0<=n<=1 for b in page['blocks'] for n in b['bbox'])
    old=page
    library.create_run(doc['id']);library.process_next_job()
    assert library.get_page(doc['id'],1,old['run_id'])==old
