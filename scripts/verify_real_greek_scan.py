"""Real textbook raster acceptance with explicit destructive negative fixtures."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,subprocess,sys
import pymupdf

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from tests.test_raster_formulas import raster_page

SUMS=[r'\sum_{i=1}^{I}(f[x_{i},\varphi]-y_{i})^{2}',r'\sum_{i=1}^{I}(\varphi_{0}+\varphi_{1}x_{i}-y_{i})^{2}.']
ROOT_FORMULA=r'\sqrt{\frac{\alpha_{t}}{\alpha_{t-1}}}'


def records(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from records(v)
    elif isinstance(value,list):
        for v in value:yield from records(v)


def main():
    os.environ['RESEARCH_OCR_ENABLED']='1';os.environ['RESEARCH_OCR_LANG']='eng'
    os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata')
    dest=ROOT/'artifacts/real-greek-scan'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');dest.mkdir(parents=True)
    library=Library(dest/'library');checks=[];selections=[]
    source=ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    cases=[(f'real-{n}-r{rot}',n,rot,None) for n in (33,387,376) for rot in (0,90)]+[
        ('erased-phi',33,0,[302.5,602,310,613]),('erased-alpha',387,0,[261,383,267.5,389]),('erased-equals',376,0,[221,377,230,383])]
    for name,number,rotation,erased in cases:
        with raster_page(source.name,number,288) as pdf:
            if erased:pdf[0].draw_rect(pymupdf.Rect(erased),color=None,fill=(1,1,1))
            check(name+'_no_native_text',not pdf[0].get_text());pdf[0].set_rotation(rotation);data=pdf.tobytes()
        doc=library.import_document(data,name+'.pdf')['document'];library.process_next_job();run=doc['current_run']['id']
        before=library.get_page(doc['id'],1,run);result=LayoutService(library).analyze_page(doc['id'],1,run)
        (dest/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        check(name+'_source_and_parse_immutable',library._source_path(doc['id']).read_bytes()==data and library.get_page(doc['id'],1,run)==before)
        positions=[r for r in records(result) if 'evidence' in r]
        check(name+'_every_position_versioned',positions and all('raw_bbox' not in r for r in records(result)) and all(
            r['evidence']['document_id']==doc['id'] and r['evidence']['run_id']==run and r['evidence']['page']==1
            and r['evidence']['bbox']==r['bbox'] and all(0<=v<=1 for v in r['bbox']) for r in positions))
        audit=result['raster_math_audit']['composition_audit']
        check(name+'_bounded_local_only',audit['model_calls']==0 and audit['summand_audit']['ocr_calls']<=160 and audit['recursive_audit']['ocr_calls']<=128)
        formulas=result['formulas'];selected=[]
        if number==33:
            selected=sorted([f for f in formulas if f['method']=='raster-summands-v1'],key=lambda f:f['operands'][0]['operands'][0]['bbox'][0 if rotation==90 else 1])
            actual=[f['latex'] for f in selected]
            check(name+'_sums_exact_or_erasure_rejected',set(actual)==set(SUMS) if not erased else SUMS[0] not in actual)
            check(name+'_not_claimed_as_whole_equations',all(f['scope']=='summation_expression' and f['needs_review'] for f in selected))
            if not erased:
                leaves=list(records(selected));refs=[r['glyph_reference'] for r in leaves if 'glyph_reference' in r]
                check(name+'_local_shape_requires_own_ocr',refs and all(r['own_ocr_candidate_required'] and r['cosine_similarity']>=.9 and min(r['threshold_ious'])>=.66 for r in refs))
                check(name+'_comma_has_own_geometry',any(r.get('latex')==',' and len(r.get('ocr_audit',{}).get('geometry_views',[]))==2 for r in leaves))
        elif number==387:
            selected=[f for f in formulas if f.get('structure_kind')=='radical']
            check(name+'_root_exact_or_erasure_rejected',not any(f['latex']==ROOT_FORMULA for f in selected) if erased else any(f['latex']==ROOT_FORMULA and f['scope']=='subexpression' for f in selected))
        else:
            selected=[f for f in formulas if f['method']=='raster-aligned-rows-v1']
            counts=[len(f['operands']) for f in selected]
            check(name+'_row_groups_match_visible_signs',counts==([2,2,2] if erased else [2,5]))
            check(name+'_derivation_not_fabricated',all(f['latex'] is None and not f['multiline_audit']['initial_expression_confirmed'] and all(r['latex'] is None for r in f['operands']) for f in selected))
        with pymupdf.open(library._source_path(doc['id'])) as pdf:
            for i,value in enumerate(selected):
                p=pdf[0];b=value['bbox'];rect=pymupdf.Rect(b[0]*p.rect.width,b[1]*p.rect.height,b[2]*p.rect.width,b[3]*p.rect.height)
                p.get_pixmap(dpi=180,clip=rect+(-2,-2,2,2)).save(str(dest/f'{name}-source-{i+1}.png'))
        selections.append({'name':name,'origin':'erased_real_page_negative_fixture' if erased else 'real_paper_page','source_physical_page':number,
            'rotation':rotation,'erased_region':erased,'document_id':doc['id'],'run_id':run,'versioned_records':len(positions)})
        print(name,'done',flush=True)
    check('original_textbook_unchanged',hashlib.sha256(source.read_bytes()).hexdigest()==digest)
    report={'source_version':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_sha256':digest,
        'selections':selections,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'model_calls':0}
    (dest/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(dest);print(f"{report['passed']}/{report['total']}")
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
