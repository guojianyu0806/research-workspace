import asyncio
from research_workspace.intelligence import IntelligenceService,Evidence
from research_workspace.library import Library
from research_workspace.question_facets import supplement_question_facets


def b(name,text,page=1):
    return Evidence(evidence_id=name,block_id=name,document_id='doc',run_id='frozen',page=page,
        bbox=[.1,.2,.8,.3],document_title='ORION Research',filename='paper.pdf',text=text)


def test_compound_request_retains_original_header_and_named_numeric_rows(tmp_path):
    source=[b('copyright','Provided proper attribution grants permission.'),b('title','ORION Research'),
        *[b('author'+str(i),'Example Author '+str(i)+'\nResearch Institute\nauthor'+str(i)+'@example.org') for i in range(9)],
        b('toronto','Alice N. Example\nExample University\nalice@example.org'),b('abstract','Abstract'),
        b('body','Unrelated original body text.'),b('caption','Table 2: ORION BLEU results for two translation tasks.',2),
        b('columns','EN-DE\nEN-FR',2),b('row','ORION (base model)\n27.3\n38.1',2),
        b('gpu','ORION uses eight GPUs.',2)]
    service=IntelligenceService(Library(tmp_path))
    evidence,meta=supplement_question_facets(service,source,[source[-1]],'作者所属机构与BLEU分数',['doc'],{'doc':['orion']})
    ids={x.evidence_id for x in evidence}
    assert {'toronto','row','columns','caption'}<=ids and 'copyright' not in ids and 'body' not in ids
    assert all(x.run_id=='frozen' for x in evidence) and len(meta['added_evidence_ids'])<=24


def test_author_initials_do_not_truncate_compact_affiliation_email_block():
    original=b('header','Alice N. Example\nExample University\nalice@example.org')
    spans=IntelligenceService._citation_spans([original],omit_trailing_fragment=True)
    assert spans['header']=={'Q1':original.text}
