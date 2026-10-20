"""Synthetic graph/radar layout contracts frozen before renderer changes."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'tests/fixtures/figure_layout/20261020-v1'


def definitions():
    result=[]
    names=['第'+str(i)+'个完整数值系列及训练与评测设置'*4 for i in range(6)]
    labels=['指标'+str(i)+'：完整指标说明与测量范围'*7 for i in range(12)]
    base=dict(labels=labels,series=[dict(name=name,unit='每秒处理样本数量与单位定义'*2,
              values=[((ai+si)%9+1)/10 for ai in range(12)]) for si,name in enumerate(names)],
              title='完整雷达图标题与评估范围以及所有参数说明'*7,normalize=False)
    def radar(name,payload,accepted=True):result.append(dict(id=name,kind='radar',payload=payload,accepted=accepted))
    for yaw in (-180,-90,0,90,180):
        for elevation in (15,30,75):
            radar(f'camera-{yaw}-{elevation}',dict(base,yaw=yaw,elevation=elevation))
    for name,values in [('huge',[-1e308,0,1e308]),('constant',[7,7,7]),('tiny',[0,5e-324,1e-323])]:
        series=[dict(name='系列'+str(i)+'的完整名称'*8,unit='个',values=[values[(ai+i)%3] for ai in range(3)]) for i in range(3)]
        radar(name,dict(labels=labels[:3],series=series,title='完整指标与单位',normalize=True,yaw=-40,elevation=65))
    radar('radar-markup',dict(labels=['<script>bad()</script>','first\nsecond','第三指标完整名称'*9],
        series=[dict(name='第一系列完整说明'*8,unit='%',values=[.1,.5,.9]),dict(name='第二系列',unit='%',values=[.3,.6,.7])],
        title='<img src=x onerror=bad()>\n完整标题',normalize=False,yaw=20,elevation=15))
    radar('invalid-yaw',dict(base,yaw=181,elevation=30),False)
    radar('invalid-elevation',dict(base,yaw=0,elevation=14),False)
    radar('duplicate-axis',dict(base,labels=['same']*12,yaw=0,elevation=30),False)
    def diagram(name,nodes,edges,accepted=True):
        result.append(dict(id=name,kind='diagram',payload=dict(nodes=nodes,edges=edges),accepted=accepted))
    long='完整节点名称与输入输出的处理方式'*8
    diagram('single-long',[dict(id='one',label=long)],[])
    diagram('three-long',[dict(id=str(i),label=str(i)+long) for i in range(3)],
            [dict(source='0',target='1'),dict(source='1',target='2')])
    diagram('thirty-nodes',[dict(id=str(i),label=str(i)+long) for i in range(30)],
            [dict(source=str(i),target=str(i+1)) for i in range(29)]+[dict(source='29',target='0')])
    diagram('cycles-self-loop',[dict(id=str(i),label=str(i)+long) for i in range(6)],
            [dict(source='0',target='5'),dict(source='5',target='0'),dict(source='2',target='2'),dict(source='4',target='1')])
    diagram('diagram-markup-newline',[dict(id='a',label='<script>bad()</script>\nnext'),dict(id='b',label='第二节点')],[dict(source='a',target='b')])
    diagram('duplicate-node',[dict(id='x',label='one'),dict(id='x',label='two')],[],False)
    diagram('dangling-edge',[dict(id='x',label='one')],[dict(source='x',target='missing')],False)
    diagram('missing-label',[dict(id='x')],[],False)
    assert len(result)==30
    return result


def freeze(directory=DEFAULT):
    directory.mkdir(parents=True,exist_ok=False)
    cases=definitions()
    body=json.dumps(cases,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    value=dict(created_at=datetime.now(timezone.utc).isoformat(),cases=cases,
        cases_sha256=hashlib.sha256(body).hexdigest(),scope='Synthetic typography/geometry contracts; not actual model output or official research data.',
        gates=dict(complete_visible_text=True,min_font=14,nonoverlapping_text=True,original_geometry_retained=True,
                   no_edges_through_other_nodes=True,standalone_utf8_identical=True),widths=[1440,1024,760,390])
    with (directory/'manifest.json').open('x',encoding='utf-8',newline='\n') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return verify(directory)


def verify(directory=DEFAULT):
    value=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    body=json.dumps(value['cases'],ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    assert hashlib.sha256(body).hexdigest()==value['cases_sha256']
    assert len(value['cases'])==len({c['id'] for c in value['cases']})==30
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    data=verify() if args.verify else freeze()
    print(json.dumps(dict(output=str(DEFAULT),cases=len(data['cases']),valid=sum(c['accepted'] for c in data['cases']))))
