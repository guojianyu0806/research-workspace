"""Observe a preceding source expression before aligned equality continuations.

The first physical line need not contain '=': the next observed equality
connects it to the continuation. Never infer a variable name or missing ink.
"""
from copy import deepcopy
import numpy as np
import pymupdf
from .scan_operators import union
from .scan_row_geometry import RowReader

VERSION='raster-initial-equations-v1'


def initial_candidates(pixels,rows,excluded=()):
    from .raster_formulas import runs
    from .scan_math import components
    if len(rows)<2:return [],{'status':'insufficient_continuations'}
    eq=[r['equality_box'] for r in rows[:2]]
    axis=(eq[0][1]+eq[0][3])/2;step=(sum(eq[1][1::2])-sum(eq[0][1::2]))/2
    if not 18<=step<=40:return [],{'status':'unsupported_row_spacing'}
    lo=max(0,int(np.floor((axis-step*1.6)*2)));hi=min(pixels.shape[0],int(np.floor((rows[0]['raw_bbox'][1]-1)*2)))
    if hi<=lo:return [],{'status':'empty_preceding_band'}
    mask=pixels<190;bands=runs(mask[lo:hi].any(0));clusters=[]
    for a,b in bands:
        if clusters and a-clusters[-1][1]<=24:clusters[-1][1]=int(b)
        else:clusters.append([int(a),int(b)])
    candidates=[]
    for a,b in clusters:
        if not (a/2<=eq[0][0]-8 and b/2>=eq[0][2]+4 and 40<=(b-a)/2<=400):continue
        yy,xx=np.nonzero(mask[lo:hi,a:b])
        if not len(yy) or yy.min()==0 or yy.max()==hi-lo-1:continue
        box=[a/2,(lo+int(yy.min()))/2,b/2,(lo+int(yy.max())+1)/2]
        if pymupdf.Rect(box).get_area()>8000 or any(pymupdf.Rect(box).intersects(pymupdf.Rect(e)) for e in excluded):continue
        local=mask[round(box[1]*2):round(box[3]*2),a:b]
        rules=[c for c in components(local,limit=1500) if c['box'][2]-c['box'][0]>=16 and c['box'][3]-c['box'][1]<=3]
        if not rules:continue
        left=min(box[0],rows[0]['raw_bbox'][0]);right=max(box[2],rows[0]['raw_bbox'][2])
        corridor=mask[round(box[3]*2):round(rows[0]['raw_bbox'][1]*2),round(left*2):round(right*2)]
        gap=rows[0]['raw_bbox'][1]-box[3]
        if not (0<=gap<=16 and not corridor.any()):continue
        candidates.append({'raw_bbox':box,'geometry_audit':{'search_band_box':[0,lo/2,pixels.shape[1]/2,hi/2],
            'following_equality_box':list(eq[0]),'method':'preceding_ink_cluster_with_rule_and_empty_corridor',
            'observed_row_step_points':step,'gap_to_first_continuation_points':gap,'intervening_ink_pixels':0}})
    return candidates,{'version':VERSION,'candidate_count':len(candidates),'status':'single_observed_candidate' if len(candidates)==1 else 'ambiguous_or_absent_initial',
        'max_groups':4,'max_calls_per_initial':384,'model_calls':0}


def compose_initial_equations(page,pixels,groups,configuration,excluded=()):
    calls=0;attempts=0
    for group in groups[:4]:
        source_rows=[{'raw_bbox':r['raw_bbox'],'equality_box':r['equality_audit']['raw_bbox']} for r in group['operands']]
        candidates,audit=initial_candidates(pixels,source_rows,excluded)
        group['initial_audit']=audit;group['initial_candidates']=candidates
        if len(candidates)!=1:continue
        candidate=candidates[0];reader=RowReader(page,candidate['raw_bbox'],configuration,allow_initial_equation=True)
        observed=reader.parse([0,0,reader.mask.shape[1],reader.mask.shape[0]])
        attempts+=1;calls+=reader.calls
        observed.update(role='initial_expression',method=VERSION,scope='initial_expression',
            geometry_audit=deepcopy(candidate['geometry_audit']),
            initial_row_audit={'ocr_calls':reader.calls,'ocr_budget':384,'model_calls':0,
                'all_regions_resolved':bool(observed['latex']),'source_dpi':288,'not_inferred_from_continuations':True})
        group['initial_expression']=observed
        confirmed=bool(observed['latex'] and not observed['latex'].startswith('='))
        audit.update(ocr_calls=reader.calls,status='initial_transcribed_needs_review' if confirmed else 'unresolved_initial_ink_or_syntax')
        group['multiline_audit'].update(initial_expression_confirmed=confirmed,source_row_count=1+len(source_rows),
            mathematical_equivalence_verified=False)
        if confirmed:
            for row in group['operands']:
                row['initial_expression_reference']={'raw_bbox':list(observed['raw_bbox']),'latex':observed['latex'],
                    'original_text':observed['original_text'],'role':'initial_expression_reference','needs_review':True,
                    'method':'same_group_observed_initial_expression'}
        if confirmed and all(r.get('latex') for r in group['operands']):
            group.update(raw_bbox=union([observed['raw_bbox'],*[r['raw_bbox'] for r in group['operands']]]),
                latex=r'\begin{aligned}&'+observed['latex']+r' \\ '+r' \\ '.join('&'+r['latex'] for r in group['operands'])+r'\end{aligned}',
                scope='whole_equation',structure_kind='aligned_derivation',status='transcribed_needs_review',
                conversion='observed_initial_expression_and_aligned_equalities',
                original_text=f"{len(source_rows)+1} source rows; initial expression and {len(source_rows)} equality continuations transcribed")
    return {'version':VERSION,'initial_rows_attempted':attempts,'max_groups':4,'ocr_calls':calls,'max_calls_per_initial':384,'model_calls':0}
