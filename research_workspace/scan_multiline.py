"""Spatial audit of aligned equality continuations, without equation completion."""
from copy import deepcopy
import numpy as np
import pymupdf
from .scan_math import components
from .scan_operators import union

VERSION='raster-aligned-rows-v1'


def aligned_row_fences(pixels,excluded=()):
    mask=pixels<190;cc=components(mask,limit=8000)
    bars=[c['box'] for c in cc if 8<=c['box'][2]-c['box'][0]<=32 and c['box'][3]-c['box'][1]<=3]
    signs=[]
    for a in bars:
        for b in bars:
            if (1<=b[1]-a[3]<=6 and abs(a[0]-b[0])<=1 and abs(a[2]-b[2])<=1):
                box=[v/2 for v in union([a,b])]
                centre=round((a[1]+b[3])/2)
                # A continuation starts with a bare equality. The small i=1
                # limits of separate sums must not become a derivation group.
                if mask[max(0,centre-6):centre+7,max(0,a[0]-20):a[0]-1].any():continue
                if not any(pymupdf.Rect(box).intersects(pymupdf.Rect(e)) for e in excluded):signs.append(box)
    groups=[]
    for sign in sorted(signs,key=lambda b:b[1]):
        eligible=[g for g in groups if len(g)<8 and abs(g[-1][0]-sign[0])<=1
            and 18<=(sign[1]+sign[3]-g[-1][1]-g[-1][3])/2<=40]
        if eligible:eligible[-1].append(sign)
        else:groups.append([sign])
    result=[]
    from .raster_formulas import runs
    for group in [g for g in groups if len(g)>=2][:4]:
        centres=[(s[1]+s[3])/2 for s in group];rows=[]
        edges=[centres[0]-(centres[1]-centres[0])/2,*[(a+b)/2 for a,b in zip(centres,centres[1:])],centres[-1]+(centres[-1]-centres[-2])/2]
        for i,sign in enumerate(group):
            lo=max(0,int(np.ceil(edges[i]*2)));hi=min(mask.shape[0],int(np.floor(edges[i+1]*2)))
            bands=runs(mask[lo:hi].any(0));clusters=[]
            for a,b in bands:
                if clusters and a-clusters[-1][1]<=24:clusters[-1][1]=int(b)
                else:clusters.append([int(a),int(b)])
            selected=next((b for b in clusters if b[0]<=sign[0]*2 and b[1]>=sign[2]*2),None)
            if not selected:break
            a,b=selected;yy,xx=np.nonzero(mask[lo:hi,a:b])
            box=[a/2,(lo+int(yy.min()))/2,b/2,(lo+int(yy.max())+1)/2]
            rows.append({'raw_bbox':box,'equality_box':sign,'fence_box':[a/2,lo/2,b/2,hi/2]})
        if len(rows)==len(group):result.append(rows)
    return result


def within_row_fence(box,groups):
    """A candidate touching an aligned block must fit a single vertical row."""
    for rows in groups:
        region=union(r['fence_box'] for r in rows)
        if not pymupdf.Rect(box).intersects(pymupdf.Rect(region)):continue
        if not any(box[1]>=r['fence_box'][1]-.5 and box[3]<=r['fence_box'][3]+.5 for r in rows):return False
    return True


