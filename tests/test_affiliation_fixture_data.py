"""Independent frozen source expectations, not application-generated labels."""
import json
from pathlib import Path

import pytest

DATA=Path(__file__).parent/'fixtures/front_matter_continuation/20261020-v1'


def test_source_families_are_separated_with_the_declared_case_counts():
    m=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    assert len(m['cases'])==len({c['id'] for c in m['cases']})==30
    assert len(m['sources'])==15
    assert sum(c['split']=='development' for c in m['cases'])==20
    assert sum(c['split']=='holdout' for c in m['cases'])==10
    dev={s['family'] for s in m['sources'] if s['split']=='development'}
    hold={s['family'] for s in m['sources'] if s['split']=='holdout'}
    assert len(dev)>=3 and len(hold)>=3 and not dev&hold
    assert 'not independent human certification' in m['review']


def test_every_expected_piece_has_its_own_exact_frozen_source_span():
    m=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    sources={s['path']:{b['block_id']:b for b in s['blocks']} for s in m['sources']}
    continuations=[]
    for case in m['cases']:
        for e in case['scope_spans']:
            b=sources[case['source']][e['block_id']]
            assert b['text'][e['char_start']:e['char_end']]==e['text']
            assert b['bbox']==e['bbox'] and e['page']==1
            assert all(0<=v<=1 for v in e['bbox'])
        for item in case['expected_affiliations']:
            assert item['text']==' '.join(p['text'] for p in item['evidence_parts'])
            assert all(p in case['scope_spans'] for p in item['evidence_parts'])
            if len(item['evidence_parts'])>1:continuations.append(case['id'])
    assert set(continuations)=={'graves-department-university','press-school-university'}
    assert {c['id'] for c in m['cases'] if not c['expected_affiliations']}=={
        'attention-illia-no-header-affiliation','summarization-email-not-affiliation'}


def test_frozen_affiliation_version_cannot_replace_existing_inputs(tmp_path):
    from scripts.freeze_affiliation_acceptance import freeze
    old=tmp_path/'old';old.mkdir();kept=old/'marker.txt';kept.write_text('keep')
    with pytest.raises(FileExistsError):freeze(old)
    assert kept.read_text()=='keep'
