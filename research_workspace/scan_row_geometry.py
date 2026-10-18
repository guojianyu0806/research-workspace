"""Bounded row-local raster transcription; no native text or formula template.

Connected operator ink is separated from operand ink at 288 DPI. OCR sees
only the declared source component. Unknown components block a complete row.
"""
import re
from pathlib import Path
import numpy as np
import pymupdf
from .scan_math import components as source_components, _read_mask
from .scan_operators import union

VERSION='raster-row-geometry-v1'


def components(mask,limit=1500):
    return source_components(mask,limit=limit,min_pixels=1)


def join_internal_specks(cc):
    """Keep overlapping base/script boxes separate; join only enclosed specks."""
    out=[]
    for c in sorted(cc,key=lambda c:-len(c['points'])):
        parent=next((p for p in out if pymupdf.Rect(p['box']).contains(pymupdf.Rect(c['box']))
            and len(c['points'])<=max(10,len(p['points'])*.1)),None)
        if parent:parent['points'].extend(c['points'])
        else:out.append({'box':list(c['box']),'points':list(c['points'])})
    changed=True
    while changed:
        changed=False
        for i,c in enumerate(out):
            for j,q in enumerate(out[i+1:],i+1):
                a,b=c['box'],q['box'];u=union([a,b])
                if u[2]-u[0]<=8 and u[3]-u[1]<=16 and min(a[2],b[2])>max(a[0],b[0]) and 0<=max(a[1],b[1])-min(a[3],b[3])<=2:
                    c['box']=u;c['points'].extend(q['points']);out.pop(j);changed=True;break
            if changed:break
    return out


