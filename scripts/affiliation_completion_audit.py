"""Read-only final checks of saved source spans, JSON exports and test artifacts."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.freeze_affiliation_acceptance import DEFAULT, verify
from scripts.freeze_local_acceptance import sha256


def main():
    parser = argparse.ArgumentParser()
    for name in ('ui', 'regression', 'worker-stress'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    ui, regression, stress = args.ui.resolve(), args.regression.resolve(), args.worker_stress.resolve()
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))
    manifest = verify(DEFAULT)
    check('All 30 frozen real source groups retain valid PDF hashes and original spans', len(manifest['cases']) == 30)
    gold_reports = [ROOT/'artifacts/front-matter-continuation'/name/'acceptance.json' for name in (
        '20261019T234811630866Z-development', '20261019T234818353562Z-holdout')]
    for path in gold_reports:
        report = read(path)
        check(report['split']+' all groups individually pass without unsafe joins',
              all(r['passed'] and not r['unsafe_joins'] for r in report['results']))
        check(report['split']+' frozen manifest matches', report['input_manifest_sha256'] == sha256(DEFAULT/'manifest.json'))
        check(report['split']+' actual parser matches current parser',
              all(value == sha256(ROOT/name) for name, value in report['source_files_sha256'].items()))
    check('All UI runtime file fingerprints still match', all(value == sha256(ROOT/name)
          for name, value in read(ui/'provenance.json')['source_sha256'].items()))
    browser = read(ui/'browser-report.json')
    check('All 42 UI/source/export/account checks pass', browser['passed'] and len(browser['checks']) == 42
          and all(r['passed'] for r in browser['checks']) and not browser['pageErrors'] and not browser['externalRequests'])
    completion = read(ui/'completion.json')
    check('Service stopped; selected main records and fixture history unchanged; no model calls',
          all(completion[k] for k in ('main_unchanged', 'fixture_history_unchanged', 'stopped')) and completion['model_calls'] == 0)
    with sqlite3.connect((ui/'workspace/library.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        snapshots = [*read(ui/'fixed-historical-structures.json'), *[read(p) for p in ui.glob('*-authors.json')]]
        check('Two historical structures and two downloaded snapshots retained', len(snapshots) == 4)
        for snapshot in snapshots:
            doc, run = snapshot['document_id'], snapshot['run_id']
            owner = db.execute('SELECT document_id FROM parse_runs WHERE id=?', (run,)).fetchone()
            check(run+' source belongs to original document', owner is not None and owner['document_id'] == doc)
            authors = json.loads(db.execute('SELECT authors FROM documents WHERE id=?', (doc,)).fetchone()[0])
            check(run+' catalog authors unchanged', authors == ['Manually Confirmed Fixture Author'])
            blocks = {row['id']: dict(row) for row in db.execute('SELECT * FROM blocks WHERE run_id=?', (run,))}
            front = snapshot['front_matter']
            orgs = {o['id']: o for o in front['affiliations']}
            authors = {a['id']: a for a in front['authors']}
            for item in [*authors.values(), *orgs.values()]:
                parts = item.get('evidence_parts') or [item['evidence']]
                for p in parts:
                    block = blocks.get(p['block_id'])
                    check(run+' exact original part '+p['block_id']+':'+str(p['char_start']), block is not None
                          and p['document_id'] == doc and p['run_id'] == run and p['page'] == block['page']
                          and p['bbox'] == json.loads(block['bbox'])
                          and p['text'] == block['text'][p['char_start']:p['char_end']])
                if 'evidence_parts' in item:
                    check(run+' explicit whitespace join and legacy first part', item['evidence'] == parts[0]
                          and item['text'] == ' '.join(p['text'] for p in parts))
            for relation in front['relations']:
                check(run+' relation contains all original institution parts', relation['evidence'] == [
                    authors[relation['author_id']]['evidence'], *orgs[relation['affiliation_id']]['evidence_parts']])
    processes = list(stress.rglob('observed-processes.json'))
    check('Three independent simultaneous owner-crash checks retained', len(processes) == 3)
    for path in processes:
        data = read(path)
        check(str(path.relative_to(stress))+' all original objects stopped before cleanup',
              len(data['observed']) >= 3 and not any(p['running'] for p in data['observed']))
    for junit in stress.glob('case-*/junit.xml'):
        suites = list(ET.parse(junit).getroot().iter('testsuite'))
        check(str(junit.relative_to(stress))+' one actual test passed', sum(int(s.get('tests', 0)) for s in suites) == 1
              and all(int(s.get(k, 0)) == 0 for s in suites for k in ('failures', 'errors', 'skipped')))
    summary, runtime = read(regression/'summary.json'), read(regression/'manifest.json')
    check('Entire current collection passes without skips or source mutation', summary['passed']
          and summary['tests'] == runtime['expected_tests'] == 1069 and all(summary['checks'].values()))
    check('All regression runtime and test files still match', all(value == sha256(ROOT/name)
          for name, value in runtime['source_sha256'].items()))
    destination = ui/('independent-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    result = dict(passed=all(c['passed'] for c in checks), checks=checks, scope='Frozen source groups, saved SQLite source spans and fixed exports; no Pro or external accuracy certification.')
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(output=str(destination), checks=len(checks), passed=result['passed'])), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
