"""Freeze visually transcribed native matrices; no production parser imports.

This is the clear-source foundation, not the complete S4 scan/degradation gold.
Coordinates and contents were read from rendered original pages. Each work is
entirely development or held out. Parser predictions never define these cells.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'book': ('data/samples/UnderstandingDeepLearning_02_09_26_C.pdf', 'development'),
    'layernorm': ('data/corpus/1607.06450v1.pdf', 'development'),
    'factorization': ('data/corpus/1703.10722v1.pdf', 'held_out'),
    'machine_reader': ('data/corpus/1601.06733v1.pdf', 'held_out'),
}


def column(*values):
    return [[value] for value in values]


# Physical pages are one-based; bounding boxes are unrotated PDF points.
CASES = [
    ('book-4.11-state', 'book', 62, [257, 537, 284, 583], 'square', column('h_{1}', 'h_{2}', 'h_{3}')),
    ('book-4.11-bias', 'book', 62, [311, 537, 338, 583], 'square', column(r'\theta_{10}', r'\theta_{20}', r'\theta_{30}')),
    ('book-4.11-weights', 'book', 62, [349, 537, 374, 583], 'square', column(r'\theta_{11}', r'\theta_{21}', r'\theta_{31}')),
    ('book-4.12-state', 'book', 62, [221, 589, 249, 629], 'square', column(r'h_{1}^{\prime}', r'h_{2}^{\prime}', r'h_{3}^{\prime}')),
    ('book-4.12-bias', 'book', 62, [276, 589, 301, 629], 'square', column(r'\psi_{10}', r'\psi_{20}', r'\psi_{30}')),
    ('book-4.12-weights', 'book', 62, [317, 589, 391, 629], 'square', [[r'\psi_{'+str(r)+str(c)+'}' for c in range(1, 4)] for r in range(1, 4)]),
    ('book-4.12-input', 'book', 62, [392, 589, 417, 629], 'square', column('h_{1}', 'h_{2}', 'h_{3}')),
    ('book-4.13-weights', 'book', 62, [301, 658, 362, 681], 'square', [[r'\phi_{1}^{\prime}', r'\phi_{2}^{\prime}', r'\phi_{3}^{\prime}']]),
    ('book-4.13-input', 'book', 62, [362, 645, 389, 693], 'square', column(r'h_{1}^{\prime}', r'h_{2}^{\prime}', r'h_{3}^{\prime}')),
    ('layernorm-17-gates', 'layernorm', 13, [213, 299, 243, 346], 'round', column(r'\mathbf{f}_{t}', r'\mathbf{i}_{t}', r'\mathbf{o}_{t}', r'\mathbf{g}_{t}')),
    ('layernorm-20-gates', 'layernorm', 13, [177, 400, 207, 448], 'round', column(r'\mathbf{f}_{t}', r'\mathbf{i}_{t}', r'\mathbf{o}_{t}', r'\mathbf{g}_{t}')),
    ('layernorm-23-gates', 'layernorm', 13, [215, 558, 242, 586], 'round', column(r'\mathbf{z}_{t}', r'\mathbf{r}_{t}')),
    ('layernorm-26-gates', 'layernorm', 13, [164, 644, 191, 673], 'round', column(r'\mathbf{z}_{t}', r'\mathbf{r}_{t}')),
    ('factorization-2-gates', 'factorization', 1, [244, 608, 272, 656], 'round', column('i', 'f', 'o', 'g')),
    ('factorization-2-activations', 'factorization', 1, [281, 608, 325, 656], 'round', column(r'\mathit{sigm}', r'\mathit{sigm}', r'\mathit{sigm}', r'\mathit{tanh}')),
    ('factorization-2-input', 'factorization', 1, [334, 616, 369, 651], 'round', column('x_{t}', 'h_{t-1}')),
    ('factorization-3-activations-1', 'factorization', 2, [225, 646, 268, 695], 'round', column(r'\mathit{sigm}', r'\mathit{sigm}', r'\mathit{sigm}', r'\mathit{tanh}')),
    ('factorization-3-input-1', 'factorization', 2, [282, 654, 314, 690], 'round', column('x_{t}^{1}', 'h_{t-1}^{1}')),
    ('factorization-3-activations-k', 'factorization', 2, [332, 646, 376, 695], 'round', column(r'\mathit{sigm}', r'\mathit{sigm}', r'\mathit{sigm}', r'\mathit{tanh}')),
    ('factorization-3-input-k', 'factorization', 2, [388, 654, 423, 690], 'round', column('x_{t}^{k}', 'h_{t-1}^{k}')),
    ('machine-reader-1-gates', 'machine_reader', 3, [347, 169, 374, 224], 'square', column('i_{t}', 'f_{t}', 'o_{t}', r'\hat{c}_{t}')),
    ('machine-reader-1-activations', 'machine_reader', 3, [385, 169, 426, 224], 'square', column(r'\sigma', r'\sigma', r'\sigma', r'\mathrm{tanh}')),
    ('machine-reader-8-gates', 'machine_reader', 4, [352, 61, 379, 117], 'square', column('i_{t}', 'f_{t}', 'o_{t}', r'\hat{c}_{t}')),
    ('machine-reader-8-activations', 'machine_reader', 4, [390, 61, 431, 117], 'square', column(r'\sigma', r'\sigma', r'\sigma', r'\mathrm{tanh}')),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(version):
    if not version.startswith('20261020-v') or not version.removeprefix('20261020-v').isdigit():
        raise ValueError('Expected a dated local fixture version')
    fixture = ROOT/'tests/fixtures/matrix_gold'/version
    manifest_path = fixture/'manifest.json'
    if manifest_path.exists():
        raise FileExistsError('Frozen gold is immutable; create a distinct version')
    output = ROOT/'artifacts/matrix-gold'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        'schema': 'matrix-source-gold-v1',
        'status': 'clear_native_foundation_not_full_scan_acceptance',
        'annotation': 'Agent visual transcription of original page crops, not parser predictions; no independent human review claim.',
        'coordinate_system': 'unrotated_pdf_points_and_normalized_unrotated_page',
        'held_out_policy': 'Whole source works held out; do not score held-out regions during development.',
        'limits': ['No low-resolution or erased negative cases yet', 'No nested matrix-cell or fraction-cell coverage yet',
                   'These 24 regions do not complete S4; whole formula, table and chart gold remain unfinished'],
        'sources': {}, 'cases': [],
    }
    handles = {}
    try:
        for key, (relative, split) in SOURCES.items():
            path = ROOT/relative
            handles[key] = pymupdf.open(path)
            manifest['sources'][key] = {'path': relative, 'sha256': sha(path), 'pages': len(handles[key]), 'split': split}
        for cid, key, number, box, delimiter, cells in CASES:
            page = handles[key][number-1]
            assert pymupdf.Rect(box) in page.rect
            path = output/(cid+'.png')
            page.get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=pymupdf.Rect(box), alpha=False).save(path)
            manifest['cases'].append({'id': cid, 'source': key, 'split': SOURCES[key][1], 'physical_page': number,
                'bbox_points': box, 'bbox': [v/(page.rect.width if i%2 == 0 else page.rect.height) for i, v in enumerate(box)],
                'delimiter': delimiter, 'expected_rows': len(cells), 'expected_columns': len(cells[0]), 'expected_cells': cells,
                'crop': {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path), 'scale': 4},
                'annotation_status': 'visual_transcription_pending_crop_review'})
        # Labeled contact sheets; actual source pixels are never generated.
        for start in range(0, len(CASES), 12):
            sheet = Image.new('RGB', (1200, 1020), 'white')
            draw = ImageDraw.Draw(sheet)
            for i, case in enumerate(manifest['cases'][start:start+12]):
                x, y = (i%4)*300, (i//4)*340
                draw.text((x+8, y+8), case['id'], fill='black')
                picture = Image.open(ROOT/case['crop']['path']).convert('RGB')
                picture.thumbnail((280, 300))
                sheet.paste(picture, (x+10+(280-picture.width)//2, y+30))
            sheet.save(output/f'contact-{start//12+1}.png')
        for key, source in manifest['sources'].items():
            assert sha(ROOT/source['path']) == source['sha256'], key
    finally:
        for handle in handles.values():
            handle.close()
    fixture.mkdir(parents=True, exist_ok=False)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'manifest': str(manifest_path), 'sha256': sha(manifest_path), 'crops': str(output), 'count': len(CASES)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    freeze(parser.parse_args().version)
