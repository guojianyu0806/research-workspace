"""Conservative shared column order for saved text and layout observations."""

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
        left_edge = max(boxes[r['id']][2] for r in left)
        right_edge = min(boxes[r['id']][0] for r in right)
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
            result.extend(sorted(band,key=lambda r:(boxes[r['id']][0]>=split,boxes[r['id']][1],boxes[r['id']][0])))
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
            'method':'gutter_and_full_width_band_heuristic' if split is not None else 'top_to_bottom_left_to_right_fallback',
            'status':'suggestion' if regions else 'unsupported','needs_review':True,
            'limitations':'仅建议单栏或双栏顺序；复杂混合栏、脚注、竖排及图表穿插可能需要人工调整。'}
