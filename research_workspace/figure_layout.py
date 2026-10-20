"""Full-text radar keys and bounded obstacle-aware diagram layout, no inference."""
import heapq
import html
import json
import math
from .chart_layout import wrap_text, svg_text


def radar_layout(spec):
    geo=spec['radar'];axes=geo['axes'];series=spec['series'];width=1200
    if not 3<=len(axes)<=12 or not 2<=len(series)<=6:
        raise ValueError('雷达图轴或系列数量无效。')
    if any(len(str(text))>5000 for text in [spec['title'],*[a['label'] for a in axes],*[s['name'] for s in series]]):
        raise ValueError('雷达图文字超过可读布局上限，未修改原数据。')
    title=wrap_text(spec['title'],1120,22);title_end=36+28*(len(title)-1);top=title_end+80
    blocks=[];y=top+500
    for start in range(0,len(axes),3):
        cells=[]
        for i,a in enumerate(axes[start:start+3],start):
            text=f'A{i+1} · {a["label"]}';lines=wrap_text(text,330,14)
            cells.append(dict(index=i,text=text,lines=lines,x=40+(i%3)*380,y=y))
        blocks.extend(cells);y+=max(len(c['lines']) for c in cells)*20+20
    legend=[];y+=30
    for start in range(0,len(series),3):
        cells=[]
        for i,s in enumerate(series[start:start+3],start):
            text=f'L{i+1} · {s["name"]}'+(f'（{s["unit"]}）' if s.get('unit') else '')
            lines=wrap_text(text,330,14);cells.append(dict(index=i,text=text,lines=lines,x=40+(i%3)*380,y=y))
        legend.extend(cells);y+=max(len(c['lines']) for c in cells)*20+20
    notes=['每个轴编号对应上方完整指标名称；半径为0至1，高度仅为系列类别，颜色与L编号对应。',
           '按指标在系列间归一化，常数半径0.5。' if geo['normalization'].startswith('per_axis') else '输入数值在0至1之间，未进行归一化。',
           '数值大不自动代表性能更优；此图未进行显著性检验。']
    note_lines=[line for note in notes for line in wrap_text(note,1120,14)]
    height=math.ceil(y+len(note_lines)*20+40)
    if height>16384:raise ValueError('雷达图文字超出画布上限，请缩短文字，原数据不变。')
    return dict(width=width,height=height,title=title,title_end=title_end,top=top,axes=blocks,legend=legend,
                note_y=y+10,note_lines=note_lines,font_size=14)


