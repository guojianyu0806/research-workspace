import json
from pathlib import Path

import pymupdf
import pytest

from scripts.freeze_book_reading import DEFAULT,freeze
from scripts.freeze_local_acceptance import sha256


def test_original_page_observations_cover_every_physical_page():
    manifest=json.loads((DEFAULT/'manifest.json').read_text(encoding='utf-8'))
    rows=manifest['original']['pages']
    assert [row['page'] for row in rows]==list(range(1,542))
    assert [row['page'] for row in rows if not row['native_has_text']]==[2,4,10,12]
    assert all(row['rotation']==0 and 0<=row['largest_clipped_image_fraction']<=1 for row in rows)
    assert all(len(row['native_text_sha256'])==64 for row in rows)
    assert {73,168,311,541}.issubset(manifest['reading_pages'])


@pytest.mark.parametrize('name',['rotated-native.pdf','oversize-native.pdf'])
def test_derived_files_keep_frozen_geometry_and_actual_native_content(name):
    manifest=json.loads((DEFAULT/'manifest.json').read_text(encoding='utf-8'))
    case=next(c for c in manifest['derived'] if c['path']==name)
    assert sha256(DEFAULT/name)==case['sha256']
    with pymupdf.open(DEFAULT/name) as pdf:
        assert len(pdf)==len(case['pages'])
        for page,expected in zip(pdf,case['pages']):
            assert page.get_text().strip()
            if 'rotation' in expected:assert page.rotation==expected['rotation']
            else:
                assert page.rect.width==expected['width'] and page.rect.height==expected['height']
                if expected['readable']:assert page.rect.get_area()*expected['safe_scale']**2<=16000000
                else:assert page.rect.get_area()*.25**2>16000000


def test_freeze_will_not_replace_existing_pdf_or_manifest(tmp_path):
    with pytest.raises(FileExistsError):freeze(tmp_path)
