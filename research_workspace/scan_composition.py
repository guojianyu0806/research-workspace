"""Bounded composition of observed scan structures, never semantic completion.

The input is raster pixels and independently audited child regions. Unknown
children, unbalanced delimiters and unassigned ink prevent whole equations.
"""
from copy import deepcopy
import re
import numpy as np
import pymupdf
from .scan_math import components
from .scan_operators import observe, union, join_ink_parts

VERSION = 'raster-composition-v1'


def valid_sequence(text, *, equation=False):
    tokens=re.findall(r'[A-Za-z]+|\d+(?:\.\d+)?|[=+\-*/(),\[\].@]',text)
    if ''.join(tokens)!=text or not tokens:return False
    stack=[];equals=0
    for i,t in enumerate(tokens):
        if t in ('(', '['):stack.append(t)
        elif t in (')', ']'):
            if not stack or stack.pop()!=('(' if t==')' else '['):return False
        elif t=='=':
            if stack or i==0 or i==len(tokens)-1:return False
            equals+=1
        elif len(t)>1 and t.isalpha():
            if i+1==len(tokens) or tokens[i+1] not in ('(', '['):return False
        if t in ('=', '+', '*', '/', ',') and (i==0 or tokens[i-1] in ('=', '+', '-', '*', '/', ',', '(', '[')):
            return False
        if t in ('=', '+', '-', '*', '/', ',') and (i+1==len(tokens) or tokens[i+1] in (')', ']', '=', '*', '/', ',')):
            return False
        if t=='.' and i!=len(tokens)-1:return False
        if t in ('(', '[') and (i+1==len(tokens) or tokens[i+1] in (')',']',',','=')):return False
    return not stack and equals==(1 if equation else 0)


