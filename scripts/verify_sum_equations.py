"""Real textbook whole-sum equations and row-local derivation acceptance."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,subprocess,sys
import pymupdf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from tests.test_raster_formulas import raster_page
from tests.test_scan_sum_equations import FIRST,ALIGNED
from scripts.verify_real_greek_scan import records


def main():
    os.environ['RESEARCH_OCR_ENABLED']='1';os.environ['RESEARCH_OCR_LANG']='eng'
    os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata')
    out=ROOT/'artifacts/sums-and-rows'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    lib=Library(out/'library');checks=[];selections=[]
    source=ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    cases=[(f'real-{n}-r{rot}',n,rot,None) for n in (33,376) for rot in (0,90)]+[
        ('missing-function',33,0,[216,602,223,613]),('missing-lhs-phi',33,0,[226,602,232.5,613]),
        ('missing-closing-bracket',33,0,[232.5,602,235,613]),('missing-first-equality',33,0,[246,605,253,610]),
        ('missing-second-equality',33,0,[246,640,253,645]),('missing-upper',33,0,[267,590,274,599]),
        ('missing-summand',33,0,[279,597,341,619])]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    for name,number,rotation,erase in cases:
        with raster_page(source.name,number,288) as pdf:
            if erase:pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1))
            check(name+'_raster_only',not pdf[0].get_text());pdf[0].set_rotation(rotation);data=pdf.tobytes()
        doc=lib.import_document(data,name+'.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
        before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        check(name+'_source_and_saved_parse_immutable',lib._source_path(doc['id']).read_bytes()==data and lib.get_page(doc['id'],1,run)==before)
        all_records=list(records(result));positions=[n for n in all_records if isinstance(n.get('evidence'),dict)]
        check(name+'_all_positions_versioned',bool(positions) and not any('raw_bbox' in n for n in all_records)
            and all(n['evidence']['document_id']==doc['id'] and n['evidence']['run_id']==run and n['evidence']['page']==1
                and n['bbox']==n['evidence']['bbox'] and all(0<=v<=1 for v in n['bbox']) for n in positions))
        audit=result['raster_math_audit']['composition_audit'];ea=audit['sum_equation_audit'];ma=audit['multiline_audit']
        check(name+'_bounded_local_only',audit['model_calls']==ea['model_calls']==ma['model_calls']==0 and ea['ocr_calls']<=96 and ma['ocr_calls']<=256)
        selected=[f for f in result['formulas'] if f['method']=='raster-sum-equations-v1']
        if number==33:
            actual=[n['latex'] for n in selected]
            if erase:
                check(name+'_removed_ink_not_reconstructed',ALIGNED not in actual and (name=='missing-second-equality' or FIRST not in actual))
            else:
                check(name+'_complete_lhs_function_equation',FIRST in actual)
                check(name+'_complete_two_row_equation',ALIGNED in actual)
                full=next(n for n in selected if n['latex']==ALIGNED)
                check(name+'_every_row_keeps_independent_audits',len(full['operands'])==2 and all(r['coverage_audit']['all_children_resolved'] and r['coverage_audit']['unassigned_ink_pixels']==0 for r in full['operands']))
                check(name+'_not_claiming_verified_equivalence',full['alignment_audit']['mathematical_equivalence_verified'] is False)
        else:
            selected=[f for f in result['formulas'] if f['method']=='raster-aligned-rows-v1']
            rows=[r for g in selected for r in g['operands']]
            check(name+'_seven_independent_continuations',sorted(len(g['operands']) for g in selected)==[2,5])
            check(name+'_unknown_rows_not_completed',all(r['latex'] is None and not r['row_audit']['full_row_resolved'] for r in rows))
            check(name+'_row_structures_rechecked',any(r['row_recursive_audit']['parsed_structure_count'] for r in rows))
            observations=list(records(selected))
            check(name+'_beta_root_exact_not_latin_B',any(n.get('latex')==r'\sqrt{1-\beta_{t}}' for n in observations) and not any(n.get('latex')==r'\sqrt{1-B_{t}}' for n in observations))
        with pymupdf.open(lib._source_path(doc['id'])) as pdf:
            for i,n in enumerate(selected):
                p=pdf[0];b=n['bbox'];clip=pymupdf.Rect(b[0]*p.rect.width,b[1]*p.rect.height,b[2]*p.rect.width,b[3]*p.rect.height)
                p.get_pixmap(dpi=180,clip=clip+(-2,-2,2,2)).save(str(out/f'{name}-source-{i+1}.png'))
        selections.append({'name':name,'source_physical_page':number,'rotation':rotation,'erase':erase,
            'origin':'erased_real_page_negative_fixture' if erase else 'real_paper_content_rasterized',
            'document_id':doc['id'],'run_id':run,'versioned_records':len(positions)})
        print(name,'done',flush=True)
    check('original_textbook_unchanged',hashlib.sha256(source.read_bytes()).hexdigest()==digest)
    report={'source_version':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_sha256':digest,
        'selections':selections,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'model_calls':0}
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(out);print(f"{report['passed']}/{report['total']}")
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
