"""Local glyph extent checks from source pixels; no alphabet completion.

The coarse scan is sampled at 144 DPI. A connected glyph can have detached
edge pixels there. Re-observe at 576 DPI within one point of the original
region, selecting only components whose centres lie in that original region.
"""
import re
from pathlib import Path
import numpy as np
import pymupdf
from .scan_math import components, _read_mask
from .scan_operators import union


def glyph_extent(page, box, *, punctuation_tail=False):
    margin=2 if punctuation_tail else 1
    image=page.get_pixmap(dpi=576,clip=pymupdf.Rect(box)+(-margin,-margin,margin,margin),colorspace=pymupdf.csGRAY,alpha=False)
    gray=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.stride)[:,:image.width]
    rough=pymupdf.Rect(box)*8-(image.x,image.y,image.x,image.y)
    candidates=components(gray<190)
    selected=[c for c in candidates if rough.contains((pymupdf.Rect(c['box']).tl+pymupdf.Rect(c['box']).br)/2)
        or punctuation_tail and (rough&pymupdf.Rect(c['box'])).get_area()>=pymupdf.Rect(c['box']).get_area()*.25]
    if not selected:return list(box)
    b=union(c['box'] for c in selected)
    return [(b[0]+image.x)/8,(b[1]+image.y)/8,(b[2]+image.x)/8,(b[3]+image.y)/8]


def greek_recheck(page,box,configuration,budget):
    """Two fixed source views must independently read the same Greek glyph."""
    audit={'status':'unresolved','views':[],'raw_bbox':list(box),'needs_review':True,
        'method':'bounded_source_greek_recheck'}
    if budget['remaining']<2 or not (Path(configuration['path'])/'ell.traineddata').is_file():return None,audit
    image=page.get_pixmap(dpi=576,clip=pymupdf.Rect(box),colorspace=pymupdf.csGRAY,alpha=False)
    gray=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.stride)[:,:image.width]
    from .scan_operators import join_ink_parts
    ink=join_ink_parts(components(gray<190))
    if len(ink)!=1:
        audit['status']='multiple_disjoint_ink_regions';return None,audit
    from .scan_operators import observe
    for dpi in (432,576):
        if budget['remaining']<2:break
        value,observed=observe(page,box,configuration,budget,language='ell',pattern='[α-ωϕϵ]',
            profile=[(dpi,170),(dpi,210)])
        audit['views'].extend(observed['views'])
        if value:
            from .formula_geometry import SYMBOLS
            latex=SYMBOLS.get(value)
            if latex:audit['status']='source_greek_two_view_candidate';return (value,latex),audit
    return None,audit


