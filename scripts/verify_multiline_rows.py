"""Accept real raster derivation rows and separately labelled erasure fixtures."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,subprocess,sys
import pymupdf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from tests.test_raster_formulas import raster_page
from tests.test_scan_row_geometry import EXPECTED
from scripts.verify_real_greek_scan import records


def main():
    os.environ['RESEARCH_OCR_ENABLED']='1';os.environ['RESEARCH_OCR_LANG']='eng'
    os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata')
    out=ROOT/'artifacts/multiline-rows'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    print(out,flush=True);lib=Library(out/'library');checks=[];selections=[]
    source=ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    cases=[('real-376-r0',0,None),('real-376-r90',90,None),
        ('erased-last-beta',0,[352,437,358.25,447]),('erased-middle-equality',0,[221,377,230,383])]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    for name,rotation,erase in cases:
        with raster_page(source.name,376,288) as pdf:
            if erase:pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1))
            check(name+'_raster_only',not pdf[0].get_text());pdf[0].set_rotation(rotation);data=pdf.tobytes()
        doc=lib.import_document(data,name+'.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
        before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        groups=[n for n in result['formulas'] if n.get('method')=='raster-aligned-rows-v1'];rows=sorted([r for g in groups for r in g['operands']],key=lambda r:r['source_row_number'])
        positions=[n for n in records(groups) if isinstance(n.get('evidence'),dict)]
        check(name+'_source_and_saved_parse_immutable',lib._source_path(doc['id']).read_bytes()==data and lib.get_page(doc['id'],1,run)==before)
        check(name+'_every_position_version_bound',bool(positions) and not any('raw_bbox' in n for n in records(result))
            and all(n['evidence']['document_id']==doc['id'] and n['evidence']['run_id']==run and n['evidence']['page']==1
                and n['bbox']==n['evidence']['bbox'] and n['bbox'] and all(0<=v<=1 for v in n['bbox']) for n in positions))
        check(name+'_each_row_has_equality_and_audit',all(r.get('equality_audit',{}).get('evidence') and r.get('row_audit') and r.get('prior_observations') for r in rows))
        check(name+'_no_invented_initial_lhs_or_algebra_proof',all(not g['multiline_audit']['initial_expression_confirmed'] and not g['multiline_audit']['mathematical_equivalence_verified'] for g in groups))
        check(name+'_bounded_local_only',all(r['row_audit']['model_calls']==0 and r['row_audit']['ocr_calls']<=384 for r in rows))
        if erase is None:
            check(name+'_two_and_five_row_groups',sorted(len(g['operands']) for g in groups)==[2,5])
            for i,(r,expected) in enumerate(zip(rows,EXPECTED),1):
                check(f'{name}_row{i}_exact',r['latex']==expected and r['row_audit']['full_row_resolved'])
                check(f'{name}_row{i}_all_ink_accounted',r['coverage_audit']['all_children_resolved'] and r['coverage_audit']['unassigned_ink_pixels']==0)
            check(name+'_groups_transcribed_without_full_equation_claim',all(g['latex'] and g['scope']=='transcribed_aligned_continuations' for g in groups))
        elif name=='erased-last-beta':
            check(name+'_missing_glyph_and_orphan_script_unresolved',len(rows)==7 and rows[-1]['latex'] is None
                and any(n.get('ocr_audit',{}).get('status')=='unresolved_orphan_lowered_glyph' for n in records(rows[-1])))
            check(name+'_other_rows_unmodified',[r['latex'] for r in rows[:-1]]==EXPECTED[:-1])
        else:
            check(name+'_missing_row_not_invented',[len(g['operands']) for g in groups]==[2,2,2])
        with pymupdf.open(lib._source_path(doc['id'])) as pdf:
            for i,r in enumerate(rows,1):
                p=pdf[0];b=r['bbox'];clip=pymupdf.Rect(b[0]*p.rect.width,b[1]*p.rect.height,b[2]*p.rect.width,b[3]*p.rect.height)
                p.get_pixmap(dpi=180,clip=clip+(-1,-1,1,1)).save(str(out/f'{name}-row-{i}.png'))
        selections.append({'name':name,'rotation':rotation,'erase':erase,'origin':'real_textbook_raster' if erase is None else 'explicit_erasure_negative_fixture',
            'source_physical_page':376,'document_id':doc['id'],'run_id':run,'versioned_records':len(positions)})
        print(name,'done',flush=True)
    check('original_textbook_unchanged',hashlib.sha256(source.read_bytes()).hexdigest()==digest)
    report={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_sha256':digest,
        'selections':selections,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'model_calls':0}
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"{report['passed']}/{report['total']}",flush=True)
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
