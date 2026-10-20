"""Check original inputs against actual SVG coordinates without renderer imports."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal,localcontext
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_figure_layout import DEFAULT,verify
from scripts.freeze_local_acceptance import sha256


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ui',type=Path,required=True);parser.add_argument('--regression',type=Path,required=True);parser.add_argument('--worker-stress',type=Path,required=True);args=parser.parse_args()
    ui=args.ui.resolve();regression=args.regression.resolve();checks=[]
    def read(path):return json.loads(path.read_text(encoding='utf-8'))
    def check(name,ok):checks.append(dict(name=name,passed=bool(ok)))
    def plain(node):return ''.join((node.text or '')+''.join(c.text or '' for c in node if c.tag.rsplit('}',1)[-1]=='tspan')).split()
    def visible(node):return ''.join(plain(node))
    data=verify();browser=read(ui/'browser-report.json');completion=read(ui/'completion.json')
    check('All 221 actual HTTP/browser checks pass',browser['passed'] and len(browser['checks'])==221 and all(c['passed'] for c in browser['checks']))
    check('No page errors or external traffic',not browser['errors'] and not browser['external'])
    check('All 24 positive cases independently measured by browser',len(browser['bounds'])==24)
    check('Original selected main data retained, zero model calls, fixture stopped',completion['main_unchanged'] and completion['stopped'] and completion['model_calls']==0)
    check('All runtime file fingerprints still match',all(sha256(ROOT/name)==value for name,value in completion['runtime_sha256'].items()))
    frozen=(DEFAULT/'manifest.json').read_bytes()
    check('Frozen manifest unchanged from initial Git commit',subprocess.check_output(['git','show','bae45de:tests/fixtures/figure_layout/20261020-v1/manifest.json'],cwd=ROOT)==frozen)
    check('Runtime used exact frozen manifest',sha256(DEFAULT/'manifest.json')==completion['manifest_sha256'])
    ns={'s':'http://www.w3.org/2000/svg'}
    for case in data['cases']:
        if not case['accepted']:continue
        folder=ui/case['id'];root=ET.fromstring((folder/'served.svg').read_bytes());record=read(folder/'record.json');p=case['payload']
        measures=next(v['boxes'] for v in browser['bounds'] if v['id']==case['id'])
        check(case['id']+' actual glyphs contained at >=14px',all(m['inside'] and m['font']>=14 for m in measures))
        check(case['id']+' no script/foreignObject',not any(n.tag.rsplit('}',1)[-1] in ('script','foreignObject') for n in root.iter()))
        if case['kind']=='radar':
            spec=record['spec'];series=spec['series'];n=len(p['labels']);count=len(p['series'])
            check(case['id']+' exact source labels, camera and layout version',spec['labels']==p['labels'] and spec['radar']['camera']==dict(yaw_degrees=p['yaw'],elevation_degrees=p['elevation']) and spec['layout_version']=='radar-v2')
            keys=root.findall('.//s:text[@data-role="axis-key"]',ns)
            check(case['id']+' complete axis-key mapping',len(keys)==n and all(visible(key)==''.join(f'A{i+1} · {p["labels"][i]}'.split()) for i,key in enumerate(keys)))
            legends=root.findall('.//s:text[@data-role="legend"]',ns)
            check(case['id']+' complete series and unit mapping',len(legends)==count and all(visible(key)==''.join((f'L{i+1} · {s["name"]}'+(f'（{s["unit"]}）' if s['unit'] else '')).split()) for i,(key,s) in enumerate(zip(legends,p['series']))))
            circles=root.findall('.//s:circle',ns);actual={(int(c.get('data-series-index')),int(c.get('data-axis-index'))):c for c in circles}
            check(case['id']+' one geometric marker per source value',len(actual)==len(circles)==n*count)
            projected=[];observed=[]
            yaw,elev=math.radians(p['yaw']),math.radians(p['elevation'])
            def project(x,y,z):return x*math.cos(yaw)-y*math.sin(yaw),-(z*math.cos(elev)-(x*math.sin(yaw)+y*math.cos(yaw))*math.sin(elev))
            for si,s in enumerate(p['series']):
                check(case['id']+' unchanged raw values '+str(si),series[si]['original_values']==s['values'] and series[si]['name']==s['name'] and series[si]['unit']==s['unit'])
                for ai,value in enumerate(s['values']):
                    with localcontext() as ctx:
                        ctx.prec=60;v=Decimal(str(value));domain=[Decimal(str(item['values'][ai])) for item in p['series']]
                        lo,hi=min(domain),max(domain);radius=float((v-lo)/(hi-lo) if hi!=lo else Decimal('.5')) if p['normalize'] else float(value)
                    angle=2*math.pi*ai/n;x=radius*math.cos(angle);y=radius*math.sin(angle);z=1.4*si/(count-1)
                    point=series[si]['points_3d'][ai]
                    check(case['id']+f' raw-to-3D {si}:{ai}',abs(series[si]['values'][ai]-radius)<1e-12 and all(abs(point[k]-val)<1e-12 for k,val in zip(('x','y','z'),(x,y,z))))
                    c=actual[si,ai];projected.append(project(x,y,z));observed.append((float(c.get('cx')),float(c.get('cy'))))
            def fit(axis):
                low=min(range(len(projected)),key=lambda i:projected[i][axis]);high=max(range(len(projected)),key=lambda i:projected[i][axis])
                scale=(observed[high][axis]-observed[low][axis])/(projected[high][axis]-projected[low][axis])
                return scale,observed[low][axis]-scale*projected[low][axis]
            sx,ox=fit(0);sy,oy=fit(1)
            check(case['id']+' isotropic camera projection',sx>0 and sy>0 and abs(sx-sy)<.01)
            for i,(point,screen) in enumerate(zip(projected,observed)):
                check(case['id']+' 3D-to-SVG '+str(i),abs(point[0]*sx+ox-screen[0])<.003 and abs(point[1]*sy+oy-screen[1])<.003)
            leaders=root.findall('.//s:path[@data-axis-index]',ns)
            check(case['id']+' all leader references retained',len(leaders)==n and {int(l.get('data-axis-index')) for l in leaders}==set(range(n)))
            for leader in leaders:
                ai=int(leader.get('data-axis-index'));angle=2*math.pi*ai/n;px,py=project(math.cos(angle),math.sin(angle),0)
                coords=[float(v) for v in re.findall(r'-?\d+(?:\.\d+)?',leader.get('d'))]
                check(case['id']+' correct unit-axis leader '+str(ai),abs(coords[0]-(sx*px+ox))<.003 and abs(coords[1]-(sy*py+oy))<.003)
        else:
            rectangles=root.findall('.//s:rect[@data-node-id-json]',ns)
            texts=root.findall('.//s:text[@data-role="node"]',ns)
            boxes={json.loads(r.get('data-node-id-json')):tuple(float(r.get(k)) for k in ('x','y','width','height')) for r in rectangles}
            check(case['id']+' exact node identities and full visible labels',list(boxes)==[str(n['id']) for n in p['nodes']] and len(texts)==len(p['nodes']) and all(visible(t)==''.join(n['label'].split()) for n,t in zip(p['nodes'],texts)))
            glyphs=[m['bbox'] for m in measures if m['role']=='node']
            for i,box in enumerate(boxes.values()):
                x,y,w,h=box;gx,gy,gw,gh=glyphs[i]
                check(case['id']+' full glyphs within node '+str(i),gx>=x-.5 and gy>=y-.5 and gx+gw<=x+w+.5 and gy+gh<=y+h+.5)
            paths=root.findall('.//s:path[@data-edge-index]',ns)
            check(case['id']+' exact directed edge count',len(paths)==len(p['edges']))
            def boundary(point,box):
                x,y=point;bx,by,bw,bh=box
                return bx-.002<=x<=bx+bw+.002 and by-.002<=y<=by+bh+.002 and min(abs(x-bx),abs(x-bx-bw),abs(y-by),abs(y-by-bh))<.002
            for i,(edge,node) in enumerate(zip(p['edges'],paths)):
                values=[float(v) for v in re.findall(r'-?\d+(?:\.\d+)?',node.get('d'))];points=list(zip(values[::2],values[1::2]))
                check(case['id']+' directed endpoint identity '+str(i),json.loads(node.get('data-source-json'))==str(edge['source']) and json.loads(node.get('data-target-json'))==str(edge['target']) and boundary(points[0],boxes[str(edge['source'])]) and boundary(points[-1],boxes[str(edge['target'])]))
                safe=True
                for a,b in zip(points,points[1:]):
                    if a[0]!=b[0] and a[1]!=b[1]:safe=False
                    for x,y,w,h in boxes.values():
                        if a[0]==b[0] and x+.001<a[0]<x+w-.001 and max(min(a[1],b[1]),y)<min(max(a[1],b[1]),y+h)-.001:safe=False
                        if a[1]==b[1] and y+.001<a[1]<y+h-.001 and max(min(a[0],b[0]),x)<min(max(a[0],b[0]),x+w)-.001:safe=False
                check(case['id']+' no route through any node interior '+str(i),safe)
    stress=args.worker_stress.resolve()
    for filename in ('observed-shutdown.json','orphan-reaped.json','observed-processes.json'):
        files=list(stress.rglob(filename));check('Three actual lifecycle runs retained: '+filename,len(files)==3)
        for path in files:
            value=read(path);groups=value if isinstance(value,list) else [value]
            check(str(path.relative_to(stress))+' original processes exited before cleanup',all(g['observed'] and not any(p['running'] for p in g['observed']) for g in groups))
    import xml.etree.ElementTree as Xml
    junit=list(stress.glob('case-*/junit.xml'));check('Three parallel worker pytest reports retained',len(junit)==3)
    for path in junit:
        suites=list(Xml.parse(path).getroot().iter('testsuite'))
        check(str(path.relative_to(stress))+' all five cases ran without failures/skips',sum(int(s.get('tests',0)) for s in suites)==5 and all(int(s.get(k,0))==0 for s in suites for k in ('errors','failures','skipped')))
    summary=read(regression/'summary.json');manifest=read(regression/'manifest.json')
    check('All current regression cases pass without skips',summary['passed'] and summary['tests']==manifest['expected_tests'] and all(summary['checks'].values()))
    check('Current regression files still match',all(sha256(ROOT/name)==value for name,value in manifest['source_sha256'].items()))
    output=ui/('independent-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    result=dict(passed=all(c['passed'] for c in checks),checks=checks,limits=['Synthetic bounded typography/geometry acceptance, not official experiments or Pro quality.','Dense edges may cross, but cannot pass through node interiors.'])
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(output),checks=len(checks),passed=result['passed'],failed=[c['name'] for c in checks if not c['passed']])),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
