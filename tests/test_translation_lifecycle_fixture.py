import hashlib
import json
from pathlib import Path

from scripts.freeze_translation_lifecycle import target_text

FIXTURE = Path(__file__).parent/'fixtures/translation_lifecycle/20261020-v1/manifest.json'


def test_frozen_source_positions_and_synthetic_targets():
    manifest = json.loads(FIXTURE.read_text(encoding='utf-8'))
    rows = manifest['source_records']
    assert len(rows) == len({r['block_id'] for r in rows}) == 260
    for i, row in enumerate(rows):
        assert row['ordinal'] == i and row['page'] > 0 and row['source_chars'] > 0
        x0, y0, x1, y1 = row['bbox']
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert hashlib.sha256(target_text(i).encode()).hexdigest() == row['target_text_sha256']
        assert len(target_text(i)) == row['target_chars']
        assert target_text(i).startswith('【本地联动夹具；非模型译文】')


def test_lifecycle_cases_do_not_reuse_existing_ids_for_prepended_rows():
    manifest = json.loads(FIXTURE.read_text(encoding='utf-8'))
    assert {case['id'] for case in manifest['cases']} == {'valid', 'legacy', 'duplicate', 'progress'}
    assert all(3 <= case['slice'][0] < case['slice'][1] <= 260 for case in manifest['cases'])
    assert manifest['gates']['ambiguous_source_navigation'] is False
    assert 'Not Pro or language-quality' in manifest['scope']


def test_literal_formula_code_and_untrusted_markup_remain_strings():
    assert '\\sum_' in target_text(2) and '√' in target_text(2)
    assert '```python' in target_text(3) and '\n    print' in target_text(3)
    assert '<script>window.fixtureXss=2</script>' in target_text(6)
