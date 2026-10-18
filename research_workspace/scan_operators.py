"""Finite pixel geometry for scripts, radical extents and summation limits.

No native text, fonts or reference answers. Unreadable operands stay explicit;
geometry and each OCR view are exported with their own source rectangle.
"""
import re
from pathlib import Path
from statistics import median
import numpy as np
import pymupdf
from .scan_math import components,_read_mask

VERSION='raster-operators-v1'


def union(boxes):
    boxes=list(boxes)
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]


def observe(page,box,configuration,budget,*,language='eng',pattern=r'[A-Za-z0-9]+',symbol=False,relation=False,upper=False,profile=None):
    views=[]
    if budget['remaining']<2 or pymupdf.Rect(box).get_area()*64>200_000:
        return None,{'status':'not_processed_budget','views':[],'raw_bbox':box,'needs_review':True}
    profile=profile or ([(216,230),(216,245)] if symbol else [(432,150),(432,190)] if relation
             else [(576,150),(576,230)] if upper else [(576,190),(576,210)])
    for dpi,threshold in profile:
        pad=.25 if symbol else .5
        image=page.get_pixmap(dpi=dpi,clip=pymupdf.Rect(box)+(-pad,-pad,pad,pad),colorspace=pymupdf.csGRAY,alpha=False)
        gray=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.stride)[:,:image.width]
        value=_read_mask(gray<threshold,configuration,language=language,mode=7)
        budget['remaining']-=1;views.append({'text':value,'normalized_text':re.sub(r'\s+','',value),'dpi':dpi,'threshold':threshold,'mode':7,'language':language,'padding_points':pad})
    text=views[0]['normalized_text'] if views[0]['normalized_text']==views[1]['normalized_text'] and re.fullmatch(pattern,views[0]['normalized_text']) else None
    return text,{'status':'two_view_candidate' if text else 'unresolved','views':views,'raw_bbox':box,'needs_review':True}


def join_ink_parts(cc):
    result=[]
    for c in cc:
        matches=[p for p in result if (min(p['box'][2],c['box'][2])-max(p['box'][0],c['box'][0])>=0
            and min(p['box'][3],c['box'][3])-max(p['box'][1],c['box'][1])>0)]
        if matches:
            p=matches[0];p['box']=union([p['box'],c['box']]);p['points']+=c['points']
        else:result.append({'box':list(c['box']),'points':list(c['points'])})
    return result


def pair_regions(cc,*,min_height=10,extended_parts=False):
    pairs=[]
    for base in cc:
        b=base['box'];h=b[3]-b[1];w=b[2]-b[0]
        if not (min_height<=h<=36 and 2<=w<=h*1.6):continue
        candidates=[]
        for small in cc:
            s=small['box'];sh=s[3]-s[1]
            if small is base or not h*.35<=sh<=h*.78 or not -1<=s[0]-b[2]<=h*.6:continue
            if abs((s[1]+s[3]-b[1]-b[3])/2)>h*.9:continue
            if (s[1]<=b[1]-h*.2 if extended_parts else s[1]<b[1]-h*.2) and s[3]<b[3]-h*.45:side='sup'
            elif s[1]>b[1]+h*.2 and (s[3]>b[3]+h*.08 or extended_parts and (s[1]+s[3]-b[1]-b[3])/2>h*.15):side='sub'
            else:continue
            candidates.append((small,side))
        for small,side in candidates:
            s=list(small['box']);parts=[small]
            # Include a detached dot or serif belonging to the same script.
            for part in cc:
                q=part['box']
                if part is small or part is base:continue
                margin=max(1,h*.2) if extended_parts else 1
                if s[0]-margin<=q[0] and q[2]<=s[2]+margin and s[1]-h*.4<=q[1]<=s[3]+(h*.15 if extended_parts else 0):parts.append(part)
            pairs.append((base,union(p['box'] for p in parts),side))
    return pairs


