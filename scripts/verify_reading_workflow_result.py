"""Independent original-block, review-index and usage checks for a saved real job."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json
from pathlib import Path
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library


def main():
    parser=argparse.ArgumentParser();parser.add_argument('job_id');args=parser.parse_args()
    output=ROOT/'artifacts/reading-workflow'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False);checks=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)})
        assert value,name
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False) as client:
        job=client.get('/api/research/tasks/'+args.job_id).json()['job']
        (output/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        check('completed_workflow',job['kind']=='discover_qa' and job['status']=='completed' and job['progress']==job['total']==3)
        result=job['result'];workflow=result['workflow'];library=Library(ROOT/'data')
        source=workflow['download'];parsed=workflow['parse'];doc=library.get_document(source['document_id'])
        check('actual_new_official_download',not source['duplicate'] and source['source']['url']=='https://arxiv.org/pdf/1312.3005v1')
        check('pdf_sha256',hashlib.sha256(library._source_path(doc['id']).read_bytes()).hexdigest()==source['sha256']==doc['sha256'])
        check('complete_six_page_parse',parsed['pages_done']==parsed['total_pages']==6 and parsed['parse_status']=='completed')
        originals={b['block_id']:b for b in library._evidence_blocks(doc['id'],parsed['run_id'])}
        sources={s['evidence_id']:s for s in result['sources']};audit=result['grounding'];catalog=audit['claim_catalog'];reviews=audit['claim_reviews']
        check('grounding_and_complete_response',audit['status']=='passed' and not audit['response_coverage'][-1]['missing_aspects'] and not audit['response_coverage'][-1]['unverified_statements'])
        check('expected_two_required_aspects',len(result['claims'])==2)
        for index,claim in enumerate(result['claims']):
            entry=catalog[index];review=next(r for r in reviews if r['claim_id']==entry['claim_id'])
            check(f'claim_{index}_review_identity',entry['claim_index']==review['claim_index']==index and entry['text']==claim['text'] and review['verdict']=='supported')
            check(f'claim_{index}_owns_citations',bool(claim['citations']) and len(entry['citations'])==len(claim['citations']))
            for n,(citation,record) in enumerate(zip(claim['citations'],entry['citations'])):
                evidence=sources[citation['evidence_id']];original=originals[evidence['block_id']]
                check(f'claim_{index}_citation_{n}_identity',all(record[k]==evidence[k]==original[k] for k in ('document_id','run_id','block_id','page','bbox')))
                check(f'claim_{index}_citation_{n}_quote',citation['quote']==record['quote'] and citation['evidence_id']==record['evidence_id'] and ' '.join(citation['quote'].split()) in ' '.join(original['text'].split()))
        usage=job['usage'];calls=job['model_calls']
        check('pro_only_complete_receipts',all(c['model']=='deepseek-v4-pro' and c['state']=='response_received' and c['http_status']==200 for c in calls) and usage['calls']==usage['usage_reported_calls']==len(calls))
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            check('usage_sum_'+key,usage[key]==sum(c['usage'][key] for c in calls))
        check('persisted_cost_range_sum',all(Decimal(usage['cost'][k])==sum(Decimal(c['cost_estimate'][k]) for c in calls) for k in ('min','max')))
        export=client.get('/api/research/tasks/'+job['id']+'/export').text
        check('export_retains_receipts',all(c['id'] in export for c in calls) and '非实际账单' in export)
        (output/'export.md').write_text(export,encoding='utf-8')
    (output/'checks.json').write_text(json.dumps({'checks':checks,'passed':len(checks),'scope':'source identity, own quotes, review correspondence, usage sums; not universal semantic correctness'},ensure_ascii=False,indent=2),encoding='utf-8')
    print('CHECKS',len(checks),'OUTPUT',output)


if __name__=='__main__':main()
