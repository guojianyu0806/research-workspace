"""Bounded local scan-table recovery from visible rules and Tesseract words.

Never changes the original PDF or treats OCR as verified canonical text.
"""
from __future__ import annotations
from statistics import median
import pymupdf


VERSION='raster-table-v1'
MAX_PIXELS=4_000_000
MAX_OCR_CELLS=200


def _runs(values,minimum):
    import numpy as np
    changes=np.flatnonzero(np.diff(np.r_[False,values,False].astype('int8')))
    return [(int(a),int(b)) for a,b in zip(changes[::2],changes[1::2]) if b-a>=minimum]


def _rules(mask,horizontal):
    """Cluster neighbouring long dark runs, not whole-row darkness ratios."""
    data=mask if horizontal else mask.T
    minimum=max(36,int(data.shape[1]*.045));segments=[]
    for position,row in enumerate(data):
        for start,end in _runs(row,minimum):
            nearby=next((s for s in reversed(segments[-32:]) if position-s['last']<=3
                and min(end,s['end'])-max(start,s['start'])>.8*min(end-start,s['end']-s['start'])),None)
            if nearby:
                nearby['positions'].append(position);nearby['last']=position
                nearby['start']=min(start,nearby['start']);nearby['end']=max(end,nearby['end'])
            else:segments.append({'positions':[position],'last':position,'start':start,'end':end})
        if len(segments)>1500:raise ValueError('Raster line budget exceeded')
    return [(float(median(s['positions'])),s['start'],s['end'],len(s['positions'])) for s in segments]


