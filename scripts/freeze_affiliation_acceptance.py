"""Freeze source-reviewed expectations before modifying affiliation extraction.

Expected strings below were transcribed from Poppler-rendered public PDF pages;
they are not generated from the application's metadata candidates. The review
was performed by the assistant, not independently certified by a human reviewer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot, sha256
DEFAULT = ROOT / 'tests/fixtures/front_matter_continuation/20261020-v1'


def definitions():
    cases = []

    def add(name, source, expected, *, split='development', empty_scope=None, protected=(), note=''):
        # Each expected institution is a list of (verbatim text, block selector)
        # pairs. Multiple pairs mean one continued institution; multiple lists
        # mean separate institutions which must NOT be concatenated.
        cases.append(dict(id=name, source=source, split=split, expected=expected,
                          empty_scope=empty_scope, protected=list(protected), note=note))

    attention = 'data/samples/1706.03762v7.pdf'
    for name, selector, institution in [
        ('attention-ashish', 4, 'Google Brain'), ('attention-noam', 6, 'Google Brain'),
        ('attention-niki', 7, 'Google Research'), ('attention-jakob', 8, 'Google Research'),
        ('attention-llion', 9, 'Google Research'), ('attention-aidan', 10, 'University of Toronto'),
        ('attention-lukasz', 12, 'Google Brain')]:
        add(name, attention, [[(institution, selector)]], note='Separate author column; do not absorb another column or email.')
    add('attention-illia-no-header-affiliation', attention, [], empty_scope='Illia Polosukhin',
        note='Header has a name/contact but no institution; later work-performed footnote is not a current affiliation.')
    add('bert-shared', 'data/samples/1810.04805v2.pdf', [[('Google AI Language', None)]])
    rag = 'data/samples/2005.11401v4.pdf'
    for name, value in [('rag-facebook', 'Facebook AI Research'),
                        ('rag-ucl', 'University College London'), ('rag-nyu', 'New York University')]:
        add(name, rag, [[(value, None)]], note='One explicitly marked institution, not the whole semicolon-separated line.')
    add('graves-department-university', 'data/corpus/1308.0850v1.pdf',
        [[('Department of Computer Science', None), ('University of Toronto', None)]],
        note='Native PDF uses different blocks; department and university form one visible affiliation.')
    billion = 'data/corpus/1312.3005v1.pdf'
    add('billion-google-address-stop', billion, [[('Google, 1600 Amphitheatre Parkway', None)]],
        protected=('Mountain View, CA 94043, USA',), note='Keep same-line address; do not absorb the next postal line.')
    add('billion-edinburgh-address-stop', billion, [[('University of Edinburgh', None)]],
        protected=('10 Crichton Street, Room 4.19', 'Edinburgh, EH8 9AB, United Kingdom'))
    add('luong-department-inline-university', 'data/corpus/1508.04025v1.pdf',
        [[('Computer Science Department Stanford University, Stanford, CA, 94305', None)]])
    add('sennrich-school-inline-university', 'data/corpus/1508.07909v1.pdf',
        [[('School of Informatics, University of Edinburgh', None)]])
    moe = 'data/corpus/1701.06538v1.pdf'
    add('moe-google-before-contact', moe, [[('Google Brain', None)]],
        note='Institution precedes a contact list on the same line; prefix marker is not part of the name.')
    add('moe-jagiellonian-before-contact', moe, [[('Jagiellonian University, Cracow', None)]])
    add('summarization-email-not-affiliation', 'data/corpus/1705.04304v1.pdf', [],
        empty_scope='Romain Paulus', note='Do not infer Salesforce from email domains.')

    add('press-school-university', 'data/corpus/1608.05859v1.pdf',
        [[('The Blavatnik School of Computer Science', None), ('Tel Aviv University', None)]], split='holdout',
        note='Independent native cross-block continuation source.')
    layer = 'data/corpus/1607.06450v1.pdf'
    add('layer-ba', layer, [[('University of Toronto', 'Jimmy Lei Ba')]], split='holdout')
    add('layer-kiros', layer, [[('University of Toronto', 'Jamie Ryan Kiros')]], split='holdout')
    add('layer-hinton-two-institutions', layer,
        [[('University of Toronto', 'Geoffrey E. Hinton')], [('and Google Inc.', None)]], split='holdout',
        note='Two distinct institutions. Do not turn university plus Google into one continued institution.')
    add('chollet-corporate-period', 'data/corpus/1610.02357v1.pdf', [[('Google, Inc.', None)]], split='holdout')
    add('bytenet-contact-above', 'data/corpus/1610.10099v1.pdf',
        [[('Google DeepMind, London, UK', None)]], split='holdout')
    structured = 'data/corpus/1703.03130v1.pdf'
    add('structured-ibm-marker', structured, [[('IBM Watson', None)]], split='holdout')
    add('structured-mila-role-stop', structured,
        [[('Montreal Institute for Learning Algorithms (MILA), Universit´e de Montr´eal', None)]],
        split='holdout', protected=('CIFAR Senior Fellow',),
        note='Keep native accent encoding; the following fellow role is not an institution continuation.')
    nvidia = 'data/corpus/1703.10722v1.pdf'
    add('factorization-left-column', nvidia, [[('NVIDIA', 'Oleksii Kuchaiev')]], split='holdout')
    add('factorization-right-column', nvidia, [[('NVIDIA', 'Boris Ginsburg')]], split='holdout')
    assert len(cases) == 30
    return cases


def native_header(path, digest):
    with pymupdf.open(path) as pdf:
        page = pdf[0]; width, height = page.rect.width, page.rect.height
        raw = page.get_text('blocks')
        boundary = next((i for i,b in enumerate(raw) if any(
            re.fullmatch(r'(?:abstract|摘要|introduction|引言)', line.strip(), re.I)
            for line in b[4].splitlines())), None)
        if boundary is None:
            raise ValueError('No bounded native header: ' + str(path))
        return [{'document_id': 'native-' + digest[:16], 'run_id': 'frozen-header-' + digest[:16],
                 'block_id': f'native-{digest[:16]}-p1-b{i}', 'native_block_index': i, 'page': 1,
                 'bbox': [b[0]/width,b[1]/height,b[2]/width,b[3]/height], 'text': b[4]}
                for i,b in enumerate(raw[:boundary+1])]


def locate(blocks, value, selector=None):
    matches = [b for b in blocks if value in b['text'] and
               (selector is None or
                isinstance(selector,int) and b['native_block_index']==selector or
                isinstance(selector,str) and selector in b['text'])]
    if len(matches) != 1 or matches[0]['text'].count(value) != 1:
        raise ValueError(f'Non-unique frozen source: {value!r}, {selector!r}')
    b=matches[0]; start=b['text'].index(value)
    return {k:b[k] for k in ('block_id','page','bbox')} | {
        'text':value,'char_start':start,'char_end':start+len(value)}


def freeze(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=False)
    before=main_snapshot();sources={};cases=[]
    for definition in definitions():
        source=definition['source']
        if source not in sources:
            digest=sha256(ROOT/source)
            sources[source]={'path':source,'sha256':digest,
                'family':re.sub(r'v\d+$','',Path(source).stem), 'split':definition['split'],
                'blocks':native_header(ROOT/source,digest)}
        src=sources[source];assert src['split']==definition['split']
        blocks=src['blocks'];expected=[];scope=[]
        for parts in definition['expected']:
            evidence=[locate(blocks,text,selector) for text,selector in parts]
            expected.append({'text':' '.join(e['text'] for e in evidence),'evidence_parts':evidence})
            scope.extend(evidence)
        if definition['empty_scope']:
            matched=[b for b in blocks if definition['empty_scope'] in b['text']];assert len(matched)==1
            b=matched[0];scope.append({k:b[k] for k in ('block_id','page','bbox')} |
                {'text':b['text'],'char_start':0,'char_end':len(b['text'])})
        for protected in definition['protected']:scope.append(locate(blocks,protected))
        cases.append({k:definition[k] for k in ('id','source','split','note')} |
            {'expected_affiliations':expected,'scope_spans':scope,'protected_texts':definition['protected']})
    development={s['family'] for s in sources.values() if s['split']=='development'}
    holdout={s['family'] for s in sources.values() if s['split']=='holdout'}
    assert not development & holdout and len(development)>=3 and len(holdout)>=3
    manifest={'version':'affiliation-continuation-20261020-v1',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'review':'Assistant visual transcription of Poppler first-page renders, not independent human certification.',
        'render_evidence':'artifacts/front-matter-continuation/source-review-20261019T233324129032Z',
        'source_scope':'Native first-page header snapshots; no application parser output used for expected labels.',
        'sources':list(sources.values()),'cases':cases,
        'contract':{'real_groups':30,'development_groups':20,'holdout_groups':10,
                    'clear_positive_complete_recovery':.9,'false_concatenations':0,
                    'all_source_spans_valid':True,'holdout_policy':'No threshold tuning on holdout; failure requires additional independent held-out sources.'},
        'limits':['Two native cross-block hierarchy cases; single-line and boundary cases are reported separately.',
                  'No general OCR or affiliation-identity accuracy claim; synthetic layouts must be identified separately.']}
    with (directory/'manifest.json').open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(manifest,stream,ensure_ascii=False,indent=2);stream.write('\n')
    assert before==main_snapshot()
    print(json.dumps({'output':str(directory),'sources':len(sources),'groups':len(cases),'main_unchanged':True}))


def verify(directory):
    directory=Path(directory);m=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    for source in m['sources']:
        assert sha256(ROOT/source['path'])==source['sha256'],source['path']
    sources={s['path']:{b['block_id']:b for b in s['blocks']} for s in m['sources']}
    for case in m['cases']:
        for e in case['scope_spans']:
            b=sources[case['source']][e['block_id']]
            assert b['text'][e['char_start']:e['char_end']]==e['text'] and b['bbox']==e['bbox']
        for item in case['expected_affiliations']:
            assert item['text']==' '.join(e['text'] for e in item['evidence_parts'])
    return m


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=DEFAULT)
    parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    if args.verify:
        m=verify(args.output);print(json.dumps({'verified':True,'sources':len(m['sources']),'groups':len(m['cases'])}))
    else:freeze(args.output)
