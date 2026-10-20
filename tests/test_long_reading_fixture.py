"""Frozen inputs are independent of paging and bilingual rendering output."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import freeze_long_reading as source


@pytest.mark.parametrize('case_id',['mixed-1000','mixed-5000','missing-5000','duplicate-1000','wrong-run-1000'])
def test_frozen_generation_and_integrity(case_id):
    manifest=json.loads((source.DEFAULT/'manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(Path(source.__file__).read_bytes()).hexdigest()==manifest['generator_sha256']
    case=next(c for c in manifest['cases'] if c['id']==case_id)
    blocks=source.synthetic_blocks(case['count'],case['variant'])
    assert hashlib.sha256(source.canonical(blocks)).hexdigest()==case['canonical_sha256']
    assert len(blocks)==case['count']
    assert sum(not b['translated_text'] for b in blocks)==case['missing_translations']
    assert len(blocks)-len({b['block_id'] for b in blocks})==case['duplicate_ids']
    assert sum(b['run_id']!='synthetic-long-run-v1' for b in blocks)==case['wrong_run_count']
    assert max(len(b['source_text']) for b in blocks)>100*min(len(b['source_text']) for b in blocks)
    assert manifest['saved_translation']['blocks']==107 and manifest['saved_translation']['pages']==6
    assert manifest['book']['pages']==541 and manifest['book']['blank_physical_pages']==[2,4,10,12]
    assert manifest['model_calls']==0 and manifest['main_unchanged']


def test_source_freeze_refuses_replacement(monkeypatch,tmp_path):
    monkeypatch.setattr(source,'DEFAULT',tmp_path)
    with pytest.raises(FileExistsError):source.freeze()
