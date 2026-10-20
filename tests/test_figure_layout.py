import copy
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from research_workspace.figure_layout import diagram_layout, segment_blocked
from research_workspace.library import Library
from research_workspace.visualization import DataStudio, render_chart, render_diagram, export_diagram_script
from scripts.figure_layout_acceptance import make_radar
from scripts.freeze_figure_layout import verify


@pytest.mark.parametrize('case',[c for c in verify()['cases'] if c['kind']=='diagram'],ids=lambda c:c['id'])
def test_diagram_complete_labels_directed_routes_and_validation(tmp_path,case):
    p=case['payload'];before=copy.deepcopy(p)
    if not case['accepted']:
        with pytest.raises(ValueError):render_diagram(p['nodes'],p['edges'])
        return
    layout=diagram_layout(p['nodes'],p['edges']);svg=render_diagram(p['nodes'],p['edges'])
    assert before==p
    root=ET.fromstring(svg);ns={'s':'http://www.w3.org/2000/svg'}
    paths=root.findall('.//s:path[@data-edge-index]',ns)
    assert len(paths)==len(p['edges'])
    for edge,node,path in zip(p['edges'],paths,layout['routes']):
        assert json.loads(node.get('data-source-json'))==str(edge['source'])
        assert json.loads(node.get('data-target-json'))==str(edge['target'])
        assert all(not segment_blocked(a,b,list(layout['boxes'].values())) for a,b in zip(path,path[1:]))
    source=tmp_path/'reproduce.py';source.write_text(export_diagram_script(p['nodes'],p['edges']),encoding='utf-8')
    subprocess.run([sys.executable,'-I',str(source)],cwd=tmp_path,check=True,capture_output=True)
    assert (tmp_path/'architecture.svg').read_bytes()==svg.encode()


@pytest.mark.parametrize('case',[c for c in verify()['cases'] if c['kind']=='radar'],ids=lambda c:c['id'])
def test_radar_all_frozen_cameras_keep_geometry_and_original_record(tmp_path,case):
    studio=DataStudio(Library(tmp_path/'library'))
    if not case['accepted']:
        with pytest.raises(ValueError):make_radar(studio,case)
        return
    chart=make_radar(studio,case);before=copy.deepcopy(chart)
    svg=render_chart(chart['spec'])
    assert 'radar-v2' in svg and chart==before and studio.get_chart(chart['id'])==before
    assert len(ET.fromstring(svg).findall('.//{http://www.w3.org/2000/svg}circle'))==len(case['payload']['labels'])*len(case['payload']['series'])


def test_legacy_radar_and_diagram_layouts_are_still_explicitly_available(tmp_path):
    studio=DataStudio(Library(tmp_path))
    case=next(c for c in verify()['cases'] if c['id']=='constant')
    chart=make_radar(studio,case);legacy=copy.deepcopy(chart['spec']);legacy.pop('layout_version');legacy.pop('layout')
    assert 'viewBox="0 0 1000 660"' in render_chart(legacy)
    nodes=[{'id':'x','label':'Existing'}]
    assert 'viewBox="0 0 1000 190"' in render_diagram(nodes,[],layout_version='diagram-v1')
    source=export_diagram_script(nodes,[],layout_version='diagram-v1');assert 'diagram-v1' in source