def audit_aligned_rows(groups,observations):
    result=[]
    for rows in groups:
        source_rows=[]
        for i,row in enumerate(rows):
            box=row['raw_bbox'];inside=[];crossing=[]
            for value in observations:
                if value.get('scope') not in ('subexpression','operator_with_limits','summation_expression'):continue
                b=value['raw_bbox']
                if not pymupdf.Rect(box).intersects(pymupdf.Rect(b)):continue
                if b[1]>=row['fence_box'][1]-.5 and b[3]<=row['fence_box'][3]+.5:inside.append(value)
                else:crossing.append({'raw_bbox':b,'original_text':value.get('original_text',''),'reason':'crosses_observed_row_fence'})
            maximal=[p for p in inside if not any(q is not p and pymupdf.Rect(q['raw_bbox']).contains(pymupdf.Rect(p['raw_bbox']))
                and pymupdf.Rect(q['raw_bbox']).get_area()>pymupdf.Rect(p['raw_bbox']).get_area()*1.05 for q in inside)]
            source_rows.append({'role':'aligned_row','raw_bbox':box,'latex':None,'original_text':f'= [unresolved continuation {i+1}]',
                'operands':deepcopy(maximal[:16]),'needs_review':True,'equality_audit':{'raw_bbox':row['equality_box'],
                    'original_text':'=','method':'two_aligned_horizontal_ink_strokes'},
                'geometry_audit':{'fence_box':row['fence_box'],'method':'aligned_equal_centres_midpoint_fences'},
                'rejected_regions':crossing[:16]})
        result.append({'method':VERSION,'raw_bbox':union(r['raw_bbox'] for r in rows),'latex':None,
            'scope':'incomplete_aligned_continuations','status':'unsupported_geometry',
            'conversion':'unresolved_multiline_rows','needs_review':True,'operands':source_rows,
            'original_text':f'{len(rows)} aligned equality continuations; full derivation unresolved',
            'multiline_audit':{'row_count':len(rows),'initial_expression_confirmed':False,
                'full_rows_resolved':False,'model_calls':0,'ocr_calls':0,
                'method':'observed_alignment_only_no_semantic_equation_merge'}})
    return result


def transcribe_aligned_rows(pixels,groups,observations,read_sequence,valid_sequence,budget):
    """Account for every row's ink; missing operands cannot be skipped."""
    values=audit_aligned_rows(groups,observations)
    # Re-run bounded structural detection inside each independent row. A large
    # earlier formula must not monopolise the page's recursive candidate list.
    from .scan_recursive import recursive_scan_structures
    rows=[r for value in values for r in value['operands']]
    for row in sorted(rows,key=lambda r:pymupdf.Rect(r['raw_bbox']).get_area()):
        fence=row['geometry_audit']['fence_box'];a,b,d,e=[round(v*2) for v in fence]
        local=np.full_like(pixels,255);local[b:e,a:d]=pixels[b:e,a:d]
        hints=[*row['operands'],{'raw_bbox':row['raw_bbox']}]
        # The horizontal caps of an already resolved Sigma are not fraction
        # rules. Keep its independently checked region as an atomic child.
        known_sums=[n['raw_bbox'] for n in row['operands'] if n.get('scope')=='summation_expression' and n.get('latex')]
        extra,audit=recursive_scan_structures(local,hints,known_sums,read_sequence,valid_sequence,budget,groups,include_flat=True)
        row['row_recursive_audit']=audit;row['operands'].extend(extra)
    for value in values:
        for row in value['operands']:
            # Equivalent observations of the same region remain available in
            # the independent records, but cannot be counted twice as ink.
            children=[]
            priority=lambda n:(n.get('method')=='raster-recursive-v1',bool(n.get('latex')),
                pymupdf.Rect(n['raw_bbox']).get_area())
            for child in sorted(row['operands'],key=priority,reverse=True):
                if not any(pymupdf.Rect(child['raw_bbox']).intersects(pymupdf.Rect(c['raw_bbox'])) for c in children):children.append(child)
            observed=read_sequence(row['raw_bbox'],children,'aligned_row',parse_scripts=True)
            text=observed['token_text']
            complete=bool(observed['latex'] and text.startswith('=') and valid_sequence(text[1:]))
            row.update(operands=observed['operands'],original_text=observed['original_text'],
                latex=observed['latex'] if complete else None,token_text=text,coverage_audit=observed['coverage_audit'],
                row_audit={'full_row_resolved':complete,'observed_equality_required':True,
                    'reason':None if complete else 'unresolved_row_ink_or_structure'})
        value['multiline_audit']['full_rows_resolved']=all(r['latex'] for r in value['operands'])
        # No implicit LHS is invented even if every bare-equality row is read.
        value['multiline_audit']['transcription_attempted']=True
    return values
