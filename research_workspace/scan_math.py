"""Bounded raster observations of separate math bases and small scripts.

This is local OCR, not a semantic completion model. Unsupported glyphs and
ambiguous geometries remain unresolved, with their source crops retained.
"""
import re
import unicodedata
from collections import Counter
import pymupdf
from .local_ocr import words


def components(mask, *, limit=800):
    import numpy as np
    seen=np.zeros(mask.shape,dtype=bool);result=[]
    for y,x in zip(*np.nonzero(mask)):
        if seen[y,x]:continue
        stack=[(int(y),int(x))];seen[y,x]=True;points=[]
        while stack:
            a,b=stack.pop();points.append((a,b))
            for j,k in ((a-1,b),(a+1,b),(a,b-1),(a,b+1)):
                if 0<=j<mask.shape[0] and 0<=k<mask.shape[1] and mask[j,k] and not seen[j,k]:
                    seen[j,k]=True;stack.append((j,k))
        if len(points)>=3:
            ys,xs=zip(*points)
            result.append({'box':[min(xs),min(ys),max(xs)+1,max(ys)+1],'points':points})
        if len(result)>limit:raise ValueError('Raster math component budget exceeded')
    return result


def _read_mask(mask,configuration,language='eng',mode=7):
    import numpy as np
    ys,xs=np.nonzero(mask)
    if not len(xs):return ''
    crop=mask[min(ys):max(ys)+1,min(xs):max(xs)+1]
    # Equal scaling only; positions used as evidence stay in original pixels.
    crop=np.repeat(np.repeat(crop,2,axis=0),2,axis=1)
    gray=np.pad(np.where(crop,0,255).astype('uint8'),32,constant_values=255)
    rgb=np.repeat(gray[:,:,None],3,axis=2)
    image=pymupdf.Pixmap(pymupdf.csRGB,rgb.shape[1],rgb.shape[0],rgb.tobytes(),False)
    image.set_dpi(576,576)
    return ' '.join(w['text'] for w in words(image,{**configuration,'languages':language},mode=mode))


def repeated_script(mask,reading,configuration,budget):
    """Verify repeated glyphs optically and by two isolated OCR observations.

    A word-level reading such as two different lowercase letters is changed
    only if equally tall, similar shapes are each read as the same character.
    """
    import numpy as np
    if len(reading)!=2 or not reading.islower() or reading[0]==reading[1]:return reading,None
    ys,xs=np.nonzero(mask)
    if not len(xs):return reading,None
    crop=mask[min(ys):max(ys)+1,min(xs):max(xs)+1];width=crop.shape[1]
    choices=[]
    for split in range(width//3,width*2//3+1):
        parts=[crop[:,:split],crop[:,split:]];normal=[];heights=[];ratios=[]
        for part in parts:
            yy,xx=np.nonzero(part)
            if not len(xx):break
            patch=part[min(yy):max(yy)+1,min(xx):max(xx)+1];h,w=patch.shape
            if h<3 or w<2:break
            normal.append(patch[np.linspace(0,h-1,32).astype(int)[:,None],np.linspace(0,w-1,32).astype(int)])
            heights.append(h);ratios.append(w/h)
        if len(normal)!=2 or abs(heights[0]-heights[1])>1:continue
        similarity=float(np.logical_and(*normal).sum()/np.logical_or(*normal).sum())*min(ratios)/max(ratios)
        if similarity>=.6:choices.append((similarity,split,parts))
    for similarity,split,parts in sorted(choices,key=lambda c:c[0],reverse=True):
        if budget['remaining']<2:break
        observations=[_read_mask(part,configuration) for part in parts];budget['remaining']-=2
        letters=[c.casefold() for c in observations]
        if letters[0]==letters[1] and re.fullmatch(r'[a-z]',letters[0]):
            return letters[0]*2,{'original_text':reading,'observations':observations,'split_pixel':split,
                'shape_similarity':round(similarity,4),'equal_ink_height':True,'method':'repeated_shape_and_isolated_ocr'}
    return reading,None


def header_scripts(page,rectangle,configuration,budget):
    """Recover script candidates in a header cell; never knows paper identity."""
    import numpy as np
    from .raster_tables import _runs
    if budget['remaining']<=0 or rectangle.get_area()*16>250_000:return []
    image=page.get_pixmap(dpi=288,clip=rectangle,colorspace=pymupdf.csGRAY,alpha=False)
    pixels=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.stride)[:,:image.width]
    mask=pixels<170
    cc=components(mask)
    if not cc:return []
    maximum=max(c['box'][3]-c['box'][1] for c in cc)
    tall=[c for c in cc if c['box'][3]-c['box'][1]>=maximum*.8]
    scores=Counter()
    for c in tall:scores[round(c['box'][3]/3)*3]+=len(c['points'])
    baseline=max(scores,key=scores.get)
    groups=[]
    for x0,x1 in _runs(mask.any(0),1):
        if groups and x0-groups[-1][1]<max(4,maximum*.3):groups[-1][1]=x1
        else:groups.append([x0,x1])
    result=[]
    for x0,x1 in groups:
        own=[c for c in cc if x0<=c['box'][0] and c['box'][2]<=x1]
        base=[c for c in own if abs(c['box'][3]-baseline)<=3 and c['box'][3]-c['box'][1]>=maximum*.4]
        if not base:continue
        left=min(c['box'][0] for c in base);right=max(c['box'][2] for c in base)
        for c in own:
            if c in base:continue
            if c['box'][0]<=right and c['box'][2]>=left and baseline-maximum*1.3<=c['box'][1] and c['box'][3]<=baseline+3:
                base.append(c);left=min(left,c['box'][0]);right=max(right,c['box'][2])
        sub=[c for c in own if c not in base and c['box'][0]>=right-4 and c['box'][3]>=baseline+4]
        if not sub or max(c['box'][3] for c in sub)-baseline>maximum*.65:continue
        if budget['remaining']<2:break
        def selected(parts):
            out=np.zeros(mask.shape,dtype=bool)
            for c in parts:
                for y,x in c['points']:
                    for dy in (-1,0,1):
                        for dx in (-1,0,1):
                            if 0<=y+dy<mask.shape[0] and 0<=x+dx<mask.shape[1] and pixels[y+dy,x+dx]<220:out[y+dy,x+dx]=True
            return out
        # Include dots of script characters without moving them to the base.
        low=min(c['box'][0] for c in sub)
        sub=[c for c in own if c not in base and c['box'][0]>=low-2]
        base_mask=selected(base);sub_mask=selected(sub)
        if min(c['box'][0] for c in sub)>=right:
            for parts,bitmap in ((base,base_mask),(sub,sub_mask)):
                xa=min(c['box'][0] for c in parts);xb=max(c['box'][2] for c in parts)
                ya=min(c['box'][1] for c in parts);yb=max(c['box'][3] for c in parts)
                bitmap[ya:yb,xa:xb]=pixels[ya:yb,xa:xb]<220
        main=_read_mask(base_mask,configuration);small=_read_mask(sub_mask,configuration)
        budget['remaining']-=2
        small,repeat_audit=repeated_script(sub_mask,small,configuration,budget)
        greek=None
        if main in {'€','E','e','c'} and budget['remaining']>0:
            from pathlib import Path
            if (Path(configuration['path'])/'ell.traineddata').is_file():
                greek=_read_mask(base_mask,configuration,'ell');budget['remaining']-=1
                plain=''.join(c for c in unicodedata.normalize('NFD',greek) if not unicodedata.combining(c))
                if plain in {'ε','ϵ'}:main=plain
        accepted=bool(re.fullmatch(r'[A-Za-zεϵ]',main) and re.fullmatch(r'[A-Za-z0-9]{1,12}',small))
        y0=min(c['box'][1] for c in base+sub);y1=max(c['box'][3] for c in base+sub)
        raw=[(image.x+x0)/4,(image.y+y0)/4,(image.x+x1)/4,(image.y+y1)/4]
        base_latex=r'\epsilon' if main in {'ε','ϵ'} else main
        suffix=small if len(small)==1 else r'\mathrm{'+small+'}'
        result.append({'base':main,'subscript':small,'latex':base_latex+'_{'+suffix+'}' if accepted else None,
            'kind':'subscript','text':main+'_'+small,
            'raw_bbox':raw,'status':'ocr_script_candidate' if accepted else 'unresolved_script',
            'needs_review':True,'greek_observation':greek,'repeated_script_audit':repeat_audit,
            'method':'raster_components_separate_script_ocr'})
    return result


