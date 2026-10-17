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


def test_near_gutter_author_outlier_does_not_interleave_two_body_columns():
    from research_workspace.reading_order import reading_order
    regions=[];boxes={}
    def add(name,text,box):regions.append({'id':name,'text':text});boxes[name]=box
    add('title','Full-width paper heading',[200,70,700,100])
    add('author_left','Left author with long affiliation',[170,140,497,200])
    add('author_right','Right author and institution',[511,140,822,200])
    add('abstract','Abstract',[260,250,340,265])
    add('left1','Left abstract body paragraph',[150,275,453,350])
    add('left2','Left introduction first paragraph',[118,400,488,470])
    add('left3','Left introduction final continuation',[118,480,488,540])
    add('right1','Right continuation of the introduction',[512,250,882,400])
    add('right2','Right contribution paragraph',[512,410,882,500])
    add('right3','Right organization paragraph',[512,510,882,550])
    order=reading_order(regions,boxes,1000)
    assert order['columns']==2
    ids=order['region_ids']
    assert ids.index('abstract')<ids.index('left1')<ids.index('left3')<ids.index('right1')
    assert ids.index('author_left')<ids.index('abstract') and ids.index('author_right')<ids.index('abstract')
    assert len(ids)==len(set(ids))==len(regions)


def test_sparse_near_overlap_keeps_conservative_fallback():
    from research_workspace.reading_order import reading_order
    regions=[{'id':str(i),'text':'Short observed paragraph'} for i in range(4)]
    boxes={'0':[100,100,535,150],'1':[100,200,535,250],'2':[530,100,900,150],'3':[530,200,900,250]}
    assert reading_order(regions,boxes,1000)['columns']==1
