import asyncio
import pytest
from research_workspace.intelligence import IntelligenceService,Evidence
from research_workspace.library import Library
from research_workspace.question_facets import supplement_question_facets,header_author_links


def b(name,text,page=1):
    return Evidence(evidence_id=name,block_id=name,document_id='doc',run_id='frozen',page=page,
        bbox=[.1,.2,.8,.3],document_title='ORION Research',filename='paper.pdf',text=text)


@pytest.mark.parametrize('columns',['EN-DE\nEN-FR','EN-DE\nEN-FR\nEN-DE\nEN-FR\nByteNet [18]\n23.75\nDeep-Att [39]\n39.2\n1.0 · 1020'])
def test_compound_request_retains_original_header_and_named_numeric_rows(tmp_path,columns):
    source=[b('copyright','Provided proper attribution grants permission.'),b('title','ORION Research'),
        *[b('author'+str(i),'Example Author '+str(i)+'\nResearch Institute\nauthor'+str(i)+'@example.org') for i in range(9)],
        b('toronto','Alice N. Example\nExample University\nalice@example.org'),b('abstract','Abstract'),
        b('body','Unrelated original body text.'),b('caption','Table 2: ORION BLEU results for two translation tasks.',2),
        b('columns',columns,2),b('row','ORION (base model)\n27.3\n38.1',2),
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


def test_bounded_paragraph_quote_keeps_model_antecedent_and_training_scope():
    text='On translation, the ORION big model scored 28.4. This model uses the listed configuration. Training took 3.5 days.'
    source=b('result',text,page=8)
    catalog=IntelligenceService._citation_spans([source],omit_trailing_fragment=True)['result']
    assert catalog['Q3']=='Training took 3.5 days.' and catalog['Q4']==text
    fragment=source.model_copy(update={'text':text+' This paragraph continues'})
    assert fragment.text not in IntelligenceService._citation_spans([fragment],omit_trailing_fragment=True)['result'].values()


def test_header_pairs_require_unique_geometry_same_version_and_visible_names():
    author=b('name','Alice Example∗').model_copy(update={'bbox':[.2,.2,.3,.22]})
    institution=b('org','Example University\nalice@example.org').model_copy(update={'bbox':[.18,.225,.32,.26]})
    links=header_author_links([author,institution])
    assert len(links)==1 and links[0]['author_evidence_id']=='name' and links[0]['affiliation_evidence_id']=='org'
    assert not header_author_links([author,institution.model_copy(update={'run_id':'other'})])
    assert not header_author_links([author,institution.model_copy(update={'bbox':[.6,.225,.8,.26]})])
    assert not header_author_links([author,institution,institution.model_copy(update={'evidence_id':'ambiguous'})])
    assert not header_author_links([author.model_copy(update={'text':'No author name quoted.'}),institution])


def test_author_marker_footnote_is_retrieved_from_below_abstract(tmp_path):
    service=IntelligenceService(Library(tmp_path))
    header=b('author','Alice Example∗†\nExample University\nalice@example.org')
    footnote=b('footnote','†Work performed while at Example Research.')
    evidence,meta=supplement_question_facets(service,[header,b('abstract','Abstract'),b('body','Unrelated abstract.'),footnote],[],
        '作者所属机构',['doc'],{})
    assert 'footnote' in {x.evidence_id for x in evidence} and footnote.text==next(x.text for x in evidence if x.evidence_id=='footnote')


def test_isolated_reviewer_receives_only_own_cited_header_layout_pair(tmp_path):
    from research_workspace.intelligence import AnswerPayload,ClaimSupportPayload,CoveragePayload
    author=b('E_name','Alice Example∗').model_copy(update={'bbox':[.2,.2,.3,.22]})
    institution=b('E_org','Example University\nalice@example.org').model_copy(update={'bbox':[.18,.225,.32,.26]})
    hidden=b('E_hidden','Forbidden uncited author detail.')
    class Provider:
        configured=True
        def __init__(self):self.seen=[]
        async def generate(self,prompt,payload,model):
            self.seen.append((payload,model))
            if model is AnswerPayload:return AnswerPayload(status='answered',claims=[{'text':'Alice Example所属机构标注为Example University。',
                'citations':[{'evidence_id':'E_name','quote_id':'Q1'},{'evidence_id':'E_org','quote_id':'Q1'}]}])
            if model is ClaimSupportPayload:return ClaimSupportPayload(verdict='supported',reason='Own quoted author and uniquely aligned affiliation.')
            return CoveragePayload(unverified_statements=[],missing_aspects=[])
    provider=Provider();service=IntelligenceService(Library(tmp_path),provider)
    asyncio.run(service._generate_grounded('fixture',{'task':'qa','question':'作者机构'},AnswerPayload,[author,institution,hidden]))
    review=next(p for p,m in provider.seen if m is ClaimSupportPayload)
    assert review['bibliographic_layout_links'][0]['author_evidence_id']=='E_name'
    assert 'Forbidden' not in str(review) and 'E_hidden' not in str(review)
