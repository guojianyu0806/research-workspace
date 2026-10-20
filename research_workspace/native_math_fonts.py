"""Recover a finite set of symbols from actual embedded glyph names.

The PDF text layer may label a summation glyph as X. Match the character's
actual text-trace glyph ID and origin to its unambiguous embedded font first.
Never infer a summation symbol just because a font is called CMEX.
"""
from collections import defaultdict
import hashlib
import re
import pymupdf

NAMES={
    'summationdisplay':'∑','summationtext':'∑','summation':'∑',
    'productdisplay':'∏','producttext':'∏','product':'∏',
    'integraldisplay':'∫','integraltext':'∫','integral':'∫',
    'radical':'√','radicalbig':'√','radicalBig':'√','radicalbigg':'√','radicalBigg':'√',
    'bracketlefttp':'⎡','bracketleftbt':'⎣','bracketleftex':'⎢',
    'bracketrighttp':'⎤','bracketrightbt':'⎦','bracketrightex':'⎥',
}

# TeX OML distinguishes phi (slot 0x1e) from varphi (0x27). Some PDF
# extractors label the actual CMMI/CMMIB glyph named phi as U+03D5. Retain
# that observation in the audit, and normalize only a trace-verified glyph.
# This is deliberately not a global Unicode substitution or a guessed map
# for every Greek variant / every font that happens to contain "CM".
OML_NAMES={'phi':'φ'}


def _names_for_font(name):
    if re.fullmatch(r'CMMI(?:B)?\d+',name):return OML_NAMES
    if 'CMEX' in name:return NAMES
    return {}


def recover_glyph_names(page,glyphs):
    resources=defaultdict(list)
    for row in page.get_fonts(full=True):
        name=row[3].split('+')[-1]
        if _names_for_font(name):resources[name].append(row[0])
    fonts={};audit=[]
    for name,refs in resources.items():
        refs=set(refs)
        if len(refs)!=1:continue
        ref=refs.pop()
        try:
            content=page.parent.extract_font(ref)[3]
            if not content or len(content)>2_000_000:continue
            fonts[name]=(pymupdf.Font(fontbuffer=content),ref,hashlib.sha256(content).hexdigest())
        except (RuntimeError,ValueError):continue
    if not fonts:return audit
    traced=defaultdict(list)
    for span in page.get_texttrace():
        if span['font'] not in fonts:continue
        for _,gid,origin,_ in span['chars']:
            traced[span['font'],round(origin[0],3),round(origin[1],3)].append(gid)
    for g in glyphs:
        if g['font'] not in fonts:continue
        ids=traced.get((g['font'],round(g['origin_x'],3),round(g['y'],3)),[])
        if len(set(ids))!=1:continue
        font,ref,digest=fonts[g['font']];gid=ids[0]
        if not 0<=gid<font.glyph_count:continue
        try:name=pymupdf.mupdf.fz_get_glyph_name2(font.this,gid)
        except (RuntimeError,ValueError):continue
        names=_names_for_font(g['font'])
        if name not in names:continue
        g['native_text']=g['char'];g['char']=names[name]
        g['glyph_mapping']={'method':'embedded_font_glyph_name','glyph_id':gid,'glyph_name':name,
            'font_name':g['font'],'font_xref':ref,'font_sha256':digest,'native_text':g['native_text'],'mapped_text':g['char']}
        audit.append(g['glyph_mapping'])
    return audit