def _grids(horizontal,vertical):
    """Connected visible rules -> rectangular cells; absent borders can merge."""
    rules=[('h',*s[:3]) for s in horizontal]+[('v',*s[:3]) for s in vertical]
    parents=list(range(len(rules)))
    def root(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    def join(a,b):parents[root(a)]=root(b)
    for i,(kind,y,x0,x1) in enumerate(rules):
        if kind!='h':continue
        for j,(other,x,y0,y1) in enumerate(rules):
            if other=='v' and x0-3<=x<=x1+3 and y0-3<=y<=y1+3:join(i,j)
    groups={}
    for i,r in enumerate(rules):groups.setdefault(root(i),[]).append(r)
    tables=[]
    for group in groups.values():
        hs=[r for r in group if r[0]=='h'];vs=[r for r in group if r[0]=='v']
        xs=sorted({r[1] for r in vs});ys=sorted({r[1] for r in hs})
        # Scientific tables often omit the outer vertical rules. Consistent
        # horizontal endpoints provide explicit boundaries without inventing
        # column splits from numbers or OCR reading order.
        open_sides=False
        if len(hs)>=3 and vs:
            left=median(r[2] for r in hs);right=median(r[3] for r in hs)
            if sum(abs(r[2]-left)<4 and abs(r[3]-right)<4 for r in hs)>=.8*len(hs):
                if min(xs)>left+4:xs.append(float(left));open_sides=True
                if max(xs)<right-4:xs.append(float(right));open_sides=True
                xs.sort()
        if len(xs)<3 or len(ys)<3 or (len(xs)-1)*(len(ys)-1)>3000:continue
        def border(axis,pos,start,end):
            if axis=='v' and open_sides and pos in {xs[0],xs[-1]}:return True
            return any(abs(r[1]-pos)<=3 and r[2]<=start+4 and r[3]>=end-4 for r in (hs if axis=='h' else vs))
        if not (border('h',ys[0],xs[0],xs[-1]) and border('h',ys[-1],xs[0],xs[-1])
                and border('v',xs[0],ys[0],ys[-1]) and border('v',xs[-1],ys[0],ys[-1])):continue
        nr,nc=len(ys)-1,len(xs)-1;cp=list(range(nr*nc))
        def cr(i):
            while cp[i]!=i:cp[i]=cp[cp[i]];i=cp[i]
            return i
        for r in range(nr):
            for c in range(nc):
                i=r*nc+c
                if c+1<nc and not border('v',xs[c+1],ys[r],ys[r+1]):cp[cr(i)]=cr(i+1)
                if r+1<nr and not border('h',ys[r+1],xs[c],xs[c+1]):cp[cr(i)]=cr(i+nc)
        merged={}
        for i in range(nr*nc):merged.setdefault(cr(i),[]).append(divmod(i,nc))
        boxes={};valid=True
        for cells in merged.values():
            r0,r1=min(r for r,c in cells),max(r for r,c in cells);c0,c1=min(c for r,c in cells),max(c for r,c in cells)
            if len(cells)!=(r1-r0+1)*(c1-c0+1):valid=False;break
            boxes[r0,c0]=([xs[c0],ys[r0],xs[c1+1],ys[r1+1]],r1-r0+1,c1-c0+1)
        if valid:tables.append({'xs':xs,'ys':ys,'boxes':boxes,'rows':nr,'columns':nc,'open_sides':open_sides})
    return tables


def extract_scan_tables(page,ocr,*,dpi=144):
    """Analyze an unrotated in-memory page; caller owns the MuPDF lock."""
    if not ocr.get('available'):
        return [],{'version':VERSION,'status':'ocr_unavailable','reason':ocr.get('reason')}
    if page.rect.width*page.rect.height*(dpi/72)**2>MAX_PIXELS:
        return [],{'version':VERSION,'status':'resource_limit','max_pixels':MAX_PIXELS}
    import numpy as np
    pix=page.get_pixmap(dpi=dpi,colorspace=pymupdf.csGRAY,alpha=False)
    pixels=np.frombuffer(pix.samples,dtype='uint8').reshape(pix.height,pix.stride)[:,:pix.width].copy()
    mask=pixels<170;horizontal=_rules(mask,True);vertical=_rules(mask,False)
    audit={'version':VERSION,'status':'no_ruled_table','dpi':dpi,'pixels':pix.width*pix.height,
        'horizontal_rules':len(horizontal),'vertical_rules':len(vertical),
        'method':'raster_rules_plus_local_tesseract','model_calls':0,
        'limitations':['OCR需核对原页，空白不等于无内容；不恢复手写、倾斜透视或跨页表格。']}
    if len(horizontal)<3:return [],audit
    # Remove detected rule pixels only in the disposable OCR view. Source pixels
    # and PDF remain unchanged. This prevents table borders becoming OCR letters.
    clean=pixels.copy()
    for y,x0,x1,thickness in horizontal:
        half=max(1,thickness//2+1);clean[max(0,int(y)-half):int(y)+half+1,max(0,x0-1):x1+1]=255
    for x,y0,y1,thickness in vertical:
        half=max(1,thickness//2+1);clean[max(0,y0-1):y1+1,max(0,int(x)-half):int(x)+half+1]=255
    grids=_grids(horizontal,vertical)
    if grids:
        sx,sy=page.rect.width/pix.width,page.rect.height/pix.height;tables=[];used=0
        for grid in grids[:12]:
            cells=[]
            for r in range(grid['rows']):
                for c in range(grid['columns']):
                    geometry=grid['boxes'].get((r,c));words=[];text='';status='merged_or_missing_cell'
                    if geometry:
                        box,rs,cs=geometry;status='not_processed_budget'
                        if used<MAX_OCR_CELLS:
                            used+=1;x0,y0,x1,y1=box
                            left,top=int(x0)+3,int(y0)+3;right,bottom=int(x1)-2,int(y1)-2
                            if right>left and bottom>top:
                                cell_dpi=288 if (right-left)*(bottom-top)<=1_000_000 else dpi
                                image=page.get_pixmap(dpi=cell_dpi,clip=pymupdf.Rect(left*sx,top*sy,right*sx,bottom*sy),alpha=False)
                                ox,oy=image.x*72/cell_dpi,image.y*72/cell_dpi
                                # A white margin is essential for clipped glyphs
                                # to be segmented as text, not border fragments.
                                pad=24
                                rgb=np.frombuffer(image.samples,dtype='uint8').reshape(image.height,image.width,image.n)
                                padded=np.pad(rgb,((pad,pad),(pad,pad),(0,0)),constant_values=255)
                                image=pymupdf.Pixmap(pymupdf.csRGB,padded.shape[1],padded.shape[0],padded.tobytes(),False)
                                image.set_dpi(cell_dpi,cell_dpi);ox-=pad*72/cell_dpi;oy-=pad*72/cell_dpi
                                with pymupdf.open(stream=image.pdfocr_tobytes(language=ocr['languages'],tessdata=ocr['path']),filetype='pdf') as doc:
                                    words=[{'text':w[4],'bbox':[w[0]+ox,w[1]+oy,w[2]+ox,w[3]+oy]} for w in doc[0].get_text('words')]
                                    text=_word_lines(words)
                            status='ocr_extracted_needs_review' if text else 'empty_or_ocr_missed'
                        raw=[box[0]*sx,box[1]*sy,box[2]*sx,box[3]*sy]
                    else:raw=None;rs=cs=1
                    cells.append({'row':r,'column':c,'row_span':rs,'column_span':cs,'text':text,
                        'raw_bbox':raw,'word_boxes':words,'status':status})
            if sum(bool(c['text']) for c in cells)<2:continue
            tables.append({'raw_bbox':[grid['xs'][0]*sx,grid['ys'][0]*sy,grid['xs'][-1]*sx,grid['ys'][-1]*sy],
                'row_count':grid['rows'],'column_count':grid['columns'],'cells':cells,
                'method':'raster_grid_cell_crop_local_ocr','needs_review':True,'status':'ocr_structure_candidate',
                'outer_boundaries_from_rule_endpoints':grid['open_sides'],
                'truncated_cells':any(c['status']=='not_processed_budget' for c in cells)})
        audit.update(status='extracted_needs_review' if tables else 'no_readable_grid',table_count=len(tables),
            ocr_cells=used,cell_budget=MAX_OCR_CELLS,language=ocr['languages'])
        return tables,audit


def _word_lines(words):
    """OCR block ordering is not table row ordering; use physical baselines."""
    rows=[]
    for word in sorted(words,key=lambda w:((w['bbox'][1]+w['bbox'][3])/2,w['bbox'][0])):
        box=word['bbox'];centre=(box[1]+box[3])/2;height=box[3]-box[1]
        row=next((r for r in rows if abs(r['centre']-centre)<max(height*.4,1.5)),None)
        if row is None:row={'centre':centre,'words':[]};rows.append(row)
        row['words'].append(word)
    return '\n'.join(' '.join(w['text'] for w in sorted(r['words'],key=lambda w:w['bbox'][0])) for r in rows)
    clean_pix=pymupdf.Pixmap(pymupdf.csGRAY,pix.width,pix.height,clean.tobytes(),False)
    clean_pix=pymupdf.Pixmap(pymupdf.csRGB,clean_pix)
    clean_pix.set_dpi(dpi,dpi)
    data=clean_pix.pdfocr_tobytes(language=ocr['languages'],tessdata=ocr['path'],compress=True)
    sx,sy=page.rect.width/pix.width,page.rect.height/pix.height
    lines=[((x0*sx,y*sy),(x1*sx,y*sy)) for y,x0,x1,_ in horizontal]
    lines += [((x*sx,y0*sy),(x*sx,y1*sy)) for x,y0,y1,_ in vertical]
    with pymupdf.open(stream=data,filetype='pdf') as recognized:
        observed=recognized[0];words=observed.get_text('words');tables=[]
        # Genuine vertical rules are preferred; horizontal-only scientific
        # tables use explicit text alignment and remain marked for review.
        found=observed.find_tables(strategy='lines',add_lines=lines,use_layout=False)
        method='raster_grid_local_ocr'
        candidates=list(found.tables)
        if not candidates:
            found=observed.find_tables(vertical_strategy='text',horizontal_strategy='lines',
                add_lines=lines,min_words_vertical=3,use_layout=False)
            candidates=list(found.tables);method='raster_horizontal_rules_text_columns_local_ocr'
        for table in candidates[:12]:
            if table.row_count<2 or table.col_count<2 or table.row_count*table.col_count>3000:continue
            cells=[]
            for ri,row in enumerate(table.rows):
                for ci,box in enumerate(row.cells):
                    selected=[]
                    if box is not None:
                        rect=pymupdf.Rect(box)
                        # Word centres avoid duplicated text from adjacent cells.
                        selected=[w for w in words if rect.contains(pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2))]
                    grouped={}
                    for w in sorted(selected,key=lambda w:(w[5],w[6],w[0])):grouped.setdefault((w[5],w[6]),[]).append(w[4])
                    text='\n'.join(' '.join(v) for v in grouped.values())
                    cells.append({'row':ri,'column':ci,'text':text,'raw_bbox':list(box) if box else None,
                        'word_boxes':[{'text':w[4],'bbox':list(w[:4])} for w in selected],
                        'status':'ocr_extracted_needs_review' if selected else 'merged_or_missing_cell' if box is None else 'empty_or_ocr_missed'})
            if sum(bool(c['text']) for c in cells)<2:continue
            tables.append({'raw_bbox':list(table.bbox),'row_count':table.row_count,'column_count':table.col_count,
                'cells':cells,'method':method,'needs_review':True,'status':'ocr_structure_candidate'})
        audit.update(status='extracted_needs_review' if tables else 'no_ruled_table',
            ocr_words=len(words),table_count=len(tables),language=ocr['languages'])
        return tables,audit
