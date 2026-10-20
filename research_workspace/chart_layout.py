"""Deterministic, bounded text layout for inert SVG; also exported standalone.

Conservative glyph budgets are verified against actual browser text bounds.
Original names remain in SVG titles and the complete plotting-data table.
"""
import html
import math
import re
import unicodedata
from .chart_numeric import unit_fraction, finite_lerp


def text_width(text, size):
    return sum(0 if unicodedata.combining(c) else size*(1 if unicodedata.east_asian_width(c) in 'WF'
               or ord(c)>127 or c in 'MW@%&' else .8) for c in str(text))


def wrap_text(text, width, size):
    text = str(text)
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', text):
        raise ValueError('图表文字包含无效控制字符。')
    lines, current = [], ''
    for char in text:
        if char in '\r\n':
            if char == '\n' or current:
                lines.append(current); current = ''
        elif current and text_width(current+char, size) > width:
            lines.append(current); current = char
        else:
            current += char
        if len(lines) > 256:
            raise ValueError('标签超出可读画布上限，请缩短标签；源数据保持不变。')
    lines.append(current)
    return lines


def svg_text(text, x, y, lines, *, size=14, anchor='start', role='label'):
    content = ''.join(f'<tspan x="{x:.2f}" dy="{0 if i == 0 else size+6}">{html.escape(line)}</tspan>'
                      for i, line in enumerate(lines))
    return (f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" data-role="{role}">'
            f'<title>{html.escape(str(text))}</title>{content}</text>')


def cartesian_layout(spec):
    labels, series = spec['labels'], spec['series']
    if not 1 <= len(labels) <= 500 or not 1 <= len(series) <= 6:
        raise ValueError('绘图需要1至500条有效记录和1至6个系列。')
    if any(len(s['values']) != len(labels) or any(not math.isfinite(v) for v in s['values']) for s in series):
        raise ValueError('系列长度或绘图数值无效。')
    texts = [spec['title'], spec['x'], *labels, *[s['name'] for s in series], *[s.get('unit','') for s in series]]
    if any(len(str(text)) > 5000 for text in texts):
        raise ValueError('图表文字超出5000字符的可读布局上限，未修改源数据。')
    values = [v for s in series for v in s['values']]
    low, high = min([0]+values), max([0]+values)
    if low == high:
        high = low+1
    ticks = list(dict.fromkeys(finite_lerp(low, high, i/5) for i in range(6)))
    left = max(90, math.ceil(max(text_width(f'{v:.6g}', 14) for v in ticks))+24)
    step = max(1, math.ceil(len(labels)/18))
    indexes = list(range(0, len(labels), step))
    cell = max(100, min(240, max(text_width(str(labels[i]),14) for i in indexes)+20))
    plot_width = max(840, len(indexes)*cell)
    width = math.ceil(left+plot_width+45)
    title = wrap_text(spec['title'], width-80, 22)
    title_end = 36+(len(title)-1)*28
    top = title_end+66; bottom = top+400
    label_width = max(70, plot_width/len(labels)*step-20)
    label_lines = {i:wrap_text(labels[i], label_width, 14) for i in indexes}
    label_bottom = bottom+28+max(len(lines) for lines in label_lines.values())*20
    xlabel = wrap_text(spec['x'], plot_width, 14)
    xlabel_y = label_bottom+15
    legend_top = xlabel_y+len(xlabel)*20+26
    columns = max(1, min(3, int((width-80)/320)))
    legend_width = (width-80)/columns
    legend, y = [], legend_top
    for start in range(0,len(series),columns):
        cells = []
        for i,s in enumerate(series[start:start+columns],start):
            text = s['name']+(f'（{s["unit"]}）' if s.get('unit') else '')
            lines = wrap_text(text, legend_width-54, 14)
            cells.append(dict(index=i, text=text, lines=lines, x=40+(i%columns)*legend_width, y=y))
        legend.extend(cells)
        y += max(len(cell['lines']) for cell in cells)*20+18
    height = math.ceil(y+20)
    if width > 8192 or height > 16384:
        raise ValueError('图表超出可读画布上限，请缩短文字或减少绘图记录，源数据不变。')
    return dict(width=width,height=height,left=left,right=left+plot_width,top=top,bottom=bottom,
        low=low,high=high,ticks=ticks,title_lines=title,title_end=title_end,label_lines=label_lines,
        xlabel_lines=xlabel,xlabel_y=xlabel_y,legend=legend,font_size=14)


def render_cartesian_chart(spec, colors):
    layout = cartesian_layout(spec)
    left,right,top,bottom = [layout[k] for k in ('left','right','top','bottom')]
    labels,series = spec['labels'],spec['series']
    def y(value):
        return bottom-unit_fraction(value,layout['low'],layout['high'])*(bottom-top)
    width,height = layout['width'],layout['height']
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
             f'<title>{html.escape(spec["title"])}</title>',
             f'<rect width="{width}" height="{height}" fill="white"/>',
             '<g font-family="Arial,Microsoft YaHei,sans-serif" fill="#1c303d">',
             svg_text(spec['title'],40,36,layout['title_lines'],size=22,role='title'),
             svg_text('归一化值' if spec.get('normalized') else '数值（各系列单位见图例）',left,top-26,
                      ['归一化值' if spec.get('normalized') else '数值（各系列单位见图例）'],role='ylabel')]
    for tick in layout['ticks']:
        sy = y(tick)
        parts.extend([f'<path d="M{left:.2f} {sy:.2f}H{right:.2f}" stroke="#dce4e9"/>',
                      svg_text(f'{tick:.6g}',left-12,sy+5,[f'{tick:.6g}'],anchor='end',role='tick')])
    slot = (right-left)/len(labels)
    for index,lines in layout['label_lines'].items():
        parts.append(svg_text(labels[index],left+(index+.5)*slot,bottom+28,lines,anchor='middle',role='category'))
    parts.append(svg_text(spec['x'],(left+right)/2,layout['xlabel_y'],layout['xlabel_lines'],anchor='middle',role='xlabel'))
    for si,s in enumerate(series):
        color = colors[si%len(colors)]; points = []
        originals = s.get('original_values',s['values'])
        for index,value in enumerate(s['values']):
            sx,sy = left+(index+.5)*slot,y(value)
            info = f'{labels[index]} / {s["name"]}: {value:.17g}; 原值 {originals[index]:.17g} {s.get("unit","")}'
            title = '<title>'+html.escape(info)+'</title>'
            if spec['kind'] == 'bar':
                bw = slot*.8/len(series); bx = left+index*slot+slot*.1+si*bw
                parts.append(f'<rect x="{bx:.3f}" y="{min(y(0),sy):.3f}" width="{bw:.3f}" height="{abs(y(0)-sy):.3f}" fill="{color}">{title}</rect>')
            else:
                points.append(f'{sx:.3f},{sy:.3f}')
                parts.append(f'<circle cx="{sx:.3f}" cy="{sy:.3f}" r="3" fill="{color}">{title}</circle>')
        if points:
            parts.append(f'<polyline points="{" ".join(points)}" stroke="{color}" stroke-width="2" fill="none"/>')
    for cell in layout['legend']:
        color = colors[cell['index']%len(colors)]
        parts.extend([f'<rect x="{cell["x"]:.2f}" y="{cell["y"]-9:.2f}" width="16" height="5" fill="{color}"/>',
                      svg_text(cell['text'],cell['x']+26,cell['y'],cell['lines'],role='legend')])
    parts.append('</g></svg>')
    return ''.join(parts)
