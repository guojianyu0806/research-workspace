"""Compose complete sum equations only from audited source regions."""
from copy import deepcopy
import numpy as np
import pymupdf
from .scan_math import components
from .scan_operators import union

VERSION='raster-sum-equations-v1'


def compose_sum_equations(pixels,sums,read_sequence,valid_sequence,excluded=()):
    from .raster_formulas import runs
    mask=pixels<190;cc=components(mask,limit=8000);bars=[c['box'] for c in cc
        if 8<=c['box'][2]-c['box'][0]<=32 and c['box'][3]-c['box'][1]<=3]
    signs=[]
    for a in bars:
        for b in bars:
            if 1<=b[1]-a[3]<=6 and abs(a[0]-b[0])<=1 and abs(a[2]-b[2])<=1:
                signs.append([v/2 for v in union([a,b])])
    result=[]
    for value in sums[:4]:
        symbol=value['operands'][0]['operator_audit']['raw_bbox'];axis=(symbol[1]+symbol[3])/2
        candidates=[s for s in signs if 1<=symbol[0]-s[2]<=36 and abs((s[1]+s[3])/2-axis)<=4]
        if not candidates:continue
        sign=max(candidates,key=lambda b:b[2]);ey=(sign[1]+sign[3])/2
        left=max(0,round((sign[0]-96)*2));right=round((sign[0]-1)*2)
        top=max(0,round((ey-8)*2));bottom=min(mask.shape[0],round((ey+8)*2))
        clusters=[]
        for a,b in runs(mask[top:bottom,left:right].any(0)):
            if clusters and a-clusters[-1][1]<=24:clusters[-1][1]=int(b)
            else:clusters.append([int(a),int(b)])
        lhs=None
        if clusters and right-(left+clusters[-1][1])<=64:
            a,b=clusters[-1];a+=left;b+=left
            near=[c['box'] for c in cc if a<=c['box'][0] and c['box'][2]<=b
                and c['box'][1]<bottom and c['box'][3]>top]
            if near:
                box=[a/2,min(c[1] for c in near)/2,b/2,max(c[3] for c in near)/2]
                if pymupdf.Rect(box).get_area()<1600:lhs=read_sequence(box,[],'left_hand_side',parse_scripts=True)
        rhs=deepcopy(value);rhs['role']='right_hand_side';rhs['token_text']='@'
        eq={'raw_bbox':sign,'latex':'=','original_text':'=','role':'equality','token_text':'=',
            'ocr_audit':{'raw_bbox':sign,'views':[],'method':'two_aligned_horizontal_ink_strokes','status':'observed_pixel_geometry','needs_review':True},'needs_review':True}
        children=[*([lhs] if lhs else []),eq,rhs];box=union(c['raw_bbox'] for c in children)
        if pymupdf.Rect(box).get_area()>8000 or any(pymupdf.Rect(box).intersects(pymupdf.Rect(e)) for e in excluded):continue
        row=read_sequence(box,children,'aligned_row',parse_scripts=True)
        for part in row['operands']:
            original=next((c for c in children if c['raw_bbox']==part['raw_bbox']),None)
            if original:part['role']=original['role']
        # Function names, delimiters, equality, every limit and summand must
        # all be present. A bare equality is only a continuation candidate.
        has_lhs=lhs is not None
        valid=bool(row['latex'] and valid_sequence(row['token_text'],equation=True)) if has_lhs else bool(
            row['latex'] and row['token_text'].startswith('=') and valid_sequence(row['token_text'][1:]))
        row.update(method=VERSION,scope='whole_equation' if valid and has_lhs else 'equality_continuation' if valid else 'incomplete_equation',
            status='transcribed_needs_review' if valid and has_lhs else 'partial_formula_needs_review' if valid else 'unsupported_geometry',
            conversion='observed_complete_sum_equation' if valid and has_lhs else 'observed_sum_continuation' if valid else 'unresolved_sum_equation',
            latex=row['latex'] if valid else None,equality_audit={'raw_bbox':sign,'original_text':'=','method':'two_aligned_horizontal_ink_strokes'},
            equation_audit={'left_hand_side_observed':has_lhs,'all_regions_resolved':valid,'model_calls':0})
        result.append(row)
    chains=[];current=[]
    for row in sorted(result,key=lambda r:r['raw_bbox'][1]):
        if row['scope']=='whole_equation':
            if len(current)>1:chains.append(current)
            current=[row];continue
        if current and row['scope']=='equality_continuation':
            previous=current[-1];a=previous['raw_bbox'];b=row['raw_bbox']
            eq0=previous['equality_audit']['raw_bbox'];eq1=row['equality_audit']['raw_bbox']
            corridor=mask[int(np.ceil(a[3]*2)):int(np.floor(b[1]*2)),int(min(a[0],b[0])*2):int(max(a[2],b[2])*2)]
            if (len(current)<4 and abs(eq0[0]-eq1[0])<=1 and 0<=b[1]-a[3]<=16
                and 18<=(eq1[1]+eq1[3]-eq0[1]-eq0[3])/2<=50
                and not previous['latex'].endswith('.') and not corridor.any()):
                current.append(row);continue
        if len(current)>1:chains.append(current)
        current=[]
    if len(current)>1:chains.append(current)
    for chain in chains:
        latex_rows=[]
        for row in chain:
            left,right=row['latex'].split('=',1);latex_rows.append(left+'&='+right)
        result.append({'method':VERSION,'raw_bbox':union(r['raw_bbox'] for r in chain),
            'latex':r'\begin{aligned}'+r' \\ '.join(latex_rows)+r'\end{aligned}',
            'original_text':'\n'.join(r['original_text'] for r in chain),'scope':'whole_equation',
            'structure_kind':'aligned_sum_equations','status':'transcribed_needs_review','needs_review':True,
            'conversion':'complete_observed_sum_rows_with_initial_lhs',
            'operands':[dict(deepcopy(r),role='equation_row') for r in chain],
            'alignment_audit':{'row_count':len(chain),'all_rows_resolved':True,'initial_lhs_observed':True,
                'equality_alignment_tolerance_points':1,'intervening_ink_pixels':0,'model_calls':0,
                'mathematical_equivalence_verified':False}})
    return result
