import math
import xml.etree.ElementTree as ET

import pytest

from research_workspace.library import Library
from research_workspace.visualization import DataStudio, render_chart


def studio_data(tmp_path, content=b'metric,A,B,C\naccuracy,0.7,0.8,0.9\nrecall,0.6,0.7,0.9\nspeed,0.4,0.8,0.6'):
    studio = DataStudio(Library(tmp_path))
    data = studio.import_csv(content, 'metrics.csv')
    return studio, data['id']


def test_radar_has_actual_three_dimensional_geometry_and_rotatable_projection(tmp_path):
    studio, dataset = studio_data(tmp_path)
    first = studio.create_chart(dataset, kind='radar3d', x='metric', y=['A','B','C'])
    spec = first['spec']
    assert spec['radar']['height_dimension'] == 'categorical_series'
    assert len({s['z'] for s in spec['series']}) == 3
    for series in spec['series']:
        for value, point in zip(series['original_values'],series['points_3d']):
            assert math.hypot(point['x'],point['y']) == pytest.approx(value)
            assert point['z'] == series['z']
    svg = render_chart(spec)
    ET.fromstring(svg)
    rotated = studio.create_chart(dataset, kind='radar3d', x='metric', y=['A','B','C'], yaw=-40,elevation=65)
    assert render_chart(rotated['spec']) != svg
    assert rotated['spec']['series'] == spec['series']
    assert studio.get_chart(first['id']) == first  # each camera view is independently retained


def test_axis_normalization_preserves_original_values_and_constant_axes(tmp_path):
    studio, dataset = studio_data(tmp_path, b'metric,A,B,C\naccuracy,70,80,90\nlatency,-2,0,2\nconstant,7,7,7')
    with pytest.raises(ValueError,match='0至1'):
        studio.create_chart(dataset,kind='radar3d',x='metric',y=['A','B','C'])
    chart = studio.create_chart(dataset,kind='radar3d',x='metric',y=['A','B','C'],normalize=True)
    assert [s['values'] for s in chart['spec']['series']] == [[0,0,.5],[.5,.5,.5],[1,1,.5]]
    assert chart['spec']['series'][0]['original_values'] == [70,-2,7]
    assert chart['spec']['radar']['axes'][1]['original_min'] == -2
    assert chart['transformations']['normalization_scope'] == 'per_axis_across_series_minmax'
    assert '高度不表示独立测量变量' in chart['caption']


@pytest.mark.parametrize('content,ys,extra',[
    (b'metric,A,B\nx,.1,.2\ny,.3,.4',['A','B'],{}),
    (b'metric,A,B\nx,.1,.2\nx,.3,.4\nz,.5,.6',['A','B'],{}),
    (None,['A'],{}), (None,['A','B'],{'yaw':181}),
    (None,['A','B'],{'elevation':float('nan')}),
])
def test_invalid_radar_mapping_is_rejected(tmp_path,content,ys,extra):
    studio, dataset = studio_data(tmp_path,content) if content else studio_data(tmp_path)
    with pytest.raises(ValueError):
        studio.create_chart(dataset,kind='radar3d',x='metric',y=ys,**extra)


def test_inert_labels_and_reproducible_script_keeps_existing_file(tmp_path,monkeypatch):
    studio, dataset = studio_data(tmp_path,b'metric,A,B\n<script>,.1,.2\ny,.3,.4\nz,.5,.6')
    chart = studio.create_chart(dataset,kind='radar3d',x='metric',y=['A','B'],title='<script>')
    svg = render_chart(chart['spec'])
    assert '<script>' not in svg
    assert '&lt;script&gt;' in svg
    monkeypatch.chdir(tmp_path)
    code = studio.export_script(chart['id'])
    exec(compile(code,'fixed-export.py','exec'),{})
    assert (tmp_path/'chart.svg').read_text(encoding='utf-8') == svg
    with pytest.raises(FileExistsError):
        exec(compile(code,'fixed-export.py','exec'),{})


def test_radar_api_exports_camera_and_rejects_invalid_angles(tmp_path):
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    with TestClient(create_app(tmp_path,start_worker=False),base_url='http://127.0.0.1') as browser:
        data = browser.post('/api/datasets',files={'file':('radar.csv',b'metric,A,B\nx,.1,.2\ny,.3,.4\nz,.5,.6')}).json()['dataset']
        path = '/api/datasets/'+data['id']+'/charts'
        payload = {'kind':'radar3d','x':'metric','y':['A','B'],'yaw':-20,'elevation':60}
        response = browser.post(path,json=payload)
        assert response.status_code == 200
        chart = response.json()['chart']
        assert chart['spec']['radar']['camera'] == {'yaw_degrees':-20,'elevation_degrees':60}
        assert '<svg' in browser.get('/api/charts/'+chart['id']+'/svg').text
        assert browser.get('/api/charts/'+chart['id']+'/json').json() == chart
        assert browser.post(path,json={**payload,'elevation':90}).status_code == 422
