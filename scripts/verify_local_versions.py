"""Accept deterministic version diff against existing public PDFs and fixtures."""
from datetime import datetime,timezone
import hashlib,json,os
from pathlib import Path
import subprocess,sys
import pymupdf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library


def main():
    os.environ['RESEARCH_SEMANTIC_ENABLED']='0';os.environ['RESEARCH_OCR_ENABLED']='0'
    out=ROOT/'artifacts/local-versions'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    print(out,flush=True);lib=Library(out/'library');checks=[];documents={};source_hashes={}
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    def add(name,data,title=None):
        doc=lib.import_document(data,name)['document'];lib.process_next_job()
        if title:lib.update_metadata(doc['id'],title=title)
        doc=lib.get_document(doc['id']);documents[name]={'document_id':doc['id'],'run_id':doc['available_run']['id'],'title':doc['title']}
        return doc
    # Explicitly add newer then older; filenames alone do not merge a family.
    for name in ['1706.03762v7.pdf','1706.03762v1.pdf','1810.04805v2.pdf']:
        source=ROOT/'data/samples'/name;data=source.read_bytes();source_hashes[name]=hashlib.sha256(data).hexdigest();add(name,data)
    def fixture(changed=False,segmented=False):
        with pymupdf.open() as pdf:
            p=pdf.new_page();p.insert_text((40,60),'Local acceptance fixture. Not an actual research paper.',fontsize=10)
            for i in range(10):p.insert_text((40,100+20*i),f'Shared source sentence {i}: controlled methods retain independent evidence.',fontsize=10)
            if segmented:
                p.insert_text((40,340),'Result value is',fontsize=10);p.insert_text((40,390),'10.',fontsize=10)
            else:p.insert_text((40,340),'Result value is '+('20.' if changed else '10.'),fontsize=10)
            p.insert_text((40,440),'Architecture name '+('bert' if changed else 'BERT')+'.',fontsize=10)
            return pdf.tobytes()
    old=add('local-fixture-old.pdf',fixture(), '本地验收夹具：旧稿')
    new=add('local-fixture-new.pdf',fixture(True),'本地验收夹具：修改数字与大小写')
    layout=add('local-fixture-layout.pdf',fixture(segmented=True),'本地验收夹具：仅改变分块')
    cases=[('public-v1-v7','1706.03762v1.pdf','1706.03762v7.pdf'),
        ('fixture-content','local-fixture-old.pdf','local-fixture-new.pdf'),('fixture-segmentation','local-fixture-old.pdf','local-fixture-layout.pdf')]
    selections=[]
    for name,left,right in cases:
        a,b=documents[left],documents[right];before=lib.get_page(a['document_id'],1,a['run_id'])
        r=lib.compare_versions(a['document_id'],b['document_id'],run_id=a['run_id'],other_run_id=b['run_id'])
        (out/(name+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
        check(name+'_complete',r['complete'] and not r['changes_truncated'])
        check(name+'_no_model_or_network',r['model_calls']==0 and not r['network_used'])
        check(name+'_fixed_runs',r['run_id']==a['run_id'] and r['other_run_id']==b['run_id'])
        for side,d in [('before',a),('after',b)]:
            blocks={x['block_id']:x for x in lib._evidence_blocks(d['document_id'],d['run_id'])}
            regions=[region for c in r['changes'] for region in c[side+'_regions']]
            check(name+'_'+side+'_every_span_is_original',all(
                region['text']==blocks[region['block_id']]['text']
                and region['text'][region['change_span']['start']:region['change_span']['end']]==region['change_span']['text']
                and all(region[k]==blocks[region['block_id']][k] for k in ['document_id','run_id','page','bbox']) for region in regions))
        if name=='fixture-content':
            check(name+'_expected_changes',{(c['before_text'],c['after_text']) for c in r['changes']}=={('10','20'),('BERT','bert')})
        elif name=='fixture-segmentation':
            check(name+'_not_reported_as_content_revision',r['text_equivalent'] and r['total_changes']==0 and not r['changes'])
        else:check(name+'_real_revision_found',r['total_changes']>0 and not r['text_equivalent'])
        check(name+'_saved_parse_unchanged',lib.get_page(a['document_id'],1,a['run_id'])==before)
        selections.append({'name':name,'before':a,'after':b,'changes':r['total_changes'],
            'source':'existing_public_papers' if name.startswith('public') else 'explicit_developer_fixture'})
        print(name,r['total_changes'],flush=True)
    lib.create_run(old['id']);check('pending_reparse_uses_old_finished_snapshot',lib.compare_versions(old['id'],new['id'])['run_id']==old['available_run']['id'])
    lib.process_next_job()
    check('explicit_history_still_available',lib.compare_versions(old['id'],new['id'],run_id=old['available_run']['id'])['run_id']==old['available_run']['id'])
    documents['local-fixture-old.pdf']['new_run_id']=lib.get_document(old['id'])['available_run']['id']
    actual=lib.find_version_candidates(documents['1706.03762v1.pdf']['document_id'])
    check('public_newer_candidate_and_unrelated_paper_separated',documents['1706.03762v7.pdf']['document_id'] in [x['document']['id'] for x in actual]
        and documents['1810.04805v2.pdf']['document_id'] not in [x['document']['id'] for x in actual])
    check('never_merged_automatically',all(lib.get_document(d['document_id'])['version_family_id'] is None for d in documents.values()))
    check('local_embedding_runtime_not_loaded','fastembed' not in sys.modules)
    check('original_public_pdfs_unchanged',all(hashlib.sha256((ROOT/'data/samples'/n).read_bytes()).hexdigest()==h for n,h in source_hashes.items()))
    report={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'documents':documents,'selections':selections,'source_sha256':source_hashes,'checks':checks,
        'passed':sum(c['passed'] for c in checks),'total':len(checks),'model_calls':0,'network_used':False}
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"{report['passed']}/{report['total']}",flush=True)
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
