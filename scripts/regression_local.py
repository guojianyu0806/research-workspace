"""Run the entire collected pytest suite in isolated, balanced file groups.

Uses only the standard library and the project's existing pytest. No network
provider is configured here; transport fixtures remain the tests' responsibility.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workers',type=int,default=4,choices=range(1,9))
    parser.add_argument('--weights',type=Path)
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:6]
    output=ROOT/'artifacts'/'regression-local'/stamp
    output.mkdir(parents=True,exist_ok=False)
    base=[sys.executable,'-m','pytest','-o','addopts=']
    collected=subprocess.run(base+['--collect-only','-q'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    (output/'collection.txt').write_text(collected.stdout+collected.stderr,encoding='utf-8')
    if collected.returncode:raise RuntimeError('Suite collection failed; see collection.txt')
    nodes=[line.strip() for line in collected.stdout.splitlines() if line.startswith('tests/') and '::' in line]
    if not nodes or len(nodes)!=len(set(nodes)):raise RuntimeError('Empty or duplicate test collection')
    files=sorted({node.split('::',1)[0] for node in nodes})
    weights={file:1.0 for file in files}
    if args.weights:
        for case in ET.parse(args.weights).getroot().iter('testcase'):
            file=case.get('classname','').replace('.','/')+'.py'
            if file in weights:weights[file]+=float(case.get('time',0))
    groups=[[] for _ in range(args.workers)];loads=[0.0]*args.workers
    for file in sorted(files,key=lambda f:(-weights[f],f)):
        index=min(range(args.workers),key=lambda i:loads[i])
        groups[index].append(file);loads[index]+=weights[file]
    assigned=[file for group in groups for file in group]
    if sorted(assigned)!=files or len(assigned)!=len(set(assigned)):raise RuntimeError('Invalid suite partition')
    before={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [*sorted((ROOT/'research_workspace').rglob('*.py')),
                     *sorted((ROOT/'research_workspace'/'static').glob('*.js')),
                     *[ROOT/file for file in files]]}
    manifest={'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'expected_tests':len(nodes),'expected_files':len(files),'groups':groups,'estimated_seconds':loads,
        'source_sha256':before,'mode':'full_collection_partitioned_by_file; separate pytest processes and temp/cache directories'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'tests':len(nodes),'files':len(files),'workers':args.workers}),flush=True)
    def run(index,group):
        directory=output/f'group-{index+1}'
        directory.mkdir(exist_ok=False)
        start=time.monotonic()
        with (directory/'pytest.log').open('w',encoding='utf-8') as log:
            result=subprocess.run(base+['-q','-o','cache_dir='+str(directory/'cache'),
                '--basetemp='+str(directory/'temp'),'--junitxml='+str(directory/'junit.xml'),*group],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        return {'group':index+1,'exit_code':result.returncode,'elapsed_seconds':round(time.monotonic()-start,3)}
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run,index,group) for index,group in enumerate(groups) if group]
        for future in as_completed(futures):
            result=future.result();results.append(result);print(json.dumps(result),flush=True)
    merged=ET.Element('testsuites');counts={'tests':0,'failures':0,'errors':0,'skipped':0}
    for result in sorted(results,key=lambda row:row['group']):
        report=ET.parse(output/f"group-{result['group']}"/'junit.xml').getroot()
        for suite in report.iter('testsuite'):
            for field in counts:counts[field]+=int(suite.get(field,0))
            merged.append(suite)
    after={file:hashlib.sha256((ROOT/file).read_bytes()).hexdigest() for file in before}
    ET.ElementTree(merged).write(output/'junit.xml',encoding='utf-8',xml_declaration=True)
    checks={'all_collected_tests_ran':counts['tests']==len(nodes),
        'all_groups_succeeded':all(row['exit_code']==0 for row in results),
        'source_and_tests_unchanged':before==after,
        'no_failures_errors_or_skips':counts['failures']==counts['errors']==counts['skipped']==0}
    summary={**counts,'checks':checks,'groups':results,'passed':all(checks.values())}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),**summary}),flush=True)
    return 0 if summary['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
