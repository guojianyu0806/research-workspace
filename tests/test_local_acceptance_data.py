"""The frozen synthetic inputs have independent, deterministic expected facts."""
import csv
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest


DATA=Path(__file__).parent/'fixtures/local_acceptance/20261019-v1'


def test_frozen_account_inputs_and_same_file_have_distinct_facts():
    manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    markers=set();hashes=set()
    for actor in manifest['fixtures']:
        assert actor['marker'] not in markers
        markers.add(actor['marker'])
        content=(DATA/actor['pdf']).read_bytes()
        assert hashlib.sha256(content).hexdigest()==actor['pdf_sha256']
        hashes.add(actor['pdf_sha256'])
        with pymupdf.open(stream=content,filetype='pdf') as pdf:
            assert len(pdf)==8
            for index,page in enumerate(pdf):
                text=page.get_text()
                assert actor['marker'] in text
                assert actor['historical_quote'] in text
                assert f'Physical page {index+1} of 8.' in text
                assert not any(m['marker'] in text for m in manifest['fixtures'] if m!=actor)
        csv_content=(DATA/actor['csv']).read_bytes()
        assert hashlib.sha256(csv_content).hexdigest()==actor['csv_sha256']
        rows=list(csv.reader(csv_content.decode().splitlines()))
        assert rows[1][0]==actor['marker']
        assert [int(r[1]) for r in rows[1:]]==actor['expected_values']
    assert len(markers)==len(hashes)==10
    shared=manifest['shared']
    assert hashlib.sha256((DATA/shared['pdf']).read_bytes()).hexdigest()==shared['sha256']


def test_frozen_contract_preserves_real_duration_and_pending_gold():
    contract=json.loads((DATA/'contract.json').read_text(encoding='utf-8'))
    manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    assert (contract['warmup_seconds'],contract['mixed_seconds'],contract['extended_seconds'])==(300,1800,900)
    assert contract['browser_contexts']==5 and contract['memory_rounds']==3
    assert manifest['book']['native_blank_pages']==[2,4,10,12]
    assert all(v['status']=='not_yet_frozen' for v in manifest['future_annotations'].values())


def test_freeze_refuses_to_replace_an_existing_version(tmp_path):
    from scripts.freeze_local_acceptance import freeze
    retained=tmp_path/'old';retained.mkdir();source=retained/'keep.txt';source.write_text('original')
    with pytest.raises(FileExistsError):freeze(retained)
    assert source.read_text()=='original'
