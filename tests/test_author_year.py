from research_workspace.structure import extract_structure


def block(text,name,page=1,x=.1):
    return {'document_id':'doc','run_id':'run','block_id':name,'page':page,'text':text,'bbox':[x,.2,x+.3,.4]}


def test_hanging_author_year_references_keep_continuations_and_bidirectional_citations():
    blocks=[block('Prior work by Smith et al. (2020) and Doe and Roe (2021).','body'),
        block('References','heading',2),block('Alice Smith, Bob Jones.','authors',2),
        block('2020. Reliable Evidence Retrieval. Journal of Results.','continuation',2,.12),
        block('Jane Doe and Richard Roe. 2021. Another Actual Research Paper.','second',2)]
    result=extract_structure(blocks,'doc','run')
    assert len(result['references'])==2
    refs=result['references'];cites=result['citations']
    assert refs[0]['first_author_surname']=='Smith' and refs[0]['year']==2020
    assert refs[0]['continuation_evidence'][0]['block_id']=='continuation'
    assert [c['reference_ids'] for c in cites]==[[refs[0]['id']],[refs[1]['id']]]
    assert refs[0]['citation_ids']==[cites[0]['id']]
    for c in cites:
        b=next(b for b in blocks if b['block_id']==c['evidence']['block_id'])
        assert b['text'][c['evidence']['char_start']:c['evidence']['char_end']]==c['marker']


def test_same_surname_and_year_remains_ambiguous_not_linked_by_position():
    result=extract_structure([block('Smith et al. (2020) offers a method.','b'),block('References','h',2),
        block('Alice Smith. 2020. A First Research Article.','r1',2),
        block('Bob Smith. 2020. A Second Research Article.','r2',2)],'doc','run')
    cite=result['citations'][0]
    assert cite['reference_ids']==[] and len(cite['candidate_reference_ids'])==2
    assert all(not r['citation_ids'] for r in result['references'])


def test_lowercase_title_before_year_does_not_create_new_author_entry():
    result=extract_structure([block('References','h',2),block('Alice Smith. 2020. First paper.','r1',2),
        block('In Proceedings of the research conference, 2021.','cont',2)],'doc','run')
    assert len(result['references'])==1
