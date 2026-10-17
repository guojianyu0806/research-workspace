"""Bounded pixel-only observations of scanned square matrices and equations.

No native glyphs, saved text or reference answers enter this module. Different
OCR views must agree; observations remain candidates, never verified truths.
"""
import re
from statistics import median
import pymupdf
from .scan_math import components, _read_mask
from .local_ocr import words

VERSION='raster-math-v1'


def runs(values):
    import numpy as np
    delta=np.diff(np.pad(np.asarray(values,dtype='int8'),1))
    return list(zip(np.flatnonzero(delta==1),np.flatnonzero(delta==-1)))


def bracket_regions(mask,scale):
    """Observe continuous vertical stems with both square-bracket arms."""
    strokes=[]
    for x in range(mask.shape[1]):
        for y0,y1 in runs(mask[:,x]):
            if 14*scale<=y1-y0<=140*scale:
                if strokes and x-strokes[-1][0]<=2 and abs(y0-strokes[-1][1])<=2 and abs(y1-strokes[-1][2])<=2:continue
                strokes.append((x,int(y0),int(y1)))
    sides=[]
    for x,y0,y1 in strokes:
        reach=max(4,int((y1-y0)*.15)); band=min(3,max(1,int(scale)))
        def arm(direction):
            start=max(0,x-reach) if direction<0 else x
            end=x+1 if direction<0 else min(mask.shape[1],x+reach+1)
            top=mask[y0:y0+band,start:end].any(0);bottom=mask[y1-band:y1,start:end].any(0)
            return int((top & bottom).sum())>=max(3,scale*2)
        a,b=arm(-1),arm(1)
        if a==b:continue
        sides.append({'x':x,'y0':y0,'y1':y1,'left':b})
    pairs=[]
    for left in sorted((s for s in sides if s['left']),key=lambda s:-s['x']):
        rights=[s for s in sides if not s['left'] and s['x']>left['x']+scale*3
            and abs(s['y0']-left['y0'])<=2 and abs(s['y1']-left['y1'])<=2
            and not any(s is p[1] for p in pairs)]
        if rights:pairs.append((left,min(rights,key=lambda s:s['x'])))
    return pairs


def consensus(mask,configuration,budget,*,scalar=False):
    if budget['remaining']<2:return None,{'status':'not_processed_budget','views':[]}
    # Two raster thresholds/segmentation views of the same immutable crop.
    views=[]
    profile=[(190,7),(210,7)] if scalar else [(210,7),(210,13)]
    for threshold,mode in profile:
        value=_read_mask(mask<threshold,configuration,mode=mode);budget['remaining']-=1
        views.append({'text':value,'threshold':threshold,'mode':mode})
    text=views[0]['text'] if views[0]['text']==views[1]['text'] else None
    return text,{'status':'two_view_candidate' if text else 'unresolved','views':views,'needs_review':True}


def _pixel_crop(page,box,dpi=288):
    import numpy as np
    image=page.get_pixmap(dpi=dpi,clip=pymupdf.Rect(box),colorspace=pymupdf.csGRAY,alpha=False)
    pixels=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.stride)[:,:image.width]
    return pixels,image


