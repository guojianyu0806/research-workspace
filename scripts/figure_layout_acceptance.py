"""Core frozen label, validation, and export checks before browser verification."""
import csv
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_figure_layout import DEFAULT,verify
from research_workspace.visualization import DataStudio,render_chart,render_diagram,export_diagram_script
from research_workspace.library import Library


def make_radar(studio,case):
    p=case['payload'];stream=io.StringIO(newline='');writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(['metric',*[s['name'] for s in p['series']]])
    for i,label in enumerate(p['labels']):writer.writerow([label,*[s['values'][i] for s in p['series']]])
    dataset=studio.import_csv(stream.getvalue().encode(),case['id']+'.csv')
    return studio.create_chart(dataset['id'],kind='radar3d',x='metric',y=[s['name'] for s in p['series']],
        units={s['name']:s['unit'] for s in p['series']},title=p['title'],normalize=p['normalize'],yaw=p['yaw'],elevation=p['elevation'])


def run():
    output=ROOT/'artifacts/figure-layout'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False);studio=DataStudio(Library(output/'workspace'));rows=[]
    for case in verify()['cases']:
        row=dict(id=case['id'],checks={})
        try:
            if case['kind']=='radar':
                chart=make_radar(studio,case);svg=render_chart(chart['spec']);code=studio.export_script(chart['id'])
                names=[case['payload']['title'],*case['payload']['labels'],*[s['name'] for s in case['payload']['series']],*[s['unit'] for s in case['payload']['series']]]
            else:
                p=case['payload'];svg=render_diagram(p['nodes'],p['edges']);code=export_diagram_script(p['nodes'],p['edges'])
                names=[n['label'] for n in p['nodes']]
            row['checks']['validation_contract']=case['accepted']
            root=ET.fromstring(svg)
            visible=[''.join((n.text or '')+''.join(t.text or '' for t in n if t.tag.rsplit('}',1)[-1]=='tspan')) for n in root.iter() if n.tag.rsplit('}',1)[-1]=='text']
            compact=''.join(''.join(visible).split())
            row['checks']['all_text_visible']=all(''.join(str(n).split()) in compact for n in names)
            row['checks']['font_at_least_14']=all(float(n.get('font-size','0'))>=14 for n in root.iter() if n.tag.rsplit('}',1)[-1]=='text')
            folder=output/case['id'];folder.mkdir();(folder/'served.svg').write_text(svg,encoding='utf-8',newline='\n');(folder/'reproduce.py').write_text(code,encoding='utf-8',newline='\n')
        except ValueError as error:
            row['checks']['validation_contract']=not case['accepted'];row['error']=str(error)
        except Exception as error:
            row['checks']['unexpected_exception']=False;row['error']=type(error).__name__+': '+str(error)
        row['passed']=bool(row['checks']) and all(row['checks'].values());rows.append(row)
    result=dict(rows=rows,passed=all(r['passed'] for r in rows),scope='Core source text and validation only; actual font bounds and geometry still require browser/independent audit.')
    (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),passed=result['passed'],failed=[r['id'] for r in rows if not r['passed']])),flush=True)
    return result


if __name__=='__main__':raise SystemExit(0 if run()['passed'] else 1)
