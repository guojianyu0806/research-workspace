import json
import math
import subprocess
import sys

import pytest

from research_workspace.chart_numeric import finite_lerp, unit_fraction
from research_workspace.library import Library
from research_workspace.visualization import DataStudio, render_chart


@pytest.mark.parametrize('low,mid,high', [(-1e308,0,1e308),(0,5e-324,1e-323),(-8,-4,0)])
def test_finite_scaling_preserves_endpoints_and_known_midpoints(low,mid,high):
    assert [unit_fraction(v,low,high) for v in (low,mid,high)] == [0,.5,1]
    ticks = [finite_lerp(low,high,i/5) for i in range(6)]
    assert all(math.isfinite(v) for v in ticks) and ticks == sorted(ticks)
    assert ticks[0] == low and ticks[-1] == high


@pytest.mark.parametrize('normalize',[False,True])
def test_huge_values_reproduce_in_standalone_script_without_nonfinite_geometry(tmp_path,normalize):
    studio=DataStudio(Library(tmp_path/'library'))
    data=studio.import_csv(b'x,value\na,-1e308\nb,0\nc,1e308','huge.csv')
    chart=studio.create_chart(data['id'],kind='line',x='x',y=['value'],normalize=normalize)
    assert chart['spec']['series'][0]['original_values'] == [-1e308,0,1e308]
    if normalize:assert chart['spec']['series'][0]['values'] == [0,.5,1]
    json.dumps(chart,allow_nan=False)
    expected=render_chart(chart['spec'])
    script=tmp_path/'reproduce.py';script.write_text(studio.export_script(chart['id']),encoding='utf-8')
    subprocess.run([sys.executable,'-I',str(script)],cwd=tmp_path,check=True,capture_output=True)
    assert (tmp_path/'chart.svg').read_text(encoding='utf-8') == expected
    assert '首末差inf' not in chart['caption']


@pytest.mark.parametrize('content',[b'x,value\na\x00b,1',b'x,value\n"not closed,1',b'x,value\na\x0bb,1'])
def test_invalid_control_and_csv_quotes_are_rejected_before_storing(tmp_path,content):
    studio=DataStudio(Library(tmp_path))
    with pytest.raises(ValueError):studio.import_csv(content,'invalid.csv')
    assert studio.list_datasets() == []


def test_quoted_newlines_reproduce_identical_utf8_svg_bytes_on_windows(tmp_path):
    studio=DataStudio(Library(tmp_path/'library'))
    data=studio.import_csv(b'x,value\n"first\nsecond",1\nthird,2','multiline.csv')
    chart=studio.create_chart(data['id'],kind='line',x='x',y=['value'])
    script=tmp_path/'reproduce.py';script.write_text(studio.export_script(chart['id']),encoding='utf-8')
    subprocess.run([sys.executable,'-I',str(script)],cwd=tmp_path,check=True,capture_output=True)
    assert (tmp_path/'chart.svg').read_bytes() == render_chart(chart['spec']).encode('utf-8')


def test_all_thirty_frozen_csv_contracts_pass_without_truncation(tmp_path):
    from scripts.csv_extremes_acceptance import evaluate
    result=evaluate(tmp_path/'acceptance')
    assert result['passed'] and len(result['cases']) == 30


@pytest.mark.parametrize('options',[{'title':'x'*151},{'units':{'value':'u'*41}},
                                    {'title':'a\x00b'},{'units':{'value':'a\x0bb'}}])
def test_invalid_chart_text_is_rejected_before_creating_history(tmp_path,options):
    studio=DataStudio(Library(tmp_path))
    data=studio.import_csv(b'x,value\na,1','text.csv')
    with pytest.raises(ValueError):studio.create_chart(data['id'],kind='line',x='x',y=['value'],**options)
    with studio.library._connection() as db:
        assert db.execute('SELECT COUNT(*) FROM charts').fetchone()[0] == 0


def test_new_layout_does_not_rewrite_legacy_chart_records(tmp_path):
    studio=DataStudio(Library(tmp_path))
    data=studio.import_csv(b'x,value\na,1\nb,2','old.csv')
    legacy={'id':'legacy-fixture','dataset_id':data['id'],'template_version':'svg-chart-v1',
            'spec':{'kind':'line','title':'Existing chart','x':'x','labels':['a','b'],
                    'series':[{'name':'value','unit':'','values':[1,2]}]}}
    with studio.library._connection() as db:
        db.execute('INSERT INTO charts VALUES(?,?,?,?)',('legacy-fixture',data['id'],'fixed',json.dumps(legacy)))
    before=render_chart(legacy['spec'])
    new=studio.create_chart(data['id'],kind='line',x='x',y=['value'])
    assert new['spec']['layout_version'] == 'cartesian-v2'
    assert studio.get_chart('legacy-fixture') == legacy
    assert render_chart(studio.get_chart('legacy-fixture')['spec']) == before
    assert 'viewBox="0 0 1000 600"' in before