def scan_matrices(page,pixels,scale,configuration,budget,excluded):
    results=[]
    for left,right in bracket_regions(pixels<170,scale)[:12]:
        box=[left['x']/scale-1,left['y0']/scale-1,right['x']/scale+2,left['y1']/scale+1]
        if any((pymupdf.Rect(box)&pymupdf.Rect(b)).get_area()>.5*pymupdf.Rect(box).get_area() for b in excluded):continue
        crop=[left['x']/scale+2,left['y0']/scale+1,right['x']/scale-1,left['y1']/scale-1]
        if pymupdf.Rect(crop).get_area()*16>200_000:continue
        gray,image=_pixel_crop(page,crop); cc=components(gray<170)
        cells=[];error=None;rows=[];columns=[]
        try:
            if not cc:raise ValueError('empty_matrix')
            height=median(c['box'][3]-c['box'][1] for c in cc)
            for c in sorted(cc,key=lambda c:(c['box'][1]+c['box'][3])/2):
                centre=(c['box'][1]+c['box'][3])/2
                if rows and abs(centre-median((n['box'][1]+n['box'][3])/2 for n in rows[-1]))<height*.55:rows[-1].append(c)
                else:rows.append([c])
            if not 2<=len(rows)<=12:raise ValueError('matrix_row_geometry_ambiguous')
            for row in rows:
                parts=[]
                for c in sorted(row,key=lambda c:c['box'][0]):
                    if parts and c['box'][0]-max(n['box'][2] for n in parts[-1])<height*.5:parts[-1].append(c)
                    else:parts.append([c])
                columns.append(parts)
            if len({len(row) for row in columns})!=1 or not 1<=len(columns[0])<=12:raise ValueError('ragged_matrix')
            for c in range(len(columns[0])):
                centres=[(min(n['box'][0] for n in row[c])+max(n['box'][2] for n in row[c]))/2 for row in columns]
                if max(centres)-min(centres)>height*.6:raise ValueError('matrix_columns_not_aligned')
            for r,row in enumerate(columns):
                for c,parts in enumerate(row):
                    x0=min(n['box'][0] for n in parts);x1=max(n['box'][2] for n in parts)
                    y0=min(n['box'][1] for n in parts);y1=max(n['box'][3] for n in parts)
                    value,audit=consensus(gray[max(0,y0-1):y1+1,max(0,x0-1):x1+1],configuration,budget)
                    valid=bool(value and re.fullmatch(r'[+-]?\d+(?:\.\d+)?|[A-Za-z]',value))
                    cells.append({'row':r,'column':c,'text':value,'latex':value if valid else None,
                        'raw_bbox':[(image.x+x0)/4,(image.y+y0)/4,(image.x+x1)/4,(image.y+y1)/4],
                        'ocr_audit':audit,'needs_review':True})
            if any(c['latex'] is None for c in cells):raise ValueError('unresolved_matrix_entry')
            latex=r'\begin{bmatrix}'+r' \\ '.join(' & '.join(c['latex'] for c in cells if c['row']==r) for r in range(len(rows)))+r'\end{bmatrix}'
        except ValueError as e:error=str(e);latex=None
        results.append({'raw_bbox':box,'cells':cells,'row_count':len(rows),'column_count':len(columns[0]) if columns else 0,
            'latex':latex,'original_text':'\n'.join(' '.join(c['text'] or '?' for c in cells if c['row']==r) for r in range(len(rows))),
            'method':VERSION,'needs_review':True,'status':'transcribed_needs_review' if latex else 'unsupported_geometry',
            'conversion':error or 'rectangular_pixel_entries_two_view_ocr','bracket_audit':[{'raw_bbox':box,'method':'observed_square_arms_and_vertical_stems'}]})
    return results


def scan_linear(page,image,configuration,budget,excluded):
    # MuPDF's OCR writer needs RGB; a gray pixmap may silently yield no text.
    image=pymupdf.Pixmap(pymupdf.csRGB,image)
    image.set_dpi(144,144)
    initial=words(image,{**configuration,'languages':'eng'},mode=11,offset=(image.x/2,image.y/2))
    lines=[]
    for word in sorted(initial,key=lambda w:(round(w['bbox'][1]/4),w['bbox'][0])):
        if lines and abs(word['bbox'][1]-median(w['bbox'][1] for w in lines[-1]))<3:lines[-1].append(word)
        else:lines.append([word])
    result=[]
    for line in lines:
        raw=' '.join(w['text'] for w in line)
        if '=' not in raw or len(raw)>120:continue
        box=[min(w['bbox'][0] for w in line)-2,min(w['bbox'][1] for w in line)-2,
             max(w['bbox'][2] for w in line)+2,max(w['bbox'][3] for w in line)+2]
        box=list(pymupdf.Rect(box)&page.rect)
        if not box or any((pymupdf.Rect(box)&pymupdf.Rect(b)).get_area()>.2*pymupdf.Rect(box).get_area() for b in excluded):continue
        if pymupdf.Rect(box).get_area()*16>200_000:continue
        gray,_=_pixel_crop(page,box);value,audit=consensus(gray,configuration,budget,scalar=True)
        compact=re.sub(r'\s+','',value or '')
        # Only explicit scalar assignments; scripts, fractions, functions and
        # missing operators cannot be inferred from an OCR text string.
        accepted=bool(re.fullmatch(r'[A-Za-z]+(?:_[A-Za-z]+)*=[+-]?\d+(?:\.\d+)?\.?',compact))
        equals=[w for w in line if w['text']=='=']
        if accepted and len(equals)==1:
            limit=int((equals[0]['bbox'][0]-box[0])*4)
            label=[c['box'] for c in components(gray[:,:max(0,limit)]<210) if c['box'][3]-c['box'][1]>3]
            if label:
                maximum=max(b[3]-b[1] for b in label); top=min(b[1] for b in label)
                base=median(b[3] for b in label if b[3]-b[1]>=maximum*.85)
                if any(maximum*.3<b[3]-b[1]<maximum*.85 and b[1]>top+maximum*.3 and b[3]>base+maximum*.15 for b in label):
                    accepted=False;audit['geometry_rejection']='displaced_small_label_glyphs'
        latex=compact.replace('_',r'\_') if accepted else None
        result.append({'raw_bbox':box,'latex':latex,'original_text':raw,'ocr_audit':audit,
            'method':VERSION,'status':'transcribed_needs_review' if accepted else 'unsupported_geometry',
            'conversion':'explicit_scalar_assignment_two_view_ocr' if accepted else 'ambiguous_or_unsupported_scan_notation','needs_review':True})
        if len(result)>=16:break
    return result


