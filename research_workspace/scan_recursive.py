"""Bounded raster structure recursion with explicit fences and failed children.

No native text, fonts, document identity, or expected formula is an input.
"""
from copy import deepcopy
import numpy as np
import pymupdf
from .scan_math import components
from .scan_operators import shape, union

VERSION='raster-recursive-v1'


def recursive_scan_structures(pixels,primitives,excluded,read_sequence,valid_sequence,budget,row_fences=(),include_flat=False):
    from .raster_formulas import runs
    mask=pixels<190;cc=components(mask,limit=8000);specs=[];rejected=[]
    hints=[p['raw_bbox'] for p in primitives]
    def allowed(box,require_hint=True):
        from .scan_multiline import within_row_fence
        r=pymupdf.Rect(box)
        return (within_row_fence(box,row_fences) and r.get_area()<=8000 and (not require_hint or any(r.intersects(pymupdf.Rect(b)+(-12,-12,12,12)) for b in hints))
            and not any(r.intersects(pymupdf.Rect(e)) for e in excluded))
    # Large radicals are allowed only when the hook, rising stem and roof are
    # present in a connected observed component. Horizontal bars alone fail.
    for c in cc:
        a,b,d,e=c['box'];h=e-b;w=d-a
        if not (14<=h<=120 and h*.8<=w<=400):continue
        patch=np.zeros((h,w),dtype=bool)
        for y,x in c['points']:patch[y-b,x-a]=True
        roofs=[(x0,y,x1) for y in range(min(4,h)) for x0,x1 in runs(patch[y]) if x0>max(3,min(w*.15,h*.2)) and x1-x0>w*.4]
        bottom=np.flatnonzero(patch[-max(2,h//10):].any(0))
        if not (roofs and len(bottom) and max(bottom)<w*.55 and patch[int(h*.4):int(h*.75),:max(2,int(w*.15))].any()):continue
        x0,y,x1=max(roofs,key=lambda r:r[2]-r[0]);roof=[(a+x0)/2,(b+y)/2,(a+x1)/2]
        inner=[(a+x0)/2,(b+y+2)/2,(a+x1)/2,(e+max(2,h*.2))/2]
        box=union([[v/2 for v in c['box']],inner])
        # If a glyph touches the roof/stem, its ink cannot safely be classified
        # as operator ink. Keep that geometry unsupported rather than erase it.
        if any(x>a+x0+3 and yy>b+y+3 for yy,x in c['points']):continue
        if allowed(box,False):
            specs.append({'kind':'radical','box':box,'operands':[inner],'roof':roof,
                'operator_box':[v/2 for v in c['box']],'operator_pixels':c['points']})
            hints.append(box)
            if len(specs)>=64:break
    bars=[]
    for y in range(mask.shape[0]):
        for a,b in runs(mask[y]):
            if not 16<=b-a<=400:continue
            if bars and abs(y-bars[-1][1])<=2 and abs(a-bars[-1][0])<=2 and abs(b-bars[-1][2])<=2:continue
            bars.append((int(a),y,int(b)))
    for a,y,b in bars:
        if len(specs)>=64 or len(rejected)>=64:break
        if any(s['kind']=='radical' and abs(s['roof'][1]*2-y)<=2 and abs(s['roof'][0]*2-a)<=3 and abs(s['roof'][2]*2-b)<=3 for s in specs):continue
        if not allowed([a/2,y/2-1,b/2,y/2+1]):continue
        top=max(0,y-96);bottom=min(mask.shape[0],y+97)
        bands=[(int(lo+top),int(hi+top)) for lo,hi in runs(mask[top:bottom,a:b].any(1))]
        above=[r for r in bands if r[1]<y-1];below=[r for r in bands if r[0]>y+1]
        if not above or not below:
            rejected.append({'kind':'fraction','raw_bbox':[a/2,y/2-.5,b/2,y/2+.5],
                'parsed':False,'reason':'missing_operand_ink','needs_review':True});continue
        upper=[above[-1]];lower=[below[0]]
        if y-upper[0][1]>16 or lower[0][0]-y>16:
            rejected.append({'kind':'fraction','raw_bbox':[a/2,y/2-.5,b/2,y/2+.5],
                'parsed':False,'reason':'operand_separated_by_unresolved_gap','needs_review':True});continue
        for row in reversed(above[:-1]):
            if any(c<=a+1 and z>=b-1 and z-c>(b-a)*1.1 and row[0]<=yy<row[1] for c,yy,z in bars):break
            if upper[0][0]-row[1]>10 or not allowed([a/2,row[0]/2,b/2,lower[-1][1]/2]):break
            upper.insert(0,row)
        for row in below[1:]:
            if any(c<=a+1 and z>=b-1 and z-c>(b-a)*1.1 and row[0]<=yy<row[1] for c,yy,z in bars):break
            if row[0]-lower[-1][1]>10 or not allowed([a/2,upper[0][0]/2,b/2,row[1]/2]):break
            lower.append(row)
        if upper[-1][1]-upper[0][0]<6 or lower[-1][1]-lower[0][0]<6:continue
        u=[a/2,upper[0][0]/2,b/2,upper[-1][1]/2];l=[a/2,lower[0][0]/2,b/2,lower[-1][1]/2]
        box=union([u,l])
        if allowed(box):specs.append({'kind':'fraction','box':box,'operands':[u,l],'rule':[a/2,y/2,b/2]})
        if len(specs)>=64:break

    def contains(a,b,margin=.6):return (pymupdf.Rect(a)+(-margin,-margin,margin,margin)).contains(pymupdf.Rect(b))
    count=0;cache={}
    def parse(spec,depth=0,trail=()):
        nonlocal count
        key=id(spec)
        if key in cache:return deepcopy(cache[key])
        count+=1
        if depth>=6 or count>64 or key in trail:
            return {'raw_bbox':spec['box'],'latex':None,'original_text':'','needs_review':True,
                'method':VERSION,'scope':'subexpression','conversion':'recursive_structure_budget','status':'unsupported_geometry'}
        nested_specs=[s for s in specs if s is not spec and any(contains(b,s['box']) for b in spec['operands'])]
        if spec['kind']=='fraction' and not nested_specs:
            flat=next((p for p in primitives if p.get('latex') and p.get('observed_rule')
                and abs(p['observed_rule'][1]-spec['rule'][1])<=.75
                and abs(p['observed_rule'][0]-spec['rule'][0])<=1.5
                and abs(p['observed_rule'][2]-spec['rule'][2])<=1.5),None)
            if flat:
                node=deepcopy(flat);node['structure_kind']='fraction'
                node['recursive_audit']={'depth':depth,'direct_structure_children':0,'reused_observation':'same_source_fraction_rule_and_operands','model_calls':0}
                cache[key]=deepcopy(node);return node
        operands=[];nested_count=0
        for index,box in enumerate(spec['operands']):
            candidates=[s for s in specs if s is not spec and id(s) not in trail
                and contains(box,s['box']) and pymupdf.Rect(s['box']).get_area()<pymupdf.Rect(spec['box']).get_area()*.95]
            maximal=[s for s in candidates if not any(t is not s and contains(t['box'],s['box'])
                and pymupdf.Rect(t['box']).get_area()>pymupdf.Rect(s['box']).get_area()*1.05 for t in candidates)]
            children=[parse(s,depth+1,(*trail,key)) for s in maximal];nested_count+=len(maximal)
            # Keep observed scripts as atomic children. Reuse the base/script
            # audit inside a simple radical, without reusing its wrapper.
            atoms=[]
            for p in primitives:
                if p.get('conversion','').startswith('observed_') and 'script_pair' in p.get('conversion','') and contains(box,p['raw_bbox']):atoms.append(p)
                if p.get('observed_operator')==r'\sqrt{}' and p.get('latex'):
                    own=p.get('operands',[])
                    if len(own)==2 and own[0].get('role')=='base' and contains(box,union(o['raw_bbox'] for o in own)):
                        atoms.append({'raw_bbox':union(o['raw_bbox'] for o in own),'latex':p['latex'][6:-1],
                            'original_text':' '.join(o['original_text'] for o in own),'operands':deepcopy(own),
                            'method':VERSION,'needs_review':True})
            for atom in atoms:
                if not any(pymupdf.Rect(atom['raw_bbox']).intersects(pymupdf.Rect(c['raw_bbox'])) for c in children):children.append(deepcopy(atom))
            # Ambiguous crossing extents cannot be silently linearized.
            overlap=any((pymupdf.Rect(c['raw_bbox'])&pymupdf.Rect(d['raw_bbox'])).get_area()>0
                for i,c in enumerate(children) for d in children[i+1:])
            role='radicand' if spec['kind']=='radical' else 'numerator' if index==0 else 'denominator'
            node=read_sequence(box,children,role,spec.get('operator_pixels',()),parse_scripts=True)
            if overlap or not valid_sequence(node['token_text']):node['latex']=None
            operands.append(node)
        good=all(o['latex'] for o in operands)
        latex=(r'\sqrt{'+operands[0]['latex']+'}' if spec['kind']=='radical' else
            r'\frac{'+operands[0]['latex']+'}{'+operands[1]['latex']+'}') if good else None
        node={'raw_bbox':union([spec['box'],*[o['raw_bbox'] for o in operands]]),'latex':latex,
            'original_text':' / '.join(o['original_text'] for o in operands),'operands':operands,
            'method':VERSION,'scope':'subexpression','needs_review':True,'structure_kind':spec['kind'],
            'status':'partial_formula_needs_review' if latex else 'unsupported_geometry',
            'conversion':'recursive_observed_'+spec['kind'] if latex else 'unresolved_recursive_'+spec['kind'],
            'recursive_audit':{'depth':depth,'direct_structure_children':nested_count,'model_calls':0}}
        if spec['kind']=='fraction':node['observed_rule']=spec['rule']
        else:node['geometry_audit']={'method':'observed_hook_stem_roof_and_recursive_radicand',
            'bar_box':[spec['roof'][0],spec['roof'][1],spec['roof'][2],spec['roof'][1]+.5],
            'operator_box':spec['operator_box']}
        cache[key]=deepcopy(node);return node
    # Largest structures first, so a bounded run cannot spend its entire OCR
    # allowance transcribing detached leaves before their containing formula.
    result=[]
    for spec in sorted(specs,key=lambda s:-pymupdf.Rect(s['box']).get_area()):
        if budget['remaining']<2:break
        nested=any(t is not spec and any(contains(b,t['box']) for b in spec['operands']) for t in specs)
        if not nested and not include_flat:continue
        if any(contains(n['raw_bbox'],spec['box']) for n in result):continue
        result.append(parse(spec))
        if len(result)>=8:break
    return result,{'version':VERSION,'candidate_count':len(specs),'parsed_structure_count':count,
        'candidate_regions':[{'kind':s['kind'],'raw_bbox':s['box'],'parsed':id(s) in cache} for s in specs]+rejected,
        'rejected_candidate_count':len(rejected),
        'max_depth':6,'max_structures':64,'model_calls':0,'needs_review':True}
