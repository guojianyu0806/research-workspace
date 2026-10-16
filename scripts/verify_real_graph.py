"""Acquire a reference-derived public corpus and verify real citation evidence."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from research_workspace.graph import GraphService
from scripts.prepare_demo import inspect_pdf


def main():
    output=ROOT/'artifacts/real-graph'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False);library=Library(output/'library')
    cache=ROOT/'data/corpus';cache.mkdir(parents=True,exist_ok=True)
    roots=[('1706.03762v7','Attention Is All You Need'),('1810.04805v2','BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding'),
        ('2005.11401v4','Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks')]
    report={'scope':'Actual public PDFs selected from existing papers reference lists; not official package. No generated papers or edges.',
        'output':str(output),'documents':[],'downloads':[],'failures':[]}
    def save():(output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    seen={re.sub(r'v\d+$','',i) for i,_ in roots};candidates=[]
    def add(content,name,title=None,source=None):
        info=inspect_pdf(content);d=library.import_document(content,name)['document']
        while library.process_next_job():pass
        if title is None:
            titles=LayoutService(library).analyze_page(d['id'],1)['metadata_candidates']['title']
            title=' '.join(x['text'] for x in titles[:5])[:300] or name
        library.update_metadata(d['id'],title=title)
        report['documents'].append({'document_id':d['id'],'filename':name,'title':title,'source':source,
            'title_policy':'source-backed supplied root title or first-page font-size candidate; not externally verified metadata',**info})
        for ref in library.get_structure(d['id'])['references']:
            identifier=ref.get('arxiv_id')
            if identifier and re.fullmatch(r'\d{4}\.\d{4,5}',identifier) and identifier not in seen:
                candidates.append({'arxiv_id':identifier,'source_document_id':d['id'],'reference_id':ref['id'],'original_reference':ref['text']});seen.add(identifier)
        save()
    for identifier,title in roots:
        add((ROOT/'data/samples'/f'{identifier}.pdf').read_bytes(),f'{identifier}.pdf',title,'existing public sample')
    next_network=time.monotonic()
    with httpx.Client(timeout=40,follow_redirects=False,trust_env=False) as client:
        position=0
        while len(report['documents'])<21 and position<len(candidates) and position<35:
            candidate=candidates[position];position+=1;identifier=candidate['arxiv_id']+'v1'
            path=cache/f'{identifier}.pdf';url='https://arxiv.org/pdf/'+identifier
            try:
                if path.exists():content=path.read_bytes();inspect_pdf(content)
                else:
                    time.sleep(max(0,next_network-time.monotonic()));next_network=time.monotonic()+3.1
                    with client.stream('GET',url) as response:
                        response.raise_for_status();body=bytearray()
                        for part in response.iter_bytes():
                            body.extend(part)
                            if len(body)>50*1024*1024:raise ValueError('PDF exceeds 50MiB')
                    content=bytes(body);inspect_pdf(content)
                    with path.open('xb') as f:f.write(content)
                add(content,path.name,source={**candidate,'url':url,'sha256':hashlib.sha256(content).hexdigest()})
                report['downloads'].append({'url':url,'cache':str(path),'sha256':hashlib.sha256(content).hexdigest()})
                print('PAPER',len(report['documents']),identifier,flush=True)
            except (httpx.HTTPError,OSError,ValueError) as error:
                report['failures'].append({'url':url,'error':type(error).__name__,'detail':str(error)[:180]});save()
                print('FAILED',identifier,type(error).__name__,flush=True)
    graph=GraphService(library).build([d['document_id'] for d in report['documents']]);checks=0
    for edge in graph['edges']:
        for item in edge['evidence']:
            page=library.get_page(item['document_id'],item['page'],item['run_id'])
            block=next(b for b in page['blocks'] if b['id']==item['block_id'])
            assert block['text']==item['text'] and block['bbox']==item['bbox'];checks+=1
    (output/'graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
    report.update(local_documents=len(report['documents']),nodes=len(graph['nodes']),edges=len(graph['edges']),canonical_edge_anchors_checked=checks,
        status='passed_21_real_pdf_source_checks' if len(report['documents'])>=21 and graph['edges'] else 'incomplete_real_corpus')
    save();print(json.dumps({k:report[k] for k in ['output','status','local_documents','nodes','edges','canonical_edge_anchors_checked']},ensure_ascii=False))


if __name__=='__main__':main()
