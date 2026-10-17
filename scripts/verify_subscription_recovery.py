"""Real public subscription checks, with explicitly injected timeout and elapsed time."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_workspace.discovery import DiscoveryService
from research_workspace.library import Library


def main():
    output=ROOT/'artifacts/subscription-recovery'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False)
    library=Library(output/'library');service=DiscoveryService(library)
    report={'scope':'real arXiv checks; clock expiration and timeout are explicitly injected; not long-duration soak','stages':[]}
    def save(stage,result):
        report['stages'].append({'stage':stage,'result':result})
        (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(stage,'error',bool(result.get('error')) if isinstance(result,dict) else False,flush=True)
    def expire():
        with service._db() as db:
            db.execute("UPDATE subscriptions SET next_check_at='2000-01-01T00:00:00+00:00'")
            db.execute("UPDATE searches SET retrieved_at='2000-01-01T00:00:00+00:00'")
    sub=service.create_subscription('1312.3005v1')
    first=service.due_checks(limit=1)[0];save('actual_first_due',first)
    if first['error']:return
    service=DiscoveryService(Library(output/'library'))
    assert service.due_checks(limit=1)==[]
    save('reopened_future_not_due',{'checked':False,'subscriptions':service.list_subscriptions()})
    expire();second=service.due_checks(limit=1)[0];save('actual_second_due_after_simulated_interval',second)
    assert not second['error'] and second['new_items']==[] and not second['cached']
    expire()
    def fail(request):raise httpx.ReadTimeout('injected test-only outage',request=request)
    fault=DiscoveryService(library,httpx.Client(transport=httpx.MockTransport(fail)),request_interval=0)
    failure=fault.due_checks(limit=1)[0];save('injected_timeout',failure)
    assert failure['error'] and failure['subscription']['last_success_at']==second['subscription']['last_success_at']
    assert library.list_documents()==[]
    expire();recovered=DiscoveryService(Library(output/'library')).due_checks(limit=1)[0];save('actual_network_recovery',recovered)
    assert not recovered['error'] and recovered['new_items']==[] and recovered['subscription']['failure_count']==0
    report['checks_passed']=True
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT',output,flush=True)


if __name__=='__main__':main()
