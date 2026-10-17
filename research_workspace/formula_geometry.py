"""Finite native-glyph math transcription; never completes missing symbols.

Fractions and radicals require actual vector rules. Scripts require explicit
font size/baseline displacement. Every non-space input glyph must be consumed.
"""
from __future__ import annotations
import re
from statistics import median

VERSION='native-math-geometry-v2'
SYMBOLS=dict(zip('αβγδεζηθικλμνξοπρστυφχψω',
    ['\\'+s for s in ['alpha','beta','gamma','delta','epsilon','zeta','eta','theta','iota','kappa','lambda','mu','nu','xi','omicron','pi','rho','sigma','tau','upsilon','phi','chi','psi','omega']]))
SYMBOLS.update({'∈':r'\in','∉':r'\notin','≤':r'\leq','≥':r'\geq','≠':r'\neq','≈':r'\approx',
    '×':r'\times','·':r'\cdot','−':'-','∂':r'\partial','∞':r'\infty','∇':r'\nabla',
    '∑':r'\sum','∏':r'\prod','∫':r'\int','±':r'\pm','…':r'\ldots','ϵ':r'\epsilon',
    'ο':'o','Σ':r'\Sigma','Π':r'\Pi','Δ':r'\Delta','Ω':r'\Omega','ϕ':r'\varphi','ℝ':r'\mathbb{R}',
    '{':r'\{','}':r'\}','|':r'\vert','_':r'\_','^':r'\char94{}'})


class Unsupported(ValueError):pass


def union(nodes):
    return [min(n['box'][0] for n in nodes),min(n['box'][1] for n in nodes),
            max(n['box'][2] for n in nodes),max(n['box'][3] for n in nodes)]


def composite(nodes,latex,y,size):
    return {'box':union(nodes),'latex':latex,'y':y,'size':size,'ids':set().union(*(n['ids'] for n in nodes)),
        'font':'','flags':0,'char':None}


def _atom(g):
    c=g['char']
    if g['font'].startswith('CMEX') and c.isascii() and c.isalpha():raise Unsupported('unmapped_native_glyph')
    if c in SYMBOLS:value=SYMBOLS[c]
    elif re.fullmatch(r'[A-Za-z0-9=+*/(),.\[\]:;<>!\-^_]',c):value=c
    else:raise Unsupported('unmapped_native_glyph')
    if g['flags']&16 and c.isalpha():value=(r'\mathbf{' if c.isascii() else r'\boldsymbol{')+value+'}'
    return value


