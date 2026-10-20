"""Source-backed TeX glyph identity; Unicode fallback cannot rename a glyph."""
import copy
import hashlib
from pathlib import Path

import pymupdf
import pytest

from research_workspace.formula_geometry import _atom, extract_display_formulas, pymupdf_flags
from research_workspace.native_math_fonts import recover_glyph_names

SOURCE = Path(__file__).resolve().parents[1]/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf'


def phi_glyphs(page):
    values = []
    for block in page.get_text('rawdict', flags=pymupdf_flags())['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                for ch in span['chars']:
                    if ch['c'] == 'ϕ' and span['font'].startswith('CMMI'):
                        values.append({'char': ch['c'], 'box': list(ch['bbox']), 'origin_x': ch['origin'][0],
                                       'y': ch['origin'][1], 'font': span['font'], 'flags': span['flags'],
                                       'size': span['size'], 'ids': {len(values)}})
    assert values
    return values


@pytest.mark.parametrize('physical_page', [32, 33, 62, 462])
def test_embedded_phi_identity_preserves_source_and_has_exact_font_evidence(physical_page):
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    with pymupdf.open(SOURCE) as pdf:
        page = pdf[physical_page-1]
        glyphs = phi_glyphs(page)
        origins = [(g['origin_x'], g['y'], list(g['box'])) for g in glyphs]
        audit = recover_glyph_names(page, glyphs)
        assert len(audit) == len(glyphs)
        for g in glyphs:
            m = g['glyph_mapping']
            assert g['char'] == 'φ' and g['native_text'] == 'ϕ'
            assert m['glyph_name'] == 'phi' and m['mapped_text'] == 'φ'
            content = pdf.extract_font(m['font_xref'])[3]
            assert m['font_sha256'] == hashlib.sha256(content).hexdigest()
            font = pymupdf.Font(fontbuffer=content)
            assert pymupdf.mupdf.fz_get_glyph_name2(font.this, m['glyph_id']) == 'phi'
            assert r'\phi' in _atom(g) and r'\varphi' not in _atom(g)
        assert origins == [(g['origin_x'], g['y'], g['box']) for g in glyphs]
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before


class AlteredPage:
    def __init__(self, page, trace=None, fonts=None):
        self.page, self.trace, self.fonts = page, trace, fonts

    def __getattr__(self, key):
        return getattr(self.page, key)

    def get_texttrace(self):
        return self.trace if self.trace is not None else self.page.get_texttrace()

    def get_fonts(self, **kwargs):
        return self.fonts if self.fonts is not None else self.page.get_fonts(**kwargs)


def test_ambiguous_trace_missing_embed_and_unmatched_origin_cannot_override():
    with pymupdf.open(SOURCE) as pdf:
        page = pdf[61]
        native = phi_glyphs(page)
        trace = copy.deepcopy(page.get_texttrace())
        for span in trace:
            span['chars'] = [*span['chars'], *((u, gid+1, p, box) for u, gid, p, box in span['chars'])]
        for wrapped in [AlteredPage(page, trace=trace), AlteredPage(page, fonts=[])]:
            glyphs = copy.deepcopy(native)
            assert recover_glyph_names(wrapped, glyphs) == []
            assert glyphs == native
        shifted = copy.deepcopy(native)
        for g in shifted:
            g['origin_x'] += .1
        before = copy.deepcopy(shifted)
        assert recover_glyph_names(page, shifted) == [] and shifted == before


def test_unknown_font_unicode_variant_remains_a_fallback():
    assert _atom({'char': 'ϕ', 'font': 'UnknownMath', 'flags': 0}) == r'\varphi'


def test_complete_equation_uses_verified_phi_without_changing_source_rows():
    with pymupdf.open(SOURCE) as pdf:
        values, audit = extract_display_formulas(pdf[31])
        equation = next(f for f in values if f.get('source_rows'))
        assert r'\phi' in equation['latex'] and r'\varphi' not in equation['latex']
        assert len(equation['source_rows']) == 2
        assert any(m['glyph_name'] == 'phi' for m in audit['glyph_mapping_audit'])
