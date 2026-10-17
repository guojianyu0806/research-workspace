"""Local PDF layout observations with conservative, version-bound candidates.

Scan tables may invoke project-local OCR. No model calls, downloads, PDF
rewrites or metadata edits are performed.
Evidence IDs here identify this layout view; only verified saved-text matches
receive a Library block_id. Formula/author/reading-order labels are hypotheses.
"""
from __future__ import annotations

import json
import math
import re
from statistics import median

import pymupdf

from .library import Library, LibraryError, MUPDF_LOCK


LAYOUT_VERSION = 'local-layout-v7-scan-operators'
_AFFILIATION = re.compile(r'\b(?:university|institute|department|laboratory|college|school|research center)\b|大学|学院|研究所|实验室',re.I)
_SECTION = re.compile(r'^(?:\d+(?:\.\d+){0,3}\.?\s+\S|abstract\b|introduction\b|conclusions?\b|references\b|摘要|结论|参考文献)',re.I)
_APPENDIX = re.compile(r'^(?:appendi(?:x|ces)(?:\s|:|$)|附录)',re.I)
_CAPTION = re.compile(r'^(?P<kind>fig(?:ure)?\.?|table|图|表)\s*[A-Z]?\d+(?:[.:：\s]|$)',re.I)
_GREEK = {'α':r'\alpha','β':r'\beta','γ':r'\gamma','δ':r'\delta','θ':r'\theta',
          'λ':r'\lambda','μ':r'\mu','σ':r'\sigma','π':r'\pi','ω':r'\omega'}


def _plain(text):
    return ' '.join(text.split())


def _union(boxes):
    return [min(box[0] for box in boxes),min(box[1] for box in boxes),
            max(box[2] for box in boxes),max(box[3] for box in boxes)]


def _coverage(inside,container):
    intersection = pymupdf.Rect(inside) & pymupdf.Rect(container)
    area = pymupdf.Rect(inside).get_area()
    return intersection.get_area()/area if area else 0.0


def _linear_latex(text,spans):
    """Only transcribe explicit linear operators, never infer 2D math or prose."""
    if any(span['flags'] & 1 for span in spans):
        return None,'superscript_layout_requires_review'
    value = text.strip()
    if len(value)>160 or not re.search(r'[=<>≤≥]',value):
        return None,'unsupported_formula_notation'
    # Adjacent letters may denote words/functions or multiplication: do not guess.
    allowed = r'[A-Za-zαβγδθλμσπω0-9 .+\-*/=<>≤≥^_()]+'
    if not re.fullmatch(allowed,value) or re.search(r'[A-Za-z]{2,}',value):
        return None,'unsupported_formula_notation'
    atom = r'(?:[A-Za-zαβγδθλμσπω](?:_[A-Za-z0-9])?(?:\^(?:[A-Za-z]|\d+))?|\d+(?:\.\d+)?)'
    expression = rf'[+\-]?\s*{atom}(?:\s*[+\-*/]\s*{atom})*'
    sides = re.split(r'[=<>≤≥]',value)
    if len(sides)!=2 or not all(re.fullmatch(expression,side.strip()) for side in sides):
        return None,'unsupported_formula_notation'
    if value.count('(')!=value.count(')') or re.search(r'[\^_](?![A-Za-z0-9])',value):
        return None,'unsupported_formula_notation'
    depth = 0
    for character in value:
        depth += (character=='(')-(character==')')
        if depth<0:
            return None,'unsupported_formula_notation'
    value = re.sub(r'([\^_])([A-Za-z]|\d+)',r'\1{\2}',value)
    value = value.replace('*',r'\cdot ').replace('≤',r'\leq ').replace('≥',r'\geq ')
    for symbol,command in _GREEK.items():
        value = value.replace(symbol,command+' ')
    return value,'explicit_linear_notation_only'