def compose_scan_math(page,pixels,configuration,primitives,excluded=()):
    from .raster_formulas import runs, _pixel_crop, consensus
    mask=pixels<190;budget={'remaining':96};result=[]
    page_components=components(mask,limit=8000)
    glyph_references=[]

    def region_mask(box):
        x0,y0,x1,y1=[int(round(v*2)) for v in box]
        return mask[y0:y1,x0:x1].copy(),x0,y0

    def sequence(box,children,role,ignored_pixels=(),parse_scripts=False):
        own,x0,y0=region_mask(box);total=int(own.sum());parts=[]
        ignored=0
        for y,x in ignored_pixels:
            if 0<=y-y0<own.shape[0] and 0<=x-x0<own.shape[1]:
                ignored+=int(own[y-y0,x-x0]);own[y-y0,x-x0]=False
        # A parent owns the original child record, including failed readings.
        for child in sorted(children,key=lambda c:c['raw_bbox'][0]):
            b=child['raw_bbox'];part=deepcopy(child);part['role']='nested_structure';parts.append(part)
            own[max(0,round(b[1]*2)-y0):round(b[3]*2)-y0,max(0,round(b[0]*2)-x0):round(b[2]*2)-x0]=False
        cc=join_ink_parts(components(own))
        if parse_scripts:
            from .scan_glyphs import small_greek_scripts
            for node in small_greek_scripts(page,own,x0,y0,configuration,budget):
                parts.append(node);b=node['raw_bbox']
                own[max(0,int(np.floor(b[1]*2))-y0):int(np.ceil(b[3]*2))-y0,max(0,int(np.floor(b[0]*2))-x0):int(np.ceil(b[2]*2))-x0]=False
            cc=join_ink_parts(components(own))
            from .scan_operators import pair_regions,transcribe_pair
            # A thin curved delimiter can be disconnected by raster sampling.
            # Join only vertically adjacent narrow strokes, without filling ink.
            changed=True
            while changed:
                changed=False
                for i,c in enumerate(cc):
                    for j,d in enumerate(cc[i+1:],i+1):
                        a,b=c['box'],d['box'];merged=union([a,b]);height=merged[3]-merged[1]
                        curve=(min(a[2],b[2])>=max(a[0],b[0]) and 0<=max(a[1],b[1])-min(a[3],b[3])<=1
                            and height>=12 and merged[2]-merged[0]<=height*.3)
                        halves=(0<=max(a[0],b[0])-min(a[2],b[2])<=1 and min(a[3],b[3])>max(a[1],b[1])
                            and min(a[3]-a[1],b[3]-b[1])>=.8*height and merged[2]-merged[0]<=height*.9)
                        short_glyph=(min(a[2],b[2])>max(a[0],b[0]) and 0<=max(a[1],b[1])-min(a[3],b[3])<=1
                            and 7<=height<=14 and height*.5<=merged[2]-merged[0]<=height)
                        if curve or halves or short_glyph:
                            c['box']=merged;c['points']+=d['points'];cc.pop(j);changed=True;break
                    if changed:break
            cc=join_ink_parts(cc)
            for base,script,side in pair_regions(cc,min_height=8,extended_parts=True):
                b=base['box']
                if budget['remaining']<4:continue
                if any(c is not base and b[2]<=c['box'][0] and c['box'][2]<=script[0]
                    and c['box'][3]-c['box'][1]>=(b[3]-b[1])*.8 for c in cc):continue
                # Preserve tiny observed dots lost by the component-size filter
                # when bounding a script such as i. Read only its own x strip.
                sy=max(0,int(script[1]-(b[3]-b[1])*.4));sx0=int(script[0]);sx1=int(script[2])
                yy,xx=np.nonzero(own[sy:int(script[3]),sx0:sx1])
                if len(yy):script=[script[0],min(script[1],sy+int(yy.min())),script[2],script[3]]
                raw=lambda r:[(r[0]+x0)/2,(r[1]+y0)/2,(r[2]+x0)/2,(r[3]+y0)/2]
                region=union([b,script])
                if any(pymupdf.Rect(raw(region)).intersects(pymupdf.Rect(p['raw_bbox'])) for p in parts):continue
                node=transcribe_pair(page,raw(b),raw(script),side,configuration,budget,extended=True);node['role']='nested_structure';parts.append(node)
                from .scan_glyphs import corroborate_base
                corroborate_base(page,node,glyph_references)
                observed=node['raw_bbox']
                own[max(0,int(np.floor(observed[1]*2))-y0):max(0,int(np.ceil(observed[3]*2))-y0),max(0,int(np.floor(observed[0]*2))-x0):max(0,int(np.ceil(observed[2]*2))-x0)]=False
                if node['latex']:
                    # OCR already observes a half-point pad. Preserve that
                    # declared span for the script's detached edge pixels.
                    own[int(script[1]):int(script[3]),int(script[2]):int(script[2])+1]=False
                    node['geometry_audit']['observed_script_right_pad_points']=.5
            cc=join_ink_parts(components(own))
        specials=[]
        # Square brackets have a continuous stem and two visible arms. Curved
        # parentheses cannot pass this test, even if OCR calls either a bar.
        for c in cc:
            a,b,d,e=c['box'];h=e-b;w=d-a
            if h<12 or w>h*.3 or w<2:continue
            patch=own[b:e,a:d]
            top=patch[:2].any(0);bottom=patch[-2:].any(0)
            if (top&bottom).sum()<2:continue
            if patch[:,0].mean()>.85 and patch[:,-1].mean()<.5:specials.append((c['box'],'['))
            elif patch[:,-1].mean()>.85 and patch[:,0].mean()<.5:specials.append((c['box'],']'))
        # A narrow closing parenthesis beside a structure is read separately
        # from its following variable; no replacement is made on shape alone.
        if children:
            edge=max(c['raw_bbox'][2] for c in children)
            for c in cc:
                a,b,d,e=c['box']
                if (a+x0)/2<edge or e-b<12 or d-a>(e-b)*.3:continue
                rb=[(a+x0)/2,(b+y0)/2,(d+x0)/2,(e+y0)/2]
                value,audit=observe(page,rb,configuration,budget,pattern=r'[()]')
                if value is None and budget['remaining']>=2:
                    gray,_=_pixel_crop(page,rb)
                    revised,extra=consensus(gray,configuration,budget,scalar=True)
                    audit['recheck_views']=extra['views']
                    if revised in ('(',')'):
                        value=revised;audit['status']='delimiter_pixel_rechecked_candidate'
                if value:
                    parts.append({'role':'observed_delimiter','raw_bbox':rb,'latex':value,'original_text':value,'ocr_audit':audit})
                    own[b:e,a:d]=False
            # A detached trailing ink point is punctuation, not a missing term.
            # Retain its geometry rather than ask OCR to magnify it into '*'.
            for c in cc:
                a,b,d,e=c['box']
                if ((a+x0)/2>edge and 1<=d-a<=3 and 1<=e-b<=3
                        and (b+y0)/2>(box[1]+box[3])/2
                        and not own[:,d+1:].any()):
                    specials.append((c['box'],'.'))
        horizontal=[c['box'] for c in cc if c['box'][2]-c['box'][0]>=8 and c['box'][3]-c['box'][1]<=3]
        for a in horizontal:
            for b in horizontal:
                if (1<=b[1]-a[3]<=6 and abs(a[0]-b[0])<=1 and abs(a[2]-b[2])<=1
                        and a[2]-a[0]>=1.8*(b[3]-a[1])):
                    specials.append((union([a,b]),'='))
        if parse_scripts:
            for b in horizontal:
                if (not any(pymupdf.Rect(p).contains(pymupdf.Rect(b)) for p,_ in specials)
                    and abs((b[1]+b[3])/2-own.shape[0]/2)<=own.shape[0]*.25):specials.append((b,'-'))
        for b,text in specials:
            rb=[(b[0]+x0)/2,(b[1]+y0)/2,(b[2]+x0)/2,(b[3]+y0)/2]
            parts.append({'role':'delimiter_or_relation','raw_bbox':rb,'latex':text,'original_text':text,
                'ocr_audit':{'status':'observed_pixel_geometry','raw_bbox':rb,'views':[],
                    'method':'observed_baseline_horizontal_stroke' if text=='-' else 'observed_trailing_ink_point' if text=='.' else 'continuous_stem_and_two_arms' if text!='=' else 'two_aligned_horizontal_strokes',
                    'needs_review':True}})
            own[b[1]:b[3],b[0]:b[2]]=False
        if parse_scripts:
            from .scan_glyphs import punctuation
            for c in components(own):
                a,b,d,e=c['box']
                if d-a>4 or e-b>8 or b<own.shape[0]*.55:continue
                rb=[(a+x0)/2,(b+y0)/2,(d+x0)/2,(e+y0)/2]
                point=punctuation(page,rb,configuration)
                if not point:continue
                # Dot geometry is accepted only as the final observed mark.
                if point['latex']=='.' and own[:,d+2:].any():continue
                parts.append(point);pb=point['raw_bbox']
                own[max(0,int(np.floor(pb[1]*2))-y0):int(np.ceil(pb[3]*2))-y0,max(0,int(np.floor(pb[0]*2))-x0):int(np.ceil(pb[2]*2))-x0]=False
        # Keep complete text runs between observed structures. Never paint a
        # missing symbol, stretch glyphs, or substitute from another equation.
        intervals=runs(own.any(0));groups=[]
        boundaries=[p['raw_bbox'] for p in parts]
        for a,b in intervals:
            if groups and a-groups[-1][1]<=20 and not any(groups[-1][1]+x0<=p[0]*2 and p[2]*2<=a+x0 for p in boundaries):groups[-1][1]=b
            else:groups.append([int(a),int(b)])
        for a,b in groups:
            ys,xs=np.nonzero(own[:,a:b]);lo=int(ys.min());hi=int(ys.max())+1
            rb=[(x0+a)/2,(y0+lo)/2,(x0+b)/2,(y0+hi)/2]
            value,audit=observe(page,rb,configuration,budget,pattern=r'[A-Za-z0-9=+\-*/(),.\[\]]{1,100}')
            if value is None and budget['remaining']>=2:
                gray,_=_pixel_crop(page,rb);revised,extra=consensus(gray,configuration,budget,scalar=True)
                audit['recheck_views']=extra['views']
                if revised and re.fullmatch('[A-Za-z0-9]',revised) and revised in [v['normalized_text'] for v in audit['views']]:
                    value=revised;audit['status']='text_pixel_rechecked_candidate'
            greek_token=False
            if parse_scripts and (value is None or re.fullmatch(r'[A-Za-z]{2,}[)\]]?',value)):
                from .scan_glyphs import greek_recheck,glyph_extent
                refined=glyph_extent(page,rb)
                checked,extra=greek_recheck(page,refined,configuration,budget)
                audit['source_greek_recheck']=extra
                if checked:
                    value=checked[1];audit['status']='source_greek_two_view_candidate';rb=refined;greek_token=True
            parts.append({'role':'observed_text_run','raw_bbox':rb,'latex':value,'original_text':value or '', 'ocr_audit':audit,
                **({'token_text':'@'} if greek_token else {})})
            own[lo:hi,a:b]=False
        parts.sort(key=lambda p:p['raw_bbox'][0])
        complete=bool(parts and all(p['latex'] is not None for p in parts) and not own.any())
        text=''.join(p.get('token_text','@' if p['role']=='nested_structure' else p['latex'] or '?') for p in parts)
        return {'raw_bbox':box,'role':role,'operands':parts,'latex':''.join(p['latex'] or '?' for p in parts) if complete else None,
            'original_text':' '.join(p['original_text'] for p in parts),'token_text':text,
            'coverage_audit':{'ink_pixels':total,'observed_operator_ink_pixels':ignored,'unassigned_ink_pixels':int(own.sum()),'all_children_resolved':complete},
            'needs_review':True}

    # Observe rule extents first; children inside either operand take priority
    # over flattened OCR. This recovers a root/subscript inside a denominator.
    bars=[]
    for y in range(mask.shape[0]):
        for a,b in runs(mask[y]):
            if not 16<=b-a<=200:continue
            if bars and abs(y-bars[-1][1])<=2 and abs(a-bars[-1][0])<=2 and abs(b-bars[-1][2])<=2:continue
            bars.append((int(a),y,int(b)))
    preferred=[(round(p['observed_rule'][0]*2),round(p['observed_rule'][1]*2),round(p['observed_rule'][2]*2))
        for p in primitives if p.get('observed_rule')]
    nearby=[b for b in bars if any(pymupdf.Rect(p['raw_bbox']).intersects(pymupdf.Rect(b[0]/2,b[1]/2-24,b[2]/2,b[1]/2+24))
        for p in primitives if p.get('method')=='raster-operators-v1')]
    ordered=list(dict.fromkeys([*preferred,*nearby,*bars]))
    for a,y,b in ordered[:160]:
        if len(result)>=8 or budget['remaining']<4:break
        if any(pymupdf.Rect(e).contains(pymupdf.Point((a+b)/4,y/2)) for e in excluded):continue
        top=max(0,y-48);bottom=min(mask.shape[0],y+49)
        bands=runs(mask[top:bottom,a:b].any(1))
        above=[(lo+top,hi+top) for lo,hi in bands if hi+top<y-1 and hi-lo>=6]
        below=[(lo+top,hi+top) for lo,hi in bands if lo+top>y+1 and hi-lo>=6]
        if not above or not below:continue
        u0,u1=above[-1];l0,l1=below[0]
        if y-u1>16 or l0-y>16:continue
        box=[a/2,u0/2,b/2,l1/2]
        envelope=pymupdf.Rect(box)+(-.5,-.5,.5,3)
        children=[p for p in primitives if envelope.contains(pymupdf.Rect(p['raw_bbox']))]
        # Existing flat fractions retain their established independent audit.
        flat=next((p for p in primitives if p.get('observed_rule') and abs(p['observed_rule'][1]-y/2)<=1),None)
        nested=[p for p in children if p is not flat]
        if not nested:
            if flat:result.append(deepcopy(flat))
            continue
        operands=[]
        for role,lo,hi in [('numerator',u0,u1),('denominator',l0,l1)]:
            rb=[a/2,lo/2,b/2,hi/2]
            own=[p for p in nested if rb[1]-.5<=p['raw_bbox'][1] and p['raw_bbox'][3]<=rb[3]+3]
            if own:rb=union([rb,*[p['raw_bbox'] for p in own]])
            node=sequence(rb,own,role)
            if not valid_sequence(node['token_text']):node['latex']=None
            operands.append(node)
        latex=r'\frac{'+operands[0]['latex']+'}{'+operands[1]['latex']+'}' if all(o['latex'] for o in operands) else None
        result.append({'raw_bbox':union([box,*[o['raw_bbox'] for o in operands]]),'latex':latex,
            'original_text':' / '.join(o['original_text'] for o in operands),'operands':operands,
            'method':VERSION,'scope':'subexpression','status':'partial_formula_needs_review' if latex else 'unsupported_geometry',
            'conversion':'nested_observed_fraction','observed_rule':[a/2,y/2,b/2],'needs_review':True})

    fractions=list(result);wholes=[]
    for fraction in fractions:
        if budget['remaining']<4:break
        b=fraction['raw_bbox'];axis=fraction['observed_rule'][1]
        # Only the connected horizontal equation neighbourhood. Keep all ink
        # in this band, so a nearby unresolved term cannot silently disappear.
        lo=max(0,round((axis-6)*2));hi=min(mask.shape[0],round((axis+6)*2))
        bands=runs(mask[lo:hi].any(0));groups=[]
        for a,z in bands:
            if groups and a-groups[-1][1]<=24:groups[-1][1]=int(z)
            else:groups.append([int(a),int(z)])
        selected=next((g for g in groups if g[0]<=b[0]*2 and g[1]>=b[2]*2),None)
        if not selected:continue
        box=[selected[0]/2,min(axis-6,b[1]),selected[1]/2,max(axis+6,b[3])]
        if pymupdf.Rect(box).get_area()>5000:continue
        # Include ink immediately outside the initial band, especially small
        # scripts. Never declare a whole equation from a vertically clipped run.
        nearby=[c['box'] for c in page_components if selected[0]<=c['box'][0] and c['box'][2]<=selected[1]
            and c['box'][1]/2<=box[3]+3 and c['box'][3]/2>=box[1]-3]
        if nearby:box=union([box,*[[v/2 for v in b] for b in nearby]])
        if any(pymupdf.Rect(box).intersects(pymupdf.Rect(e)) for e in excluded):continue
        def structure_candidate(p):
            if p.get('latex'):return True
            geometry=p.get('geometry_audit',{});small=geometry.get('script_box');base=geometry.get('base_box')
            # Tiny descenders such as an argument comma are not confirmed
            # script nodes. Their ink is still read in the full text run,
            # while the original failed script observation remains exported.
            return not (small and base and small[3]-small[1] < .5*(base[3]-base[1]))
        siblings=[p for p in primitives if p.get('scope')=='subexpression' and structure_candidate(p)
            and pymupdf.Rect(box).contains(pymupdf.Rect(p['raw_bbox']))
            and not pymupdf.Rect(fraction['raw_bbox']).intersects(pymupdf.Rect(p['raw_bbox']))]
        node=sequence(box,[fraction,*siblings],'equation')
        # A record is useful even when recovery fails: retain its failed run
        # observations and child structures rather than discard the claim.
        if '=' not in node['token_text']:continue
        def complexity(n):
            children=[complexity(c) for c in n.get('operands',[])]
            return (1+max((c[0] for c in children),default=0),1+sum(c[1] for c in children))
        depth,count=complexity(node)
        node['coverage_audit'].update({'structure_depth':depth,'structure_node_count':count})
        valid=bool(node['latex'] and depth<=8 and count<=128 and valid_sequence(node['token_text'],equation=True))
        node.update({'method':VERSION,'scope':'whole_equation' if valid else 'incomplete_equation',
            'status':'transcribed_needs_review' if valid else 'unsupported_geometry',
            'conversion':'observed_equation_composition' if valid else 'unresolved_equation_composition',
            'latex':node['latex'] if valid else None})
        if not any(pymupdf.Rect(w['raw_bbox']).intersects(pymupdf.Rect(box)) for w in wholes):wholes.append(node)
    # Only add new nested fractions; flat fraction records already exist.
    new=[f for f in fractions if f['method']==VERSION]
    used=96-budget['remaining'];budget={'remaining':128}
    from .scan_recursive import recursive_scan_structures
    from .scan_multiline import aligned_row_fences,audit_aligned_rows
    row_fences=aligned_row_fences(pixels,excluded)
    recursive,recursive_audit=recursive_scan_structures(pixels,primitives,excluded,sequence,valid_sequence,budget,row_fences)
    recursive_audit.update({'ocr_calls':128-budget['remaining'],'ocr_budget':128})
    budget={'remaining':160}
    from .scan_summands import compose_summands
    summands=compose_summands(pixels,primitives,sequence,valid_sequence,budget)
    multiline=audit_aligned_rows(row_fences,[*primitives,*new,*recursive,*summands])
    return new+wholes+recursive+summands+multiline,{'version':VERSION,'ocr_calls':used,'ocr_budget':96,
        'multiline_audit':{'groups':len(multiline),'max_groups':4,'max_rows_per_group':8,'model_calls':0,'ocr_calls':0},
        'summand_audit':{'ocr_calls':160-budget['remaining'],'ocr_budget':160,'model_calls':0},
        'recursive_audit':recursive_audit,'model_calls':0,'max_depth':8,'max_nodes':128,'needs_review':True}
