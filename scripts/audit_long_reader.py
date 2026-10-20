"""Audit measured DOM positions and immutable evidence, not controller output."""
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def audit(directory,regression,expected_tests=1154):
    directory=Path(directory).resolve();regression=Path(regression).resolve()
    if not directory.is_relative_to(ROOT/'artifacts/long-reader'):raise ValueError('Expected long-reader artifacts')
    report=json.loads((directory/'report.json').read_text(encoding='utf-8'))
    checks=[]
    def check(name,passed):
        checks.append(dict(name=name,passed=bool(passed)))
        if not passed:raise AssertionError(name)
    check('complete browser acceptance',report['passed'] and report['jumps_per_direction']==100 and not report['errors'] and not report['external'])
    check('all explicit browser checks passed',len(report['checks'])>=437 and all(item['passed'] for item in report['checks']))
    counts=Counter(item['side'] for item in report['positions'][:200])
    check('100 jumps in each direction',counts=={'source':100,'translation':100})
    check('200 native scroll samples recorded',len(report['native_scrolls'])==200)
    for category in ('positions','native_scrolls','reflows'):
        for index,item in enumerate(report[category]):
            for side in ('source','translation'):
                value=item[side]
                check(f'{category} {index} {side} body DOM bounded',0<value['dom']<=120)
                check(f'{category} {index} {side} same fixed ordinal',value['ordinal']==item['ordinal'])
                check(f'{category} {index} {side} finite measured position',isinstance(value['error'],(float,int)) and 0<=value['error']<=value['height']*.1)
    check('scroll main-thread task limit',all(item['duration']<=200 for item in report['scroll_long_tasks']))
    requests=report['requests']
    check('summary first, no full result GET',all(r['method']!='GET' or r['path'].count('/')!=4 or 'include_result=false' in r['search'] for r in requests))
    completion=report['completion']
    check('isolated API stopped with no writes to main',completion['stopped'] and completion['main_unchanged'] and completion['runtime_unchanged'] and completion['model_calls']==0)
    for name,value in completion['runtime_sha256'].items():check('current runtime '+name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value)
    summary=json.loads((regression/'summary.json').read_text(encoding='utf-8'))
    manifest=json.loads((regression/'manifest.json').read_text(encoding='utf-8'))
    expected_tests=int(expected_tests)
    check(f'full {expected_tests}-test regression',expected_tests>=1154 and summary['passed'] and summary['tests']==manifest['expected_tests']==expected_tests
          and all(summary['checks'].values()) and not(summary['failures'] or summary['errors'] or summary['skipped']))
    for name,value in manifest['source_sha256'].items():check('regression fingerprint '+name,hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value)
    path=directory/('independent-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with path.open('x',encoding='utf-8') as stream:json.dump(dict(passed=True,checks=checks,regression=str(regression.relative_to(ROOT)),expected_tests=expected_tests,
        verification_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Measured DOM positions, protocol behavior and current fingerprints; no actual PDF reader/translation quality claim.'),stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(output=str(path),passed=True,checks=len(checks))),flush=True)


if __name__=='__main__':audit(*sys.argv[1:])
