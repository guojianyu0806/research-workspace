from research_workspace.fulltext_context import supplement_fulltext_context
from research_workspace.intelligence import Evidence,IntelligenceService
from research_workspace.library import Library


def block(identifier,text,y1,y2,*,doc='doc',run='run',page=2):
    return Evidence(evidence_id='E_'+identifier,block_id=identifier,document_id=doc,run_id=run,
        document_title='Fixture',filename='fixture.pdf',page=page,bbox=[.1,y1,.9,y2],text=text)


def test_recalls_original_table_above_caption_and_validity_paragraph(tmp_path):
    service=IntelligenceService(Library(tmp_path))
    body=block('table','Model\nTraining Time [hours]\nPerplexity\nBaseline 3 74.4\nRNN 168 58.3',.1,.4)
    caption=block('caption','Table 1: Results with various language models.',.42,.46)
    heading=block('heading','6 Threats to Validity',.6,.65)
    limitation=block('limitation','Only ten systems were selected; other systems may differ.',.66,.8)
    unrelated=block('other','Unrelated introduction.',.1,.2,page=1)
    foreign=block('foreign','Training time 999 hours.',.1,.3,doc='other')
    evidence,audit=supplement_fulltext_context(service,[unrelated,body,caption,heading,limitation,foreign],[unrelated],
        'Compare model performance and computational cost',['doc'])
    assert {b.evidence_id for b in evidence}=={b.evidence_id for b in [unrelated,body,caption,heading,limitation]}
    assert body.evidence_id in audit['table_evidence_ids']
    assert limitation.evidence_id in audit['limitation_evidence_ids']
    assert next(b for b in evidence if b.block_id=='table').text==body.text


def test_recall_is_bounded_and_does_not_infer_adjacent_prose_as_table(tmp_path):
    service=IntelligenceService(Library(tmp_path));snapshot=[]
    for i in range(5):
        snapshot.extend([block('caption'+str(i),f'Table {i+1}: Scores',.1,.15,page=i+1),
            block('body'+str(i),'Model 1 2 3 4',.17,.3,page=i+1),
            block('prose'+str(i),'A nearby prose paragraph with no numeric rows.',.31,.4,page=i+1)])
    evidence,audit=supplement_fulltext_context(service,snapshot,[],'Scores',['doc'])
    assert len(evidence)<=audit['max_added']==16
    assert len([b for b in evidence if b.block_id.startswith('caption')])==2
    assert all(not b.block_id.startswith('prose') for b in evidence)