def render_radar_v2(spec,colors):
    layout=radar_layout(spec);geo=spec['radar'];series=spec['series'];top=layout['top']
    yaw=math.radians(geo['camera']['yaw_degrees']);elevation=math.radians(geo['camera']['elevation_degrees'])
    def project(x,y,z):
        return x*math.cos(yaw)-y*math.sin(yaw),-(z*math.cos(elevation)-(x*math.sin(yaw)+y*math.cos(yaw))*math.sin(elevation))
    anchors=[project(1.2*math.cos(a['angle_radians']),1.2*math.sin(a['angle_radians']),z) for a in geo['axes'] for z in (0,1.4)]
    lx,hx=min(p[0] for p in anchors),max(p[0] for p in anchors);ly,hy=min(p[1] for p in anchors),max(p[1] for p in anchors)
    scale=min(650/(hx-lx),400/(hy-ly))
    def screen(x,y,z):
        px,py=project(x,y,z);return 600+(px-(hx+lx)/2)*scale,top+220+(py-(hy+ly)/2)*scale
    def polygon(radius,z):return [screen(radius*math.cos(a['angle_radians']),radius*math.sin(a['angle_radians']),z) for a in geo['axes']]
    def points(values):return ' '.join(f'{x:.3f},{y:.3f}' for x,y in values)
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{layout["height"]}" viewBox="0 0 1200 {layout["height"]}" role="img" data-layout-version="radar-v2">',
         f'<title>{html.escape(spec["title"])}</title><rect width="1200" height="{layout["height"]}" fill="white"/>',
         '<g font-family="Arial,Microsoft YaHei,sans-serif" fill="#1c303d">',
         svg_text(spec['title'],40,36,layout['title'],size=22,role='title'),
         svg_text('三维分层雷达图 · 半径0–1 · 高度为系列类别',40,layout['title_end']+30,['三维分层雷达图 · 半径0–1 · 高度为系列类别'],role='subtitle')]
    sides={-1:[],1:[]}
    for i,a in enumerate(geo['axes']):
        ax,ay=screen(math.cos(a['angle_radians']),math.sin(a['angle_radians']),0)
        bx,by=screen(math.cos(a['angle_radians']),math.sin(a['angle_radians']),1.4)
        out.append(f'<path d="M{ax:.3f},{ay:.3f}L{bx:.3f},{by:.3f}" fill="none" stroke="#dce4e9"/>')
        side=-1 if ax<600 else 1;sides[side].append((ay,ax,i))
    for side,items in sides.items():
        for rank,(ay,ax,i) in enumerate(sorted(items)):
            baseline=top+20+(rank+.5)*420/max(1,len(items));tx=200 if side<0 else 1000
            endpoint=tx+12 if side<0 else tx-12
            out.append(f'<path data-axis-index="{i}" d="M{ax:.3f},{ay:.3f}L{endpoint},{baseline-5:.3f}" fill="none" stroke="#899eaa" stroke-dasharray="3 3"/>')
            out.append(svg_text(f'A{i+1}',tx,baseline,[f'A{i+1}'],anchor='end' if side<0 else 'start',role='axis-id'))
    for si,s in enumerate(series):
        color=colors[si];z=s['z']
        for radius in (.25,.5,.75,1):out.append(f'<polygon points="{points(polygon(radius,z))}" stroke="#dce4e9" fill="none" stroke-width=".8"/>')
        cx,cy=screen(0,0,z)
        for sx,sy in polygon(1,z):out.append(f'<path d="M{cx:.3f},{cy:.3f}L{sx:.3f},{sy:.3f}" stroke="#e2e8ed"/>')
        shape=[screen(p['x'],p['y'],p['z']) for p in s['points_3d']]
        out.append(f'<polygon points="{points(shape)}" stroke="{color}" fill="{color}" fill-opacity=".15" stroke-width="2"/>')
        for ai,(sx,sy) in enumerate(shape):
            label=f'A{ai+1} {geo["axes"][ai]["label"]} / L{si+1} {s["name"]}: {s["original_values"][ai]:.17g} {s.get("unit","")}; radius={s["values"][ai]:.17g}'
            out.append(f'<circle data-series-index="{si}" data-axis-index="{ai}" cx="{sx:.3f}" cy="{sy:.3f}" r="3" fill="{color}"><title>{html.escape(label)}</title></circle>')
    for cell in layout['axes']:out.append(svg_text(cell['text'],cell['x'],cell['y'],cell['lines'],role='axis-key'))
    for cell in layout['legend']:
        out.append(f'<rect x="{cell["x"]}" y="{cell["y"]-9}" width="16" height="5" fill="{colors[cell["index"]]}"/>')
        out.append(svg_text(cell['text'],cell['x']+26,cell['y'],cell['lines'],role='legend'))
    out.append(svg_text('\n'.join(layout['note_lines']),40,layout['note_y'],layout['note_lines'],role='notes'))
    out.append('</g></svg>');return ''.join(out)


def validate_diagram(nodes,edges):
    if not 1<=len(nodes)<=30 or len(edges)>100:raise ValueError('结构图需要1至30个节点，最多100条连接。')
    for n in nodes:
        if not isinstance(n,dict) or 'id' not in n or 'label' not in n or not isinstance(n['label'],str) or not n['label'].strip():
            raise ValueError('每个结构图节点需要id及非空label文字。')
        if not isinstance(n['id'],(str,int)) or isinstance(n['id'],bool) or not str(n['id']) or len(str(n['id']))>200 or len(n['label'])>5000:
            raise ValueError('结构图节点编号或文字长度无效。')
        wrap_text(str(n['id']),5000,14);wrap_text(n['label'],280,14)
    ids=[str(n['id']) for n in nodes]
    if len(set(ids))!=len(ids):raise ValueError('结构图节点编号必须唯一。')
    for e in edges:
        if not isinstance(e,dict) or 'source' not in e or 'target' not in e or str(e['source']) not in ids or str(e['target']) not in ids:
            raise ValueError('连接引用了不存在的节点，或缺少source/target。')
    return ids


def segment_blocked(a,b,boxes):
    for box in boxes:
        x,y,w,h=box
        if a[1]==b[1] and y<a[1]<y+h and max(min(a[0],b[0]),x)<min(max(a[0],b[0]),x+w):return True
        if a[0]==b[0] and x<a[0]<x+w and max(min(a[1],b[1]),y)<min(max(a[1],b[1]),y+h):return True
    return False


def route_graph(xs,ys,boxes):
    graph={}
    for xi,x in enumerate(xs):
        for yi,y in enumerate(ys):
            if not any(bx<x<bx+bw and by<y<by+bh for bx,by,bw,bh in boxes):graph[x,y]=[]
    for p in graph:
        xi=xs.index(p[0]);yi=ys.index(p[1])
        for xj,yj in ((xi-1,yi),(xi+1,yi),(xi,yi-1),(xi,yi+1)):
            if 0<=xj<len(xs) and 0<=yj<len(ys):
                q=xs[xj],ys[yj]
                if q in graph and not segment_blocked(p,q,boxes):graph[p].append(q)
    return graph