def transcribe_pair(page,b,s,side,configuration,budget,*,extended=False):
    original_b=list(b)
    base,ba=observe(page,b,configuration,budget,pattern=r'[A-Za-z)\]]' if extended else r'[A-Za-z]')
    observed_base=base
    if extended and base is None and budget['remaining']>=2:
        from .raster_formulas import _pixel_crop,consensus
        gray,_=_pixel_crop(page,b);revised,extra=consensus(gray,configuration,budget,scalar=True)
        ba['recheck_views']=extra['views']
        if revised and re.fullmatch(r'[A-Za-z)\]]',revised) and (revised in (')',']') or revised in [v['normalized_text'] for v in ba['views']]):
            observed_base=base=revised;ba['status']='base_pixel_rechecked_candidate'
        if base is None and budget['remaining']>=2:
            from .scan_math import _read_mask
            readings=[];ba['additional_recheck_views']=[]
            for threshold in (170,190):
                text=_read_mask(gray<threshold,configuration,mode=7);budget['remaining']-=1;readings.append(text)
                ba['additional_recheck_views'].append({'text':text,'dpi':288,'threshold':threshold,'mode':7})
            if readings[0]==readings[1] and re.fullmatch(r'[A-Za-z)\]]',readings[0]) and (readings[0] in (')',']') or readings[0] in [v['normalized_text'] for v in ba['views']]):
                observed_base=base=readings[0];ba['status']='base_pixel_rechecked_candidate'
    if extended and base is None and (Path(configuration['path'])/'ell.traineddata').is_file():
        greek,ga=observe(page,b,configuration,budget,language='ell',pattern='[α-ωϕϵ]',profile=[(432,170),(432,190)])
        ba['greek_views']=ga['views']
        if greek:
            from .formula_geometry import SYMBOLS
            observed_base=greek;base=SYMBOLS.get(greek);ba['status']='local_greek_two_view_candidate'
    if extended and base is None:
        from .scan_glyphs import greek_recheck,glyph_extent
        refined=glyph_extent(page,b)
        checked,extra=greek_recheck(page,refined,configuration,budget)
        ba['source_greek_recheck']=extra
        if checked:observed_base,base=checked;b=refined;ba['status']='source_greek_two_view_candidate'
    if extended and base:
        from .scan_glyphs import glyph_extent
        b=glyph_extent(page,b)
    if extended and base=='B' and (Path(configuration['path'])/'ell.traineddata').is_file():
        from .scan_glyphs import beta_recheck
        checked,extra,ambiguous=beta_recheck(page,b,configuration,budget)
        ba['greek_beta_recheck']=extra
        if checked:base=checked;observed_base='β';ba['status']='greek_beta_and_descender_candidate'
        elif ambiguous or not extra['views']:base=None;observed_base=None;ba['status']='latin_greek_conflict'
    small,sa=observe(page,s,configuration,budget,pattern=r'[A-Za-z0-9]')
    if extended and small is None and budget['remaining']>=2:
        from .raster_formulas import _pixel_crop
        from .scan_math import _read_mask
        gray,_=_pixel_crop(page,s,dpi=432);readings=[];sa['recheck_views']=[]
        for threshold in (170,190):
            text=_read_mask(gray<threshold,configuration,mode=7);budget['remaining']-=1;readings.append(text)
            sa['recheck_views'].append({'text':text,'dpi':432,'threshold':threshold,'mode':7})
        if readings[0]==readings[1] and re.fullmatch(r'[A-Za-z0-9]',readings[0]):
            small=readings[0];sa['status']='script_pixel_rechecked_candidate'
    latex=base+('_{' if side=='sub' else '^{')+small+'}' if base and small else None
    return {'raw_bbox':union([b,s]),'latex':latex,'scope':'subexpression','method':VERSION,
        'status':'partial_formula_needs_review' if latex else 'unsupported_geometry','needs_review':True,
        'conversion':'observed_'+side+'script_pair' if latex else 'unresolved_script_glyph',
        'original_text':(observed_base or '?')+' '+(small or '?'),
        'operands':[{'role':'base','original_text':observed_base or '', 'latex':base,'raw_bbox':b,'ocr_audit':ba},
                    {'role':side+'script','original_text':small or '', 'latex':small,'raw_bbox':s,'ocr_audit':sa}],
        'geometry_audit':{'base_box':b,'coarse_base_box':original_b,'script_box':s,'relation':side,'method':'observed_relative_ink_extent'},
        **({'token_text':base+'@'} if base in (')',']') else {})}


