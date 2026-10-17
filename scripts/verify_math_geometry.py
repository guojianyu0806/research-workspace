"""Local acceptance against immutable existing papers. No remote model calls."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.layout import LayoutService
from research_workspace.library import Library
from research_workspace.formula_geometry import extract_display_formulas


def compact(value):
    return re.sub(r'\s+', '', value or '')


def main():
    os.environ['RESEARCH_OCR_ENABLED'] = '0'
    output = ROOT/'artifacts/math-geometry'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True)
    library = Library(output/'library')
    expected = {
        3: [r'\operatorname{Attention}(Q,K,V)=\operatorname{softmax}(\frac{QK^{T}}{\sqrt{d_{k}}})V'],
        4: [r'\operatorname{MultiHead}(Q,K,V)=\operatorname{Concat}(\mathrm{head}_{1},...,\mathrm{head}_{h})',
            r'\text{where}\mathrm{head}_{i}=\operatorname{Attention}(QW_{i}^{Q},KW_{i}^{K},VW_{i}^{V})'],
        5: [r'\operatorname{FFN}(x)=\operatorname{max}(0,xW_{1}+b_{1})W_{2}+b_{2}',
            r'PE_{(pos,2i)}=sin(pos/10000^{2i/d_{\mathrm{model}}})',
            r'PE_{(pos,2i+1)}=cos(pos/10000^{2i/d_{\mathrm{model}}})'],
        7: [r'lrate=d_{\mathrm{model}}^{-0.5}\cdot\operatorname{min}(step\_num^{-0.5},step\_num\cdot warmup\_steps^{-1.5})',
            r'warmup\_steps=4000.', r'P_{drop}=0.1.',
            r'\operatorname{Attention}(Q,K,V)=\operatorname{dropout}(\operatorname{softmax}(\frac{QK^{T}}{\sqrt{d}}))V'],
    }
    checks = []
    def check(name, value):
        checks.append({'name': name, 'passed': bool(value)})

    source = ROOT/'data/samples/1706.03762v1.pdf'
    original = source.read_bytes()
    doc = library.import_document(original, source.name)['document']
    library.process_next_job()
    run = doc['current_run']['id']
    with pymupdf.open(source) as pdf:
        for number, gold in expected.items():
            saved = library.get_page(doc['id'], number, run)
            result = LayoutService(library).analyze_page(doc['id'], number, run)
            values = [f for f in result['formulas'] if f.get('method') == 'native-math-geometry-v2']
            check(f'page{number}_exact_formula_structures', [compact(f['latex']) for f in values] == [compact(x) for x in gold])
            check(f'page{number}_stable_saved_parse', saved == library.get_page(doc['id'], number, run))
            check(f'page{number}_unique_ids', len({f['id'] for f in result['formulas']}) == len(result['formulas']))
            check(f'page{number}_version_and_glyph_anchors', all(f['evidence']['run_id'] == run and f['evidence']['page'] == number
                and f['glyph_count'] == len(f['glyph_evidence']) and f['needs_review'] for f in values))
            (output/f'page{number}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            for i, formula in enumerate(values):
                b = formula['bbox']; p = pdf[number-1]
                clip = pymupdf.Rect(b[0]*p.rect.width-3, b[1]*p.rect.height-3, b[2]*p.rect.width+3, b[3]*p.rect.height+3)
                p.get_pixmap(dpi=200, clip=clip).save(str(output/f'page{number}-formula{i+1}.png'))
    check('source_unchanged', source.read_bytes() == original and library._source_path(doc['id']).read_bytes() == original)
    negatives = []
    for filename, pages in [('1810.04805v2.pdf', [7]), ('2005.11401v4.pdf', [3,4]),
            ('UnderstandingDeepLearning_02_09_26_C.pdf', [32,33,39,72])]:
        path = ROOT/'data/samples'/filename
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with pymupdf.open(path) as pdf:
            for number in pages:
                values, audit = extract_display_formulas(pdf[number-1])
                negatives.append({'file': filename, 'page': number, 'formulas': values, 'audit': audit})
                check(f'{filename}_page{number}_unsupported_not_guessed', all(f['latex'] is None for f in values if f['status'] == 'unsupported_geometry'))
                for i,formula in enumerate(values):
                    if formula.get('source_rows') or formula['latex'] and r'\prod' in formula['latex']:
                        pdf[number-1].get_pixmap(dpi=170,clip=pymupdf.Rect(formula['raw_bbox'])+(-3,-3,3,3)).save(
                            str(output/f'complex-{number}-formula{i+1}.png'))
        check(filename+'_source_unchanged', hashlib.sha256(path.read_bytes()).hexdigest() == before)
    rag = next(n for n in negatives if n['file'].startswith('2005') and n['page']==3)
    check('rag_both_encoder_subscripts', any(compact(f['latex']) == r'\mathbf{d}(z)=\operatorname{BERT}_{d}(z),\mathbf{q}(x)=\operatorname{BERT}_{q}(x)' for f in rag['formulas']))
    textbook = [f for n in negatives if n['file'].startswith('Understanding') for f in n['formulas']]
    for reason in ['prose_mixed_with_math']:
        check(reason, any(f['conversion']==reason and f['latex'] is None for f in textbook))
    check('three_complete_aligned_regions',all(any(len(f.get('source_rows',[]))==2 and f['latex'] is not None for f in n['formulas'])
        for n in negatives if n['file'].startswith('Understanding') and n['page'] in [32,33,39]))
    sums=next(n for n in negatives if n['file'].startswith('Understanding') and n['page']==33)['formulas']
    check('two_sums_and_limits_in_one_complete_region',len(sums)==1 and compact(sums[0]['latex'])==
        r'\begin{aligned}L[\boldsymbol{\varphi}]&=\sum_{i=1}^{I}(f[x_{i},\boldsymbol{\varphi}]-y_{i})^{2}\\&=\sum_{i=1}^{I}(\varphi_{0}+\varphi_{1}x_{i}-y_{i})^{2}.\end{aligned}')
    mappings=[g['glyph_mapping'] for f in sums for g in f['glyphs'] if g.get('glyph_mapping')]
    check('summation_from_actual_font_glyph_names',len(mappings)==2 and all(m['glyph_name']=='summationdisplay'
        and m['native_text']=='X' and m['mapped_text']=='∑' and len(m['font_sha256'])==64 for m in mappings))
    check('limits_not_mislabelled_as_standalone_equations',not any(compact(f['latex'])=='i=1' for f in sums))
    products=next(n for n in negatives if n['file'].startswith('Understanding') and n['page']==72)['formulas']
    check('product_with_observed_limits',any(compact(f['latex'])==r'Pr(\mathbf{y}_{1},\mathbf{y}_{2},...,\mathbf{y}_{I}\vert\mathbf{x}_{1},\mathbf{x}_{2},...,\mathbf{x}_{I})=\prod_{i=1}^{I}Pr(\mathbf{y}_{i}\vert\mathbf{x}_{i}).' for f in products))
    report = {'checks': checks, 'expected_attention_formulas': sum(map(len,expected.values())), 'model_calls': 0,
        'source_sha256': hashlib.sha256(original).hexdigest(), 'document_id': doc['id'], 'run_id': run,
        'negative_and_scope_samples': negatives, 'status': 'finite_native_subset_verified',
        'limitations': ['No general scan-formula OCR', 'No arbitrary matrices or unbounded multiline derivations',
            'All transcriptions remain unverified candidates until original-page review']}
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output': str(output), 'checks': len(checks), 'passed': sum(x['passed'] for x in checks),
        'failed': [x['name'] for x in checks if not x['passed']], 'formula_count': report['expected_attention_formulas']},ensure_ascii=False))
    assert all(x['passed'] for x in checks), 'Actual failed evidence retained'


if __name__ == '__main__':
    main()
