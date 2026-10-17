"""Read-only existing Attention raster sample vs its native table observations."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import re
import pymupdf

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService


def main():
    os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata');os.environ['RESEARCH_OCR_LANG']='eng+chi_sim'
    previous=ROOT/'artifacts/ocr/20261016T151922515093Z';library=Library(previous/'library')
    doc=library.list_documents()[0];run=doc['available_run']['id']
    output=ROOT/'artifacts/scan-tables'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');output.mkdir(parents=True)
    source=ROOT/'data/samples/1706.03762v1.pdf';before=hashlib.sha256(library._source_path(doc['id']).read_bytes()).hexdigest()
    start=time.perf_counter();observed=LayoutService(library).analyze_page(doc['id'],9,run)
    (output/'scan-structure.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2),encoding='utf-8')
    with pymupdf.open(source) as native,pymupdf.open(library._source_path(doc['id'])) as scanned:
        assert not scanned[8].get_text().strip()
        gold=native[8].find_tables(strategy='lines_strict').tables
        native[8].get_pixmap(dpi=120).save(str(output/'native-page9.png'))
        scanned[8].get_pixmap(dpi=120).save(str(output/'scan-page9.png'))
        checks=[];cells=[]
        def check(name,value):checks.append({'name':name,'passed':bool(value)})
        for i,t in enumerate(gold):
            rect=pymupdf.Rect(t.bbox);expected=[rect.x0/612,rect.y0/792,rect.x1/612,rect.y1/792]
            choices=sorted(observed['tables'],key=lambda s:sum(abs(a-b) for a,b in zip(s['bbox'],expected)))
            actual=choices[0] if choices else None
            check(f'table{i}_found',actual is not None)
            if actual is None:continue
            check(f'table{i}_shape',(actual['row_count'],actual['column_count'])==(t.row_count,t.col_count))
            check(f'table{i}_geometry',max(abs(a-b) for a,b in zip(actual['bbox'],expected))<.008)
            original=t.extract()
            for c in actual['cells']:
                if c['row']>=len(original) or c['column']>=len(original[c['row']]):continue
                value=original[c['row']][c['column']] or '';a=' '.join(c['text'].split());b=' '.join(value.split())
                cells.append({'table':i,'row':c['row'],'column':c['column'],'native':value,'ocr':c['text'],'exact':a==b,
                    'native_numbers':re.findall(r'(?<![A-Za-z])[-+]?\d+(?:\.\d+)?',value),
                    'ocr_numbers':re.findall(r'(?<![A-Za-z])[-+]?\d+(?:\.\d+)?',c['text'])})
            scanned[8].get_pixmap(dpi=180,clip=rect).save(str(output/f'table{i+1}-source.png'))
    check('source_unchanged',before==hashlib.sha256(library._source_path(doc['id']).read_bytes()).hexdigest())
    report={'scope':'developer rasterization of existing public arXiv PDF, not official scanned test set',
        'status':'structure_verified_text_requires_review',
        'source_sha256':before,'run_id':run,'page':9,'seconds':round(time.perf_counter()-start,3),'checks':checks,
        'native_tables':len(gold),'observed_tables':len(observed['tables']),'cells':cells,
        'exact_cells':sum(c['exact'] for c in cells),
        'numeric_sequence_matching_cells':sum(c['native_numbers']==c['ocr_numbers'] for c in cells),'model_calls':0}
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'checks':checks,'tables':len(observed['tables']),
        'exact_cells':report['exact_cells'],'cells':len(cells),'seconds':report['seconds']},ensure_ascii=False))
    assert all(c['passed'] for c in checks),'See retained actual failures'


if __name__=='__main__':main()
