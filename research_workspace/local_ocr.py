"""Project-local OCR with an explicit segmentation mode, no global settings."""
import io
import re
import pymupdf


def words(image, configuration, *, mode=11, offset=(0, 0)):
    """MuPDF owns the engine; options and buffers live through the whole call.

    Exactly one Tesseract option is passed. In the pinned MuPDF build, passing
    multiple consumable options can raise Invalid option index during startup.
    """
    if mode not in {3, 6, 7, 10, 11, 13}:
        raise ValueError('Unsupported local segmentation mode')
    buffer = io.BytesIO()
    output = pymupdf.JM_new_output_fileptr(buffer)
    settings = pymupdf.mupdf.FzPdfocrOptions()
    settings.language_set2(configuration['languages'])
    settings.datadir_set2(configuration['path'])
    values = pymupdf.mupdf.fz_new_options(f'tessedit_pageseg_mode={mode}')
    settings.options = values.m_internal
    try:
        pymupdf.mupdf.fz_write_pixmap_as_pdfocr(output, image.this, settings)
    finally:
        output.fz_close_output()
        settings.options = None
    ox, oy = offset
    with pymupdf.open(stream=buffer.getvalue(), filetype='pdf') as pdf:
        return [{'text': w[4], 'bbox': [w[0]+ox, w[1]+oy, w[2]+ox, w[3]+oy]}
                for w in pdf[0].get_text('words')]


def refine_numeric_cell(image, observations, configuration, offset):
    """Sparse mode can recover decimal dots; baseline keeps isolated digits.

    Only spatially one-to-one observations are replaced. Unmatched baseline
    words are never removed merely because the second mode missed them.
    """
    text = ' '.join(w['text'] for w in observations)
    if not re.fullmatch(r'[\s\d.+,\-KkMmGgEe%=/\]Il|O]+', text):
        return observations, []
    sparse = words(image, configuration, offset=offset)
    def overlaps(a, b):
        a, b = pymupdf.Rect(a['bbox']), pymupdf.Rect(b['bbox'])
        return (a & b).get_area() > .45 * min(a.get_area(), b.get_area())
    result, audit = [], []
    for original in observations:
        matches = [w for w in sparse if overlaps(original, w)]
        revised = original
        if len(matches) == 1 and sum(overlaps(w, matches[0]) for w in observations) == 1:
            revised = matches[0]
            if revised['text'] != original['text']:
                audit.append({'status':'sparse_mode_candidate', 'original_text':original['text'],
                    'text':revised['text'], 'bbox':original['bbox'], 'needs_review':True, 'modes':[6,11]})
        result.append(revised)
    return result, audit


def numeric_rechecks(page, observations, configuration, budget):
    """Re-read suspect numeric glyphs from pixels; never infer decimal places.

    Accept only agreement of two unrestricted OCR views. Original readings,
    alternatives and crop geometry are retained and remain review candidates.
    A bracketed expression is never stripped into a bare numeric value.
    """
    import numpy as np
    source = ' '.join(w['text'] for w in observations)
    if any(c in source for c in '([{') or not re.fullmatch(r'[\s\d.+,=\-\]Il|O]+', source):
        return observations, []
    revised = [dict(w) for w in observations]
    audit = []
    for word in revised:
        if not re.fullmatch(r'[+-]?\d[0-9.]*[\]Il|O][0-9.]*', word['text']):
            continue
        if budget['remaining'] < 2:
            audit.append({'original_text': word['text'], 'bbox': word['bbox'], 'status': 'not_processed_budget'})
            continue
        rect = (pymupdf.Rect(word['bbox']) + (-1, -1, 1, 1)) & page.rect
        if rect.is_empty or rect.get_area()*36 > 500_000:
            continue
        views = []
        for dpi, threshold in ((288, 210), (432, 210)):
            image = page.get_pixmap(dpi=dpi, clip=rect, colorspace=pymupdf.csRGB, alpha=False)
            rgb = np.frombuffer(image.samples, dtype='uint8').reshape(image.height, image.width, image.n)
            pixels = np.where(rgb < threshold, 0, 255).astype('uint8')
            padded = np.pad(pixels, ((24,24),(24,24),(0,0)), constant_values=255)
            view = pymupdf.Pixmap(pymupdf.csRGB, padded.shape[1], padded.shape[0], padded.tobytes(), False)
            view.set_dpi(dpi,dpi)
            found = ''.join(w['text'] for w in words(view, configuration))
            views.append({'threshold': threshold, 'dpi': dpi, 'text': found, 'mode': 11})
            budget['remaining'] -= 1
        accepted = (views[0]['text'] == views[1]['text'] and
                    re.fullmatch(r'[+-]?\d+(?:\.\d+)?', views[0]['text']))
        audit.append({'original_text': word['text'], 'text': views[0]['text'] if accepted else word['text'],
                      'bbox': word['bbox'], 'crop_bbox':list(rect), 'status': 'two_view_candidate' if accepted else 'unresolved',
                      'views': views, 'needs_review': True})
        if accepted:
            word['text'] = views[0]['text']
    return revised, audit


def empty_artifact_checks(page, observations, interior):
    """Suppress only operator-like OCR words whose source crop is white.

    This is a pixel observation, not a guess that a numerical table cannot
    contain equality signs. Real operators with ink remain untouched.
    """
    kept, audit = [], []
    for word in observations:
        if word['text'] and all(c in '=|~' for c in word['text']):
            rect = pymupdf.Rect(word['bbox']) & interior & page.rect
            if not rect.is_empty and rect.get_area()*16 < 50_000:
                image = page.get_pixmap(dpi=288, clip=rect, colorspace=pymupdf.csGRAY, alpha=False)
                minimum = min(image.samples) if image.samples else 0
                if minimum >= 250:
                    audit.append({'status':'empty_crop_artifact','original_text':word['text'],'text':'',
                        'bbox':word['bbox'],'crop_bbox':list(rect),'minimum_gray':minimum,'needs_review':True})
                    continue
        kept.append(word)
    return kept, audit