def header_scales(page,rectangle,configuration,budget):
    """Observe a header's cross-number and detached raised numeric exponent."""
    import numpy as np
    from .raster_tables import _runs
    if budget['remaining']<2 or rectangle.get_area()*16>250_000:return []
    image=page.get_pixmap(dpi=288,clip=rectangle,colorspace=pymupdf.csGRAY,alpha=False)
    pixels=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.stride)[:,:image.width]
    mask=pixels<170;groups=[];result=[]
    for x0,x1 in _runs(mask.any(0),1):
        if groups and x0-groups[-1][1]<9:groups[-1][1]=x1
        else:groups.append([x0,x1])
    for x0,x1 in groups:
        for y0,y1 in _runs(mask[:,x0:x1].any(1),1):
            cc=components(mask[y0:y1,x0:x1])
            if len(cc)<3:continue
            height=max(c['box'][3]-c['box'][1] for c in cc)
            tall=[c for c in cc if c['box'][3]-c['box'][1]>=height*.8]
            baseline=max(c['box'][3] for c in tall)
            base=[c for c in cc if c['box'][3]>=baseline-2]
            right=max(c['box'][2] for c in base)
            raised=[c for c in cc if c not in base and c['box'][3]<baseline-4 and c['box'][0]>=right-2]
            if not raised or budget['remaining']<2:continue
            def bitmap(parts):
                out=np.zeros((y1-y0,x1-x0),dtype=bool)
                xa=min(c['box'][0] for c in parts);xb=max(c['box'][2] for c in parts)
                ya=min(c['box'][1] for c in parts);yb=max(c['box'][3] for c in parts)
                out[ya:yb,xa:xb]=pixels[y0+ya:y0+yb,x0+xa:x0+xb]<220
                return out
            main=_read_mask(bitmap(base),configuration);sup=_read_mask(bitmap(raised),configuration,mode=13)
            budget['remaining']-=2
            compact=re.sub(r'\s+','',main)
            if not re.fullmatch(r'[x×]\d+',compact) or not re.fullmatch(r'\d{1,3}',sup):continue
            number=compact[1:]
            xa=min(c['box'][0] for c in base+raised);xb=max(c['box'][2] for c in base+raised)
            ya=min(c['box'][1] for c in base+raised);yb=max(c['box'][3] for c in base+raised)
            result.append({'kind':'scientific_scale','base':number,'superscript':sup,'text':'×'+number+'^'+sup,
                'latex':r'\times '+number+'^{'+sup+'}','raw_bbox':[(image.x+x0+xa)/4,(image.y+y0+ya)/4,(image.x+x0+xb)/4,(image.y+y0+yb)/4],
                'ocr_base_observation':main,'status':'ocr_scale_candidate','needs_review':True,
                'method':'raster_raised_digits_scientific_scale'})
    return result
