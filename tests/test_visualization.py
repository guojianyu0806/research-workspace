import xml.etree.ElementTree as ET

import pytest

from research_workspace.library import Library
from research_workspace.visualization import DataStudio, render_chart, render_diagram


def test_csv_mapping_missing_values_and_reproducible_svg(tmp_path):
    studio = DataStudio(Library(tmp_path))
    data = studio.import_csv('轮数,损失\n1,0.9\n2,\n3,0.2\n'.encode(), 'run.csv')
    assert data['columns'][1]['missing'] == 1
    with pytest.raises(ValueError, match='第3行'):
        studio.create_chart(data['id'],kind='line',x='轮数',y=['损失'])
    chart = studio.create_chart(data['id'],kind='line',x='轮数',y=['损失'],missing='drop')
    assert chart['transformations']['dropped_csv_rows'] == [3]
    assert chart['spec']['series'][0]['values'] == [0.9, 0.2]
    svg = render_chart(chart['spec'])
    ET.fromstring(svg)
    assert '0.9' in chart['caption'] and '显著性' in chart['caption']
    assert 'open("x"' in studio.export_script(chart['id'])
    changed = studio.import_csv('轮数,损失\n1,0.1\n3,0.2\n'.encode(), 'run.csv')
    chart2 = studio.create_chart(changed['id'],kind='line',x='轮数',y=['损失'])
    assert chart2['dataset_sha256'] != chart['dataset_sha256']
    assert render_chart(chart2['spec']) != svg


def test_chart_handles_negative_constant_and_normalized_values(tmp_path):
    studio = DataStudio(Library(tmp_path))
    data = studio.import_csv(b'x,a,b\none,-2,4\ntwo,-2,8', 'x.csv')
    chart = studio.create_chart(data['id'],kind='bar',x='x',y=['a','b'],normalize=True)
    assert chart['spec']['series'][0]['values'] == [0,0]
    assert chart['spec']['series'][1]['values'] == [0,1]
    ET.fromstring(render_chart(chart['spec']))


def test_unsafe_labels_are_inert_and_bad_csv_rejected(tmp_path):
    studio = DataStudio(Library(tmp_path))
    for content in (b'a,a\n1,2', b'a,b\n1,2,3', b''):
        with pytest.raises(ValueError):
            studio.import_csv(content,'bad.csv')
    data = studio.import_csv(b'x,value\n<script>,nan\ngood,2', 'x.csv')
    with pytest.raises(ValueError):
        studio.create_chart(data['id'],kind='line',x='x',y=['value'])
    diagram = render_diagram([{'id':'x','label':'<script>alert(1)</script>'}],[])
    assert '<script>' not in diagram
    ET.fromstring(diagram)
    with pytest.raises(ValueError):
        render_diagram([{'id':'x','label':'X'}],[{'source':'x','target':'missing'}])