def scan_fractions(page,pixels,configuration,budget,excluded):
    """Finite horizontal-rule fractions; explicitly return subexpressions."""
    mask=pixels<170;bars=[];results=[]
    for y in range(mask.shape[0]):
        for x0,x1 in runs(mask[y]):
            if not 16<=x1-x0<=200:continue
            if bars and abs(y-bars[-1][1])<=2 and abs(x0-bars[-1][0])<=2 and abs(x1-bars[-1][2])<=2:continue
            bars.append((int(x0),y,int(x1)))
    for x0,y,x1 in bars[:160]:
        if budget['remaining']<4:break
        if any(pymupdf.Rect(b).contains(pymupdf.Point((x0+x1)/4,y/2)) for b in excluded):continue
        top=max(0,y-40);bottom=min(mask.shape[0],y+41)
        bands=runs(mask[top:bottom,x0:x1].any(1))
        above=[(a+top,b+top) for a,b in bands if b+top<y-1 and b-a>=6]
        below=[(a+top,b+top) for a,b in bands if a+top>y+1 and b-a>=6]
        if not above or not below:continue
        a0,a1=above[-1];b0,b1=below[0]
        if y-a1>16 or b0-y>16:continue
        box=[x0/2-1,a0/2-1,x1/2+1,b1/2+1]
        values=[];observations=[]
        for lo,hi in [(a0,a1),(b0,b1)]:
            gray,image=_pixel_crop(page,[x0/2,lo/2-1,x1/2,hi/2+1])
            value,audit=consensus(gray,configuration,budget,scalar=True)
            compact=re.sub(r'\s+','',value or '').replace('−','-').replace('—','-')
            valid=bool(re.fullmatch(r'[A-Za-z0-9+\-*/().\[\]]{1,80}',compact))
            if compact in {'L','l','I','O','o'}:valid=False
            ink=[c['box'] for c in components(gray<210) if c['box'][3]-c['box'][1]>=6]
            if len(ink)>1:
                height=max(b[3]-b[1] for b in ink);base=median(b[3] for b in ink)
                if any(b[3]-b[1]<height*.75 and abs(b[3]-base)>height*.4 for b in ink):
                    valid=False;audit['geometry_rejection']='displaced_operand_glyphs'
            # Balanced explicit delimiters only; functions must be observed.
            stack=[]
            for ch in compact:
                if ch in '([':stack.append(ch)
                elif ch in ')]':
                    if not stack or stack.pop()!=('(' if ch==')' else '['):valid=False;break
            if stack:valid=False
            for word in re.findall('[A-Za-z]{2,}',compact):
                if word not in {'exp','sin','cos','log'}:valid=False
            latex=re.sub(r'\b(exp|sin|cos|log)\b',lambda m:r'\operatorname{'+m[1]+'}',compact) if valid else None
            observations.append({'original_text':value or '', 'latex':latex,'raw_bbox':[(image.x)/4,(image.y)/4,(image.x+image.width)/4,(image.y+image.height)/4],'ocr_audit':audit})
            values.append(latex)
        # A single isolated line with no stable operands is not a formula.
        if not any(v for v in values):continue
        results.append({'raw_bbox':box,'latex':r'\frac{'+values[0]+'}{'+values[1]+'}' if all(values) else None,
            'original_text':' / '.join(o['original_text'] or '?' for o in observations),
            'operands':observations,'observed_rule':[x0/2,y/2,x1/2], 'scope':'subexpression',
            'method':VERSION,'status':'partial_formula_needs_review' if all(values) else 'unsupported_geometry',
            'conversion':'observed_fraction_subexpression' if all(values) else 'unresolved_fraction_operand','needs_review':True})
        if len(results)>=8:break
    return results


def extract_raster_math(page,configuration,excluded=()):
    if not configuration.get('enabled') or not configuration.get('available'):
        return [],[],{'status':'unavailable','reason':configuration.get('reason'),'model_calls':0}
    if page.rect.get_area()*4>4_000_000:return [],[],{'status':'pixel_budget_exceeded','model_calls':0}
    pixels,image=_pixel_crop(page,page.rect,dpi=144);budget={'remaining':128}
    matrices=scan_matrices(page,pixels,2,configuration,budget,excluded)
    forbidden=[*excluded,*(m['raw_bbox'] for m in matrices)]
    formulas=scan_fractions(page,pixels,configuration,budget,forbidden)
    formulas+=scan_linear(page,image,configuration,budget,forbidden)
    return formulas,matrices,{'status':'analyzed','version':VERSION,'model_calls':0,'ocr_calls':129-budget['remaining'],
        'ocr_budget':128,'needs_review':True,'supported':['rectangular_simple_entries','explicit_scalar_assignments','observed_fraction_subexpressions'],
        'unsupported':['general_scan_formula_latex','nested_matrices','unobserved_scripts','unknown_glyphs']}
