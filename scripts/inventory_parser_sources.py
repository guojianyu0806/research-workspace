"""Read-only candidate inventory; never uses the parsers being evaluated as gold.

Native labels and bracket fragments only locate pages for subsequent visual
annotation. They do not establish formulas, matrix entries or chart semantics.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
CAPTION = re.compile(r'^(?:fig(?:ure)?\.?|table)\s*(?:[A-Z]?\d+)', re.I)
NUMBER = re.compile(r'^\((?:\d+(?:\.\d+)*|[A-Z]\.\d+)\)$')
BRACKETS = set('⎡⎣⎤⎦⎢⎥⎛⎝⎞⎠⎜⎟')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inventory():
    output = ROOT/'artifacts/parser-source-inventory'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    report = {'sources': [], 'candidates': [], 'model_calls': 0,
              'scope': 'Candidate locations from native PDF labels and glyphs; no parser correctness or human annotation claim.',
              'driver_sha256': sha256(Path(__file__))}
    started = time.monotonic()
    paths = sorted((ROOT/'data/samples').glob('*.pdf'))+sorted((ROOT/'data/corpus').glob('*.pdf'))
    for path in paths:
        before = sha256(path)
        with pymupdf.open(path) as pdf:
            first = ' '.join(pdf[0].get_text().split())
            source = {'path': path.relative_to(ROOT).as_posix(), 'sha256': before, 'pages': len(pdf),
                      'work_group': re.sub(r'v\d+$', '', path.stem),
                      'first_page_excerpt': first[:220]}
            report['sources'].append(source)
            for number, page in enumerate(pdf, 1):
                blocks = page.get_text('blocks')
                text = ''.join(b[4] for b in blocks if b[6] == 0)
                fragments = [(i, b) for i, b in enumerate(blocks) if b[6] == 0 and any(c in BRACKETS for c in b[4])]
                for kind, selected in [('bracket_fragment', fragments),
                                       ('caption', [(i, b) for i, b in enumerate(blocks) if b[6] == 0 and CAPTION.match(' '.join(b[4].split()))]),
                                       ('equation_number', [(i, b) for i, b in enumerate(blocks) if b[6] == 0 and NUMBER.fullmatch(' '.join(b[4].split())) and b[0] > page.rect.width*.65])]:
                    for ordinal, block in selected:
                        report['candidates'].append({'source': source['path'], 'work_group': source['work_group'],
                            'physical_page': number, 'kind': kind, 'native_block_ordinal': ordinal,
                            'native_bbox': [block[i]/(page.rect.width if i%2 == 0 else page.rect.height) for i in range(4)],
                            'native_text_excerpt': block[4][:500],
                            'annotation_status': 'unreviewed_candidate_only'})
                # Record a page-level pointer when unusual extraction drops the
                # delimiter shape; the font name alone never proves a matrix.
                if not fragments and re.search(r'\bmatrix\b', text, re.I):
                    report['candidates'].append({'source': source['path'], 'work_group': source['work_group'],
                        'physical_page': number, 'kind': 'matrix_word_only', 'annotation_status': 'unreviewed_candidate_only'})
        assert before == sha256(path), 'Original source changed during read-only inventory'
        print(json.dumps({'file': path.name, 'pages': source['pages'],
            'bracket_pages': len({c['physical_page'] for c in report['candidates'] if c['source'] == source['path'] and c['kind'] == 'bracket_fragment'})}), flush=True)
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    with (output/'inventory.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'sources': len(report['sources']), 'candidates': len(report['candidates'])}), flush=True)


if __name__ == '__main__':
    inventory()
