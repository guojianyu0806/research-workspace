"""Read-only completion audit of a finished sustained run, never of a preflight.

Uses frozen facts, raw request/memory observations and isolated SQLite records.
Does not open account/session databases or modify original reports and libraries.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from freeze_local_acceptance import sha256, verify

DATA = ROOT / 'tests/fixtures/local_acceptance/20261019-v1'


def successful_search_p95_upper_bound(values, maximum_busy):
    """Worst successful p95 if every possibly busy call was among the fastest.

    Busy counts include other phases too, so this may overestimate successful
    latency. It cannot improve a failing latency by counting fast busy replies.
    """
    remaining = sorted(values)[maximum_busy:]
    if not remaining:
        raise ValueError('Insufficient successful semantic latency evidence')
    return remaining[int((len(remaining) - 1) * .95)]


def audit(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / 'artifacts/local-concurrency'):
        raise ValueError('Audit only accepts an isolated concurrency run directory')
    checks = []

    def require(name, condition, details=None):
        checks.append({'name': name, 'passed': bool(condition), 'details': details})
        if not condition:
            raise AssertionError(name)

    manifest = verify(DATA)
    contract = json.loads((DATA / 'contract.json').read_text(encoding='utf-8'))
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    require('Finished full sustained mode', report['mode'] == 'full_sustained_acceptance')
    require('Frozen contract and input manifest retained', report['contract'] == contract and
            report['frozen_manifest_sha256'] == sha256(DATA / 'manifest.json'))
    require('All driver gates passed without recorded errors', report['passed'] and
            report['sustained_accepted'] and not report['errors'] and
            report['checks'] and all(row['passed'] for row in report['checks']))
    require('Runtime files still match actual tested sources', all(
        sha256(ROOT / name) == digest for name, digest in report['source_sha256'].items()))
    phases = {row['name']: row for row in report['phases']}
    for name, seconds, actors in [('warmup', 300, 5), ('mixed_5_accounts', 1800, 5),
                                  ('extended_10_accounts', 900, 10)]:
        row = phases[name]
        require(name + ' full duration and actor count', row['seconds'] >= seconds and
                row['actors'] == actors, row)
    for i in range(1, 4):
        row = phases['memory_load_' + str(i)]
        require('Memory load duration ' + str(i), row['seconds'] >= 60 and row['actors'] == 10)
    actors = report['actors']
    require('All ten actors exercised complete operation cycles and real semantics',
            {a['name'] for a in actors} == {a['name'] for a in manifest['fixtures']} and
            all(a['iterations'] >= 12 and a['semantic_successes'] > 0 for a in actors))
    browser = json.loads((directory / 'browser-report.json').read_text(encoding='utf-8'))
    overlap = (min(c['last_at'] for c in browser['contexts']) -
               max(c['first_at'] for c in browser['contexts'])) / 1000
    require('Five browser contexts actually overlap for at least 45 minutes',
            browser['passed'] and len(browser['contexts']) == 5 and overlap >= 2700 and
            all(c['cycles'] >= 2 for c in browser['contexts']) and not browser['errors'] and
            not browser['externalRequests'], overlap)
    require('Browser response bodies and handles released after every operation', all(
        c['disposed_api_responses'] == c['cycles'] + 1 and c['disposed_js_handles'] == c['cycles']
        for c in browser['contexts']))
    windows = report['browser_idle_windows']
    require('Three acknowledged true idle windows', len(windows) == 3 and all(
        w['seconds'] >= 30 and len(w['acknowledged_at']) == 5 for w in windows))
    for window in windows:
        for context in browser['contexts']:
            intervals = [i for i in context['pause_intervals'] if i['token'] == window['token']]
            require(context['name'] + ' idle coverage ' + window['token'], len(intervals) == 1 and
                    intervals[0]['start_at'] <= window['start_at'] * 1000 + 1 and
                    intervals[0]['end_at'] is not None and intervals[0]['end_at'] >= window['end_at'] * 1000 - 1)
    raw = [json.loads(line) for line in (directory / 'requests.jsonl').read_text().splitlines()]
    allowed_errors = {('POST', '/api/documents', 400), ('GET', '/api/documents', 401),
                      ('POST', '/api/research/tasks', 503)}
    require('No active load requests labelled as idle',
            not any(r['phase'].startswith('memory_idle_') for r in raw))
    require('Raw HTTP log contains no unexpected failures', raw and all(
        row['status'] < 400 or row['status'] == 404 or
        (row['method'], row['path'], row['status']) in allowed_errors for row in raw))
    require('Raw HTTP log includes expected denial and invalid-input cases',
            all(any(r['status'] == status for r in raw) for status in [400, 401, 404, 503]))
    for phase, count in [('mixed_5_accounts', 5), ('extended_10_accounts', 10)]:
        observed = {r['actor'] for r in raw if r['phase'] == phase and r['actor'].startswith('LocalActor')}
        # New actors are prepared between the two timed spans. Those setup
        # requests retain the preceding phase tag; the actual span above still
        # records only its concurrent load actors.
        require(phase + ' raw actor activity',
                {f'LocalActor{i:02}' for i in range(1, count + 1)} <= observed)
    # The original request latency summary includes explicit busy responses.
    # Recover semantic calls from the driver's sequential structure->search
    # operation pair, then remove the maximum possible count of fast busy calls.
    previous = {}; semantic_latencies = []
    for row in raw:
        before = previous.get(row['actor'])
        if (row['phase'] in ('mixed_5_accounts', 'extended_10_accounts') and
                row['path'] == '/api/search' and before and
                before['method'] == 'GET' and before['path'].endswith('/structure')):
            semantic_latencies.append(row['elapsed_ms'])
        previous[row['actor']] = row
    require('Raw semantic call count matches warm latency summary',
            len(semantic_latencies) == report['metrics']['semantic_warm']['count'])
    maximum_busy = sum(a['semantic_busy'] for a in actors)
    bound = successful_search_p95_upper_bound(semantic_latencies, maximum_busy)
    require('Successful semantic p95 conservative upper bound below 3 seconds', bound < 3000,
            {'upper_bound_ms': bound, 'raw_calls': len(semantic_latencies),
             'maximum_busy_calls_removed': maximum_busy})
    samples = json.loads((directory / 'memory.json').read_text())
    require('All memory samples sum actual owned process RSS', samples and all(
        row['rss_bytes'] == sum(row['processes'].values()) for row in samples))
    require('Actual sampled peak RSS within frozen bound',
            max(row['rss_bytes'] for row in samples) <= contract['peak_rss_bytes'])
    idle = [row for row in samples if row['phase'].startswith('memory_idle_')]
    require('All three idle rounds observed',
            {r['phase'] for r in idle} == {'memory_idle_1', 'memory_idle_2', 'memory_idle_3'})
    require('Raw idle RSS growth matches report and bound',
            idle[-1]['rss_bytes'] - idle[0]['rss_bytes'] == report['idle_rss_growth_bytes'] and
            report['idle_rss_growth_bytes'] <= contract['idle_growth_bytes'])
    for tag, bound in [('light', 1000), ('indexed_search', 3000), ('semantic_warm', 3000)]:
        require(tag + ' latency bound', report['metrics'][tag]['count'] > 0 and
                report['metrics'][tag]['p95_ms'] < bound)
    require('Actual running cancellation and bounded acknowledgement',
            report['operations']['cancel_running_confirmed'] > 0 and
            next(c for c in report['checks'] if c['name'] == 'Cancellation confirmations within 5 seconds')['passed'])
    fixture = json.loads((directory / 'fixture-final.json').read_text())
    require('Service shutdown and main-library invariants', fixture['server_stopped'] and
            fixture['main_unchanged'] and not fixture['shutdown_orphans_before_fallback'])
    network = directory / 'network-violations.jsonl'
    require('No recorded external connection attempt', not network.exists() or not network.read_text().strip())

    # Read only library records, never accounts.sqlite3 or session/credential data.
    markers = {a['marker'] for a in manifest['fixtures']}
    seen = set()
    source_checks = 0
    books = 0
    for path in sorted((directory / 'workspace/spaces').glob('*/library.sqlite3')):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            texts = [r[0] for r in db.execute('SELECT text FROM blocks')]
            present = {marker for marker in markers if any(marker in text for text in texts)}
            require(path.parent.name + ' has exactly one private account marker', len(present) == 1)
            marker = next(iter(present))
            require('Private marker belongs to one isolated space', marker not in seen)
            seen.add(marker)
            actor = next(a for a in manifest['fixtures'] if a['marker'] == marker)
            allowed = {actor['pdf_sha256'], manifest['shared']['sha256']}
            if actor['name'] == 'LocalActor01':
                allowed.add(manifest['book']['sha256'])
            for doc in db.execute('SELECT * FROM documents'):
                require('Document bytes and ownership ' + doc['id'], doc['sha256'] in allowed and
                        sha256(path.parent / 'originals' / doc['storage_name']) == doc['sha256'])
                if doc['sha256'] == manifest['book']['sha256']:
                    complete = db.execute('SELECT * FROM parse_runs WHERE document_id=? AND pages_done=541 '
                                          'AND failed_pages=0 AND text_pages=537 AND no_text_pages=4',
                                          (doc['id'],)).fetchall()
                    require('Isolated book has an actually completed full parse', bool(complete))
                    run = complete[-1]
                    pages = db.execute('SELECT page,status,text FROM pages WHERE run_id=? ORDER BY page',
                                       (run['id'],)).fetchall()
                    require('All 541 saved pages and four original blanks preserved',
                            [p['page'] for p in pages] == list(range(1, 542)) and
                            [p['page'] for p in pages if not p['text'].strip()] == manifest['book']['native_blank_pages'] and
                            all(p['status'] in ('parsed', 'no_extractable_text') for p in pages))
                    books += 1
            require('No orphaned or unfinished parse rows in completed workload',
                    db.execute("SELECT COUNT(*) FROM parse_runs WHERE status IN ('queued','running','failed')").fetchone()[0] == 0)
            for row in db.execute('SELECT payload FROM research_job_items WHERE item_key=?', ('fixed-source',)):
                item = json.loads(row['payload']);e = item['evidence']
                block = db.execute('SELECT b.*,r.document_id FROM blocks b JOIN parse_runs r ON r.id=b.run_id '
                                   'WHERE b.id=?', (e['block_id'],)).fetchone()
                require('Historical citation matches its own original block, version and coordinates',
                        item['marker'] == marker and block is not None and
                        block['run_id'] == e['run_id'] and block['document_id'] == e['document_id'] and
                        block['page'] == e['page'] and json.loads(block['bbox']) == e['bbox'] and
                        block['text'][e['char_start']:e['char_end']] == actor['historical_quote'] == e['text'])
                source_checks += 1
            require('Model call table remains empty', db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] == 0)
            for row in db.execute('SELECT payload FROM datasets'):
                value = row[0]
                require('Stored dataset has no foreign account content',
                        not any(m in value for m in markers if m != marker))
    require('Ten historical source records independently checked', seen == markers and source_checks == 10)
    require('Exactly one private book owner verified', books == 1)
    result = {'passed': True, 'run': str(directory), 'checks': checks,
              'historical_sources_checked': source_checks, 'requests_checked': len(raw),
              'browser_overlap_seconds': overlap}
    output = directory / ('independent-audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return output, result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    output, result = audit(args.directory)
    print(json.dumps({'passed': result['passed'], 'checks': len(result['checks']), 'output': str(output)}))