def shape(component):
    b=component['box'];h=b[3]-b[1];w=b[2]-b[0]
    mask=np.zeros((h,w),dtype=bool)
    for y,x in component['points']:mask[y-b[1],x-b[0]]=True
    def span(rows):
        return max((len(np.flatnonzero(row)) for row in mask[rows]),default=0)
    middle=np.flatnonzero(mask[max(0,h//2-1):h//2+2].any(0))
    sigma=(20<=h<=65 and .6<=w/h<=1.4 and .08<mask.mean()<.5
        and span(slice(0,max(2,h//10)))>w*.65 and span(slice(-max(2,h//10),None))>w*.65
        and len(middle) and min(middle)>w*.15 and max(middle)<w*.8)
    # Radical: a rightward top rule plus a low left-hand tick and rising stem.
    from .raster_formulas import runs
    bars=[(x0,y,x1) for y in range(min(3,h)) for x0,x1 in runs(mask[y]) if x1-x0>w*.4 and x0>w*.2]
    bottom=np.flatnonzero(mask[-max(2,h//10):].any(0))
    radical=(14<=h<=45 and w>=h*.8 and bars and len(bottom) and max(bottom)<w*.55
             and mask[int(h*.4):int(h*.75),:max(2,int(w*.15))].any())
    return ('sum',None) if sigma else ('root',max(bars,key=lambda r:r[2]-r[0])) if radical else (None,None)


def extract_scan_operators(page,pixels,configuration,excluded=(),hint_boxes=None):
    cc=components(pixels<190,limit=8000);budget={'remaining':80};result=[];used=[]
    def raw(box):return [float(v)/2 for v in box]
    def blocked(box):
        rb=pymupdf.Rect(raw(box))
        return (hint_boxes is not None and not any(rb.intersects(pymupdf.Rect(h)+(-12,-12,12,12)) for h in hint_boxes)
            or any((rb&pymupdf.Rect(e)).get_area()>.4*rb.get_area() for e in excluded))
    candidates=[(c,*shape(c)) for c in cc if not blocked(c['box'])]
    for component,kind,bar in [c for c in candidates if c[1]][:8]:
        b=component['box'];box=raw(b)
        if kind=='sum':
            if not (Path(configuration['path'])/'ell.traineddata').is_file():continue
            operator,oa=observe(page,box,configuration,budget,language='ell',pattern='Σ',symbol=True)
            if not operator:continue
            h=b[3]-b[1];w=b[2]-b[0]
            upper=[c['box'] for c in cc if b[0]-2<=(c['box'][0]+c['box'][2])/2<=b[2]+2 and b[1]-h*.85<c['box'][1] and c['box'][3]<b[1]-1]
            lower=[c['box'] for c in cc if b[0]-2<=c['box'][0] and c['box'][2]<=b[2]+2 and b[3]+1<c['box'][1]<b[3]+h*.7]
            operands=[];values=[]
            for role,parts in [('upper_limit',upper),('lower_limit',lower)]:
                search=([b[0]-2,b[1]-h*.85,b[2]+2,b[1]-1] if role=='upper_limit'
                    else [b[0]-2,b[3]+1,b[2]+2,b[3]+h*.7])
                rb=raw(union(parts)) if parts else raw(search)
                text,audit=observe(page,rb,configuration,budget,pattern=r'[A-Za-z0-9=+\-]{1,16}',relation=role=='lower_limit',upper=role=='upper_limit') if parts else (None,{'status':'not_observed','views':[]})
                operands.append({'role':role,'original_text':text or '', 'latex':text,'raw_bbox':rb,'ocr_audit':audit});values.append(text)
            latex=r'\sum_{'+values[1]+'}^{'+values[0]+'}' if all(values) else None
            overall=union([box,*[o['raw_bbox'] for o in operands]])
            result.append({'raw_bbox':overall,'latex':latex,'observed_operator':r'\sum','operator_audit':oa,
                'operands':operands,'original_text':'Σ '+(values[0] or '?')+' '+(values[1] or '?'),
                'method':VERSION,'scope':'operator_with_limits','needs_review':True,
                'status':'partial_formula_needs_review' if latex else 'unsupported_geometry','conversion':'observed_sum_and_limits' if latex else 'unresolved_sum_limits'})
            used.append(overall)
        elif kind=='root':
            x0,y,x1=bar;child_px=[b[0]+x0,b[1]+y+2,b[0]+x1,b[3]+max(2,(b[3]-b[1])*.2)]
            inner=[c for c in cc if c is not component and child_px[0]-1<=c['box'][0] and c['box'][2]<=child_px[2]+1
                and child_px[1]<=c['box'][1] and c['box'][3]<=child_px[3]]
            inner=join_ink_parts(inner)
            pairs=pair_regions(inner);operands=[];latex=None
            if len(pairs)==1:
                base,script,side=pairs[0];p=transcribe_pair(page,raw(base['box']),raw(script),side,configuration,budget)
                operands=p['operands'];latex=r'\sqrt{'+p['latex']+'}' if p['latex'] else None
            elif len(inner)==1:
                rb=raw(inner[0]['box']);value,audit=observe(page,rb,configuration,budget,pattern=r'[A-Za-z0-9]')
                operands=[{'role':'radicand','original_text':value or '', 'latex':value,'raw_bbox':rb,'ocr_audit':audit}]
                if value:latex=r'\sqrt{'+value+'}'
            result.append({'raw_bbox':union([box,raw(child_px)]),'latex':latex,'observed_operator':r'\sqrt{}',
                'operands':operands,'original_text':'√ '+(' '.join(o['original_text'] for o in operands) or '?'),
                'scope':'subexpression','method':VERSION,'needs_review':True,'status':'partial_formula_needs_review' if latex else 'unsupported_geometry',
                'conversion':'observed_radical_extent' if latex else 'unresolved_radical_operand',
                'geometry_audit':{'method':'observed_hook_rising_stem_and_overbar','bar_box':raw([b[0]+x0,b[1]+y,b[0]+x1,b[1]+y+1])}})
            used.append(union([box,raw(child_px)]))
    script_count=0
    letters=join_ink_parts([c for c,kind,_ in candidates if not kind])
    for base,script,side in pair_regions(letters):
        box=raw(union([base['box'],script]))
        if blocked(union([base['box'],script])) or any((pymupdf.Rect(box)&pymupdf.Rect(b)).get_area()>.5*pymupdf.Rect(box).get_area() for b in used):continue
        if script_count>=12 or budget['remaining']<4:break
        value=transcribe_pair(page,raw(base['box']),raw(script),side,configuration,budget)
        result.append(value);used.append(box);script_count+=1
    return result,{'version':VERSION,'status':'analyzed','model_calls':0,'ocr_calls':80-budget['remaining'],
        'ocr_budget':80,'component_count':len(cc),'component_budget':8000,'needs_review':True}
