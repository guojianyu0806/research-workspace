"""Independently compare saved charts with original CSV records and SVG geometry."""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.freeze_csv_extremes import DEFAULT,verify
from scripts.freeze_local_acceptance import sha256


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ui',type=Path,required=True);parser.add_argument('--regression',type=Path,required=True)
    args=parser.parse_args();ui=args.ui.resolve();regression=args.regression.resolve()
    checks=[]
    def check(name,ok):checks.append(dict(name=name,passed=bool(ok)))
    def read(path):return json.loads(path.read_text(encoding='utf-8'))
    manifest=verify();browser=read(ui/'browser-report.json');completion=read(ui/'completion.json')
    check('277 actual API/browser/export/isolation checks pass',browser['passed'] and len(browser['checks'])==277 and all(c['passed'] for c in browser['checks']))
    check('No browser errors or external page traffic',not browser['errors'] and not browser['external'])
    check('Original font measurements cover 18 Cartesian plots',len(browser['textBounds'])==18 and all(b['inside'] and b['font']>=14 for c in browser['textBounds'] for b in c['bounds']))
    check('Main selected data unchanged, fixture stopped, zero model calls',completion['main_unchanged'] and completion['stopped'] and completion['model_calls']==0)
    check('Runtime files still match',all(sha256(ROOT/name)==value for name,value in completion['runtime_sha256'].items()))
    check('Frozen input version still matches',completion['manifest_sha256']==sha256(DEFAULT/'manifest.json'))
    check('All frozen bytes match original Git freeze commit',all(
        subprocess.check_output(['git','show','c18b92b:'+str((DEFAULT/c['path']).relative_to(ROOT)).replace('\\','/')],cwd=ROOT)==(DEFAULT/c['path']).read_bytes()
        for c in manifest['cases']))
    with sqlite3.connect((ui/'workspace/library.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        datasets={row[0]:json.loads(row[1]) for row in db.execute('SELECT id,payload FROM datasets')}
        charts=[json.loads(row[0]) for row in db.execute('SELECT payload FROM charts')]
    by_file={c['path']:c for c in manifest['cases']}
    check('All accepted datasets, and no rejected dataset, saved once',len(datasets)==sum(c['import_accepted'] for c in manifest['cases'])
          and {d['filename'] for d in datasets.values()}=={c['path'] for c in manifest['cases'] if c['import_accepted']})
    check('19 valid API charts plus four UI charts; no failed creation remnants',len(charts)==23)
    for dataset in datasets.values():
        case=by_file[dataset['filename']]
        raw=(DEFAULT/case['path']).read_bytes()
        records=list(csv.reader(io.StringIO(raw.decode(case['encoding'])),strict=True))
        check(case['id']+' complete original rows and SHA retained',dataset['sha256']==sha256(DEFAULT/case['path'])
              and dataset['headers']==[s.strip() for s in records[0]] and dataset['rows']==[[v.strip() for v in r] for r in records[1:]])
    for chart in charts:
        dataset=datasets[chart['dataset_id']];spec=chart['spec'];headers=dataset['headers']
        case=by_file[dataset['filename']]
        check(chart['id']+' exact requested fields, title, units and normalization',spec['x']==case['options']['x']
              and [s['name'] for s in spec['series']]==case['options']['y'] and spec['title']==case['options']['title']
              and spec['kind']==case['options']['kind'] and chart['transformations']['minmax_normalized']==case['options']['normalize']
              and all(s['unit']==case['options']['units'].get(s['name'],'') for s in spec['series']))
        xi=headers.index(spec['x']);yi=[headers.index(s['name']) for s in spec['series']]
        valid=[]
        for index,row in enumerate(dataset['rows'],2):
            try:
                values=[Decimal(row[i]) for i in yi]
                if not row[xi] or any(not v.is_finite() for v in values):continue
            except Exception:continue
            valid.append((index,row[xi],values))
        check(chart['id']+' exact source record IDs, labels and dataset digest',spec['source_csv_records']==[v[0] for v in valid]
              and spec['labels']==[v[1] for v in valid] and chart['dataset_sha256']==dataset['sha256'])
        expected=[]
        with localcontext() as ctx:
            ctx.prec=60
            for si,s in enumerate(spec['series']):
                originals=[v[2][si] for v in valid]
                check(chart['id']+' original '+s['name'],s['original_values']==[float(v) for v in originals])
                result=[]
                for index,value in enumerate(originals):
                    domain=valid[index][2] if spec['kind']=='radar3d' else originals
                    low,high=min(domain),max(domain)
                    if chart['transformations']['minmax_normalized']:
                        value=(value-low)/(high-low) if high!=low else Decimal('.5' if spec['kind']=='radar3d' else '0')
                    result.append(float(value))
                expected.append(result)
                check(chart['id']+' independent decimal normalization '+s['name'],len(result)==len(s['values'])
                      and all(math.isfinite(v) and math.isclose(v,w,rel_tol=1e-12,abs_tol=1e-15) for v,w in zip(s['values'],result)))
        saved=ui/by_file[dataset['filename']]['id']/'chart.json'
        if not saved.exists() or read(saved)['id']!=chart['id'] or spec['kind']=='radar3d':continue
        root=ET.fromstring((saved.parent/'served.svg').read_bytes())
        ns={'s':'http://www.w3.org/2000/svg'}
        grids=[]
        for node in root.findall('.//s:path',ns):
            match=re.fullmatch(r'M([-\d.]+) ([-\d.]+)H([-\d.]+)',node.get('d',''))
            if match:grids.append(tuple(float(x) for x in match.groups()))
        left,right=grids[0][0],grids[0][2];top=min(g[1] for g in grids);bottom=max(g[1] for g in grids)
        all_values=[Decimal(str(v)) for values in expected for v in values];low=min([Decimal(0),*all_values]);high=max([Decimal(0),*all_values])
        if low==high:high=low+1
        points=[n for n in root.iter() if n.tag.rsplit('}',1)[-1] in ('circle','rect') and n.find('s:title',ns) is not None]
        check(chart['id']+' every original value has a geometric object',len(points)==len(valid)*len(expected))
        for index,node in enumerate(points):
            si,ri=divmod(index,len(valid));value=Decimal(str(expected[si][ri]))
            with localcontext() as ctx:
                ctx.prec=60;sy=bottom-float((value-low)/(high-low))*(bottom-top);zero=bottom-float((0-low)/(high-low))*(bottom-top)
            if spec['kind']=='bar':
                ok=abs(float(node.get('height'))-abs(zero-sy))<=.002 and abs(float(node.get('y'))-min(zero,sy))<=.002
            else:
                sx=left+(ri+.5)*(right-left)/len(valid)
                ok=abs(float(node.get('cy'))-sy)<=.002 and abs(float(node.get('cx'))-sx)<=.002
            check(chart['id']+' CSV-to-SVG object '+str(index),ok)
    summary=read(regression/'summary.json');run=read(regression/'manifest.json')
    check('Entire current pytest collection passes without skips',summary['passed'] and summary['tests']==run['expected_tests'] and all(summary['checks'].values()))
    check('Regression files unchanged now',all(sha256(ROOT/name)==value for name,value in run['source_sha256'].items()))
    output=ui/('independent-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    result=dict(passed=all(c['passed'] for c in checks),checks=checks,
        limits=['Synthetic CSV cases, not official experiments.','Long radar/architecture labels and arbitrary camera layouts not accepted here.'])
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(output),checks=len(checks),passed=result['passed'],failed=[c['name'] for c in checks if not c['passed']])),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
