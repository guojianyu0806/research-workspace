"""Conservative shared column order for saved text and layout observations."""
import math
from bisect import bisect_right

def _coverage(inside,container):
    area=max(0,inside[2]-inside[0])*max(0,inside[3]-inside[1])
    intersection=max(0,min(inside[2],container[2])-max(inside[0],container[0]))*max(0,min(inside[3],container[3])-max(inside[1],container[1]))
    return intersection/area if area else 0


def reading_order(regions,boxes,width,tables=()):
    body = [region for region in regions if len(region['text'].strip())>=12
            and not any(_coverage(boxes[region['id']],table)>.6 for table in tables)]
    left = [r for r in body if boxes[r['id']][2]<=width*.54]
    right = [r for r in body if boxes[r['id']][0]>=width*.46]
    split = None
    if len(left)>=2 and len(right)>=2:
        left_edges=sorted(boxes[r['id']][2] for r in left)
        right_edges=sorted(boxes[r['id']][0] for r in right)
        # A long author line may approach the gutter more closely than the
        # actual body columns. With >=4 witnesses per side, require support
        # from at least three quarters rather than one extreme edge.
        left_edge=left_edges[math.ceil(.75*len(left_edges))-1] if len(left_edges)>=4 else left_edges[-1]
        right_edge=right_edges[math.floor(.25*len(right_edges))] if len(right_edges)>=4 else right_edges[0]
        overlap = min(max(boxes[r['id']][3] for r in left),max(boxes[r['id']][3] for r in right))-max(
            min(boxes[r['id']][1] for r in left),min(boxes[r['id']][1] for r in right))
        if right_edge-left_edge>=width*.015 and overlap>0:
            split = (left_edge+right_edge)/2
    ordered = sorted(regions,key=lambda r:(boxes[r['id']][1],boxes[r['id']][0]))
    if split is not None:
        # Full-width lines delimit bands; within each band read the left
        # column top-to-bottom, then right. Preserve all observed line IDs.
        spanning = [r for r in ordered if boxes[r['id']][0]<split<boxes[r['id']][2]]
        result,band = [],[]
        def flush():
            # An explicit abstract plus a shared gap separates front matter.
            # A generic body paragraph gap must NOT turn column order into rows.
            breaks=[];bottom=None
            abstracts=[boxes[r['id']][1] for r in band if r['text'].strip().casefold() in {'abstract','摘要'}]
            for region in sorted(band,key=lambda r:boxes[r['id']][1]):
                box=boxes[region['id']]
                if box[2]-box[0]<width*.08 and box[3]-box[1]>4*(box[2]-box[0]):continue
                if (bottom is not None and box[1]-bottom>width*.025
                        and any(abs(box[1]-top)<width*.01 for top in abstracts)):
                    breaks.append((bottom+box[1])/2)
                bottom=max(bottom or box[3],box[3])
            result.extend(sorted(band,key=lambda r:(bisect_right(breaks,boxes[r['id']][1]),boxes[r['id']][0]>=split,boxes[r['id']][1],boxes[r['id']][0])))
            band.clear()
        spanning_ids = {r['id'] for r in spanning}
        for region in ordered:
            if region['id'] in spanning_ids:
                flush()
                result.append(region)
            else:
                band.append(region)
        flush()
        ordered = result
    return {'columns':(2 if split is not None else 1) if regions else None,'region_ids':[r['id'] for r in ordered],
            'method':'supported_gutter_and_full_width_band_v2' if split is not None else 'top_to_bottom_left_to_right_fallback',
            'status':'suggestion' if regions else 'unsupported','needs_review':True,
            'limitations':'仅建议单栏或双栏顺序；复杂混合栏、脚注、竖排及图表穿插可能需要人工调整。'}
