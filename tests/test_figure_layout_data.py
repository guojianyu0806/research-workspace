import pytest
from scripts.freeze_figure_layout import freeze, verify


def test_frozen_layout_contract_counts_and_camera_boundaries():
    data=verify();assert len(data['cases'])==30 and sum(c['accepted'] for c in data['cases'])==24
    cameras=[c['payload'] for c in data['cases'] if c['id'].startswith('camera-')]
    assert {(p['yaw'],p['elevation']) for p in cameras}=={(y,e) for y in (-180,-90,0,90,180) for e in (15,30,75)}
    assert 'Synthetic' in data['scope']


def test_freeze_cannot_replace_old_version(tmp_path):
    with pytest.raises(FileExistsError):freeze(tmp_path)
