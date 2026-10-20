"""Independent SQL, source-file and actual browser-pixel checks for book G."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import math
import struct
import sys

import pymupdf

ROOT=Path(__file__).resolve().parents[1]


def audit(parse_dir,ui_dir,navigation_dir=None,regression_dir=None):
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
            expected_keys=set()
            for block in blocks:
                for offset in range(0,min(len(block['text']),5800),1400):
                    text=block['text'][offset:min(offset+1600,5800)]
                    key=hashlib.sha256(json.dumps([parsed['semantic']['fingerprint'],parsed['document_id'],parsed['current_run_id'],block['id'],offset,text],ensure_ascii=False).encode()).hexdigest()
                    expected_keys.add(key)
            with sqlite3.connect((path.parent/'semantic.sqlite3').as_uri()+'?mode=ro',uri=True) as vectors:
                actual=dict(vectors.execute('SELECT cache_key,vector FROM vectors'))
            check('all vector keys bound to original source version',set(actual)==expected_keys and len(actual)==parsed['semantic']['chunks'])
            for key,value in actual.items():
                values=struct.unpack('<384f',value)
                check('finite normalized vector '+key,all(math.isfinite(v) for v in values) and abs(sum(v*v for v in values)-1)<.0001)
    for item in ui['images']:
        png=ui_dir/item['file'];check('stored PNG checksum '+item['label'],hashlib.sha256(png.read_bytes()).hexdigest()==item['png_sha256'])
        pix=pymupdf.Pixmap(str(png));rgba=pymupdf.Pixmap(pix,1)
        check('actual decoded browser pixels '+item['label'],hashlib.sha256(rgba.samples).hexdigest()==item['rgba_sha256'])
        check('actual raster geometry '+item['label'],pix.width==item['width'] and pix.height==item['height'] and pix.width*pix.height<=16000000)
        if item['label'].startswith('book-'):
            source_pdf=ROOT/manifest['original']['path']
            check('original image document identity '+item['label'],item['document_id']==parsed['document_id'])
        else:
            source_pdf=ROOT/'tests/fixtures/book_reading/20261020-v1'/('rotated-native.pdf' if item['label'].startswith('rotated-') else 'oversize-native.pdf')
        with pymupdf.open(source_pdf) as pdf:
            direct=pdf[item['page']-1].get_pixmap(matrix=pymupdf.Matrix(item['scale'],item['scale']),alpha=False)
            direct_rgba=pymupdf.Pixmap(direct,1)
            check('source PDF physical page pixels '+item['label'],hashlib.sha256(direct_rgba.samples).hexdigest()==item['rgba_sha256'])
    check('original selected page image coverage',{i['page'] for i in ui['images'] if i['label'].startswith('book-')}==set(manifest['reading_pages']))
    warm=next(i for i in ui['memory'] if i['page']==100);last=next(i for i in ui['memory'] if i['page']==541)
    check('bounded native/browser image resident growth',last['rss_bytes']-warm['rss_bytes']<512*1024**2)
    for evidence in [parsed,ui['completion']]:
        check('owned test stopped and main data retained',evidence['stopped'] and evidence['main_unchanged'] and evidence['model_calls']==0 and evidence['runtime_unchanged'])
        for name,value in evidence['runtime_sha256'].items():check('current runtime '+name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value)
    if navigation_dir:
        navigation_dir=Path(navigation_dir).resolve();assert navigation_dir.is_relative_to(ROOT/'artifacts/book-reader-ui')
        nav=json.loads((navigation_dir/'navigation-report.json').read_text(encoding='utf-8'))
        check('actual chapter and original return supplement',nav['passed'] and len(nav['checks'])==10 and not nav['errors'] and all(c['passed'] for c in nav['checks']))
        check('exact navigation driver',hashlib.sha256((ROOT/'scripts/book_navigation_smoke.cjs').read_bytes()).hexdigest()==nav['driver_sha256'])
        check('supplement has isolated shutdown and intact history',nav['completion']['stopped'] and nav['completion']['main_unchanged'] and nav['completion']['book_history_unchanged'] and nav['completion']['model_calls']==0)
    if regression_dir:
        regression_dir=Path(regression_dir).resolve();assert regression_dir.is_relative_to(ROOT/'artifacts/regression-local')
        full=json.loads((regression_dir/'summary.json').read_text(encoding='utf-8'))
        source=json.loads((regression_dir/'manifest.json').read_text(encoding='utf-8'))
        check('full current 1161-test regression',full['passed'] and full['tests']==1161 and not(full['failures'] or full['errors'] or full['skipped']))
        for name,value in source['source_sha256'].items():check('current regression fingerprint '+name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value)
    output=ui_dir/('independent-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with output.open('x',encoding='utf-8') as stream:json.dump(dict(passed=True,checks=checks,scope='Original book geometry/provenance, pixel identity and bounded residency; not all content meaning or Pro quality.'),stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(output),passed=True,checks=len(checks))),flush=True)


if __name__=='__main__':audit(*sys.argv[1:])
