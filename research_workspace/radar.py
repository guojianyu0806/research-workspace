"""Fixed 3D radar template: metric angle/radius, categorical series height."""
from __future__ import annotations

import html
import math


def radar_geometry(labels, series, *, normalize, yaw, elevation):
    if not 3 <= len(labels) <= 12 or len(set(labels)) != len(labels):
        raise ValueError('三维雷达图需要3至12个不重复的指标行。')
    if not 2 <= len(series) <= 6:
        raise ValueError('三维雷达图需要2至6个系列列，分别对应高度层。')
    if not math.isfinite(yaw) or not -180 <= yaw <= 180 or not math.isfinite(elevation) or not 15 <= elevation <= 75:
        raise ValueError('方位角范围为-180至180度，仰角为15至75度。')
    axes = []
    for index, label in enumerate(labels):
        values = [s['values'][index] for s in series]
        low, high = min(values), max(values)
        if not normalize and (low < 0 or high > 1):
            raise ValueError('未归一化的雷达图要求所有数值在0至1之间；其他数值请选择按指标行归一化。')
        axes.append({'label': label, 'angle_radians': 2 * math.pi * index / len(labels),
                     'original_min': low, 'original_max': high, 'constant': low == high})
    for si, item in enumerate(series):
        item['original_values'] = list(item['values'])
        if normalize:
            item['values'] = [(v-a['original_min'])/(a['original_max']-a['original_min'])
                              if not a['constant'] else .5 for v, a in zip(item['values'], axes)]
        item['z'] = 1.4 * si / (len(series)-1)
        item['points_3d'] = [{'x': v*math.cos(a['angle_radians']), 'y': v*math.sin(a['angle_radians']), 'z': item['z']}
                             for v, a in zip(item['values'], axes)]
    return {'axes': axes, 'camera': {'yaw_degrees': yaw, 'elevation_degrees': elevation},
            'projection': 'orthographic', 'height_dimension': 'categorical_series',
            'normalization': 'per_axis_across_series_minmax' if normalize else 'input_0_to_1',
            'constant_axis_radius': .5 if normalize else None}


def render_radar(spec, colors):
    geo, series = spec['radar'], spec['series']
    yaw, elevation = [math.radians(geo['camera'][key]) for key in ('yaw_degrees', 'elevation_degrees')]

    def project(x, y, z):
        return x*math.cos(yaw)-y*math.sin(yaw), -(z*math.cos(elevation)-(x*math.sin(yaw)+y*math.cos(yaw))*math.sin(elevation))

    # Bounds include all label anchors; geometry is never clipped when rotating.
    anchors = [project(1.2*math.cos(a['angle_radians']), 1.2*math.sin(a['angle_radians']), z)
               for a in geo['axes'] for z in (0, 1.4)]
    lowx, highx = min(p[0] for p in anchors), max(p[0] for p in anchors)
    lowy, highy = min(p[1] for p in anchors), max(p[1] for p in anchors)
    scale = min(650/(highx-lowx), 410/(highy-lowy))

    def screen(x, y, z):
        px, py = project(x, y, z)
        return 470+(px-(highx+lowx)/2)*scale, 290+(py-(highy+lowy)/2)*scale

    def polygon(radius, z):
        return [screen(radius*math.cos(a['angle_radians']), radius*math.sin(a['angle_radians']), z) for a in geo['axes']]

    def points(values):
        return ' '.join(f'{x:.3f},{y:.3f}' for x, y in values)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 660" role="img">',
           '<rect width="1000" height="660" fill="white"/>', '<g font-family="Arial,sans-serif" fill="#1c303d">',
           f'<text x="40" y="32" font-size="21">{html.escape(spec["title"])}</text>',
           '<text x="40" y="56" font-size="12">3D stacked radar · radius: 0–1 · height: series category</text>']
    for a in geo['axes']:
        angle = a['angle_radians']
        ax, ay = screen(math.cos(angle), math.sin(angle), 0)
        bx, by = screen(math.cos(angle), math.sin(angle), 1.4)
        out.append(f'<path d="M{ax:.3f},{ay:.3f}L{bx:.3f},{by:.3f}" fill="none" stroke="#dce4e9"/>')
        lx, ly = screen(1.12*math.cos(angle), 1.12*math.sin(angle), 0)
        out.append(f'<text x="{lx:.3f}" y="{ly+5:.3f}" font-size="12" text-anchor="middle">{html.escape(a["label"][:30])}</text>')
    # Bottom to top is the painter order for parallel horizontal planes.
    for si, item in enumerate(series):
        color, z = colors[si], item['z']
        for radius in (.25, .5, .75, 1):
            out.append(f'<polygon points="{points(polygon(radius,z))}" stroke="#dce4e9" fill="none" stroke-width=".8"/>')
        cx, cy = screen(0, 0, z)
        for sx, sy in polygon(1,z):
            out.append(f'<path d="M{cx:.3f},{cy:.3f}L{sx:.3f},{sy:.3f}" stroke="#e2e8ed"/>')
        shape = [screen(p['x'],p['y'],p['z']) for p in item['points_3d']]
        out.append(f'<polygon points="{points(shape)}" stroke="{color}" fill="{color}" fill-opacity=".15" stroke-width="2"/>')
        for ai, (sx,sy) in enumerate(shape):
            label = f'{item["name"]} / {geo["axes"][ai]["label"]}: {item["original_values"][ai]:.6g}; radius={item["values"][ai]:.6g}'
            out.append(f'<circle cx="{sx:.3f}" cy="{sy:.3f}" r="3" fill="{color}"><title>{html.escape(label)}</title></circle>')
        tx, ty = screen(1.08,0,z)
        out.append(f'<text x="{tx+8:.3f}" y="{ty:.3f}" fill="{color}" font-size="11">L{si+1}</text>')
        lx, ly = 40+(si % 3)*320, 554+(si//3)*28
        out.append(f'<rect x="{lx}" y="{ly-8}" width="14" height="5" fill="{color}"/><text x="{lx+22}" y="{ly}" font-size="13">L{si+1} · {html.escape(item["name"][:28])}</text>')
    mode = 'Per-axis min–max across series; constant axes = 0.5' if geo['normalization'].startswith('per_axis') else 'Input values already in [0,1]; no normalization'
    out.append(f'<text x="40" y="620" font-size="12">{mode}</text><text x="40" y="644" font-size="12">Height is categorical. Larger radius does not necessarily mean better performance.</text></g></svg>')
    return ''.join(out)