def _parse(glyphs,rules,depth=0):
    if depth>8 or len(glyphs)>400:raise Unsupported('formula_complexity_limit')
    if not glyphs:raise Unsupported('empty_math_operand')
    size=max(g['size'] for g in glyphs);nodes=list(glyphs);used_rules=[]
    for operator in [n for n in nodes if n['char'] in {'∑','∏','∫'}]:
        nearby=[n for n in nodes if n is not operator and n['size']>=size*.88
            and operator['box'][2]-1<=n['box'][0]<=operator['box'][2]+size*4
            and abs(n['y']-(operator['y']+(size if operator['font'].startswith('CMEX') else 0)))<size*.75]
        if not nearby:raise Unsupported('operator_baseline_ambiguous')
        baseline=median(n['y'] for n in nearby)
        limits=[n for n in nodes if n is not operator and n['size']<size*.88
            and operator['box'][0]-size*.6<=(n['box'][0]+n['box'][2])/2<=operator['box'][2]+size*.6
            and operator['y']-size*1.5<n['y']<baseline+size*1.7]
        above=[n for n in limits if n['y']<baseline-size*.35]
        below=[n for n in limits if n['y']>baseline+size*.45]
        if len(above)+len(below)!=len(limits):raise Unsupported('ambiguous_operator_limit')
        latex={'∑':r'\sum','∏':r'\prod','∫':r'\int'}[operator['char']]
        if below:latex+='_{'+_parse(below,[],depth+1)['latex']+'}'
        if above:latex+='^{'+_parse(above,[],depth+1)['latex']+'}'
        combined=composite([operator,*limits],latex,baseline,size)
        nodes=[n for n in nodes if not n['ids']&combined['ids']]+[combined]
    # Wider rules first: the denominator may itself contain a radical or fraction.
    for rule in sorted(rules,key=lambda r:r[2]-r[0],reverse=True):
        x0,y,x1=rule
        if any(n['char']=='√' and 0<=x0-n['box'][2]<size*.5 and n['box'][1]-1<=y<=n['box'][3]+1 for n in nodes):continue
        inside=[n for n in nodes if x0-.7<=(n['box'][0]+n['box'][2])/2<=x1+.7 and abs(n['y']-y)<size*2.2]
        above=[n for n in inside if n['y']<y-.8];below=[n for n in inside if n['y']>y+.8]
        if not above or not below:continue
        if len(above)+len(below)!=len(inside):continue
        a=_parse(above,[r for r in rules if r!=rule and r[1]<y],depth+1)
        b=_parse(below,[r for r in rules if r!=rule and r[1]>y],depth+1)
        node=composite(inside,r'\frac{'+a['latex']+'}{'+b['latex']+'}',y+size*.25,max(n['size'] for n in inside))
        ids=node['ids'];nodes=[n for n in nodes if not n['ids']&ids]+[node];used_rules.append(rule)
    for root in [n for n in nodes if n['char']=='√']:
        candidates=[r for r in rules if r not in used_rules and abs(r[0]-root['box'][2])<size*.5
            and root['box'][1]-2<=r[1]<=root['box'][3]+2]
        if not candidates:raise Unsupported('radical_extent_not_observed')
        bar=min(candidates,key=lambda r:abs(r[0]-root['box'][2]));x0,y,x1=bar
        children=[n for n in nodes if n is not root and x0-.7<=(n['box'][0]+n['box'][2])/2<=x1+.7
            and y+.7<n['y']<y+size*2]
        child=_parse(children,[r for r in rules if r!=bar and r[1]>y],depth+1)
        node=composite([root,*children],r'\sqrt{'+child['latex']+'}',child['y'],child['size'])
        nodes=[n for n in nodes if not n['ids']&node['ids']]+[node]
    # Assign directly on a copy; original glyph observations remain immutable.
    nodes=[dict(n,ids=set(n['ids']),box=list(n['box']),latex=_atom(n)) if n['char'] is not None else dict(n,ids=set(n['ids']),box=list(n['box'])) for n in nodes]
    size=max(n['size'] for n in nodes)
    candidates=[n for n in nodes if n['size']>=size*.88]
    clusters={}
    for n in candidates:clusters.setdefault(round(n['y']/(size*.2)),[]).append(n)
    baseline=median(n['y'] for n in max(clusters.values(),key=lambda ns:sum(len(n['ids']) for n in ns)))
    base=[n for n in nodes if abs(n['y']-baseline)<size*.3 and n['size']>=size*.88]
    scripts=[n for n in nodes if n not in base]
    if not base:raise Unsupported('no_stable_math_baseline')
    if any(n['size']>=size*.88 and abs(n['y']-baseline)>size*.45 for n in scripts):raise Unsupported('multiple_unresolved_math_rows')
    base.sort(key=lambda n:n['box'][0]);merged=[]
    for n in base:
        previous=merged[-1] if merged else None
        roman=bool(n['char'] and n['char'].isalpha() and not(n['flags']&2))
        number=bool(n['char'] and re.fullmatch(r'[0-9.]',n['char']))
        if (previous and n['char'] and previous.get('merge_kind')==('roman' if roman else 'number' if number else None) and (roman or number)
            and n['font']==previous['font'] and n['box'][0]-previous['box'][2]<size*.25):
            previous['literal']+=n['char'];previous['box']=union([previous,n]);previous['ids']|=n['ids']
        else:merged.append({**n,'ids':set(n['ids']),'box':list(n['box']),'merge_kind':'roman' if roman else 'number' if number else None,'literal':n['char'] or ''})
    attached={i:{'sub':[],'sup':[]} for i in range(len(merged))}
    centre=median((n['box'][1]+n['box'][3])/2 for n in base)
    for n in sorted(scripts,key=lambda n:n['box'][0]):
        eligible=[i for i,b in enumerate(merged) if b['box'][0]<=n['box'][0]+1 and -.5*size<=n['box'][0]-b['box'][2]<=size]
        side='sup' if (n['box'][1]+n['box'][3])/2<centre else 'sub'
        if not eligible:
            eligible=[i for i,a in attached.items() if a[side] and -size*.25<=n['box'][0]-max(g['box'][2] for g in a[side])<size]
        if not eligible:raise Unsupported('unattached_math_script')
        i=min(eligible,key=lambda i:abs(n['box'][0]-merged[i]['box'][2]))
        attached[i][side].append(n)
    rendered=[]
    for i,n in enumerate(merged):
        latex=n['latex']
        if n['merge_kind']=='roman' and len(n['literal'])>1:
            following=merged[i+1]['char'] if i+1<len(merged) else None
            latex=(r'\operatorname{' if following=='(' else r'\text{' if n['literal'] in {'where','and','for'} else r'\mathrm{')+n['literal']+'}'
        elif n['merge_kind']=='number':latex=n['literal']
        for side,mark in (('sub','_'),('sup','^')):
            children=attached[i][side]
            if children:latex+=mark+'{'+_parse(children,[],depth+1)['latex']+'}'
        rendered.append(latex)
    value=' '.join(rendered)
    if value.count('(')!=value.count(')') or value.count('[')!=value.count(']'):raise Unsupported('unbalanced_math_delimiters')
    result=composite(nodes,value,baseline,size)
    result['relation_indexes']=[sum(len(s)+1 for s in rendered[:i]) for i,n in enumerate(merged) if n['char']=='=']
    if result['ids']!=set().union(*(g['ids'] for g in glyphs)):raise Unsupported('unconsumed_math_glyph')
    return result


