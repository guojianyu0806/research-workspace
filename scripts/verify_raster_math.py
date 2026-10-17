"""Local pixel-only math acceptance, preserving every earlier artifact."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import sys
import pymupdf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService


def main():
    os.environ['RESEARCH_OCR_ENABLED']='1'
    os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata')
    os.environ['RESEARCH_OCR_LANG']='eng'
    out=ROOT/'artifacts/raster-math'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True);lib=Library(out/'library');checks=[];selections=[]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    cases=[('native-parameters','UnderstandingDeepLearning_02_09_26_C.pdf',462,0),
           ('scan-permutation-288','UnderstandingDeepLearning_02_09_26_C.pdf',461,288),
           ('scan-permutation-144','UnderstandingDeepLearning_02_09_26_C.pdf',461,144),
           ('scan-fraction-288','UnderstandingDeepLearning_02_09_26_C.pdf',80,288),
           ('scan-scalar-144','1706.03762v1.pdf',7,144),
           ('scan-radical-scripts-288','1706.03762v1.pdf',3,288),
           ('scan-sums-288','UnderstandingDeepLearning_02_09_26_C.pdf',33,288),
           ('scan-missing-upper-288','UnderstandingDeepLearning_02_09_26_C.pdf',33,288),
           ('scan-missing-close-288','1706.03762v1.pdf',3,288),
           ('scan-missing-radicand-288','1706.03762v1.pdf',3,288),
           ('scan-missing-letter-144','UnderstandingDeepLearning_02_09_26_C.pdf',461,144)]
    for name,filename,number,dpi in cases:
        path=ROOT/'data/samples'/filename;digest=hashlib.sha256(path.read_bytes()).hexdigest()
        with pymupdf.open(path) as source,pymupdf.open() as sample:
            p=source[number-1]
            if dpi:
                page=sample.new_page(width=p.rect.width,height=p.rect.height)
                page.insert_image(page.rect,stream=p.get_pixmap(dpi=dpi).tobytes('png'))
                if name=='scan-missing-upper-288':page.draw_rect(pymupdf.Rect(267,590,274,599),color=None,fill=(1,1,1),overlay=True)
                if name=='scan-missing-close-288':page.draw_rect(pymupdf.Rect(380,707,384,719),color=None,fill=(1,1,1),overlay=True)
                if name=='scan-missing-radicand-288':page.draw_rect(pymupdf.Rect(366,717,377,728),color=None,fill=(1,1,1),overlay=True)
                if name=='scan-missing-letter-144':page.draw_rect(pymupdf.Rect(322,393,329,400),color=None,fill=(1,1,1),overlay=True)
                check(name+'_no_native_text',page.get_text()=='')
            else:sample.insert_pdf(source,from_page=number-1,to_page=number-1)
            data=sample.tobytes()
        doc=lib.import_document(data,name+'.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
        saved=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        check(name+'_immutable_source',hashlib.sha256(path.read_bytes()).hexdigest()==digest and lib._source_path(doc['id']).read_bytes()==data)
        check(name+'_saved_parse_unchanged',lib.get_page(doc['id'],1,run)==saved)
        check(name+'_no_model_calls',result['raster_math_audit']['model_calls']==0 and result['matrix_audit']['model_calls']==0)
        values=[*result['matrices'],*result['formulas']]
        def descendants(node):
            for c in node.get('operands',[]):
                yield c
                yield from descendants(c)
        nested=[c for m in result['matrices'] for c in m['cells']]+[c for f in result['formulas'] for c in descendants(f)]
        check(name+'_all_versioned_regions',all(x['evidence']['run_id']==run and x['evidence']['document_id']==doc['id'] and x['evidence']['page']==1
            and x['bbox']==x['evidence']['bbox'] and all(0<=v<=1 for v in x['bbox']) for x in values+nested))
        if name=='native-parameters':
            check('native_four_matrices',len(result['matrices'])==4 and all(m['latex'] for m in result['matrices']))
        elif name.startswith('scan-permutation'):
            matrix=next(m for m in result['matrices'] if m['column_count']==3)
            check(name+'_nine_actual_entries',[c['latex'] for c in matrix['cells']]==['0','1','0','0','0','1','1','0','0'])
            if dpi==144:
                vector=next((m for m in result['matrices'] if [c['latex'] for c in m['cells']]==['b','c','a']),None)
                check('low_resolution_letters_pixel_and_case_checked',bool(vector and vector['cells'][1]['ocr_audit']['letter_recheck_views']))
                refs=vector['cells'][1]['ocr_audit']['case_geometry']['reference_regions'] if vector else []
                check('letter_height_references_versioned',len(refs)==2 and all(r['evidence']['run_id']==run and r['evidence']['document_id']==doc['id'] for r in refs))
            check(name+'_retains_ocr_views',all(c['ocr_audit']['views'] for m in result['matrices'] for c in m['cells']))
        elif name=='scan-fraction-288':
            f=next((f for f in result['formulas'] if f['latex']==r'\frac{1}{1+\operatorname{exp}[-z]}'),None)
            check('fraction_exact_observed_operands',bool(f and len(f['operands'])==2))
            check('fraction_not_reported_as_whole_equation',bool(f and f['scope']=='subexpression' and f['status']=='partial_formula_needs_review'))
            check('sigmoid_whole_equation',any(f.get('scope')=='whole_equation' and f['latex']==r'sig[z]=\frac{1}{1+\operatorname{exp}[-z]}.' for f in result['formulas']))
        elif name=='scan-scalar-144':
            check('scalar_actual_assignment',any(f['latex']==r'warmup\_steps=4000.' for f in result['formulas']))
            check('subscript_not_flattened_as_Parop',not any(f['latex'] and 'Parop' in f['latex'] for f in result['formulas']))
        elif name=='scan-radical-scripts-288':
            converted={f['latex'] for f in result['formulas'] if f['method']=='raster-operators-v1' and f['latex']}
            check('actual_radical_subscript_and_transpose',converted=={r'\sqrt{d_{k}}',r'K^{T}'})
            check('radical_scripts_are_subexpressions',all(f['scope']=='subexpression' for f in result['formulas'] if f['method']=='raster-operators-v1'))
            whole=next((f for f in result['formulas'] if f.get('scope')=='whole_equation'),None)
            check('attention_whole_nested_equation',bool(whole and whole['latex']==r'Attention(Q,K,V)=softmax(\frac{QK^{T}}{\sqrt{d_{k}}})V'))
            check('nested_nodes_and_audits_versioned',bool(whole and all(n['evidence']['run_id']==run
                and (not n.get('ocr_audit') or n['ocr_audit']['evidence']['run_id']==run) for n in descendants(whole))))
        elif name in {'scan-missing-close-288','scan-missing-radicand-288'}:
            check(name+'_no_invented_whole',not any(f.get('scope')=='whole_equation' for f in result['formulas']))
            check(name+'_retains_incomplete',any(f.get('scope')=='incomplete_equation' and f['latex'] is None for f in result['formulas']))
        elif name=='scan-missing-letter-144':
            right=next(m for m in result['matrices'] if m['bbox'][0]*result['width']>310)
            check('blank_letter_not_replaced_or_row_deleted',right['latex'] is None and right['conversion']=='matrix_row_spacing_ambiguous')
        else:
            sums=[f for f in result['formulas'] if f.get('observed_operator')==r'\sum']
            if name=='scan-sums-288':check('two_sums_actual_independent_limits',len(sums)==2 and all(f['latex']==r'\sum_{i=1}^{I}' for f in sums))
            else:
                first=min(sums,key=lambda f:f['bbox'][1]);check('missing_upper_is_not_copied',first['latex'] is None and first['operands'][0]['latex'] is None)
                check('missing_upper_anchor_is_in_blank_search_region',first['operands'][0]['bbox'][3]<first['operator_audit']['bbox'][1])
            check(name+'_operator_and_observations_versioned',all(f['operator_audit']['evidence']['run_id']==run
                and all(o['ocr_audit'].get('status')=='not_observed' or o['ocr_audit']['evidence']['run_id']==run for o in f['operands']) for f in sums))
            if name=='scan-sums-288':
                check('limit_lines_are_not_independent_equations',not any(f['latex']=='i=1' for f in result['formulas']))
                check('limit_line_observations_preserved_with_owner',all(f.get('associated_line_observations')
                    and all(o['evidence']['run_id']==run for o in f['associated_line_observations']) for f in sums))
        with pymupdf.open(lib._source_path(doc['id'])) as pdf:
            for kind,items in [('matrix',result['matrices']),('formula',[f for f in result['formulas'] if f['latex']])]:
                for i,item in enumerate(items):
                    b=item['bbox'];p=pdf[0];clip=pymupdf.Rect(b[0]*p.rect.width,b[1]*p.rect.height,b[2]*p.rect.width,b[3]*p.rect.height)
                    p.get_pixmap(dpi=180,clip=clip+(-2,-2,2,2)).save(str(out/f'{name}-{kind}{i+1}.png'))
        selections.append({'name':name,'document_id':doc['id'],'run_id':run,'source_file':filename,'source_physical_page':number,'raster_dpi':dpi,'source_sha256':digest})
        print(name, 'done',flush=True)
    result={'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'selections':selections}
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(out);print(f"{result['passed']}/{len(checks)}")
    return 0 if all(c['passed'] for c in checks) else 1


if __name__=='__main__':raise SystemExit(main())
