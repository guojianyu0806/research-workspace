"""Byte-preserving boundary samples; not generated from implementation output."""
import csv
import io

import pytest

from scripts.freeze_csv_extremes import DEFAULT, freeze, verify


def test_frozen_sources_and_independent_boundary_counts():
    manifest = verify()
    assert len(manifest['cases']) == 30
    for case in manifest['cases']:
        if not case['import_accepted']:
            continue
        rows = list(csv.reader(io.StringIO((DEFAULT/case['path']).read_bytes().decode(case['encoding']))))
        assert rows[0] == case['headers'] and len(rows)-1 == case['row_count']
    by_id = {c['id']: c for c in manifest['cases']}
    assert by_id['rows-500']['chart_accepted']
    assert not by_id['rows-501']['chart_accepted']
    assert by_id['rows-10000']['import_accepted']
    assert not by_id['rows-10001']['import_accepted']
    assert {c['encoding'] for c in manifest['cases']} == {'utf-8','utf-8-sig','gb18030'}


def test_freeze_refuses_existing_directory(tmp_path):
    with pytest.raises(FileExistsError):
        freeze(tmp_path)