def extract_display_formulas(page):
    raw=page.get_text('rawdict',flags=pymupdf_flags());lines=[];glyphs=[]
    for block in raw.get('blocks',[]):
        if block.get('type')!=0:continue
        for line in block.get('lines',[]):
            if line.get('dir',(1,0))[0]<.9:continue
            own=[]
            for span in line.get('spans',[]):
                for char in span.get('chars',[]):
                    if not char['c'].strip():continue
                    g={'char':char['c'],'box':list(char['bbox']),'origin_x':char['origin'][0],'y':char['origin'][1],
                        'size':span['size'],'font':span['font'],'flags':span['flags'],'ids':{len(glyphs)}}
                    glyphs.append(g);own.append(g)
            if own:lines.append(own)
    if len(glyphs)>15000:return [],{'version':VERSION,'status':'glyph_budget_exceeded','model_calls':0}
    from .native_math_fonts import recover_glyph_names
    font_audit=recover_glyph_names(page,glyphs)
    seeds=[ln for ln in lines if any(g['char'] in '=≤≥' for g in ln)
        and sum(bool(re.match(r'CM|MS[AB]',g['font'])) or bool(g['flags']&2) for g in ln)>.45*len(ln)]
    operators=[g for g in glyphs if g['char'] in {'∑','∏','∫'}]
    seeds=[s for s in seeds if not any(max(g['size'] for g in s)<op['size']*.88
        and op['box'][0]-op['size']*.6<=median((g['box'][0]+g['box'][2])/2 for g in s)<=op['box'][2]+op['size']*.6
        and abs(median(g['y'] for g in s)-op['y'])<op['size']*3 for op in operators)]
    context=[g for ln in lines if (sum(bool(re.match(r'CM|MS[AB]',g['font'])) or bool(g['flags']&2) for g in ln)>.45*len(ln)
        or len(ln)<=4 and all(g['char'] in '()[]0123456789=+-,.' for g in ln)) for g in ln]
    rules=[]
    drawings=page.get_drawings()
    if len(drawings)>5000:return [],{'version':VERSION,'status':'vector_budget_exceeded','model_calls':0}
    for path in drawings:
        for item in path['items']:
            if item[0]=='l' and abs(item[1].y-item[2].y)<.2 and 3<abs(item[1].x-item[2].x)<page.rect.width*.7:
                rules.append((min(item[1].x,item[2].x),item[1].y,max(item[1].x,item[2].x)))
    formulas=[];seen=set()
    for seed in seeds[:40]:
        baseline=median(g['y'] for g in seed if g['size']>=max(x['size'] for x in seed)*.88);size=max(g['size'] for g in seed)
        own=list(seed);box=union(own)
        other_baselines=[median(g['y'] for g in s) for s in seeds if s is not seed]
        top=baseline-size*2;bottom=baseline+size*2
        for y in other_baselines:
            if y<baseline-2:top=max(top,(baseline+y)/2)
            elif y>baseline+2:bottom=min(bottom,(baseline+y)/2)
        for _ in range(12):
            extra=[g for g in context if top<=g['y']<=bottom and g not in own
                and box[0]-size*2<=g['box'][0]<=box[2]+size*2]
            if not extra:break
            own.extend(extra);box=union(own)
        ids=set().union(*(g['ids'] for g in own))
        if ids&seen:continue
        seen|=ids;local=[r for r in rules if box[0]-size*.7<=r[0] and r[2]<=box[2]+size*.7 and box[1]-1<=r[1]<=box[3]+1]
        if local:
            box=union([{'box':box}]+[{'box':[r[0],r[1]-.15,r[2],r[1]+.15]} for r in local])
        try:
            prose=[]
            for i,g in enumerate(seed):
                if i and g['box'][0]-seed[i-1]['box'][2]>size*.15:prose.append(' ')
                prose.append(g['char'] if not (re.match(r'CM|MS[AB]',g['font']) or g['flags']&2) else ' ')
            words=re.findall(r'[A-Za-z]{3,}', ''.join(prose))
            if len(words)>=3:raise Unsupported('prose_mixed_with_math')
            if any((g['font'].startswith('CMEX') and g['char'].isascii() and g['char'].isalpha()) and abs(g['box'][0]-box[0])<size*2 and abs(g['y']-baseline)<max(g['size'],size)*3 for g in glyphs):
                raise Unsupported('large_operator_limits_require_review')
            missing=[g for g in glyphs if g not in own and box[0]<(g['box'][0]+g['box'][2])/2<box[2] and box[1]<(g['box'][1]+g['box'][3])/2<box[3]]
            if missing:raise Unsupported('unassigned_glyph_inside_formula_region')
            parsed=_parse(own,local);latex=parsed['latex'];status='transcribed_needs_review';reason='native_geometry_complete_glyph_coverage'
        except Unsupported as error:latex=None;status='unsupported_geometry';reason=str(error)
        formulas.append({'raw_bbox':box,'latex':latex,'status':status,'conversion':reason,'method':VERSION,
            'original_text':page.get_text('text',clip=box).strip(),'needs_review':True,
            'relation_indexes':parsed.get('relation_indexes',[]) if latex else [],'baseline':baseline,'base_size':size,
            'relation_x':min((g['box'][0] for g in own if g['char']=='=' and g['size']>=size*.88),default=None),
            'glyphs':[{'text':g['char'],'native_text':g.get('native_text',g['char']),'glyph_mapping':g.get('glyph_mapping'),
                'bbox':g['box'],'origin_y_unrotated_points':g['y'],'size':g['size'],'font':g['font'],'flags':g['flags']} for g in own],
            'rules':[list(r) for r in local],'glyph_count':len(own)})
    formulas=_join_aligned(formulas,glyphs,page)
    for formula in formulas:
        for key in ('relation_indexes','baseline','base_size','relation_x'):formula.pop(key,None)
    return formulas,{'version':VERSION,'status':'analyzed','formula_count':len(formulas),'seed_count':len(seeds),'truncated':len(seeds)>40,'seed_budget':40,'model_calls':0,'glyph_mapping_audit':font_audit,
        'supported':['native_scripts','vector_rule_fractions','vector_rule_radicals','nested_operators','embedded_named_operators','observed_operator_limits','aligned_equal_continuations'],
        'unsupported':['scanned_math','arbitrary_matrices','missing_font_maps','unbounded_multiline_derivations']}


