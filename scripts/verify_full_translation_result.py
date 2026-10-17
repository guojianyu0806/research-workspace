"""Independent whole-paper completeness checks for an actual completed translation."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json,re
from pathlib import Path
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library


def main():
    parser=argparse.ArgumentParser();parser.add_argument('job_id')
    parser.add_argument('--page',type=int);parser.add_argument('--expected-blocks',type=int,default=107)
    parser.add_argument('--require-glossary',action='store_true');parser.add_argument('--check-first-page-order',action='store_true')
    args=parser.parse_args()
    output=ROOT/'artifacts/full-translation-checks'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False);checks=[]
    def check(name,value):
        checks.append({'name':name,'passed':bool(value)});assert value,name
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as client:
        job=client.get('/api/research/tasks/'+args.job_id).json()['job']
        (output/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        check('selected_scope_completed',job['status']=='completed' and job['result']['scope']==('page' if args.page else 'document'))
        result=job['result'];doc_id=result['document_id'];run_id=result['run_id'];library=Library(ROOT/'data')
        original=[b for b in library._evidence_blocks(doc_id,run_id) if not args.page or b['page']==args.page];translated=result['blocks']
        check('all_selected_pages',result['total_pages']==6 and result['parsed_pages']==(1 if args.page else 6) and not result['missing_pages'])
        check('all_blocks_exact_order',len(translated)==len(original)==args.expected_blocks and [b['block_id'] for b in translated]==[b['block_id'] for b in original])
        check('unique_blocks',len({b['block_id'] for b in translated})==args.expected_blocks)
        for i,(source,target) in enumerate(zip(original,translated)):
            check(f'block_{i}_source_identity',all(source[k]==target[k] for k in ('document_id','run_id','block_id','page','bbox')) and target['source_text']==source['text'])
            numbers=lambda text:Counter(re.findall(r'\d+(?:[.,]\d+)*',text))
            check(f'block_{i}_numbers_and_content',bool(target['translated_text'].strip()) and numbers(source['text'])==numbers(target['translated_text']) and not re.search(r'⟪K[a-f0-9]{12}_\d+⟫',target['translated_text']))
        # Select the earliest actual retry snapshot; never reconstruct paid results.
        snapshots=sorted((ROOT/'artifacts/full-translation').glob('*/before-retry.json'))
        matching=[value for path in snapshots if (value:=json.loads(path.read_text(encoding='utf-8')))['id']==job['id']]
        saved={b['block_id']:b for b in translated}
        if matching:
            before=matching[0]
            check('first_six_responses_unchanged',len(before['completed_items'])==6 and all(saved[b['block_id']]==b for b in before['completed_items']))
        glossary=[b for b in translated if re.search(r'perplexit(?:y|ies)',b['source_text'],re.I)]
        check('perplexity_term_consistency',len(glossary)>=(1 if args.page else 5) and all('困惑度' in b['translated_text'] for b in glossary))
        if not args.page:check('original_arxiv_and_date',any('arXiv:1312.3005v1' in b['translated_text'] and '11 Dec 2013' in b['translated_text'] for b in translated))
        if matching:check('prior_failures_retained',sum(e['stage']=='failed' for e in job['events'])>=2)
        if args.check_first_page_order:
            opening=[b['source_text'] for b in translated if b['page']==1]
            rank=lambda prefix:next(i for i,text in enumerate(opening) if text.startswith(prefix))
            check('authors_and_abstract_before_body',rank('Phillipp Koehn')<rank('Abstract')<rank('We propose a new benchmark'))
            check('left_to_right_continuation',rank('Statistical language modeling has')<rank('The performance of LMs')<rank('and match of the training data'))
            previous=client.get('/api/research/tasks/be144d2fc3cb4e25be78df0f9146e1ac').json()['job']
            baseline=json.loads((ROOT/'artifacts/full-translation-checks/20261017T004212572020Z/job.json').read_text(encoding='utf-8'))
            check('old_completed_translation_not_rewritten',previous['result']==baseline['result'] and previous['result']['run_id']!=run_id)
        if args.require_glossary:
            check('glossary_frozen',result['glossary']==job['payload']['glossary'] and len(result['glossary'])==6)
            for term in ['n-gram','n-grams','5-gram','4-gram','9-gram']:
                # A printed "n-\ngram" is the same named term as n-gram;
                # normalize only this wrap in the checker, never stored source.
                occurrences=lambda text:len(re.findall(r'(?<![A-Za-z0-9_])'+re.escape(term)+r'(?![A-Za-z0-9_])',
                    re.sub(r'-[ \t]*\r?\n[ \t]*','-',text),re.I))
                check('controlled_term_'+term,all(occurrences(b['source_text'])==occurrences(b['translated_text']) for b in translated))
        usage=job['usage'];calls=job['model_calls']
        check('all_paid_attempts_have_pro_receipts',usage['calls']==usage['usage_reported_calls']==len(calls) and all(c['model']=='deepseek-v4-pro' and c['http_status']==200 for c in calls))
        for key in ('total_tokens','prompt_tokens','completion_tokens'):
            check('usage_sum_'+key,usage[key]==sum(c['usage'][key] for c in calls))
        export=client.get('/api/research/tasks/'+job['id']+'/export').text
        check('export_contains_every_block_id',all(b['block_id'] in export for b in translated))
        (output/'export.md').write_text(export,encoding='utf-8')
    samples=[]
    for page in ([args.page] if args.page else range(1,7)):
        candidates=[b for b in translated if b['page']==page]
        selected=max(candidates,key=lambda b:len(b['source_text']))
        samples.append({'page':page,'block_id':selected['block_id'],'source':selected['source_text'],'translation':selected['translated_text']})
    (output/'page-samples.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'checks.json').write_text(json.dumps({'checks':checks,'passed':len(checks),'terminology_blocks':len(glossary),'scope':'whole public PDF completeness, original identity, numbers, one repeated term; not full semantic quality scoring'},ensure_ascii=False,indent=2),encoding='utf-8')
    print('CHECKS',len(checks),'TERM_BLOCKS',len(glossary),'OUTPUT',output)


if __name__=='__main__':main()
