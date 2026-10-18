"""Reproduce source-local initial equations without changing historical reports."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,subprocess,sys
import pymupdf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from tests.test_raster_formulas import raster_page
from tests.test_scan_row_geometry import EXPECTED,INITIAL
from scripts.verify_real_greek_scan import records


def main():
    os.environ['RESEARCH_OCR_ENABLED']='1';os.environ['RESEARCH_OCR_LANG']='eng'
    os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata')
    out=ROOT/'artifacts/initial-equations'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    print(out,flush=True);lib=Library(out/'library');checks=[];selections=[]
    source=ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    cases=[('real-376-r0',0,None),('real-376-r90',90,None),
        ('erased-first-initial',0,[150,155,301,179]),('erased-first-x',0,[293,164.5,300,172]),
        ('erased-last-beta',0,[352,437,358.25,447]),('erased-middle-equality',0,[221,377,230,383])]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    for name,rotation,erase in cases:
        with raster_page(source.name,376,288) as pdf:
            if erase:pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1))
            check(name+'_raster_only',not pdf[0].get_text());pdf[0].set_rotation(rotation);data=pdf.tobytes()
        doc=lib.import_document(data,name+'.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
        before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        groups=[n for n in result['formulas'] if n.get('method')=='raster-aligned-rows-v1']
        # Rotation changes presentation order; original row numbers do not change.
        groups.sort(key=lambda g:g['operands'][0]['source_row_number'])
        rows=sorted([r for g in groups for r in g['operands']],key=lambda r:r['source_row_number'])
        positions=[n for n in records(groups) if isinstance(n.get('evidence'),dict)]
        check(name+'_source_and_saved_parse_immutable',lib._source_path(doc['id']).read_bytes()==data and lib.get_page(doc['id'],1,run)==before)
        check(name+'_every_position_version_bound',bool(positions) and not any('raw_bbox' in n for n in records(result))
            and all(n['evidence']['document_id']==doc['id'] and n['evidence']['run_id']==run and n['evidence']['page']==1
                and n['bbox']==n['evidence']['bbox'] and n['bbox'] and all(0<=v<=1 for v in n['bbox']) for n in positions))
        check(name+'_each_row_has_own_equality_and_audit',all(r.get('equality_audit',{}).get('evidence') and r.get('row_audit') and r.get('prior_observations') for r in rows))
        check(name+'_algebra_not_claimed',all(not g['multiline_audit']['mathematical_equivalence_verified'] for g in groups))
        check(name+'_bounded_local_only',all(r['row_audit']['model_calls']==0 and r['row_audit']['ocr_calls']<=384 for r in rows)
            and result['raster_math_audit']['composition_audit']['initial_equation_audit']['model_calls']==0
            and all(g.get('initial_expression',{}).get('initial_row_audit',{}).get('ocr_calls',0)<=384 for g in groups))
        for g in groups:
            initial=g.get('initial_expression')
            if g['multiline_audit']['initial_expression_confirmed']:
                check(name+f"_group{g['operands'][0]['source_row_number']}_references_own_initial",bool(initial)
                    and initial['initial_row_audit']['not_inferred_from_continuations']
                    and all(r['initial_expression_reference']['bbox']==initial['bbox']
                        and r['initial_expression_reference']['latex']==initial['latex']
                        and r['initial_expression_reference']['evidence']['run_id']==run for r in g['operands']))
        if erase is None:
            check(name+'_two_complete_groups',len(groups)==2 and [g['multiline_audit']['source_row_count'] for g in groups]==[3,6]
                and all(g['scope']=='whole_equation' and g['initial_expression']['latex']==INITIAL for g in groups)
                and groups[0]['initial_expression']['bbox']!=groups[1]['initial_expression']['bbox'])
            for i,(r,expected) in enumerate(zip(rows,EXPECTED),1):
                check(f'{name}_row{i}_exact',r['latex']==expected and r['row_audit']['full_row_resolved'])
            check(name+'_exactly_seven_rows',len(rows)==7)
            check(name+'_x_conflict_audits_retained',sum(bool(n.get('ocr_audit',{}).get('latin_greek_crossing_audit'))
                for g in groups for n in records(g['initial_expression']))==2)
        elif name=='erased-first-initial':
            check(name+'_no_borrowed_initial',len(groups)==2 and not groups[0]['multiline_audit']['initial_expression_confirmed']
                and groups[0]['latex']!=groups[1]['latex'] and groups[0].get('initial_expression') is None
                and all('initial_expression_reference' not in r for r in groups[0]['operands'])
                and groups[1]['initial_expression']['latex']==INITIAL)
            check(name+'_continuations_unmodified',[r['latex'] for r in rows]==EXPECTED)
        elif name=='erased-first-x':
            check(name+'_missing_x_not_invented',len(groups)==2 and groups[0]['initial_expression']['latex']!=INITIAL
                and groups[1]['initial_expression']['latex']==INITIAL)
            check(name+'_continuations_unmodified',[r['latex'] for r in rows]==EXPECTED)
        elif name=='erased-last-beta':
            check(name+'_orphan_script_unresolved',len(rows)==7 and rows[-1]['latex'] is None
                and any(n.get('ocr_audit',{}).get('status')=='unresolved_orphan_lowered_glyph' for n in records(rows[-1])))
            check(name+'_other_rows_unmodified',[r['latex'] for r in rows[:-1]]==EXPECTED[:-1])
            check(name+'_partial_group_not_complete',groups[-1]['scope']!='whole_equation' and groups[-1]['latex'] is None)
        else:
            check(name+'_missing_row_not_invented',[len(g['operands']) for g in groups]==[2,2,2])
            check(name+'_fragment_not_reused_as_initial',not groups[-1]['multiline_audit']['initial_expression_confirmed'])
        with pymupdf.open(lib._source_path(doc['id'])) as pdf:
            for i,g in enumerate(groups,1):
                if not g.get('initial_expression'):continue
                p=pdf[0];b=g['initial_expression']['bbox'];clip=pymupdf.Rect(b[0]*p.rect.width,b[1]*p.rect.height,b[2]*p.rect.width,b[3]*p.rect.height)
                p.get_pixmap(dpi=240,clip=clip+(-1,-1,1,1)).save(str(out/f'{name}-initial-{i}.png'))
        selections.append({'name':name,'rotation':rotation,'erase':erase,'origin':'real_textbook_raster' if erase is None else 'explicit_erasure_negative_fixture',
            'source_physical_page':376,'document_id':doc['id'],'run_id':run,'versioned_records':len(positions)})
        print(name,'done',flush=True)
    plain=json.loads((out/'real-376-r0.json').read_text(encoding='utf-8'))
    rotated=json.loads((out/'real-376-r90.json').read_text(encoding='utf-8'))
    def ordered_groups(result):
        return sorted([n for n in result['formulas'] if n.get('method')=='raster-aligned-rows-v1'],
            key=lambda g:g['operands'][0]['source_row_number'])
    for i,(a,b) in enumerate(zip(ordered_groups(plain),ordered_groups(rotated)),1):
        for label,x,y in [('initial',a['initial_expression'],b['initial_expression']),
            *[(f"row{r['source_row_number']}",r,s) for r,s in zip(a['operands'],b['operands'])]]:
            x0,y0,x1,y1=x['bbox'];expected=[1-y1,x0,1-y0,x1]
            check(f'rotation_group{i}_{label}_position',all(abs(u-v)<1e-5 for u,v in zip(expected,y['bbox'])))
    check('original_textbook_unchanged',hashlib.sha256(source.read_bytes()).hexdigest()==digest)
    report={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_sha256':digest,
        'selections':selections,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'model_calls':0}
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"{report['passed']}/{report['total']}",flush=True)
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
