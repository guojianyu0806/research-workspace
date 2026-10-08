"""Verify project-local OCR using a newly rasterized public PDF sample."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import pymupdf
from research_workspace.library import Library


def main():
    root = Path(__file__).resolve().parent.parent
    source = root/'data/samples/1706.03762v1.pdf'
    language_data = root/'data/ocr/tessdata'
    os.environ['RESEARCH_OCR_TESSDATA'] = str(language_data)
    os.environ['RESEARCH_OCR_LANG'] = 'eng+chi_sim'
    os.environ['RESEARCH_OCR_ENABLED'] = '1'
    output = root/'artifacts/ocr'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False)
    with pymupdf.open(source) as original, pymupdf.open() as scanned:
        for page in original:
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
            target = scanned.new_page(width=page.rect.width,height=page.rect.height)
            target.insert_image(target.rect,stream=pixmap.tobytes('png'))
        scanned_path = output/'attention-rasterized.pdf'
        scanned.save(scanned_path)
    with pymupdf.open(scanned_path) as scanned:
        assert all(not page.get_text().strip() for page in scanned), 'Fixture must contain image-only pages.'
    library = Library(output/'library')
    document = library.import_document(scanned_path.read_bytes(),'attention-rasterized.pdf')['document']
    started = time.perf_counter()
    library.process_next_job()
    run = library.get_document(document['id'])['current_run']
    checks = []
    for page_number in (1,8,15):
        page = library.get_page(document['id'],page_number)
        assert page['extraction_method']=='ocr'
        assert page['blocks'] and page['text'].strip(), f'No OCR text at page {page_number}'
        for block in page['blocks']:
            assert 0 <= block['bbox'][0] < block['bbox'][2] <= 1
            assert 0 <= block['bbox'][1] < block['bbox'][3] <= 1
        checks.append({'page':page_number,'text_characters':len(page['text']),'blocks':len(page['blocks'])})
    hits = library.search('attention',document_id=document['id'])
    assert hits
    report = {'fixture':'Developer-rasterized arXiv v1; not official competition scanned PDF',
              'status':run['status'],'total_pages':run['total_pages'],'text_pages':run['text_pages'],
              'failed_pages':run['failed_pages'],'seconds':round(time.perf_counter()-started,3),
              'checks':checks,'search_hits':len(hits),'ocr':library.ocr_status(),
              'language_sha256':{name:hashlib.sha256((language_data/(name+'.traineddata')).read_bytes()).hexdigest() for name in ('eng','chi_sim')}}
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),**report},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
