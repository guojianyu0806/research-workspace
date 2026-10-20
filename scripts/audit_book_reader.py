"""Independent SQL, source-file and actual browser-pixel checks for book G."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pymupdf

ROOT=Path(__file__).resolve().parents[1]


def audit(parse_dir,ui_dir):
    parse_dir=Path(parse_dir).resolve();ui_dir=Path(ui_dir).resolve()
    assert parse_dir.is_relative_to(ROOT/'artifacts/book-reader') and ui_dir.is_relative_to(ROOT/'artifacts/book-reader-ui')
    parsed=json.loads((parse_dir/'parse-report.json').read_text(encoding='utf-8'))
    ui=json.loads((ui_dir/'report.json').read_text(encoding='utf-8'))
    manifest=json.loads((ROOT/'tests/fixtures/book_reading/20261020-v1/manifest.json').read_text(encoding='utf-8'))
    checks=[]
    def check(name,passed):
        checks.append(dict(name=name,passed=bool(passed)))
        if not passed:raise AssertionError(name)
    check('whole worker/API run passed',parsed['passed'] and not parsed['errors'] and all(c['passed'] for c in parsed['checks']))
    check('whole original reader UI run passed',ui['passed'] and not ui['errors'] and not ui['external'] and all(c['passed'] for c in ui['checks']))
    check('middle crash checkpoint',160<=parsed['checkpoint_pages']<500)
    check('all physical pages read through API',[r['page'] for r in parsed['pages']]==list(range(1,542)))
    check('blank physical pages preserved',[r['page'] for r in parsed['pages'] if not r['blocks']]==[2,4,10,12])
    checkpoint=json.loads((parse_dir/'checkpoint.json').read_text(encoding='utf-8'))
    for path in (parse_dir/'workspace').rglob('library.sqlite3'):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            if not db.execute('SELECT 1 FROM documents WHERE id=?',(parsed['document_id'],)).fetchone():continue
            found=db.execute('SELECT * FROM documents WHERE id=?',(parsed['document_id'],)).fetchone()
            original=path.parent/'originals'/found['storage_name']
            check('actual stored original PDF byte hash',hashlib.sha256(original.read_bytes()).hexdigest()==manifest['original']['sha256'])
            for rid in [parsed['historical_run_id'],parsed['current_run_id']]:
                pages=db.execute('SELECT * FROM pages WHERE run_id=? ORDER BY page',(rid,)).fetchall()
                blocks=db.execute('SELECT * FROM blocks WHERE run_id=? ORDER BY page,ordinal',(rid,)).fetchall()
                check('541 persisted pages '+rid,len(pages)==541 and [p['page'] for p in pages]==list(range(1,542)))
                check('no failed page '+rid,all(p['status'] in ('parsed','no_extractable_text') for p in pages))
                check('all saved block identities unique '+rid,len(blocks)==len({b['id'] for b in blocks}))
                for page,expected in zip(pages,manifest['original']['pages']):
                    check(f'page source geometry {rid[:6]} {page["page"]}',page['width']==expected['width'] and page['height']==expected['height'] and page['rotation']==expected['rotation'])
                if rid==parsed['historical_run_id']:
                    numbers={row[1] for row in checkpoint['pages']}
                    check('persisted checkpoint page rows immutable',[list(p) for p in pages if p['page'] in numbers]==checkpoint['pages'])
                    check('persisted checkpoint block rows immutable',[list(b) for b in blocks if b['page'] in numbers]==checkpoint['blocks'])
            check('actual offline vector scope complete',parsed['semantic']['source_blocks']==len(blocks) and parsed['semantic']['truncated_blocks']==0)
    for item in ui['images']:
        png=ui_dir/item['file'];check('stored PNG checksum '+item['label'],hashlib.sha256(png.read_bytes()).hexdigest()==item['png_sha256'])
        pix=pymupdf.Pixmap(str(png));rgba=pymupdf.Pixmap(pix,1)
        check('actual decoded browser pixels '+item['label'],hashlib.sha256(rgba.samples).hexdigest()==item['rgba_sha256'])
        check('actual raster geometry '+item['label'],pix.width==item['width'] and pix.height==item['height'] and pix.width*pix.height<=16000000)
    check('original selected page image coverage',{i['page'] for i in ui['images'] if i['label'].startswith('book-')}==set(manifest['reading_pages']))
    warm=next(i for i in ui['memory'] if i['page']==100);last=next(i for i in ui['memory'] if i['page']==541)
    check('bounded native/browser image resident growth',last['rss_bytes']-warm['rss_bytes']<512*1024**2)
    for evidence in [parsed,ui['completion']]:
        check('owned test stopped and main data retained',evidence['stopped'] and evidence['main_unchanged'] and evidence['model_calls']==0 and evidence['runtime_unchanged'])
        for name,value in evidence['runtime_sha256'].items():check('current runtime '+name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value)
    output=ui_dir/('independent-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with output.open('x',encoding='utf-8') as stream:json.dump(dict(passed=True,checks=checks,scope='Original book geometry/provenance, pixel identity and bounded residency; not all content meaning or Pro quality.'),stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(output),passed=True,checks=len(checks))),flush=True)


if __name__=='__main__':audit(sys.argv[1],sys.argv[2])
