"""Keep summands attached to their observed sum and independent limit audits."""
from copy import deepcopy
import pymupdf
from .scan_math import components
from .scan_operators import union

VERSION='raster-summands-v1'


def compose_summands(pixels,primitives,read_sequence,valid_sequence,budget):
    from .raster_formulas import runs
    mask=pixels<190;cc=components(mask,limit=8000);result=[]
    for operator in [p for p in primitives if p.get('observed_operator')==r'\sum'][:4]:
        symbol=operator['operator_audit']['raw_bbox'];axis=(symbol[1]+symbol[3])/2
        left=round((symbol[2]+.5)*2);top=max(0,round((axis-8)*2));bottom=min(mask.shape[0],round((axis+8)*2))
        bands=runs(mask[top:bottom,left:].any(0));groups=[]
        for a,b in bands:
            if groups and a-groups[-1][1]<=16:groups[-1][1]=int(b)
            else:groups.append([int(a),int(b)])
        missing=not groups or groups[0][0]>24
        if missing:
            box=[left/2,top/2,min(mask.shape[1]/2,left/2+48),bottom/2]
            summand={'raw_bbox':box,'role':'summand','latex':None,'original_text':'','token_text':'?',
                'ocr_audit':{'status':'not_observed','views':[]},'needs_review':True}
        else:
            a,b=groups[0];a+=left;b+=left
            nearby=[c['box'] for c in cc if a<=c['box'][0] and c['box'][2]<=b
                and c['box'][1]<=bottom and c['box'][3]>=top]
            if not nearby:continue
            box=[a/2,min(c[1] for c in nearby)/2,b/2,max(c[3] for c in nearby)/2]
            if pymupdf.Rect(box).get_area()>4000:continue
            summand=read_sequence(box,[],'summand',parse_scripts=True)
        valid=bool(summand['latex'] and valid_sequence(summand['token_text']))
        if not valid:summand['latex']=None
        own=deepcopy(operator);own['role']='sum_operator_limits'
        latex=own['latex']+summand['latex'] if own['latex'] and summand['latex'] else None
        result.append({'raw_bbox':union([operator['raw_bbox'],box]),'latex':latex,
            'original_text':operator['original_text']+' '+summand['original_text'],
            'operands':[own,summand],'method':VERSION,'scope':'summation_expression',
            'structure_kind':'summation','needs_review':True,
            'status':'partial_formula_needs_review' if latex else 'unsupported_geometry',
            'conversion':'observed_sum_with_summand' if latex else 'summand_not_observed' if missing else 'unresolved_sum_or_summand',
            'summand_audit':{'operator_limits_resolved':bool(own['latex']),'summand_resolved':valid,
                'model_calls':0}})
    return result
