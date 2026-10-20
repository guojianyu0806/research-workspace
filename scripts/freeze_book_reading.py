"""Freeze original page observations and explicitly transformed local fixtures."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys

import pymupdf

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import sha256,main_snapshot
DEFAULT=ROOT/'tests/fixtures/book_reading/20261020-v1'


def freeze(directory=DEFAULT):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    before=main_snapshot()
    prior=json.loads((ROOT/'tests/fixtures/long_reading/20261020-v1/manifest.json').read_text(encoding='utf-8'))
    source=ROOT/prior['book']['path'];assert sha256(source)==prior['book']['sha256']
    rows=[];derived=[]
    with pymupdf.open(source) as book:
        for number,page in enumerate(book,1):
            area=page.rect.get_area();images=page.get_image_info()
            clipped=[(pymupdf.Rect(value['bbox'])&page.rect).get_area()/area for value in images]
            text=page.get_text()
            rows.append(dict(page=number,width=page.rect.width,height=page.rect.height,rotation=page.rotation,
                native_has_text=bool(text.strip()),native_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                images=len(images),largest_clipped_image_fraction=max(clipped,default=0)))
        with pymupdf.open() as rotated:
            for source_page,rotation in [(73,90),(311,270)]:
                rotated.insert_pdf(book,from_page=source_page-1,to_page=source_page-1)
                rotated[-1].set_rotation(rotation)
            path=directory/'rotated-native.pdf';rotated.save(path)
        derived.append(dict(path=path.name,sha256=sha256(path),kind='derived_rotation_not_external_scan',
            pages=[dict(page=1,source_page=73,rotation=90),dict(page=2,source_page=311,rotation=270)]))
        with pymupdf.open() as oversized:
            for width,height in [(3200,4000),(12000,16000),(40000,50000)]:
                page=oversized.new_page(width=width,height=height)
                page.show_pdf_page(page.rect,book,72)
            path=directory/'oversize-native.pdf';oversized.save(path)
        derived.append(dict(path=path.name,sha256=sha256(path),kind='derived_geometry_not_original_large_scan',
            pages=[dict(page=i+1,source_page=73,width=w,height=h,readable=i<2,
                        safe_scale=1 if i==0 else .25 if i==1 else None)
                   for i,(w,h) in enumerate([(3200,4000),(12000,16000),(40000,50000)])]))
    assert len(rows)==541 and [r['page'] for r in rows if not r['native_has_text']]==[2,4,10,12]
    manifest=dict(version='book-reading-20261020-v1',created_at=datetime.now(timezone.utc).isoformat(),
        original=dict(path=str(source.relative_to(ROOT)).replace('\\','/'),sha256=sha256(source),pages=rows),derived=derived,
        reading_pages=[1,2,4,10,12,20,73,168,271,311,399,541],
        scope='Native page presence/geometry and explicitly transformed PDFs; no semantic/OCR accuracy or official dataset claim.',
        gates=dict(crash_after_min_pages=160,crash_before_max_pages=500,all_pages=541,text_pages=537,blank_pages=[2,4,10,12],
                   main_unchanged=True,old_blocks_unchanged=True,max_image_pixels=16000000),
        main_unchanged=main_snapshot()==before)
    assert manifest['main_unchanged']
    with (directory/'manifest.json').open('x',encoding='utf-8') as stream:json.dump(manifest,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(directory),original_pages=len(rows),derived_pdfs=len(derived),main_unchanged=True)),flush=True)


if __name__=='__main__':freeze()
