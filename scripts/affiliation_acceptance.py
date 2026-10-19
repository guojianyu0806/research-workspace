"""Evaluate frozen source groups; holdout requires an explicit release flag."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.freeze_affiliation_acceptance import DEFAULT,verify
from scripts.freeze_local_acceptance import main_snapshot,sha256
from research_workspace.structure import extract_structure


def overlaps(a,b):
    return a['block_id']==b['block_id'] and a['char_start']<b['char_end'] and b['char_start']<a['char_end']


def canonical(item):
    parts=item.get('evidence_parts') or [item['evidence']]
    return item['text'],tuple((p['block_id'],p['char_start'],p['char_end'],p['text']) for p in parts)


def unsafe_join(case, observed):
    """Missing fragments lower recovery; mixing entities/source text is a veto."""
    expected=case['expected_affiliations']
    owners=set()
    for p in observed.get('evidence_parts') or [observed['evidence']]:
        matches={i for i,item in enumerate(expected) for s in item['evidence_parts']
                 if p['block_id']==s['block_id'] and s['char_start']<=p['char_start']<p['char_end']<=s['char_end']}
        if not matches:return True
        owners.update(matches)
    return len(owners)!=1


def evaluate(split,output=None):
    m=verify(DEFAULT);before=main_snapshot()
    output=output or ROOT/'artifacts/front-matter-continuation'/(
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+split)
    output.mkdir(parents=True,exist_ok=False)
    cases=[c for c in m['cases'] if c['split']==split]
    sources={s['path']:s for s in m['sources'] if s['split']==split};results={}
    for path,source in sources.items():
        blocks=source['blocks'];front=extract_structure(blocks,blocks[0]['document_id'],blocks[0]['run_id'])['front_matter']
        assert front['audit']['source_spans_valid']
        by_id={b['block_id']:b for b in blocks}
        for org in front['affiliations']:
            for p in org.get('evidence_parts') or [org['evidence']]:
                source_block=by_id[p['block_id']]
                assert source_block['text'][p['char_start']:p['char_end']]==p['text']
                assert all(p[k]==source_block[k] for k in ['document_id','run_id','page','bbox'])
        results[path]=front
    rows=[]
    for case in cases:
        front=results[case['source']]
        actual=[o for o in front['affiliations'] if any(overlaps(p,s)
                for p in o.get('evidence_parts') or [o['evidence']] for s in case['scope_spans'])]
        expected=sorted(canonical(o) for o in case['expected_affiliations'])
        observed=sorted(canonical(o) for o in actual)
        rows.append({'id':case['id'],'source':case['source'],'positive':bool(expected),
                     'unsafe_joins':sum(unsafe_join(case,o) for o in actual),
                     'passed':expected==observed,'expected':expected,'actual':observed})
    positive=[r for r in rows if r['positive']]
    negatives=[r for r in rows if not r['positive']]
    rate=sum(r['passed'] for r in positive)/len(positive)
    after=main_snapshot();assert before==after
    report={'split':split,'input_manifest_sha256':sha256(DEFAULT/'manifest.json'),
        'source_files_sha256':{name:sha256(ROOT/name) for name in ['research_workspace/front_matter.py','research_workspace/structure.py']},
        'results':rows,'source_results':results,'positive_count':len(positive),
        'positive_complete_recovery':rate,'negative_count':len(negatives),
        'negative_violations':sum(not r['passed'] for r in negatives),
        'unsafe_join_count':sum(r['unsafe_joins'] for r in rows),
        'main_unchanged':True,'scope':m['review'],
        'passed':rate>=.9 and all(r['passed'] for r in negatives) and not any(r['unsafe_joins'] for r in rows)}
    with (output/'acceptance.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(output),'split':split,'positive_recovery':rate,
        'negative_violations':report['negative_violations'],'unsafe_join_count':report['unsafe_join_count'],
        'passed':report['passed']}),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--split',choices=['development','holdout'],default='development')
    parser.add_argument('--release-holdout',action='store_true');args=parser.parse_args()
    if args.split=='holdout' and not args.release_holdout:parser.error('Freeze implementation before explicitly releasing held-out sources.')
    r=evaluate(args.split);raise SystemExit(0 if r['passed'] else 1)
