"""Actual public metadata/GitHub checks in a new isolated workspace."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.discovery import DiscoveryService
from research_workspace.library import Library


def main():
    output=ROOT/'artifacts/code-verification'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False)
    service=DiscoveryService(Library(output/'library'));report={'scope':'actual public sources, no clone or code execution','papers':[]}
    for identifier in ['1908.10084v1','1810.04805v2']:
        try:
            paper=service.search(identifier)['items'][0]
            if not paper['code_links']:
                service.import_paper(paper['id'])
                while service.library.process_next_job():pass
            checked=service.verify_code(paper['id'])
            report['papers'].append(checked)
            print(identifier,'verified',checked['verified_code'],'links',len(checked['code_links']),flush=True)
        except Exception as error:report['papers'].append({'id':identifier,'error_type':type(error).__name__})
        (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report['verified_count']=sum(p.get('verified_code',False) for p in report['papers'])
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT',output,flush=True)


if __name__=='__main__':main()
