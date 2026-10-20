"""Independently bind manage previews to frozen physical PDF pages and raw rows."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlparse, parse_qs

import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory, regression):
    directory = Path(directory).resolve()
    regression = Path(regression).resolve()
    assert directory.is_relative_to(ROOT / 'artifacts/book-reader-ui')
    assert regression.is_relative_to(ROOT / 'artifacts/regression-local')
    report = json.loads((directory / 'large-preview-report.json').read_text(encoding='utf-8'))
    frozen = ROOT / 'tests/fixtures/book_reading/20261020-v1'
    manifest = json.loads((frozen / 'manifest.json').read_text(encoding='utf-8'))
    checks = []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)

    check('entire actual UI/API test passed', report['passed'] and not report['errors']
          and not report['external'] and len(report['checks']) == 38 and all(c['passed'] for c in report['checks']))
    check('exact browser driver', digest(ROOT / 'scripts/manage_large_preview_smoke.cjs') == report['driver_sha256'])
    check('all raw rows and originals retained', report['data_before'] == report['data_after'])
    # Recompute the after-snapshot rather than trusting two equal saved strings.
    snapshot = {}
    for database in sorted((directory / 'workspace').rglob('library.sqlite3')):
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            snapshot[str(database.relative_to(directory))] = {
                table: hashlib.sha256(json.dumps(db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall(),
                    ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                for table in ('documents', 'parse_runs', 'pages', 'blocks')}
    snapshot['originals'] = {str(p.relative_to(directory)): digest(p)
                            for p in sorted((directory / 'workspace').rglob('originals/*.pdf'))}
    check('independent persisted raw snapshot', snapshot == report['data_after'])
    expected = {'thumb-1': ('oversize-native.pdf', 1, .5), 'modal-1': ('oversize-native.pdf', 1, 1),
                'thumb-2': ('oversize-native.pdf', 2, .25), 'modal-2': ('oversize-native.pdf', 2, .25),
                'rotated-thumb': ('rotated-native.pdf', 1, .5), 'rotated-modal': ('rotated-native.pdf', 1, 1.5)}
    check('all six physical image observations present', len(report['images']) == 6
          and {i['label'] for i in report['images']} == set(expected))
    for item in report['images']:
        label = item['label']
        filename, number, scale = expected[label]
        source = frozen / filename
        frozen_sha = next(f['sha256'] for f in manifest['derived'] if f['path'] == filename)
        check('frozen PDF checksum ' + label, digest(source) == frozen_sha)
        matches = []
        for database in (directory / 'workspace').rglob('library.sqlite3'):
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
                row = db.execute('SELECT sha256,storage_name FROM documents WHERE id=?',
                                 (item['document_id'],)).fetchone()
                if row:
                    matches.append((row, database.parent / 'originals' / row[1]))
        check('unique owned original identity ' + label, len(matches) == 1)
        row, original = matches[0]
        check('stored original matches frozen source ' + label, row[0] == frozen_sha == digest(original))
        url = urlparse(item['src'])
        check('image URL bound to exact document and page ' + label,
              url.hostname == '127.0.0.1' and url.path == f'/api/documents/{item["document_id"]}/pages/{number}/image'
              and float(parse_qs(url.query)['scale'][0]) == item['scale'] == scale and item['page'] == number)
        image = directory / item['file']
        check('saved PNG checksum ' + label, digest(image) == item['png_sha256'])
        pixmap = pymupdf.Pixmap(str(image))
        check('PNG dimensions and pixel bound ' + label,
              pixmap.width == item['width'] and pixmap.height == item['height']
              and pixmap.width * pixmap.height <= 16000000)
        rgba = pymupdf.Pixmap(pixmap, 1)
        check('actual browser canvas pixels ' + label, hashlib.sha256(rgba.samples).hexdigest() == item['rgba_sha256'])
        with pymupdf.open(source) as pdf:
            physical = pdf[number - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            physical_rgba = pymupdf.Pixmap(physical, 1)
            check('direct frozen physical page pixels ' + label,
                  hashlib.sha256(physical_rgba.samples).hexdigest() == item['rgba_sha256'])
    completion = report['completion']
    check('isolated server stopped with no model use', completion['stopped'] and completion['model_calls'] == 0)
    check('main workspace and book history unchanged', completion['main_unchanged'] and completion['book_history_unchanged'])
    check('runtime unchanged through acceptance', completion['runtime_unchanged'])
    for name, sha in completion['runtime_sha256'].items():
        check('current accepted runtime ' + name, digest(ROOT / name) == sha)
    full = json.loads((regression / 'summary.json').read_text(encoding='utf-8'))
    source = json.loads((regression / 'manifest.json').read_text(encoding='utf-8'))
    check('full 1161-test regression', full['passed'] and full['tests'] == 1161
          and not (full['failures'] or full['errors'] or full['skipped']) and all(full['checks'].values()))
    for name, sha in source['source_sha256'].items():
        check('current regression input ' + name, digest(ROOT / name) == sha)
    output = directory / ('independent-preview-audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump({'passed': True, 'checks': checks, 'driver_sha256': digest(Path(__file__)),
                   'scope': 'Frozen derived PDF identity, physical pixels, bounded geometry and stored history; not OCR or translation quality.'},
                  stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'passed': True, 'checks': len(checks)}), flush=True)


if __name__ == '__main__':
    audit(*sys.argv[1:])
