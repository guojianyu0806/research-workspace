"""Independently bind each normalized phi to original trace and font bytes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.formula_geometry import extract_display_formulas
from research_workspace.matrix_geometry import extract_native_matrices


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    output = ROOT/'artifacts/native-font-identity'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf'
    before = sha(source)
    report = {'source': str(source.relative_to(ROOT)), 'source_sha256': before,
              'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'code_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [ROOT/'scripts/audit_native_font_identity.py', ROOT/'research_workspace/native_math_fonts.py', ROOT/'research_workspace/formula_geometry.py', ROOT/'research_workspace/matrix_geometry.py']},
              'meaning': 'CMMI/CMMIB glyph named phi belongs to TeX OML phi; other variants are not inferred.',
              'primary_definition': 'https://github.com/latex3/latex2e/blob/develop/base/fontdef.dtx',
              'scope': 'Four development pages of one public book; not held-out math quality or scanned math acceptance.',
              'checks': [], 'observations': [], 'model_calls': 0, 'socket_attempts': 0}

    def check(name, passed):
        report['checks'].append({'name': name, 'passed': bool(passed)})
        assert passed, name

    def denied(*args, **kwargs):
        report['socket_attempts'] += 1
        raise RuntimeError('Offline source font audit')

    with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied), pymupdf.open(source) as pdf:
        for number in [32, 33, 62, 462]:
            page = pdf[number-1]
            observations = []
            resources = page.get_fonts(full=True)
            for span in page.get_texttrace():
                if not span['font'].startswith('CMMI'):
                    continue
                refs = {f[0] for f in resources if f[3].split('+')[-1] == span['font']}
                for native, gid, origin, box in span['chars']:
                    if native != ord('ϕ'):
                        continue
                    check(f'page {number} unique embedded resource at {origin}', len(refs) == 1)
                    xref = next(iter(refs))
                    content = pdf.extract_font(xref)[3]
                    font = pymupdf.Font(fontbuffer=content)
                    name = pymupdf.mupdf.fz_get_glyph_name2(font.this, gid)
                    check(f'page {number} actual named phi at {origin}', name == 'phi')
                    observations.append({'physical_page': number, 'native_text': chr(native), 'glyph_id': gid, 'origin': origin,
                                         'trace_bbox': box, 'font_name': span['font'], 'font_xref': xref,
                                         'font_sha256': hashlib.sha256(content).hexdigest(), 'glyph_name': name})
            values, formula_audit = extract_display_formulas(page)
            mapped = [m for m in formula_audit['glyph_mapping_audit'] if m['glyph_name'] == 'phi']
            check(f'page {number} each source observation mapped once', len(mapped) == len(observations))
            expected = sorted((o['font_xref'], o['glyph_id'], o['font_sha256']) for o in observations)
            actual = sorted((m['font_xref'], m['glyph_id'], m['font_sha256']) for m in mapped)
            check(f'page {number} actual resource/glyph hashes preserved', actual == expected)
            check(f'page {number} original Unicode preserved', all(m['native_text'] == 'ϕ' and m['mapped_text'] == 'φ' for m in mapped))
            check(f'page {number} no equation guesses alternate phi', all(r'\varphi' not in f['latex'] for f in values if f.get('latex') and any(g.get('glyph_mapping', {}).get('glyph_name') == 'phi' for g in f['glyphs'] if g.get('glyph_mapping'))))
            report['observations'].extend(observations)
        matrices, _ = extract_native_matrices(pdf[461])
        matrix = next(m for m in matrices if m['column_count'] == 3)
        for c in matrix['cells']:
            check(f"cell {c['row']},{c['column']} source-labelled phi", c['latex'].replace(' ', '') == r'\phi_{'+str(c['row']+1)+str(c['column']+1)+'}')
            check(f"cell {c['row']},{c['column']} separate audit", len(c['glyph_mapping_audit']) == 1 and c['glyph_mapping_audit'][0]['glyph_name'] == 'phi')
            check(f"cell {c['row']},{c['column']} source observation in cell region", any(o['physical_page'] == 462 and c['raw_bbox'][0] <= o['origin'][0] <= c['raw_bbox'][2] and c['raw_bbox'][1] <= o['origin'][1] <= c['raw_bbox'][3] for o in report['observations']))
        check('matrix delimiter audit excludes content symbols', len(matrix['bracket_audit']) == 4 and all(m['glyph_name'].startswith('bracket') for m in matrix['bracket_audit']))
    check('original PDF unchanged', sha(source) == before)
    check('no network or paid calls', report['socket_attempts'] == report['model_calls'] == 0)
    report['passed'] = all(c['passed'] for c in report['checks'])
    report['source_glyph_count'] = len(report['observations'])
    with (output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'source_glyphs': report['source_glyph_count'], 'checks': len(report['checks']), 'passed': report['passed']}), flush=True)


if __name__ == '__main__':
    audit()
