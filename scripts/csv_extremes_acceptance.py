"""Execute all frozen CSV contracts in a new isolated workspace, without models."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.freeze_csv_extremes import DEFAULT, verify
from scripts.freeze_local_acceptance import main_snapshot, sha256
from research_workspace.library import Library
from research_workspace.visualization import DataStudio, render_chart


def evaluate(output=None):
    manifest = verify()
    output = output or ROOT/'artifacts/csv-extremes'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    before = main_snapshot()
    studio = DataStudio(Library(output/'workspace'))
    reports = []
    for case in manifest['cases']:
        row = dict(id=case['id'], checks={}, errors=[])
        try:
            raw = (DEFAULT/case['path']).read_bytes()
            data = studio.import_csv(raw, case['path'])
            row['checks']['import_contract'] = case['import_accepted']
            if not case['import_accepted']:
                raise AssertionError('Invalid input accepted')
            row['checks']['dataset_sha_and_row_count'] = data['sha256'] == case['sha256'] and data['row_count'] == case['row_count']
            try:
                chart = studio.create_chart(data['id'], **case['options'])
                row['checks']['chart_contract'] = case['chart_accepted']
                if not case['chart_accepted']:
                    raise AssertionError('Invalid or excessive plotting rows accepted')
                expected = case['expected']
                spec = chart['spec']
                actual = [s['values'] for s in spec['series']]
                row['checks']['finite_values'] = all(math.isfinite(v) for s in actual for v in s)
                json.dumps(chart, allow_nan=False)
                if 'values' in expected:
                    row['checks']['independent_values'] = actual == expected['values']
                if 'labels' in expected:
                    row['checks']['labels'] = spec['labels'] == expected['labels']
                if 'dropped' in expected:
                    row['checks']['drop_record'] = chart['transformations']['dropped_csv_rows'] == expected['dropped']
                svg = render_chart(spec)
                tree = ET.fromstring(svg)
                row['checks']['finite_geometry'] = not re.search(r'(?i)(?:[=,\s"(])(?:nan|[-+]?inf(?:inity)?)(?:[,\s"<)]|$)', svg)
                text = ' '.join(tree.itertext())
                row['checks']['full_labels_accessible'] = all(str(label) in text for label in spec['labels'])
                row['checks']['full_series_units_accessible'] = all(s['name'] in text and (not s['unit'] or s['unit'] in text) for s in spec['series'])
                row['checks']['no_executable_markup'] = not any(n.tag.rsplit('}',1)[-1] in {'script','foreignObject'} for n in tree.iter())
                directory = output/case['id']; directory.mkdir()
                (directory/'chart.svg').write_text(svg, encoding='utf-8')
                (directory/'chart.json').write_text(json.dumps(chart, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
                (directory/'chart.py').write_text(studio.export_script(chart['id']), encoding='utf-8')
                row['chart_id'], row['dataset_id'] = chart['id'], data['id']
            except ValueError as error:
                row['checks']['chart_contract'] = not case['chart_accepted']
                row['errors'].append(str(error))
        except ValueError as error:
            row['checks']['import_contract'] = not case['import_accepted']
            row['errors'].append(str(error))
        except Exception as error:
            row['errors'].append(type(error).__name__+': '+str(error))
            row['checks']['unexpected_exception'] = False
        row['passed'] = bool(row['checks']) and all(row['checks'].values())
        reports.append(row)
    assert before == main_snapshot()
    result = dict(cases=reports, passed=all(c['passed'] for c in reports), main_unchanged=True,
                  manifest_sha256=sha256(DEFAULT/'manifest.json'), scope=manifest['scope'])
    (output/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(output), passed=result['passed'], total=len(reports),
        failed=[r['id'] for r in reports if not r['passed']])), flush=True)
    return result


if __name__ == '__main__':
    raise SystemExit(0 if evaluate()['passed'] else 1)
