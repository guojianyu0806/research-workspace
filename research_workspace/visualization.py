"""CSV analysis and deterministic SVG templates; no generated code is executed."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import inspect
import json
import math
from pathlib import Path
from uuid import uuid4

from .library import Library, _now

COLORS = ['#165f63', '#b96928', '#547ab0', '#9871a5', '#749353', '#b45263']


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def render_chart(spec: dict) -> str:
    """Render a validated, numeric specification as an inert standalone SVG."""
    if spec['kind'] == 'radar3d':
        from .radar import render_radar
        return render_radar(spec, COLORS)
    labels, series = spec['labels'], spec['series']
    width, height = 1000, 600
    left, top, bottom, right = 90, 80, 480, 930
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             '<rect width="1000" height="600" fill="white"/>',
             '<g font-family="Arial,sans-serif" fill="#1c303d">',
             f'<text x="90" y="38" font-size="22">{html.escape(spec["title"])}</text>']
    values = [v for item in series for v in item['values']]
    low, high = min([0] + values), max([0] + values)
    if low == high:
        high = low + 1
    y = lambda v: bottom - (v - low) / (high - low) * (bottom - top)
    for step in range(6):
        value = low + (high - low) * step / 5
        sy = y(value)
        parts.extend([f'<path d="M{left} {sy:.2f}H{right}" stroke="#dce4e9"/>',
                      f'<text x="80" y="{sy + 4:.2f}" text-anchor="end" font-size="12">{value:.4g}</text>'])
    slots = max(1, len(labels))
    slot = (right - left) / slots
    for index, label in enumerate(labels):
        if index % max(1, math.ceil(len(labels) / 18)) == 0:
            sx = left + (index + .5) * slot
            parts.append(f'<text transform="translate({sx:.2f},502) rotate(20)" font-size="11">{html.escape(str(label)[:35])}</text>')
    for si, item in enumerate(series):
        color = COLORS[si % len(COLORS)]
        points = []
        for index, value in enumerate(item['values']):
            sx = left + (index + .5) * slot
            sy = y(value)
            if spec['kind'] == 'bar':
                bar_width = slot * .8 / len(series)
                bx = left + index * slot + slot * .1 + si * bar_width
                parts.append(f'<rect x="{bx:.2f}" y="{min(y(0), sy):.2f}" width="{bar_width:.2f}" height="{max(.5, abs(y(0)-sy)):.2f}" fill="{color}"/>')
            else:
                points.append(f'{sx:.2f},{sy:.2f}')
                parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="3" fill="{color}"/>')
        if points:
            parts.append(f'<polyline points="{" ".join(points)}" stroke="{color}" stroke-width="2" fill="none"/>')
        parts.append(f'<rect x="{left + si*140}" y="557" width="14" height="4" fill="{color}"/>')
        parts.append(f'<text x="{left+20+si*140}" y="564" font-size="12">{html.escape(item["name"][:18])}</text>')
    parts.append('</g></svg>')
    return ''.join(parts)


def render_diagram(nodes: list[dict], edges: list[dict]) -> str:
    if not 1 <= len(nodes) <= 30 or len(edges) > 100:
        raise ValueError('结构图需要1至30个节点，最多100条连接。')
    ids = [str(n['id']) for n in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError('结构图节点编号必须唯一。')
    positions = {key: (70 + index % 3 * 300, 65 + index // 3 * 130) for index, key in enumerate(ids)}
    height = 60 + math.ceil(len(nodes)/3)*130
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}">',
             '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="#657782"/></marker></defs>',
             f'<rect width="1000" height="{height}" fill="white"/>']
    for edge in edges:
        if str(edge['source']) not in positions or str(edge['target']) not in positions:
            raise ValueError('连接引用了不存在的节点。')
        x1, y1 = positions[str(edge['source'])]
        x2, y2 = positions[str(edge['target'])]
        parts.append(f'<path d="M{x1+120} {y1+55}L{x2+120} {y2}" stroke="#657782" fill="none" marker-end="url(#arrow)"/>')
    for node in nodes:
        x, y = positions[str(node['id'])]
        parts.append(f'<rect x="{x}" y="{y}" width="240" height="55" rx="6" fill="#e8f1f0" stroke="#165f63"/>')
        parts.append(f'<text x="{x+120}" y="{y+32}" text-anchor="middle" font-size="14" font-family="Arial,sans-serif">{html.escape(str(node["label"])[:32])}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def export_diagram_script(nodes: list[dict], edges: list[dict]) -> str:
    render_diagram(nodes,edges)  # Apply the same validation as the displayed SVG.
    data=json.dumps({'nodes':nodes,'edges':edges},ensure_ascii=False)
    return ('# Standalone Python 3; standard library only.\n'
        '# Fixed rendering template; labels are data, never executable code.\n'
        'import html\nimport math\nimport json\nfrom pathlib import Path\n\n'
        +inspect.getsource(render_diagram)+'\n'
        +f'spec=json.loads({data!r})\n'
        +'with Path("architecture.svg").open("x",encoding="utf-8") as stream:\n'
        +'    stream.write(render_diagram(spec["nodes"],spec["edges"]))\n')


def describe_series(labels,series,kind):
    descriptions=[]
    for s in series:
        values=s['values']
        if kind=='line' and len(values)>1:
            if all(v==values[0] for v in values):trend='相邻记录数值保持一致'
            elif all(b>=a for a,b in zip(values,values[1:])):trend='沿CSV记录顺序单调不降'
            elif all(b<=a for a,b in zip(values,values[1:])):trend='沿CSV记录顺序单调不升'
            else:trend='沿CSV记录顺序有升降波动'
            descriptions.append(f'{s["name"]}{trend}，首条{values[0]:.6g}、末条{values[-1]:.6g}，首末差{values[-1]-values[0]:.6g}')
        elif kind=='bar':
            high=max(values);low=min(values)
            def categories(value):
                names=[str(label) for label,v in zip(labels,values) if v==value]
                return '、'.join(names[:3])+(f'等{len(names)}类' if len(names)>3 else '')
            descriptions.append(f'{s["name"]}最大值{high:.6g}对应{categories(high)}，最小值{low:.6g}对应{categories(low)}')
    return '；'.join(descriptions)


class DataStudio:
    def __init__(self, library: Library):
        self.library = library
        with library._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS datasets(
                  id TEXT PRIMARY KEY,filename TEXT,sha256 TEXT,created_at TEXT,payload TEXT);
                CREATE TABLE IF NOT EXISTS charts(
                  id TEXT PRIMARY KEY,dataset_id TEXT,created_at TEXT,payload TEXT);
            ''')

    def import_csv(self, content: bytes, filename: str) -> dict:
        if not content or len(content) > 5*1024*1024:
            raise ValueError('CSV必须非空且不超过5 MiB。')
        encoding = 'utf-8-sig'
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            encoding = 'gb18030'
            try:
                text = content.decode(encoding)
            except UnicodeDecodeError as exc:
                raise ValueError('CSV编码应为UTF-8或GB18030。') from exc
        reader = csv.reader(io.StringIO(text))
        try:
            headers = [h.strip() for h in next(reader)]
        except StopIteration as exc:
            raise ValueError('CSV缺少表头。') from exc
        if not 1 <= len(headers) <= 60 or any(not h or len(h)>100 for h in headers) or len(set(headers)) != len(headers):
            raise ValueError('表头需要1至60个不重复的非空字段，每个不超过100字。')
        rows = []
        for index, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f'第{index}行列数与表头不一致。')
            if len(rows) >= 10000:
                raise ValueError('当前每个CSV最多10000行。')
            rows.append([cell.strip() for cell in row])
        if not rows:
            raise ValueError('CSV没有数据行。')
        columns = []
        for index, header in enumerate(headers):
            values = [r[index] for r in rows]
            present = [v for v in values if v]
            columns.append({'name': header, 'missing': len(values)-len(present),
                            'numeric_count': sum(number(v) is not None for v in present),
                            'type': 'number' if present and all(number(v) is not None for v in present) else 'text',
                            'unit': None})
        dataset_id = uuid4().hex
        item = {'id': dataset_id, 'filename': Path(filename.replace('\\','/')).name[:255],
                'sha256': hashlib.sha256(content).hexdigest(), 'created_at': _now(),
                'encoding': encoding, 'headers': headers, 'columns': columns, 'row_count':len(rows), 'rows':rows}
        with self.library._connection() as db:
            db.execute('INSERT INTO datasets VALUES(?,?,?,?,?)', (dataset_id, item['filename'],item['sha256'],item['created_at'],json.dumps(item,ensure_ascii=False)))
        return self.get_dataset(dataset_id)

    def get_dataset(self, dataset_id, *, all_rows=False):
        with self.library._connection() as db:
            row = db.execute('SELECT payload FROM datasets WHERE id=?',(dataset_id,)).fetchone()
        if row is None:
            raise KeyError(dataset_id)
        item = json.loads(row[0])
        if not all_rows:
            item['rows'] = item['rows'][:50]
            item['preview_limit'] = 50
        return item

    def list_datasets(self):
        with self.library._connection() as db:
            rows = db.execute('SELECT id,filename,sha256,created_at FROM datasets ORDER BY created_at DESC').fetchall()
        return [dict(r) for r in rows]

    def create_chart(self, dataset_id, *, kind, x, y, missing='reject', normalize=False, title='', units=None, yaw=35.0, elevation=30.0):
        data = self.get_dataset(dataset_id, all_rows=True)
        if kind not in {'line','bar','radar3d'} or not 1 <= len(y) <= 6 or len(set(y)) != len(y):
            raise ValueError('请选择支持的图表类型，以及1至6个不同数值字段。')
        if x not in data['headers'] or any(name not in data['headers'] for name in y):
            raise ValueError('字段不存在。')
        if missing not in {'reject','drop'}:
            raise ValueError('缺失值处理应为拒绝或明确忽略无效行。')
        xi, yi = data['headers'].index(x), [data['headers'].index(name) for name in y]
        valid, dropped = [], []
        for index, row in enumerate(data['rows'],2):
            nums = [number(row[i]) for i in yi]
            if not row[xi] or any(v is None for v in nums):
                if missing == 'reject':
                    raise ValueError(f'第{index}行缺少横轴值或数值字段无效，请更正数据或选择忽略无效行。')
                dropped.append(index)
                continue
            valid.append((row[xi], nums))
        if not valid or len(valid)>500:
            raise ValueError('绘图需要1至500条有效行；请先缩小数据范围，系统不会静默截断。')
        units = units or {}
        series = []
        for index, name in enumerate(y):
            values = [r[1][index] for r in valid]
            original_min, original_max = min(values), max(values)
            if normalize and kind != 'radar3d':
                delta = original_max-original_min
                values = [(v-original_min)/delta if delta else 0 for v in values]
            series.append({'name':name,'unit':str(units.get(name,''))[:40], 'values':values,
                           'original_min':original_min,'original_max':original_max})
        spec = {'kind':kind, 'title':str(title or f'{x} 与 {", ".join(y)}')[:150], 'x':x,
                'labels':[r[0] for r in valid], 'series':series}
        if kind == 'radar3d':
            from .radar import radar_geometry
            spec['radar'] = radar_geometry(spec['labels'], series, normalize=normalize, yaw=yaw, elevation=elevation)
        descriptions = [f'{s["name"]}范围为{min(s["values"]):.6g}至{max(s["values"]):.6g}{"（归一化）" if normalize else s["unit"]}' for s in series]
        item = {'id':uuid4().hex,'dataset_id':dataset_id,'dataset_sha256':data['sha256'], 'created_at':_now(), 'spec':spec,
                'transformations':{'missing':missing,'dropped_csv_rows':dropped,'minmax_normalized':normalize,'aggregation':'none','row_order':'source'},
                'caption':f'{spec["title"]}。横轴为{x}，保留CSV原行顺序，共{len(valid)}条有效记录。'+ '；'.join(descriptions)+'。该图未进行显著性检验，不表示因果关系。',
                'template_version':'svg-chart-v1'}
        if kind == 'radar3d':
            item['template_version'] = 'svg-radar3d-v1'
            item['transformations']['normalization_scope'] = spec['radar']['normalization']
            item['caption'] = (f'{spec["title"]}。{len(valid)}个指标映射为极角轴，半径为0至1，高度层对应{len(series)}个系列的类别顺序。'
                               + ('每个指标在所选系列间进行Min–Max归一化；常数指标半径设为0.5。' if normalize else '输入数值已在0至1之间，未归一化。')
                               + '高度不表示独立测量变量，半径大小不自动代表性能优劣；未进行显著性检验。')
        else:
            description=describe_series(spec['labels'],series,kind)
            if description:item['caption']+='按图中绘图数值描述：'+description+'。顺序或类别关系不自动构成时间趋势。'
        item['caption_version']='descriptive-v2'
        with self.library._connection() as db:
            db.execute('INSERT INTO charts VALUES(?,?,?,?)',(item['id'],dataset_id,item['created_at'],json.dumps(item,ensure_ascii=False)))
        return item

    def get_chart(self, chart_id):
        with self.library._connection() as db:
            row = db.execute('SELECT payload FROM charts WHERE id=?',(chart_id,)).fetchone()
        if row is None:
            raise KeyError(chart_id)
        return json.loads(row[0])

    def export_script(self, chart_id):
        spec = self.get_chart(chart_id)['spec']
        from .radar import render_radar
        renderer=inspect.getsource(render_chart).replace('        from .radar import render_radar\n','')
        return ('# Standalone Python 3; standard library only.\n'
                '# Fixed rendering template: no user-supplied Python is executed.\n'
                'import html\nimport math\nimport json\nfrom pathlib import Path\n'
                +f'COLORS = {COLORS!r}\n\n'+inspect.getsource(render_radar)+'\n'+renderer+'\n'
                f'spec = json.loads({json.dumps(spec,ensure_ascii=False)!r})\n'
                'with Path("chart.svg").open("x", encoding="utf-8") as stream:\n'
                '    stream.write(render_chart(spec))\n')
