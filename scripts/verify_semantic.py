"""Real local embedding checks and declared public-paper retrieval probes."""
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['RESEARCH_SEMANTIC_ENABLED']='1'
from research_workspace.intelligence import IntelligenceService
from research_workspace.library import Library
from research_workspace.semantic import _encode,SemanticIndex


async def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=ROOT/'artifacts/semantic'/stamp;output.mkdir(parents=True,exist_ok=False)
    library=Library(output/'library');ids={}
    for name in ['1706.03762v7.pdf','1810.04805v2.pdf','2005.11401v4.pdf']:
        doc=library.import_document((ROOT/'data/samples'/name).read_bytes(),name)['document']
        library.process_next_job();ids[name]=doc['id']
    class NoLanguageModel:configured=False
    service=IntelligenceService(library,NoLanguageModel())
    # These labels are defined from real PDF wording before rankings are run.
    probes=[('bert_mask','1810.04805v2.pdf','How does the network learn to recover hidden words?','we simply mask'),
        ('bert_architecture','1810.04805v2.pdf','What kind of neural network combines the left and right context?','multi-layer bidirectional'),
        ('parallelization','1706.03762v7.pdf','Why do methods that go word by word take longer to teach?','precludes parallelization'),
        ('retrieval_memory','2005.11401v4.pdf','Which information store supplies facts that were not memorized in the weights?','non-parametric memory')]
    rows=[]
    for name,filename,query,marker in probes:
        document=ids[filename];snapshot=service._blocks([document])
        expected={b.block_id for b in snapshot if marker in ' '.join(b.text.lower().split())}
        assert expected,(name,'source marker absent')
        lexical=service.retrieve(query,[document],limit=5)
        started=time.perf_counter()
        hybrid=await service.retrieve_for_question(query,[document],limit=5,expansion='off')
        default_budget=await service.retrieve_for_question(query,[document],limit=12,expansion='off')
        assert hybrid['semantic']['status']=='applied',hybrid['semantic']
        canonical={b.block_id:b for b in snapshot}
        for b in hybrid['evidence']:
            assert b.document_id==document and b.run_id==canonical[b.block_id].run_id
            assert b.text==canonical[b.block_id].text[:len(b.text)] and b.bbox==canonical[b.block_id].bbox
        rows.append({'case':name,'question':query,'expected_marker':marker,'expected_block_ids':sorted(expected),
            'lexical_hit_at_5':bool(expected.intersection(b.block_id for b in lexical)),
            'hybrid_hit_at_5':bool(expected.intersection(b.block_id for b in hybrid['evidence'])),
            'hybrid_hit_at_default_12':bool(expected.intersection(b.block_id for b in default_budget['evidence'])),
            'hybrid_block_ids':[b.block_id for b in hybrid['evidence']],
            'semantic':hybrid['semantic'],'seconds':round(time.perf_counter()-started,3)})
        print(name,rows[-1]['lexical_hit_at_5'],rows[-1]['hybrid_hit_at_5'],rows[-1]['seconds'],flush=True)
    import numpy as np
    query=np.asarray(_encode(['A physician treats an infection.'],True)[0])
    passages=np.stack(_encode(['A doctor gives antibiotics to a sick patient.','A truck carries concrete to a building site.']))
    similarity=(passages@query)/(np.linalg.norm(passages,axis=1)*np.linalg.norm(query))
    assert similarity[0]>similarity[1]
    versions={}
    # Renamed files ensure filenames do not supply the arXiv identity.
    version_library=Library(output/'renamed_versions')
    for filename,renamed in [('1706.03762v7.pdf','renamed-latest.pdf'),('1706.03762v1.pdf','renamed-earlier.pdf')]:
        d=version_library.import_document((ROOT/'data/samples'/filename).read_bytes(),renamed)['document']
        while version_library.process_next_job():pass
        assert version_library.get_document(d['id'])['filename']==renamed
        versions[filename]=d['id']
    negatives=[]
    for filename in ['1810.04805v2.pdf','2005.11401v4.pdf']:
        d=version_library.import_document((ROOT/'data/samples'/filename).read_bytes(),filename)['document']
        while version_library.process_next_job():pass
        negatives.append(d['id'])
    candidates=version_library.find_version_candidates(versions['1706.03762v1.pdf'])
    candidate=next(c for c in candidates if c['document']['id']==versions['1706.03762v7.pdf'])
    assert candidate['semantic_similarity'] is not None and candidate['requires_confirmation']
    assert all(c['document']['id'] not in negatives for c in candidates)
    assert version_library.get_document(versions['1706.03762v1.pdf'])['version_family_id'] is None
    report={'scope':'Four declared English paraphrase probes over actual public PDFs; not a general retrieval accuracy metric or official-package acceptance.',
        'probes':rows,'real_embedding_synonym_scores':similarity.tolist(),
        'renamed_version_candidate':{k:v for k,v in candidate.items() if k!='document'},
        'negative_papers':['BERT','RAG'],'automatic_merge':False,
        'lexical_hits':sum(r['lexical_hit_at_5'] for r in rows),'hybrid_hits':sum(r['hybrid_hit_at_5'] for r in rows)}
    report['hybrid_hits_at_default_12']=sum(r['hybrid_hit_at_default_12'] for r in rows)
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'lexical_hits':report['lexical_hits'],'hybrid_hits':report['hybrid_hits']},ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
