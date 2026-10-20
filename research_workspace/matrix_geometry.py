"""Finite rectangular matrices from verified embedded bracket glyphs.

Each entry is parsed independently. Unequal row lengths, missing delimiters,
unknown entries and nested delimiters cannot become a complete matrix.
"""
from statistics import median
from .formula_geometry import _parse, Unsupported, union, pymupdf_flags
from .native_math_fonts import recover_glyph_names

VERSION = 'native-matrix-geometry-v2'


def extract_native_matrices(page):
    glyphs=[]
    for block in page.get_text('rawdict',flags=pymupdf_flags()).get('blocks',[]):
        for line in block.get('lines',[]):
            if line.get('dir',(1,0))[0]<.9:continue
            for span in line['spans']:
                for ch in span['chars']:
                    if ch['c'].strip():glyphs.append({'char':ch['c'],'box':list(ch['bbox']),
                        'origin_x':ch['origin'][0],'y':ch['origin'][1], 'size':span['size'],
                        'font':span['font'],'flags':span['flags'],'ids':{len(glyphs)}})
    if len(glyphs)>15000:return [],{'status':'glyph_budget_exceeded','model_calls':0}
    mappings=recover_glyph_names(page,glyphs)
    top=[g for g in glyphs if g['char'] in {'⎡','⎤'} and g.get('glyph_mapping')]
    sides=[]; rejected=[]
    for g in top:
        end='⎣' if g['char']=='⎡' else '⎦'
        bottoms=[b for b in glyphs if b['char']==end and b.get('glyph_mapping')
            and abs(b['origin_x']-g['origin_x'])<.5 and 0<b['y']-g['y']<g['size']*30]
        if not bottoms:rejected.append({'reason':'missing_bracket_bottom','raw_bbox':g['box']});continue
        b=min(bottoms,key=lambda x:x['y'])
        sides.append({'top':g,'bottom':b,'left':g['char']=='⎡'})
    pairs=[]
    # Nearest available right delimiter gives innermost pairs first.
    for left in sorted([s for s in sides if s['left']],key=lambda s:-s['top']['origin_x']):
        rights=[s for s in sides if not s['left'] and s['top']['origin_x']>left['top']['origin_x']
            and abs(s['top']['y']-left['top']['y'])<.5 and abs(s['bottom']['y']-left['bottom']['y'])<.5
            and not any(s is p[1] for p in pairs)]
        if rights:pairs.append((left,min(rights,key=lambda s:s['top']['origin_x'])))
        else:rejected.append({'reason':'missing_matching_right_bracket','raw_bbox':union([left['top'],left['bottom']])})
    result=[]
    for left,right in pairs[:16]:
        size=left['top']['size']; x0=left['top']['origin_x']+size*.6;x1=right['top']['origin_x']
        y0=left['top']['y']-size*.1;y1=left['bottom']['y']+size*1.7
        inner=[g for g in glyphs if x0<=(g['box'][0]+g['box'][2])/2<x1 and y0<=g['y']<=y1]
        own=[left['top'],left['bottom'],right['top'],right['bottom'],*inner]
        box=union(own); cells=[]; reason=None
        try:
            if not inner:raise Unsupported('empty_matrix')
            if any(g['char'] in '⎡⎣⎤⎦⎢⎥' for g in inner):raise Unsupported('nested_matrix_requires_review')
            rows=[]
            for g in sorted(inner,key=lambda g:(g['y'],g['box'][0])):
                if rows and abs(g['y']-median(n['y'] for n in rows[-1]))<size*.35:rows[-1].append(g)
                else:rows.append([g])
            # Scripts belong to the closest larger baseline rather than a new row.
            main_rows=[row for row in rows if max(g['size'] for g in row)>=size*.85]
            if len(main_rows)<2 or len(main_rows)>16:raise Unsupported('matrix_row_geometry_ambiguous')
            grouped=[[] for _ in main_rows]
            for g in inner:
                distance=[abs(g['y']-median(n['y'] for n in row if n['size']>=size*.85)) for row in main_rows]
                k=min(range(len(distance)),key=distance.__getitem__)
                if distance[k]>size*.6:raise Unsupported('matrix_script_row_ambiguous')
                grouped[k].append(g)
            columns=[]
            for r,row in enumerate(grouped):
                parts=[]
                for g in sorted(row,key=lambda g:g['box'][0]):
                    if parts and g['box'][0]-max(n['box'][2] for n in parts[-1])<size*.6:parts[-1].append(g)
                    else:parts.append([g])
                if not parts or len(parts)>16:raise Unsupported('matrix_column_budget')
                columns.append(parts)
            if len({len(row) for row in columns})!=1:raise Unsupported('ragged_matrix')
            for col in range(len(columns[0])):
                centers=[sum(union(row[col])[::2])/2 for row in columns]
                if max(centers)-min(centers)>size*.5:raise Unsupported('matrix_columns_not_aligned')
            for r,row in enumerate(columns):
                for c,entry in enumerate(row):
                    value=_parse(entry,[])['latex']
                    cells.append({'row':r,'column':c,'latex':value,'original_text':''.join(g.get('native_text',g['char']) for g in sorted(entry,key=lambda g:g['box'][0])),
                        'raw_bbox':union(entry),'needs_review':True,
                        'glyph_mapping_audit':[g['glyph_mapping'] for g in entry if g.get('glyph_mapping')]})
            latex=r'\begin{bmatrix}'+r' \\ '.join(' & '.join(c['latex'] for c in cells if c['row']==r) for r in range(len(columns)))+r'\end{bmatrix}'
        except Unsupported as error:latex=None;reason=str(error)
        result.append({'raw_bbox':box,'latex':latex,'row_count':len(columns) if latex else None,
            'column_count':len(columns[0]) if latex else None,'cells':cells,
            'original_text':page.get_text('text',clip=box).strip(),'method':VERSION,
            'status':'transcribed_needs_review' if latex else 'unsupported_geometry','needs_review':True,
            'conversion':reason or 'complete_rectangular_entries',
            'bracket_audit':[g['glyph_mapping'] for g in own if g.get('glyph_mapping') and g['char'] in '⎡⎣⎤⎦⎢⎥'],
            'glyph_mapping_audit':[g['glyph_mapping'] for g in own if g.get('glyph_mapping')],
            'glyph_count':len(own)})
    return result,{'version':VERSION,'status':'analyzed','model_calls':0,'matrix_count':len(result),
        'rejected_delimiters':rejected,'truncated':len(pairs)>16,'glyph_mapping_count':len(mappings)}