def punctuation(page,box,configuration):
    """Recognise only a compact dot or a visible comma head and descending tail."""
    refined=glyph_extent(page,box,punctuation_tail=True);views=[];readings=[]
    for threshold in (170,210):
        image=page.get_pixmap(dpi=288,clip=pymupdf.Rect(refined),colorspace=pymupdf.csGRAY,alpha=False)
        gray=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.stride)[:,:image.width]
        ys,xs=np.nonzero(gray<threshold)
        if not len(xs):return None
        mask=(gray<threshold)[ys.min():ys.max()+1,xs.min():xs.max()+1];h,w=mask.shape
        upper=np.flatnonzero(mask[:max(1,h//3)].any(0));lower=np.flatnonzero(mask[-max(1,h//4):].any(0))
        if h<=7 and w<=7 and .65<=h/w<=1.5 and mask.mean()>.5:text='.'
        elif (6<=h<=20 and 2<=w<=9 and 1.6<=h/w<=4 and len(upper)>=w*.6
                and len(lower)<=w*.6 and len(lower) and lower.mean()<upper.mean()):text=','
        else:return None
        readings.append(text);views.append({'dpi':288,'threshold':threshold,'ink_height':h,'ink_width':w,'observed_punctuation':text})
    if readings[0]!=readings[1]:return None
    return {'role':'delimiter_or_relation','raw_bbox':refined,'latex':readings[0],'original_text':readings[0],
        'ocr_audit':{'raw_bbox':refined,'method':'two_threshold_punctuation_ink_geometry','status':'observed_pixel_geometry','views':[],
            'geometry_views':views,'needs_review':True}}


def corroborate_base(page,node,references):
    """Require a local OCR candidate plus a close same-page, independently read shape.

    Reference corrections cannot become new references. Empty source ink and
    alphabet candidates absent from the target's own OCR cannot be completed.
    """
    base=node['operands'][0];audit=base['ocr_audit'];value=base['latex']
    views=[v for key in ('views','recheck_views','additional_recheck_views') for v in audit.get(key,[])]
    local=[re.sub(r'\s+','',v.get('text','')) for v in views]
    def patch(box):
        im=page.get_pixmap(dpi=288,clip=pymupdf.Rect(box)+(-.25,-.25,.25,.25),colorspace=pymupdf.csGRAY,alpha=False)
        gray=np.frombuffer(im.samples,dtype='uint8').reshape(im.height,im.stride)[:,:im.width];yy,xx=np.nonzero(gray<190)
        if not len(xx):return None
        tight=gray[yy.min():yy.max()+1,xx.min():xx.max()+1];h,w=tight.shape
        if h<8 or w<6:return None
        scaled=(255-tight[np.linspace(0,h-1,32).astype(int)[:,None],np.linspace(0,w-1,32).astype(int)]).astype(float)
        return scaled,w/h
    if value and re.fullmatch('[A-Za-z]',value) and local.count(value)>=2:
        p=patch(base['raw_bbox'])
        if p is not None and len(references)<16:references.append({'latex':value,'raw_bbox':list(base['raw_bbox']),'patch':p})
        return
    if value is not None:return
    p=patch(base['raw_bbox'])
    if p is None:return
    a,ratio=p;matches=[]
    for ref in references:
        if ref['latex'] not in local:continue
        b,other_ratio=ref['patch']
        if min(ratio,other_ratio)/max(ratio,other_ratio)<.9:continue
        cosine=float((a*b).sum()/np.sqrt((a*a).sum()*(b*b).sum()))
        ious=[float(((a>t)&(b>t)).sum()/((a>t)|(b>t)).sum()) for t in (45,65)]
        if cosine>=.9 and min(ious)>=.66:matches.append((ref,cosine,ious))
    if not matches or len({r['latex'] for r,_,_ in matches})!=1:return
    ref,cosine,ious=max(matches,key=lambda p:p[1]);base['latex']=ref['latex'];base['original_text']=ref['latex']
    audit['status']='local_ocr_and_same_page_shape_candidate'
    audit['glyph_reference']={'raw_bbox':ref['raw_bbox'],'original_text':ref['latex'],'method':'same_page_independently_read_glyph',
        'cosine_similarity':cosine,'threshold_ious':ious,'minimum_cosine':.9,'minimum_iou':.66,'own_ocr_candidate_required':True}
    side=node['geometry_audit']['relation'];small=node['operands'][1]['latex']
    if small:
        node['latex']=base['latex']+('_{' if side=='sub' else '^{')+small+'}'
        node['original_text']=base['latex']+' '+node['operands'][1]['original_text'];node['conversion']='observed_'+side+'script_pair'
        node['status']='partial_formula_needs_review'


def small_greek_scripts(page,own,x0,y0,configuration,budget):
    """Small Greek x-heights cannot use Latin font-height script thresholds."""
    from .scan_operators import join_ink_parts,observe
    if own.shape[0]>18:return []
    cc=components(own)
    if len(cc)>32:return []
    for _ in range(4):
        before=len(cc);cc=join_ink_parts(cc)
        if len(cc)==before:break
    found=[];used=[]
    raw=lambda b:[(b[0]+x0)/2,(b[1]+y0)/2,(b[2]+x0)/2,(b[3]+y0)/2]
    for c in sorted(cc,key=lambda c:c['box'][0]):
        b=c['box'];h=b[3]-b[1]
        if not 6<=h<=10 or b[2]-b[0]<h*.5 or any(pymupdf.Rect(b).intersects(pymupdf.Rect(u)) for u in used):continue
        candidates=[p for p in cc if p is not c and 1<=p['box'][0]-b[2]<=h*.8
            and h*.45<=p['box'][3]-p['box'][1]<=h
            and p['box'][1]>b[1]+h*.15 and p['box'][3]>b[3]+h*.08]
        if not candidates or budget['remaining']<4:continue
        script=min(candidates,key=lambda p:p['box'][0])['box'];extent=glyph_extent(page,raw(b))
        greek,ga=greek_recheck(page,extent,configuration,budget)
        if not greek:continue
        # A script's following operators and digits must share its observed
        # baseline and remain separated from a full-size neighbouring glyph.
        chain=[script]
        for p in sorted(cc,key=lambda c:c['box'][0]):
            q=p['box']
            if q[0]<script[2] or len(chain)>=4:continue
            if q[0]-chain[-1][2]>4:break
            if q[1]>=script[1] and q[3]<=script[3]+1:chain.append(q)
            else:break
        sb=glyph_extent(page,raw(union(chain)))
        text,sa=observe(page,sb,configuration,budget,pattern=r'[A-Za-z0-9](?:[+\-][A-Za-z0-9]){0,2}',profile=[(288,170),(288,210)])
        latex=greek[1]+'_{'+text+'}' if text else None
        node={'role':'nested_structure','raw_bbox':union([extent,sb]),'latex':latex,'original_text':greek[0]+' '+(text or '?'),
            'token_text':'@','needs_review':True,'method':'source-small-greek-scripts-v1','scope':'subexpression',
            'status':'partial_formula_needs_review' if latex else 'unsupported_geometry',
            'conversion':'observed_small_greek_script' if latex else 'unresolved_small_greek_script',
            'geometry_audit':{'base_box':extent,'script_box':sb,'relation':'sub','method':'small_greek_ink_baseline_and_observed_script_run'},
            'operands':[{'role':'base','raw_bbox':extent,'latex':greek[1],'original_text':greek[0],'ocr_audit':ga},
                {'role':'subscript','raw_bbox':sb,'latex':text,'original_text':text or '', 'ocr_audit':sa}]}
        found.append(node);used.append(union([b,*chain]))
        if len(found)>=4:break
    return found
