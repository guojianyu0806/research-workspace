import xml.etree.ElementTree as ET
import subprocess
import sys

import pytest

from research_workspace.library import Library
from research_workspace.visualization import DataStudio, render_chart, render_diagram
from research_workspace.visualization import export_diagram_script


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


def test_exported_architecture_script_reproduces_svg_without_repo_and_preserves_existing_file(tmp_path):
    nodes=[{'id':'question','label':'科研问题'},{'id':'retriever','label':'检索器 <script>bad()</script>'}]
    edges=[{'source':'question','target':'retriever'}]
    script=tmp_path/'architecture.py';script.write_text(export_diagram_script(nodes,edges),encoding='utf-8')
    subprocess.run([sys.executable,str(script)],cwd=tmp_path,check=True,capture_output=True)
    expected=render_diagram(nodes,edges)
    assert (tmp_path/'architecture.svg').read_text(encoding='utf-8')==expected
    assert '<script>' not in expected
    second=subprocess.run([sys.executable,str(script)],cwd=tmp_path,capture_output=True)
    assert second.returncode!=0 and (tmp_path/'architecture.svg').read_text(encoding='utf-8')==expected


@pytest.mark.parametrize('values,expected',[(('3','2','1'),'单调不升'),(('1','3','2'),'升降波动'),(('2','2','2'),'保持一致')])
def test_caption_describes_actual_csv_order_without_inventing_significance(tmp_path,values,expected):
    studio=DataStudio(Library(tmp_path));data=studio.import_csv(('step,value\na,'+values[0]+'\nb,'+values[1]+'\nc,'+values[2]).encode(),'values.csv')
    chart=studio.create_chart(data['id'],kind='line',x='step',y=['value'])
    assert expected in chart['caption'] and '不表示因果关系' in chart['caption']
    bar=studio.create_chart(data['id'],kind='bar',x='step',y=['value'])
    assert '最大值' in bar['caption'] and '对应' in bar['caption']


@pytest.mark.parametrize('kind',['line','bar','radar3d'])
def test_chart_export_runs_in_independent_directory_without_project_import(tmp_path,kind):
    studio=DataStudio(Library(tmp_path/'library'))
    data=studio.import_csv(b'x,a,b\none,0.1,0.3\ntwo,0.2,0.4\nthree,0.3,0.5','sample.csv')
    chart=studio.create_chart(data['id'],kind=kind,x='x',y=['a','b'])
    output=tmp_path/'independent';output.mkdir()
    script=output/'chart.py';script.write_text(studio.export_script(chart['id']),encoding='utf-8')
    subprocess.run([sys.executable,'-I',str(script)],cwd=output,check=True,capture_output=True)
    assert (output/'chart.svg').read_text(encoding='utf-8')==render_chart(chart['spec'])
    assert subprocess.run([sys.executable,'-I',str(script)],cwd=output,capture_output=True).returncode!=0