class RowReader:
    def __init__(self,page,box,configuration,max_calls=384):
        self.page=page;self.configuration=configuration;self.calls=0;self.max_calls=max_calls
        im=page.get_pixmap(dpi=288,clip=pymupdf.Rect(box),colorspace=pymupdf.csGRAY,alpha=False)
        gray=np.frombuffer(im.samples,dtype='uint8').reshape(im.height,im.stride)[:,:im.width]
        self.gray=gray;self.mask=gray<190;self.origin=(im.x,im.y);self.nodes=0
        self.cache={};self.references=[]
        for c in components(self.mask,limit=1500):
            a,b,d,e=c['box']
            if 22<=e-b<=34 and 5<=d-a<=(e-b)*.65:self.references.append(c['box'])

    def raw(self,b):
        x,y=self.origin
        return [(b[0]+x)/4,(b[1]+y)/4,(b[2]+x)/4,(b[3]+y)/4]

    def node(self,b,latex,**kw):
        return {'raw_bbox':self.raw(b),'latex':latex,'original_text':latex or '',
            'needs_review':True,**kw}

    def read(self,box,mask,language,mode,dpi,threshold):
        if self.calls>=self.max_calls:return None
        # Re-observe the same declared component at the source resolution;
        # neighbouring components are excluded with their observed pixel mask.
        a,b,d,e=box;scale=dpi/288
        ys=np.minimum((np.arange(max(1,round((e-b)*scale)))/scale).astype(int),e-b-1)
        xs=np.minimum((np.arange(max(1,round((d-a)*scale)))/scale).astype(int),d-a-1)
        own=mask[b:e,a:d]
        gray=self.gray[b:e,a:d]
        pixels=(gray<threshold)&own
        pixels=pixels[ys[:,None],xs]
        text=_read_mask(pixels,self.configuration,language=language,mode=mode);self.calls+=1
        return {'text':text,'normalized_text':re.sub(r'\s+','',text),'language':language,
            'dpi':dpi,'source_dpi':288,'resampled_from_source':dpi!=288,'threshold':threshold,'mode':mode,
            'component_mask':True,'component_membership_threshold':190}

    def glyph(self,c,mask):
        b=c['box'];a,y,d,e=b;key=tuple(b)
        if key in self.cache:return self.cache[key].copy()
        if d-a>=8 and e-y<=4:
            return self.node(b,'-',role='delimiter_or_relation',ocr_audit={'raw_bbox':self.raw(b),'views':[],
                'method':'isolated_horizontal_ink','status':'observed_pixel_geometry','needs_review':True})
        if d-a<=8 and e-y<=14:
            readings=[];geometry=[]
            for threshold in (170,210):
                local=(self.gray[y:e,a:d]<threshold)&mask[y:e,a:d]
                yy,xx=np.nonzero(local)
                if not len(xx):break
                m=local[yy.min():yy.max()+1,xx.min():xx.max()+1];h,w=m.shape
                upper=np.flatnonzero(m[:max(1,h//3)].any(0));lower=np.flatnonzero(m[-max(1,h//6):].any(0))
                text=',' if (6<=h<=14 and 2<=w<=8 and 1.6<=h/w<=4 and len(upper)>=w*.6
                    and len(lower) and len(lower)<=w*.6 and lower.mean()<upper.mean()) else None
                readings.append(text);geometry.append({'dpi':288,'threshold':threshold,'ink_height':h,'ink_width':w,
                    'head_columns':upper.tolist(),'tail_columns':lower.tolist(),'observed_punctuation':text})
            if len(readings)==2 and readings==[',',',']:
                return self.node(b,',',role='delimiter_or_relation',ocr_audit={'raw_bbox':self.raw(b),'views':[],
                    'method':'isolated_comma_head_and_left_descending_tail','geometry_views':geometry,'status':'observed_pixel_geometry','needs_review':True})
            return self.node(b,None,role='observed_glyph',ocr_audit={'raw_bbox':self.raw(b),'views':[],
                'method':'small_punctuation_geometry','status':'unresolved','needs_review':True})
        views=[];value=None
        isolated=np.zeros_like(mask)
        for py,px in c['points']:isolated[py,px]=True
        def pair(lang,mode,dpi,pattern):
            out=[]
            for threshold in (170,210):
                v=self.read(b,isolated,lang,mode,dpi,threshold)
                if v is None:return None
                views.append(v);out.append(v['normalized_text'])
            return out[0] if out[0]==out[1] and re.fullmatch(pattern,out[0]) else None
        en=pair('eng',7,288,r'[A-Za-z0-9()+,\[\].]')
        if en in ('L','I','l'):
            numeral=pair('eng',13,432,r'[0-9LIil]')
            if numeral and numeral.isdigit():en=numeral
        greek=None
        if en is None or en.isalpha():
            if (Path(self.configuration['path'])/'ell.traineddata').is_file():
                greek=pair('ell',13,432,'[α-ωϕϵ]')
                if greek is None:greek=pair('ell',7,432,'[α-ωϕϵ]')
        if greek:
            from .formula_geometry import SYMBOLS
            value=SYMBOLS.get(greek)
        elif en:value=en
        if value is None:value=pair('eng',13,432,r'[A-Za-z0-9()+,\[\].]')
        audit={'raw_bbox':self.raw(b),'views':views,'method':'isolated_source_component_two_view_ocr',
            'status':'two_view_component_candidate' if value else 'unresolved','needs_review':True}
        if value==r'\beta':
            geometry=[]
            for threshold in (170,210):
                m=(self.gray[y:e,a:d]<threshold)&isolated[y:e,a:d];h,w=m.shape
                tail=np.flatnonzero(m[int(h*.8):].any(0));body=np.flatnonzero(m[int(h*.3):int(h*.75)].any(0))
                visible=bool(h>=24 and len(tail) and len(tail)<=w*.5 and tail.max()<w*.6 and len(body)>w*.6)
                geometry.append({'dpi':288,'threshold':threshold,'descender_observed':visible,'ink_height':h,'ink_width':w,
                    'tail_columns':tail.tolist(),'body_columns':body.tolist()})
            audit['descender_views']=geometry
            if not all(g['descender_observed'] for g in geometry):value=None;audit['status']='greek_beta_without_descender_unresolved'
        # OCR's capital Z is visually case-ambiguous. Require actual short
        # x-height against independently observed same-row full-height digits.
        if value=='Z':
            anchors=[]
            for rb in self.references:
                if rb==b:continue
                if rb[3]-rb[1]<(e-y)*1.3:continue
                anchor=pair_anchor(self,rb)
                if anchor is not None:anchors.append((rb,anchor))
                if len(anchors)>=2:break
            if anchors:
                height=float(np.median([r[3]-r[1] for r,t in anchors]))
                if e-y<height*.8:
                    value='z';audit['case_geometry']={'target_height':(e-y)/4,
                        'ascender_height':height/4,'method':'own_capital_z_reading_and_short_xheight',
                        'reference_regions':[{'raw_bbox':self.raw(r),'original_text':t['text'],
                            'ocr_audit':{'raw_bbox':self.raw(r),'views':t['views'],'method':'same_row_digit_two_view_observation','needs_review':True}} for r,t in anchors]}
        n=self.node(b,value,role='observed_glyph',ocr_audit=audit,**({'token_text':'@'} if value and value.startswith('\\') else {}))
        self.cache[key]=n
        return n.copy()

    def parse(self,box,mask=None,depth=0):
        self.nodes+=1
        if mask is None:mask=self.mask.copy()
        a,b,d,e=map(int,box);own=np.zeros_like(mask);own[b:e,a:d]=mask[b:e,a:d]
        cc=components(own,limit=1500);specs=[];operator=[]
        if depth>=7 or self.nodes>128:return self.node(box,None,operands=[],role='sequence',coverage_audit={'all_children_resolved':False})
        from .raster_formulas import runs
        for c in cc:
            x,y,z,w=c['box'];h=w-y;width=z-x
            if h<20 or width<h*.8:continue
            patch=np.zeros((h,width),dtype=bool)
            for py,px in c['points']:patch[py-y,px-x]=True
            roofs=[(lo,yy,hi) for yy in range(min(5,h)) for lo,hi in runs(patch[yy])
                if lo>max(4,min(width*.15,h*.2)) and hi-lo>width*.4]
            tail=np.flatnonzero(patch[-max(3,h//10):].any(0))
            if not (roofs and len(tail) and tail.max()<width*.55 and patch[int(h*.4):int(h*.75),:max(3,int(width*.15))].any()):continue
            lo,yy,hi=max(roofs,key=lambda r:r[2]-r[0])
            if any(px>x+lo+6 and py>y+yy+6 for py,px in c['points']):continue
            inner=[x+lo,y+yy+3,x+hi,min(e,w+8)]
            rem=own.copy()
            for py,px in c['points']:rem[py,px]=False
            iy,ix=np.nonzero(rem[inner[1]:inner[3],inner[0]:inner[2]])
            if len(iy):inner[3]=inner[1]+int(iy.max())+1
            region=union([c['box'],inner]);specs.append({'kind':'radical','box':region,'inner':[inner],'ink':c['points'],'operator_box':c['box']})
            operator.append(c)
        for c in cc:
            if c in operator:continue
            x,y,z,w=c['box'];h=w-y;width=z-x
            if h>4 or width<48:continue
            # Fractions must have independent operand ink on both sides.
            above=[q for q in cc if q is not c and x<=q['box'][0] and q['box'][2]<=z and q['box'][3]<=y-2]
            below=[q for q in cc if q is not c and x<=q['box'][0] and q['box'][2]<=z and q['box'][1]>=w+2]
            if not above or not below:continue
            if y-max(q['box'][3] for q in above)>32 or min(q['box'][1] for q in below)-w>32:continue
            upper=[x,min(q['box'][1] for q in above),z,y-1]
            lower=[x,w+1,z,max(q['box'][3] for q in below)]
            specs.append({'kind':'fraction','box':union([upper,lower]),'inner':[upper,lower],
                'ink':c['points'],'operator_box':c['box']})
        maximal=[s for s in specs if not any(t is not s and pymupdf.Rect(t['box']).contains(pymupdf.Rect(s['box']))
            and pymupdf.Rect(t['box']).get_area()>pymupdf.Rect(s['box']).get_area()*1.03 for t in specs)]
        parts=[];rejected=[]
        for s in sorted(maximal,key=lambda s:-pymupdf.Rect(s['box']).get_area()):
            if any(pymupdf.Rect(s['box']).intersects(pymupdf.Rect(n['px_box'])) for n in parts):
                rejected.append({'raw_bbox':self.raw(s['box']),'reason':'crossing_structural_candidates'});continue
            childmask=own.copy()
            for py,px in s['ink']:childmask[py,px]=False
            children=[self.parse(r,childmask,depth+1) for r in s['inner']]
            assigned=np.zeros_like(own)
            for py,px in s['ink']:assigned[py,px]=True
            for x,y,z,w in s['inner']:assigned[int(y):int(w),int(x):int(z)]=True
            x,y,z,w=map(int,s['box']);unassigned=int((own[y:w,x:z]&~assigned[y:w,x:z]).sum())
            good=all(n['latex'] is not None for n in children) and unassigned==0
            latex=(r'\sqrt{'+children[0]['latex']+'}' if s['kind']=='radical' else
                r'\frac{'+children[0]['latex']+'}{'+children[1]['latex']+'}') if good else None
            n=self.node(s['box'],latex,role='nested_structure',structure_kind=s['kind'],operands=children,
                coverage_audit={'all_children_resolved':good,'unassigned_ink_pixels':unassigned},
                geometry_audit={'operator_box':self.raw(s['operator_box']),'method':'source_connected_hook_and_roof' if s['kind']=='radical' else 'source_rule_with_independent_upper_lower_ink'})
            for ch,role in zip(children,['radicand'] if s['kind']=='radical' else ['numerator','denominator']):ch['role']=role
            n['px_box']=s['box'];parts.append(n)
            x,y,z,w=map(int,s['box']);own[y:w,x:z]=False
        cc=join_internal_specks(components(own,limit=1500))
        # Pixel geometry for equality, plus and minus; no character completion.
        horizontal=[c for c in cc if c['box'][2]-c['box'][0]>=12 and c['box'][3]-c['box'][1]<=4]
        used=[]
        for c in horizontal:
            if c in used:continue
            x,y,z,w=c['box'];other=next((q for q in horizontal if q is not c and 2<=q['box'][1]-w<=12
                and abs(x-q['box'][0])<=2 and abs(z-q['box'][2])<=2),None)
            if other:
                rb=union([c['box'],other['box']]);used.extend([c,other]);value='='
            else:continue
            n=self.node(rb,value,role='delimiter_or_relation',ocr_audit={'raw_bbox':self.raw(rb),'views':[],
                'method':'two_aligned_horizontal_strokes' if value=='=' else 'isolated_horizontal_ink','status':'observed_pixel_geometry','needs_review':True})
            n['px_box']=rb;parts.append(n)
            for q in ([c,other] if other else [c]):
                for py,px in q['points']:own[py,px]=False
        cc=join_internal_specks(components(own,limit=1500))
        # A component is paired only with a smaller, lowered immediate neighbour.
        consumed=[]
        for c in sorted(cc,key=lambda c:c['box'][0]):
            if c in consumed:continue
            x,y,z,w=c['box'];h=w-y
            candidates=[q for q in cc if q not in consumed and q is not c and 0<=q['box'][0]-z<=max(6,h*.4)
                and max(14,h*.35)<=q['box'][3]-q['box'][1]<=h*1.05
                and q['box'][1]>=y+h*.23 and (q['box'][3]>w+h*.05 or q['box'][3]-q['box'][1]<h*.7)]
            q=min(candidates,key=lambda q:q['box'][0]) if candidates else None
            base=self.glyph(c,own)
            if q and base['latex'] and (re.fullmatch('[A-Za-z]',base['latex']) or base['latex'].startswith('\\')):
                script=self.glyph(q,own);sb=list(q['box']);smalls=[script];consumed.append(q)
                # Extend a script baseline only through actually observed small
                # minus and digit components (e.g. t-1).
                following=sorted([t for t in cc if t not in consumed and t is not c and t['box'][0]>=sb[2]],key=lambda t:t['box'][0])
                for t in following:
                    r=t['box']
                    if r[0]-sb[2]>6 or r[1]<sb[1]-1 or r[3]>sb[3]+1:break
                    sn=self.glyph(t,own)
                    if sn['latex'] is None:break
                    smalls.append(sn);sb=union([sb,r]);consumed.append(t)
                    if len(smalls)>=3:break
                sl=''.join(n['latex'] or '?' for n in smalls)
                for n in smalls:n['role']='subscript_part'
                base['role']='base';scriptnode=self.node(sb,sl if all(n['latex'] for n in smalls) else None,role='subscript',operands=smalls)
                rb=union([c['box'],sb]);latex=base['latex']+'_{'+sl+'}' if scriptnode['latex'] else None
                n=self.node(rb,latex,role='nested_structure',operands=[base,scriptnode],
                    geometry_audit={'method':'lowered_smaller_immediate_component','base_box':base['raw_bbox'],'script_box':self.raw(sb)})
            else:n=base;rb=c['box']
            n['px_box']=rb;parts.append(n);consumed.append(c)
        parts.sort(key=lambda n:n['px_box'][0])
        accounted=np.zeros_like(own)
        for c in consumed:
            for py,px in c['points']:accounted[py,px]=True
        unassigned=int((own&~accounted).sum())
        good=bool(parts) and all(n['latex'] is not None for n in parts) and not rejected and unassigned==0
        text=''.join(n.get('token_text','@' if n['role']=='nested_structure' else n['latex'] or '?') for n in parts)
        from .scan_composition import valid_sequence
        grammar=valid_sequence(text[1:].removesuffix(',')) if depth==0 and text.startswith('=') else valid_sequence(text)
        good=good and grammar
        latex=''.join(n['latex'] or '?' for n in parts) if good else None
        for n in parts:n.pop('px_box',None)
        return self.node(box,latex,role='sequence',operands=parts,token_text=text,
            coverage_audit={'ink_pixels':int(mask[b:e,a:d].sum()),'all_children_resolved':good,'unassigned_ink_pixels':unassigned,
                'component_count':len(parts),'grammar_valid':grammar},rejected_regions=rejected)


def pair_anchor(reader,box):
    key=('anchor',*box)
    if key in reader.cache:return reader.cache[key]
    views=[reader.read(box,reader.mask,'eng',7,288,t) for t in (170,210)]
    text=views[0]['normalized_text'] if all(views) and views[0]['normalized_text']==views[1]['normalized_text'] and re.fullmatch('[0-9]',views[0]['normalized_text']) else None
    reader.cache[key]={'text':text,'views':views} if text else None
    return reader.cache[key]


def refine_aligned_rows(page,values,configuration,max_rows=8):
    """Re-observe each row independently, preserving the previous audit tree."""
    from copy import deepcopy
    calls=0;attempts=0
    for group in values:
        for row in group['operands']:
            if attempts>=max_rows:break
            attempts+=1
            reader=RowReader(page,row['raw_bbox'],configuration)
            observed=reader.parse([0,0,reader.mask.shape[1],reader.mask.shape[0]])
            if not observed['token_text'].startswith('='):observed['latex']=None
            calls+=reader.calls
            prior={'raw_bbox':list(row['raw_bbox']),'latex':row.get('latex'),'original_text':row['original_text'],
                'operands':deepcopy(row['operands']),'coverage_audit':deepcopy(row['coverage_audit']),
                'role':'prior_row_observation','needs_review':True}
            # Keep the complete finer observation even on failure. The old
            # partial tree cannot supply missing characters to this new pass.
            row['prior_observations']=[prior]
            row.update({k:observed[k] for k in ('raw_bbox','latex','original_text','operands','token_text','coverage_audit')})
            row['row_audit']={'full_row_resolved':bool(observed['latex']),'observed_equality_required':True,
                'reason':None if observed['latex'] else 'unresolved_row_ink_or_structure',
                'method':VERSION,'source_dpi':288,'ocr_calls':reader.calls,'ocr_budget':reader.max_calls,
                'model_calls':0,'max_nodes':128,'max_depth':7,'prior_observation_preserved':True}
            row['source_row_number']=attempts
        complete=all(r.get('latex') for r in group['operands'])
        group['multiline_audit'].update(full_rows_resolved=complete,mathematical_equivalence_verified=False,
            initial_expression_confirmed=False,row_refinement_version=VERSION)
        if complete:
            group.update(latex=r'\begin{aligned}'+r' \\ '.join('&'+r['latex'] for r in group['operands'])+r'\end{aligned}',
                scope='transcribed_aligned_continuations',status='transcribed_needs_review',conversion='observed_rows_without_initial_lhs',
                original_text=f"{len(group['operands'])} aligned equality continuations; rows transcribed; initial expression unconfirmed")
    return {'version':VERSION,'rows_attempted':attempts,'max_rows':max_rows,'ocr_calls':calls,
        'max_calls_per_row':384,'model_calls':0}