def _join_aligned(formulas,glyphs,page):
    """Join only aligned bare-equality continuations with complete row parses."""
    ordered=sorted(formulas,key=lambda f:(f['baseline'],f['raw_bbox'][0]));result=[]
    def aligned(row):
        value=row['latex'];indexes=row['relation_indexes']
        if len(indexes)!=1:raise Unsupported('ambiguous_alignment_relation')
        i=indexes[0]
        return value[:i]+'&'+value[i:]
    def source_row(row):
        return {'latex':row['latex'],'raw_bbox':row['raw_bbox'],'original_text':row['original_text'],
                'baseline_unrotated_points':row['baseline']}
    for current in ordered:
        if not current['latex'] or not current['latex'].lstrip().startswith('='):
            result.append(current);continue
        previous=result[-1] if result else None
        eligible=bool(previous and previous['latex'] and previous['relation_x'] is not None and current['relation_x'] is not None
            and abs(previous['relation_x']-current['relation_x'])<2
            and current['base_size']*.6<current['baseline']-previous['baseline']<current['base_size']*4.5
            and len(previous.get('source_rows',[]))<8)
        if eligible:
            combined=union([{'box':previous['raw_bbox']},{'box':current['raw_bbox']}])
            own={(round(g['bbox'][0],3),round(g['origin_y_unrotated_points'],3),g['text']) for row in (previous,current) for g in row['glyphs']}
            missing=[g for g in glyphs if combined[0]<(g['box'][0]+g['box'][2])/2<combined[2]
                and combined[1]<(g['box'][1]+g['box'][3])/2<combined[3]
                and (round(g['box'][0],3),round(g['y'],3),g['char']) not in own]
            eligible=not missing
        if not eligible:
            current['latex']=None;current['status']='unsupported_geometry';current['conversion']='orphan_or_ambiguous_continuation'
            result.append(current);continue
        try:
            rows=previous.get('source_rows',[source_row(previous)])+[source_row(current)]
            latex_rows=(previous['_aligned_latex_rows'] if '_aligned_latex_rows' in previous else [aligned(previous)])+[aligned(current)]
        except Unsupported:
            current['latex']=None;current['status']='unsupported_geometry';current['conversion']='ambiguous_alignment_relation'
            result.append(current);continue
        previous.update(raw_bbox=combined,latex=r'\begin{aligned}'+r' \\ '.join(latex_rows)+r'\end{aligned}',
            source_rows=rows,_aligned_latex_rows=latex_rows,baseline=current['baseline'],
            glyphs=previous['glyphs']+current['glyphs'],rules=previous['rules']+current['rules'],
            glyph_count=previous['glyph_count']+current['glyph_count'],
            original_text=page.get_text('text',clip=combined).strip(),conversion='complete_aligned_rows_with_glyph_coverage')
    for formula in result:formula.pop('_aligned_latex_rows',None)
    return result


def pymupdf_flags():
    import pymupdf
    return pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
