"""Separate real-page checks from explicitly derived recursive raster fixtures."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,sys,subprocess
import pymupdf

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.library import Library
from research_workspace.layout import LayoutService
from tests.recursive_scan_fixtures import fixture,ascii_sum_fixture
from tests.test_raster_formulas import raster_page
from tests.test_scan_recursive import EXPECTED
from tests.test_scan_summands import EXPECTED as SUM


def walk(n):
    yield n
    if n.get('ocr_audit'):yield n['ocr_audit']
    if n.get('operator_audit'):yield n['operator_audit']
    for c in n.get('operands',[]):yield from walk(c)


def main():
    os.environ['RESEARCH_OCR_ENABLED']='1';os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata');os.environ['RESEARCH_OCR_LANG']='eng'
    out=ROOT/'artifacts/recursive-scan'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    lib=Library(out/'library');checks=[];selections=[]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    cases=[*[(k,'derived_fixture',None,None,0) for k in EXPECTED],
        ('derived-square-sum','derived_fixture',None,None,0),
        ('derived-missing-roof','derived_fixture','double-root',[91.5,66,104,68.5],0),
        ('derived-missing-denominator','derived_fixture','nested-fraction',[79,75,130,86],0),
        ('derived-rotated-fraction','derived_fixture','nested-fraction',None,90),
        ('real-root-fraction','real_paper_page',387,None,0),
        ('real-complex-sums','real_paper_page',33,None,0),
        ('real-missing-upper','real_paper_page',33,[267,590,274,599],0),
        ('real-missing-summand','real_paper_page',33,[279,597,364,619],0)]
    source_files=['1706.03762v1.pdf','UnderstandingDeepLearning_02_09_26_C.pdf']
    digests={f:hashlib.sha256((ROOT/'data/samples'/f).read_bytes()).hexdigest() for f in source_files}
    for name,origin,which,erase,rotation in cases:
        pdf=(raster_page(source_files[1],which,288) if origin=='real_paper_page' else
            ascii_sum_fixture() if name=='derived-square-sum' else fixture(which or name,erase=erase))
        with pdf:
            if erase and origin=='real_paper_page':pdf[0].draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1),overlay=True)
            check(name+'_raster_only',pdf[0].get_text()=='');pdf[0].set_rotation(rotation);data=pdf.tobytes()
        doc=lib.import_document(data,name+'.pdf')['document'];lib.process_next_job();run=doc['current_run']['id']
        before=lib.get_page(doc['id'],1,run);result=LayoutService(lib).analyze_page(doc['id'],1,run)
        (out/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        check(name+'_source_and_parse_immutable',lib._source_path(doc['id']).read_bytes()==data and lib.get_page(doc['id'],1,run)==before)
        audit=result['raster_math_audit']['composition_audit'];recursive=audit['recursive_audit']
        check(name+'_bounded_local_ocr',recursive['ocr_calls']<=128 and audit['summand_audit']['ocr_calls']<=160 and recursive['model_calls']==audit['summand_audit']['model_calls']==0)
        values=[f for f in result['formulas'] if f['method'] in ('raster-recursive-v1','raster-summands-v1')]
        records=[p for f in values for p in walk(f)]+recursive['candidate_regions']
        check(name+'_all_regions_versioned',bool(records) and all(p['evidence']['document_id']==doc['id'] and p['evidence']['run_id']==run and p['evidence']['page']==1 and p['bbox']==p['evidence']['bbox'] and 'raw_bbox' not in p and all(0<=v<=1 for v in p['bbox']) for p in records))
        if name in EXPECTED or name=='derived-rotated-fraction':
            expected=EXPECTED[which or name];check(name+'_explicitly_derived_expected_structure',any(f['latex']==expected for f in values))
        elif name=='derived-square-sum':check(name+'_complete_summand_with_independent_limits',any(f['latex']==SUM and f['scope']=='summation_expression' for f in values))
        elif name.startswith('derived-missing'):
            check(name+'_not_completed',not any(f['latex']==EXPECTED[which] for f in result['formulas']))
            check(name+'_failure_positions_retained',bool(recursive['candidate_regions']))
        elif name=='real-root-fraction':
            roots=[f for f in values if f.get('structure_kind')=='radical'];check(name+'_unknown_greek_retained',bool(roots and all(f['latex'] is None for f in roots)))
            check(name+'_fraction_child_observed',any(n.get('structure_kind')=='fraction' for f in roots for n in walk(f)))
        else:
            sums=sorted([f for f in values if f['method']=='raster-summands-v1'],key=lambda f:f['bbox'][1]);check(name+'_two_independent_sums',len(sums)==2)
            check(name+'_unknown_not_reported_complete',all(s['latex'] is None for s in sums))
            if name=='real-complex-sums':
                check(name+'_known_scripts_and_group_power',all({r'y_{i}',r')^{2}',r'x_{i}' if i==0 else r'\varphi_{0}'}<={n['latex'] for n in walk(s) if n.get('latex')} for i,s in enumerate(sums)))
            elif name=='real-missing-upper':check(name+'_upper_not_borrowed',sums[0]['operands'][0]['operands'][0]['latex'] is None)
            else:check(name+'_empty_summand_position_retained',sums[0]['conversion']=='summand_not_observed' and sums[0]['operands'][1]['ocr_audit']['status']=='not_observed')
        with pymupdf.open(lib._source_path(doc['id'])) as source:
            for index,item in enumerate(values[:8]):
                b=item['bbox'];p=source[0];box=pymupdf.Rect(b[0]*p.rect.width,b[1]*p.rect.height,b[2]*p.rect.width,b[3]*p.rect.height)
                p.get_pixmap(dpi=180,clip=box+(-2,-2,2,2)).save(str(out/f'{name}-source-{index+1}.png'))
        selections.append({'name':name,'origin':origin,'document_id':doc['id'],'run_id':run,'source_physical_page':which if origin=='real_paper_page' else None,'derived_from_existing_source_pixels':origin=='derived_fixture','rotation':rotation,'erased_region':erase})
        print(name,'done',flush=True)
    check('original_source_pdfs_unchanged',all(hashlib.sha256((ROOT/'data/samples'/f).read_bytes()).hexdigest()==h for f,h in digests.items()))
    report={'source_version':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_sha256':digests,'selections':selections,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'model_calls':0}
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(out);print(f"{report['passed']}/{report['total']}")
    return 0 if all(c['passed'] for c in checks) else 1


if __name__=='__main__':raise SystemExit(main())
