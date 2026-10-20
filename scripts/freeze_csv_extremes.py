"""Freeze synthetic CSV contracts independently of the chart implementation."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT/'tests/fixtures/csv_extremes/20261020-v1'


def cases():
    out = []
    def add(name, headers, rows, *, encoding='utf-8', accepted=True, chart=True,
            normalize=False, missing='reject', kind='line', expected=None, title='', units=None):
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerows([headers, *rows])
        out.append(dict(id=name, content=stream.getvalue().encode(encoding), encoding=encoding,
            headers=headers, row_count=len(rows), import_accepted=accepted, chart_accepted=chart,
            options=dict(kind=kind, x=headers[0], y=headers[1:7], normalize=normalize, missing=missing,
                         title=title or name, units=units or {}), expected=expected or {}))
    basic = [['a','1'],['b','2']]
    for name, encoding in [('utf8','utf-8'),('bom','utf-8-sig'),('gb18030','gb18030')]:
        add(name, ['标签','数值'], [['甲','1'],['乙','2']], encoding=encoding)
    add('quoted-comma', ['label','value'], [['a,b','1'],['c','2']])
    add('quoted-newline', ['label','value'], [['first\nsecond','1'],['third','2']])
    add('duplicate-header', ['x','x'], basic, accepted=False, chart=False)
    add('missing-reject', ['x','value'], [['a','1'],['b',''],['c','3']], chart=False)
    add('missing-drop', ['x','value'], [['a','1'],['b',''],['c','3']], missing='drop',
        expected=dict(labels=['a','c'], values=[[1,3]], dropped=[3]))
    add('nonnumeric', ['x','value'], [['a','one'],['b','2']], chart=False)
    add('negative', ['x','value'], [['a','-9'],['b','-1']], kind='bar', expected=dict(values=[[-9,-1]]))
    add('all-zero', ['x','value'], [['a','0'],['b','0']], kind='bar', expected=dict(values=[[0,0]]))
    add('constant-normalized', ['x','value'], [['a','4'],['b','4']], normalize=True, expected=dict(values=[[0,0]]))
    huge = [['a','-1e308'],['b','0'],['c','1e308']]
    add('huge-normalized', ['x','value'], huge, normalize=True, expected=dict(values=[[0,.5,1]]))
    add('huge-raw', ['x','value'], huge, expected=dict(values=[[-1e308,0,1e308]]))
    add('subnormal-normalized', ['x','value'], [['a','0'],['b','5e-324'],['c','1e-323']], normalize=True,
        expected=dict(values=[[0,.5,1]]))
    long_headers = ['模型完整名称']+['第'+str(i)+'个数值系列与完整实验说明'*5 for i in range(1,7)]
    add('long-chinese', long_headers, [[('完整模型名称含训练设置与评测范围'*9)+str(i), *[str(i+j) for j in range(6)]] for i in range(3)],
        title='这是完整图表标题用于检查换行与边界而非裁切'*7,
        units={h:'每秒处理样本数量和完整单位说明'*2 for h in long_headers[1:]})
    add('six-series', ['label', *['series'+str(i) for i in range(6)]],
        [['A',1,2,3,4,5,6],['B',2,3,4,5,6,7]], kind='bar')
    for count in (500,501,10000,10001):
        add('rows-'+str(count), ['x','value'], [[str(i),str(i)] for i in range(count)],
            accepted=count<=10000, chart=count<=500)
    for count in (60,61):
        add('columns-'+str(count), ['x', *['c'+str(i) for i in range(1,count)]],
            [['A', *['1']*(count-1)]], accepted=count<=60, chart=count<=60)
    add('markup-as-data', ['x','value'], [['<script>alert(1)</script>','1'],['=2+2','2']],
        title='<img src=x onerror=alert(1)> & no execution')
    add('unclosed-quote', ['x','value'], basic, accepted=False, chart=False)
    out[-1]['content'] = b'x,value\n"unfinished,1\n'
    add('nonfinite', ['x','value'], [['a','NaN'],['b','Infinity'],['c','1e309'],['d','1']], chart=False)
    add('nul-control', ['x','value'], [['a\x00b','1']], accepted=False, chart=False)
    add('radar-huge-normalized', ['metric','A','B'], [['one','-1e308','1e308'],['two','0','0'],['three','1e308','-1e308']],
        kind='radar3d', normalize=True, expected=dict(values=[[0,.5,1],[1,.5,0]]))
    add('subnormal-raw', ['x','value'], [['a','0'],['b','5e-324']], expected=dict(values=[[0,5e-324]]))
    add('all-missing', ['x','value'], [['a',''],['b','']], missing='drop', chart=False)
    assert len(out) == 30
    return out


def freeze(directory):
    directory.mkdir(parents=True, exist_ok=False)
    records = []
    for case in cases():
        content = case.pop('content')
        name = case['id']+'.csv'
        with (directory/name).open('xb') as stream:
            stream.write(content)
        records.append(dict(case, path=name, sha256=hashlib.sha256(content).hexdigest(), bytes=len(content)))
    manifest = dict(version='csv-extremes-20261020-v1', created_at=datetime.now(timezone.utc).isoformat(),
        scope='Synthetic boundary contracts; not measured experiments or official competition data.',
        cases=records, browser_widths=[1440,1024,760,390],
        gates=dict(no_silent_row_truncation=True, max_import_rows=10000, max_plot_rows=500,
                   finite_numeric_geometry=True, full_text_accessible=True, min_font_css_px=14,
                   standalone_export_identical=True, cross_account_access=False))
    with (directory/'manifest.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return verify(directory)


def verify(directory=DEFAULT):
    data = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    assert len(data['cases']) == len({c['id'] for c in data['cases']}) == 30
    for case in data['cases']:
        raw = (directory/case['path']).read_bytes()
        assert len(raw) == case['bytes'] and hashlib.sha256(raw).hexdigest() == case['sha256']
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    result = verify() if args.verify else freeze(DEFAULT)
    print(json.dumps(dict(cases=len(result['cases']), output=str(DEFAULT), verified=True)))
