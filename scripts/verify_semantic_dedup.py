"""Real local-vector dedup acceptance; original samples stay untouched."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pymupdf

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.dedup import DedupService,DedupConflict
from research_workspace.jobs import JobStore
from research_workspace.library import Library,LibraryError
from research_workspace.reader_search import ReaderSearch


def main():
    os.environ['RESEARCH_SEMANTIC_ENABLED']='auto';os.environ['RESEARCH_OCR_ENABLED']='0'
    out=ROOT/'artifacts/semantic-dedup'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    print(out,flush=True);lib=Library(out/'library');docs={};hashes={};checks=[]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    def add(name,data):
        doc=lib.import_document(data,name)['document'];lib.process_next_job();docs[name]=lib.get_document(doc['id']);return docs[name]
    for name in ['1706.03762v1.pdf','1706.03762v7.pdf','1810.04805v2.pdf','2005.11401v4.pdf']:
        data=(ROOT/'data/samples'/name).read_bytes();hashes[name]=hashlib.sha256(data).hexdigest();add(name,data)
    # Derived variants only alter PDF metadata, never the actual sample files.
    with pymupdf.open(ROOT/'data/samples/1706.03762v7.pdf') as pdf:
        pdf.set_metadata({**pdf.metadata,'subject':'Local acceptance: renamed metadata-only variant'})
        add('transformer-renamed-local-copy.pdf',pdf.tobytes())
    with pymupdf.open(ROOT/'data/samples/1810.04805v2.pdf') as pdf:
        pdf.set_metadata({**pdf.metadata,'subject':'Local negative fixture: misleading filename'})
        add('1706.03762v99.pdf',pdf.tobytes())
    source_bytes={d['id']:lib._source_path(d['id']).read_bytes() for d in docs.values()}
    a,b=docs['1706.03762v1.pdf'],docs['1706.03762v7.pdf'];oldrun=a['available_run']['id']
    original_pages={p:lib.get_page(a['id'],p,oldrun) for p in range(1,a['page_count']+1)}
    oldblocks=lib._evidence_blocks(a['id'],oldrun)
    citation=next(x for x in oldblocks if len(x['text'])>100)
    store=JobStore(lib);job=store.create('qa',{'question':'Local citation-preservation fixture; no model answer generated.','document_ids':[a['id']]})
    store.finish(job['id'],'completed',result={'fixture':True,'citations':[citation],'model_generated':False})
    historical_job=store.get(job['id'])
    connect=socket.socket.connect
    def no_external(sock,address,*args,**kwargs):
        if isinstance(address,tuple) and address[0] in {'127.0.0.1','::1','localhost'}:return connect(sock,address,*args,**kwargs)
        raise AssertionError('External connections are disabled during local acceptance')
    socket.socket.connect=no_external
    try:
        service=DedupService(lib);r=service.scan();(out/'initial-scan.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
        def pair(x,y):return next(p for p in r['pairs'] if {p['left']['document_id'],p['right']['document_id']}=={x['id'],y['id']})
        p=pair(a,b)
        check('real_embeddings_applied',r['semantic']['status']=='applied')
        check('v1_v7_same_work_candidate',p['classification']=='same_work_candidate' and p['can_group'])
        copy=docs['transformer-renamed-local-copy.pdf'];check('renamed_real_copy_candidate',pair(b,copy)['can_group'])
        check('bert_not_merged_with_transformer',not pair(b,docs['1810.04805v2.pdf'])['can_group'])
        check('rag_not_merged_with_transformer',not pair(b,docs['2005.11401v4.pdf'])['can_group'])
        check('misleading_filename_blocked',pair(a,docs['1706.03762v99.pdf'])['classification']=='identity_conflict')
        check('scan_has_no_automatic_groups',r['automatic_merges']==0 and all(lib.get_document(d['id'])['version_family_id'] is None for d in docs.values()))
        for pair_item in r['pairs']:
            for side in ['left','right']:
                src=pair_item[side];blocks={x['block_id']:x for x in lib._evidence_blocks(src['document_id'],src['run_id'])}
                evidences=pair_item['source_samples'][side]+pair_item['identities'][side]['evidence']
                check(pair_item['pair_id']+'_'+side+'_every_source_matches',all(
                    e['text']==blocks[e['block_id']]['text'][:len(e['text'])] and all(e[k]==blocks[e['block_id']][k] for k in ['document_id','run_id','page','bbox']) for e in evidences))
        # A blocked pair remains blocked through both public service entry points.
        wrong=pair(b,docs['1810.04805v2.pdf'])
        try:service.decide(r['id'],wrong['pair_id'],'group',b['id']);blocked=False
        except DedupConflict:blocked=True
        check('conflicting_pair_rejected_by_dedup_write',blocked)
        try:lib.confirm_version_relation(b['id'],docs['1810.04805v2.pdf']['id']);blocked=False
        except LibraryError:blocked=True
        check('conflicting_pair_rejected_by_legacy_version_write',blocked)
        action=service.decide(r['id'],p['pair_id'],'group',b['id'])
        check('only_chosen_version_is_default',not lib.get_document(a['id'])['is_current_version'] and lib.get_document(b['id'])['is_current_version'])
        check('literal_global_search_folds_old_version',a['id'] not in {h['document_id'] for h in lib.search('attention',limit=100)})
        check('explicit_old_version_is_searchable',bool(lib.search('attention',document_id=a['id'],run_id=oldrun)))
        selected=ReaderSearch(lib).snapshot()[1]
        check('semantic_global_snapshot_folds_old_version',a['id'] not in {s['document_id'] for s in selected})
        check('saved_citation_and_job_unchanged',store.get(job['id'])==historical_job)
        check('all_old_pages_unchanged',all(lib.get_page(a['id'],page,oldrun)==value for page,value in original_pages.items()))
        lib.create_run(a['id']);lib.process_next_job()
        check('history_after_reparse_still_exact',all(lib.get_page(a['id'],page,oldrun)==value for page,value in original_pages.items()))
        check('citation_after_reparse_still_exact',store.get(job['id'])==historical_job)
        grouped=service.get(r['id']);(out/'grouped-scan.json').write_text(json.dumps(grouped,ensure_ascii=False,indent=2),encoding='utf-8')
        service.undo(action['id'])
        check('undo_restores_both_versions',all(lib.get_document(d['id'])['version_family_id'] is None for d in [a,b]))
        check('undo_keeps_audit_record',bool(service.get(r['id'])['actions'][0]['reverted_at']))
        check('all_imported_originals_unchanged',all(lib._source_path(did).read_bytes()==data for did,data in source_bytes.items()))
        final=service.scan();(out/'final-scan.json').write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
        check('repeat_uses_vector_cache',final['semantic']['cache_hits']>=9)
    finally:socket.socket.connect=connect
    check('original_sample_files_unchanged',all(hashlib.sha256((ROOT/'data/samples'/n).read_bytes()).hexdigest()==sha for n,sha in hashes.items()))
    with lib._connection() as db:check('zero_paid_model_calls',db.execute('SELECT count(*) FROM model_calls').fetchone()[0]==0)
    result={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'documents':docs,
        'initial_scan_id':r['id'],'final_scan_id':final['id'],'group_action_id':action['id'],'fixture_job_id':job['id'],
        'source_sha256':hashes,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),
        'model_calls':0,'external_socket_connections_denied':True}
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print([(p['left']['filename'],p['right']['filename'],p['classification'],p['semantic_similarity'],p['body_overlap']) for p in r['pairs']],flush=True)
    print(f"{result['passed']}/{result['total']}",flush=True)
    return 0 if result['passed']==result['total'] else 1


if __name__=='__main__':raise SystemExit(main())