def orthogonal_path(graph,start,end):
    queue=[(0,start,2)];cost={(start,2):0};previous={};finish=None
    while queue:
        distance,p,direction=heapq.heappop(queue)
        if distance!=cost.get((p,direction)):continue
        if p==end:finish=(p,direction);break
        for q in graph[p]:
            turn=0 if p[1]==q[1] else 1
            new=distance+abs(p[0]-q[0])+abs(p[1]-q[1])+(15 if direction!=2 and direction!=turn else 0)
            key=q,turn
            if new<cost.get(key,float('inf')):cost[key]=new;previous[key]=(p,direction);heapq.heappush(queue,(new,q,turn))
    if finish is None:raise ValueError('结构图无法在节点外布线，请减少连接或节点文字。')
    path=[]
    while True:
        path.append(finish[0])
        if finish not in previous:break
        finish=previous[finish]
    path.reverse();return path


def diagram_layout(nodes,edges):
    ids=validate_diagram(nodes,edges);columns=min(3,len(nodes));width=140+columns*320+(columns-1)*100
    boxes={};texts={};y=60
    for start in range(0,len(nodes),columns):
        lines={str(n['id']):wrap_text(n['label'],280,14) for n in nodes[start:start+columns]}
        height=max(70,max(len(v)*20+28 for v in lines.values()))
        for col,n in enumerate(nodes[start:start+columns]):boxes[str(n['id'])]=(70+col*420,y,320,height);texts[str(n['id'])]=lines[str(n['id'])]
        y+=height+80
    height=y-20
    if height>16384:raise ValueError('结构图高度超出可读画布上限，未截断节点文字。')
    ports=[];xs={30,width-30};ys={30,height-30}
    for x,y,w,h in boxes.values():xs.update((x-20,x,x+w/2,x+w,x+w+20));ys.update((y-20,y,y+h/2,y+h,y+h+20))
    for e in edges:
        a,b=boxes[str(e['source'])],boxes[str(e['target'])];ax,ay,aw,ah=a;bx,by,bw,bh=b
        middle=None
        if str(e['source'])==str(e['target']):start=(ax+aw,ay+ah/2);end=(ax+aw/2,ay+ah);middle=(ax+aw+28,ay+ah+28)
        elif ax==bx:start=(ax+aw/2,ay+ah if by>ay else ay);end=(bx+bw/2,by if by>ay else by+bh)
        elif bx>ax:start=(ax+aw,ay+ah/2);end=(bx,by+bh/2)
        else:start=(ax,ay+ah/2);end=(bx+bw,by+bh/2)
        for p in (start,end,middle):
            if p is not None:xs.add(p[0]);ys.add(p[1])
        ports.append((start,end,middle))
    graph=route_graph(sorted(xs),sorted(ys),list(boxes.values())) if edges else {}
    routes=[]
    for start,end,middle in ports:
        path=orthogonal_path(graph,start,end) if middle is None else orthogonal_path(graph,start,middle)+orthogonal_path(graph,middle,end)[1:]
        routes.append(path)
    return dict(width=width,height=height,boxes=boxes,texts=texts,routes=routes,font_size=14)


def render_diagram_v2(nodes,edges):
    data=diagram_layout(nodes,edges);width,height=data['width'],data['height']
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" data-layout-version="diagram-v2" role="img">',
         '<title>按输入节点与有向连接绘制的结构图</title>',
         '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="#657782"/></marker></defs>',
         f'<rect width="{width}" height="{height}" fill="white"/>','<g font-family="Arial,Microsoft YaHei,sans-serif" fill="#1c303d">']
    for index,(e,path) in enumerate(zip(edges,data['routes'])):
        d='M'+'L'.join(f'{x:.3f},{y:.3f}' for x,y in path)
        source=html.escape(json.dumps(str(e['source'])),quote=True);target=html.escape(json.dumps(str(e['target'])),quote=True)
        out.append(f'<path data-edge-index="{index}" data-source-json="{source}" data-target-json="{target}" d="{d}" stroke="#657782" stroke-width="1.4" fill="none" marker-end="url(#arrow)"/>')
    for n in nodes:
        key=str(n['id']);x,y,w,h=data['boxes'][key];lines=data['texts'][key]
        out.append(f'<rect data-node-id-json="{html.escape(json.dumps(key),quote=True)}" x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#e8f1f0" stroke="#165f63"/>')
        out.append(svg_text(n['label'],x+w/2,y+(h-len(lines)*20)/2+16,lines,anchor='middle',role='node'))
    out.append('</g></svg>');return ''.join(out)