class LayoutService:
    def __init__(self,library: Library):
        self.library = library

    def _selection(self,document_id,page,run_id):
        document = self.library.get_document(document_id)
        self.library._validate_page(page,document['page_count'])
        selected = run_id or (document['available_run'] or document['current_run'])['id']
        run = self.library.get_run(selected)
        if run['document_id'] != document_id:
            raise LibraryError('解析版本不属于指定文档。')
        return document,run

    def analyze_page(self,document_id: str,page: int,run_id: str | None = None) -> dict:
        document,run = self._selection(document_id,page,run_id)
        persisted = self.library.get_page(document_id,page,run['id'])
        with MUPDF_LOCK,pymupdf.open(self.library._source_path(document_id)) as pdf:
            source = pdf.load_page(page-1)
            width,height,rotation = source.rect.width,source.rect.height,source.rotation
            matrix = source.rotation_matrix

            def bbox(raw):
                rect = (pymupdf.Rect(raw)*matrix) & pymupdf.Rect(0,0,width,height)
                if rect.is_empty or not all(math.isfinite(value) for value in rect):
                    return None
                return [max(0.0,min(1.0,value)) for value in
                        (rect.x0/width,rect.y0/height,rect.x1/width,rect.y1/height)]

            def anchor(text,box,precision='native_pdf_line'):
                matches = [block for block in persisted['blocks'] if text.strip()
                           and _plain(text) in _plain(block['text']) and box is not None
                           and _coverage(box,block['bbox'])>=.6]
                item = {'document_id':document_id,'run_id':run['id'],'page':page,
                        'block_id':matches[0]['id'] if len(matches)==1 else None,
                        'bbox':box,'text':text,'location_precision':precision,
                        'source':'immutable_pdf_native','coordinate_system':'normalized_rendered_page'}
                return item

            flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
            native = source.get_text('dict',flags=flags,sort=False)
            regions,spans_by_id,raw_boxes = [],{},{}
            truncated = False
            for block in native.get('blocks',[]):
                if block.get('type') != 0:
                    continue
                for line in block.get('lines',[]):
                    if len(regions)>=10000:
                        truncated = True
                        break
                    spans = []
                    for raw in line.get('spans',[]):
                        if not raw.get('text') or bbox(raw['bbox']) is None:
                            continue
                        identifier = f'layout-{run["id"]}-{page}-span-{len(spans_by_id)+1}'
                        span = {'id':identifier,'text':raw['text'],'bbox':bbox(raw['bbox']),
                                'font':raw.get('font',''),'size':float(raw.get('size',0)),
                                'flags':int(raw.get('flags',0)),
                                'evidence':anchor(raw['text'],bbox(raw['bbox']),'native_pdf_span')}
                        spans.append(span)
                        spans_by_id[identifier] = span
                    text = ''.join(span['text'] for span in spans)
                    if not text.strip():
                        continue
                    identifier = f'layout-{run["id"]}-{page}-line-{len(regions)+1}'
                    box = _union([span['bbox'] for span in spans])
                    regions.append({'id':identifier,'text':text,'bbox':box,'spans':spans,
                                    'direction':list(line.get('dir',(1,0))),
                                    'font_size':max(span['size'] for span in spans),
                                    'bold':any(span['flags'] & 16 for span in spans),
                                    'evidence':anchor(text,box)})
                    raw_boxes[identifier] = list(line['bbox'])
            sizes = [span['size'] for span in spans_by_id.values() if span['text'].strip()]
            body_size = median(sizes) if sizes else 0
            notes = ['标题、作者机构、栏序、图注和公式均为规则候选，需核对原页。',
                     '原生分式、根号与上下标按实际字形及线条转写；候选均需原页复核，不保证通用公式识别。',
                     '原生字形与扫描表格使用独立本地通道；不支持通用OCR版面、矢量图语义或任意图片公式还原。',
                     '图像区域不代表已经识别出图中内容；表格使用线框检测，可能漏检无边框表或误检装饰线。']
            tables,table_raw_boxes = [],[]
            table_status = 'detected_or_none'
            # PyMuPDF 1.28 table detection uses the current page rotation. Analyze
            # the unrotated in-memory view, then restore it; never save the PDF.
            try:
                source.set_rotation(0)
                found = source.find_tables(strategy='lines_strict')
                for index,table in enumerate(found.tables):
                    values = table.extract()
                    cells = []
                    for row_index,row in enumerate(table.rows):
                        for column_index,raw_cell in enumerate(row.cells):
                            text = values[row_index][column_index]
                            box = bbox(raw_cell) if raw_cell is not None else None
                            cells.append({'row':row_index,'column':column_index,'text':text,'bbox':box,
                                'status':'extracted' if box is not None else 'merged_or_missing_cell',
                                'evidence':anchor(text or '',box,'table_cell') if box is not None else None})
                    raw = list(table.bbox)
                    table_raw_boxes.append(raw)
                    text = '\n'.join(' | '.join(value or '' for value in row) for row in values)
                    tables.append({'id':f'table-{index+1}','bbox':bbox(raw),
                        'row_count':table.row_count,'column_count':table.col_count,'cells':cells,
                        'text':text,'text_method':'extracted_cells_joined_by_row',
                        'evidence':anchor(text,bbox(raw),'table_region'),
                        'method':'pymupdf_find_tables_lines_strict','needs_review':True})
            except Exception:
                table_status = 'unsupported_or_failed'
                notes.append('当前页表格检测未成功，未输出猜测的单元格。')
            finally:
                source.set_rotation(rotation)

            scan_audit={'status':'not_needed'}
            images=source.get_image_info(hashes=False,xrefs=False)
            scan_like=(persisted.get('extraction_method')=='ocr' or any(
                pymupdf.Rect(i['bbox']).get_area()>.2*source.cropbox.get_area() for i in images))
            if scan_like:
                try:
                    from .raster_tables import extract_scan_tables
                    source.set_rotation(0)
                    observed,scan_audit=extract_scan_tables(source,self.library._ocr_configuration())
                    for extracted in observed:
                        box=bbox(extracted['raw_bbox'])
                        if any(_coverage(box,t['bbox'])>.8 and _coverage(t['bbox'],box)>.8 for t in tables):continue
                        cells=[]
                        for cell in extracted['cells']:
                            cell_box=bbox(cell['raw_bbox']) if cell['raw_bbox'] else None
                            evidence=anchor(cell['text'],cell_box,'ocr_table_cell') if cell_box else None
                            if evidence:evidence['source']='immutable_pdf_raster_local_ocr'
                            cells.append({k:v for k,v in cell.items() if k not in {'raw_bbox','word_boxes'}} | {
                                'bbox':cell_box,'evidence':evidence,'needs_review':True,
                                'ocr_audit':[{**a,'bbox':bbox(a['bbox']) if a.get('bbox') else None,
                                    'crop_bbox':bbox(a['crop_bbox']) if a.get('crop_bbox') else None,
                                    'coordinate_system':'normalized_rendered_page',
                                    'evidence':anchor(a.get('original_text',''),bbox(a['bbox']),'ocr_recheck_region') | {
                                        'source':'immutable_pdf_raster_local_ocr'}} for a in cell.get('ocr_audit',[])],
                                'math_tokens':[{k:v for k,v in t.items() if k!='raw_bbox'} | {
                                    'bbox':bbox(t['raw_bbox']),'evidence':anchor(t['text'],bbox(t['raw_bbox']),'ocr_math_header') | {
                                        'source':'immutable_pdf_raster_local_ocr'}} for t in cell.get('math_tokens',[])],
                                'word_evidence':[{'text':w['text'],'bbox':bbox(w['bbox'])} for w in cell['word_boxes']]})
                        text='\n'.join(' | '.join(c['text'] for c in cells if c['row']==r) for r in range(extracted['row_count']))
                        evidence=anchor(text,box,'ocr_table_region');evidence['source']='immutable_pdf_raster_local_ocr'
                        table_raw_boxes.append(extracted['raw_bbox'])
                        tables.append({k:v for k,v in extracted.items() if k not in {'raw_bbox','cells'}} | {
                            'id':f'table-{len(tables)+1}','bbox':box,'cells':cells,'text':text,
                            'text_method':'ocr_cells_joined_by_row','evidence':evidence})
                    if observed:table_status='native_and_or_scan_extracted_needs_review'
                except (ImportError,RuntimeError,ValueError) as error:
                    scan_audit={'status':'unsupported_or_failed','reason':type(error).__name__}
                    notes.append('扫描表格解析未成功；未将缺失单元格补写为推测值。')
                finally:source.set_rotation(rotation)

            figures = []
            try:
                for index,image in enumerate(source.get_image_info(hashes=False,xrefs=False)):
                    box = bbox(image['bbox'])
                    if box is None:
                        continue
                    figures.append({'id':f'image-{index+1}','bbox':box,'text':'',
                        'pixel_width':image['width'],'pixel_height':image['height'],
                        'method':'native_image_region','semantic_role':'unverified_image_or_figure',
                        'evidence':anchor('',box,'image_region'),'caption_ids':[]})
            except Exception:
                notes.append('当前页图像区域读取失败，未猜测图像内容。')

            candidates = {'title':[],'authors':[],'affiliations':[]}
            ordered = sorted(regions,key=lambda region:(raw_boxes[region['id']][1],raw_boxes[region['id']][0]))
            raw_height = source.cropbox.height
            if page==1 and body_size:
                title_pool=[]
                for region in ordered:
                    raw = raw_boxes[region['id']]
                    if raw[1]<raw_height*.35 and region['font_size']>=body_size*1.3 and 4<=len(region['text'])<=300:
                        dx,dy=region['direction']
                        if (not _SECTION.match(region['text']) and dx>.8 and abs(dy)<.2
                                and not re.search(r'^\s*arxiv\s*:|@',region['text'],re.I)):
                            title_pool.append(region)
                largest=max((r['font_size'] for r in title_pool),default=0)
                for region in title_pool:
                    if region['font_size']>=largest*.9:
                        candidates['title'].append(self._candidate(region,'largest_horizontal_font_at_top'))
                if candidates['title']:
                    title_ids = {item['region_id'] for item in candidates['title']}
                    title_bottom = max(raw_boxes[identifier][3] for identifier in title_ids)
                    end = min([raw_boxes[r['id']][1] for r in ordered
                               if _SECTION.match(r['text']) and raw_boxes[r['id']][1]>title_bottom]
                              or [min(raw_height*.45,title_bottom+140)])
                    for region in ordered:
                        raw,text = raw_boxes[region['id']],region['text']
                        if not title_bottom<=raw[1]<end or len(text)>250:
                            continue
                        if _AFFILIATION.search(text):
                            candidates['affiliations'].append(self._candidate(region,'affiliation_terms_and_title_proximity'))
                        elif (not re.search(r'[@:=.!?]',text) and not _SECTION.match(text)
                              and (re.search(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b',text)
                                   or re.search(r'[\u3400-\u9fff]{2,4}[、，,]',text))):
                            candidates['authors'].append(self._candidate(region,'name_pattern_and_title_proximity'))

            headings = [self._candidate(region,'section_marker_or_larger_bold_font') for region in ordered
                        if _SECTION.match(region['text']) or _APPENDIX.match(region['text'])
                        or (region['bold'] and region['font_size']>body_size*1.1 and len(region['text'])<180)]
            appendices = [self._candidate(region,'appendix_heading_marker') for region in ordered if _APPENDIX.match(region['text'])]
            captions,formulas = [],[]
            for region in ordered:
                match = _CAPTION.match(region['text'])
                if match:
                    kind = 'table' if match['kind'].lower() in {'table','表'} else 'figure'
                    targets = tables if kind=='table' else figures
                    eligible = []
                    for target in targets:
                        box,other = region['bbox'],target['bbox']
                        if not other:
                            continue
                        overlap = max(0,min(box[2],other[2])-max(box[0],other[0]))
                        gap = max(other[1]-box[3],box[1]-other[3],0)*height
                        if overlap>0 and gap<=48:
                            eligible.append((gap,target))
                    target = min(eligible,key=lambda item:item[0])[1] if eligible else None
                    caption = self._candidate(region,'caption_prefix_and_spatial_proximity') | {
                        'id':f'caption-{len(captions)+1}','kind':kind,'target_id':target['id'] if target else None}
                    captions.append(caption)
                    if target is not None and kind=='figure':
                        target['caption_ids'].append(caption['id'])
                text = region['text']
                is_math = bool(re.search(r'[=≤≥∑∏∫√±]|[α-ωΑ-Ω]|\^[A-Za-z0-9]',text))
                if is_math and len(text)<=500 and not _AFFILIATION.search(text) and '@' not in text:
                    latex,conversion = _linear_latex(text,region['spans'])
                    formulas.append(self._candidate(region,'math_symbol_candidate') | {
                        'id':f'formula-{len(formulas)+1}','original_text':text,'latex':latex,
                        'conversion':conversion,'status':'candidate','needs_review':True})

            try:
                from .formula_geometry import extract_display_formulas
                source.set_rotation(0)
                native_formulas,math_audit=extract_display_formulas(source)
                for formula_index,formula in enumerate(native_formulas):
                    box=bbox(formula['raw_bbox'])
                    # Replace only overlapping line candidates with the complete
                    # native equation region; other candidates remain untouched.
                    formulas=[f for f in formulas if not (_coverage(f['bbox'],box)>.65)]
                    evidence=anchor(formula['original_text'],box,'native_formula_geometry')
                    formulas.append({k:v for k,v in formula.items() if k not in {'raw_bbox','glyphs','rules','source_rows'}} | {
                        'id':f'formula-geometry-{formula_index+1}','bbox':box,'evidence':evidence,
                        'glyph_evidence':[{**g,'bbox':bbox(g['bbox'])} for g in formula['glyphs']],
                        'source_rows':[{k:v for k,v in row.items() if k!='raw_bbox'} | {
                            'bbox':bbox(row['raw_bbox']),'evidence':anchor(row['original_text'],bbox(row['raw_bbox']),'aligned_formula_row')} for row in formula.get('source_rows',[])],
                        'rule_evidence':[{'bbox':bbox([r[0],r[1]-.15,r[2],r[1]+.15]),'kind':'observed_horizontal_rule'} for r in formula['rules']]})
            finally:source.set_rotation(rotation)

            from .matrix_geometry import extract_native_matrices
            try:
                source.set_rotation(0)
                matrices,matrix_audit=extract_native_matrices(source)
                for index,entry_matrix in enumerate(matrices):
                    entry_matrix['bbox']=bbox(entry_matrix.pop('raw_bbox'))
                    entry_matrix['id']=f'native-matrix-{index+1}'
                    entry_matrix['evidence']=anchor(entry_matrix['original_text'],entry_matrix['bbox'],'native_matrix_region')
                    for cell in entry_matrix['cells']:
                        cell['bbox']=bbox(cell.pop('raw_bbox'))
                        cell['evidence']=anchor(cell['original_text'],cell['bbox'],'native_matrix_entry')
                for item in matrix_audit.get('rejected_delimiters',[]):
                    item['bbox']=bbox(item.pop('raw_bbox'))
            finally:source.set_rotation(rotation)

            raster_math_audit={'status':'not_needed','model_calls':0}
            if scan_like and not regions:
                try:
                    from .raster_formulas import extract_raster_math
                    source.set_rotation(0)
                    scan_formulas,scan_matrices,raster_math_audit=extract_raster_math(source,self.library._ocr_configuration(),table_raw_boxes)
                    def raster_record(item,precision):
                        item['bbox']=bbox(item.pop('raw_bbox'))
                        item['evidence']=anchor(item.get('original_text') or item.get('text') or '',item['bbox'],precision) | {
                            'source':'immutable_pdf_raster_local_ocr','block_id':None}
                        if item.get('ocr_audit',{}).get('raw_bbox'):
                            raster_record(item['ocr_audit'],'ocr_observation_region')
                    for index,formula in enumerate(scan_formulas):
                        raster_record(formula,'ocr_formula_region');formula['id']=f'scan-formula-{index+1}'
                        for operand in formula.get('operands',[]):raster_record(operand,'ocr_fraction_operand')
                        if formula.get('operator_audit',{}).get('raw_bbox'):
                            raster_record(formula['operator_audit'],'ocr_operator_region')
                        if formula.get('geometry_audit'):
                            geometry=formula['geometry_audit']
                            for key in list(geometry):
                                if key.endswith('_box'):geometry[key]=bbox(geometry[key])
                            geometry['coordinate_system']='normalized_rendered_page'
                        if formula.get('observed_rule'):
                            x0,y,x1=formula.pop('observed_rule');formula['rule_evidence']=[{'bbox':bbox([x0,y-.25,x1,y+.25]),'kind':'observed_raster_horizontal_rule'}]
                        formulas.append(formula)
                    for index,entry_matrix in enumerate(scan_matrices):
                        raster_record(entry_matrix,'ocr_matrix_region');entry_matrix['id']=f'scan-matrix-{index+1}'
                        for cell in entry_matrix['cells']:raster_record(cell,'ocr_matrix_entry')
                        for item in entry_matrix['bracket_audit']:raster_record(item,'ocr_matrix_bracket')
                        matrices.append(entry_matrix)
                except (ImportError,RuntimeError,ValueError) as error:
                    raster_math_audit={'status':'unsupported_or_failed','reason':type(error).__name__,'model_calls':0}
                    notes.append('扫描公式或矩阵未完成；不将未知符号补写为确定结果。')
                finally:source.set_rotation(rotation)

            formulas.sort(key=lambda f:(f['bbox'][1],f['bbox'][0]))

            reading = self._reading_order(regions,raw_boxes,source.cropbox.width,table_raw_boxes)
            if truncated:
                notes.append('本页文字行超过10000行上限，结果已明确截断。')
            return {'document_id':document_id,'run_id':run['id'],'page':page,
                    'width':width,'height':height,'rotation':rotation,
                    'coordinate_system':'normalized_rendered_page','version':LAYOUT_VERSION,
                    'method':'local_pdf_with_heuristic_candidates','status':'analyzed' if regions else 'ocr_layout_analyzed' if tables or formulas or matrices else 'no_native_text',
                    'processing_status':run['status'],'parsed_page_status':persisted['status'],
                    'source_sha256':document['sha256'],'text_regions':regions,'truncated':truncated,
                    'metadata_candidates':candidates,'heading_candidates':headings,'reading_order':reading,
                    'tables':tables,'table_status':table_status,'scan_table_audit':scan_audit,'figures':figures,'captions':captions,
                    'formulas':formulas,'formula_audit':math_audit,'matrices':matrices,'matrix_audit':matrix_audit,'raster_math_audit':raster_math_audit,'appendix_markers':appendices,'limitations':notes,
                    'unsupported':['general_OCR_layout','vector_figure_semantics','general_formula_to_latex','verified_author_affiliations']}

    @staticmethod
    def _candidate(region,method):
        return {'region_id':region['id'],'text':region['text'],'bbox':region['bbox'],
                'evidence':region['evidence'],'method':method,'status':'candidate','needs_review':True}

    @staticmethod
    def _reading_order(regions,boxes,width,tables):
        from .reading_order import reading_order
        return reading_order(regions,boxes,width,tables)

    def get_document_summary(self,document_id: str,run_id: str | None = None) -> dict:
        document,run = self._selection(document_id,1,run_id)
        first = self.analyze_page(document_id,1,run['id'])
        structure = self.library.get_structure(document_id,run_id=run['id'])
        headings = structure.get('headings',[])
        return {'document_id':document_id,'run_id':run['id'],'page_count':document['page_count'],
                'version':LAYOUT_VERSION,'method':'first_native_page_and_saved_text_structure',
                'metadata_candidates':first['metadata_candidates'],'headings':headings,
                'appendix_markers':[heading for heading in headings if _APPENDIX.match(heading['title'])],
                'processing_status':run['status'],'layout_analyzed_pages':[1],
                'limitations':['仅第一页执行原生版面分析；全篇标题和附录来自已保存文字结构，未逐页分析图表。',
                               '标题、作者及机构均为待核对候选，不是已核实的出版元数据。']}

    def export_page_structure(self,document_id: str,page: int,run_id: str | None = None) -> str:
        """Return portable JSON only; image binaries and original PDFs are absent."""
        return json.dumps(self.analyze_page(document_id,page,run_id),ensure_ascii=False,indent=2,allow_nan=False)
