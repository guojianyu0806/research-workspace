"""Actual local weights and frozen paper sources; development split only by default.

This phase tests the new index primitive. It does not claim the reader endpoint,
hybrid thresholds, document deduplication or all S3 gates have been accepted.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.library import Library
from research_workspace.multilingual_index import MultilingualIndex
from research_workspace.reader_search import ReaderSearch
from scripts.freeze_local_acceptance import main_snapshot, sha256
from scripts.freeze_multilingual_queries import normalized


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--split', choices=['development', 'held_out_source'], default='development')
    parser.add_argument('--reuse-artifact', type=Path, help='Copy a previous isolated public-paper workspace; never modify it')
    args = parser.parse_args()
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = '0'
    os.environ['RESEARCH_OCR_ENABLED'] = '0'
    output = ROOT / 'artifacts/multilingual-index' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    print(json.dumps({'output': str(output), 'split': args.split}), flush=True)
    manifest_path = ROOT/'tests/fixtures/multilingual/20261020-v1/manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    report = {'split': args.split, 'checks': [], 'errors': [], 'queries': [], 'external_connection_attempts': 0,
              'paid_model_calls': 0, 'scope': 'Actual paper index primitive; no reader API/hybrid or dedup quality claim.',
              'fixture_sha256': sha256(manifest_path), 'driver_sha256': sha256(Path(__file__)),
              'runtime_sha256': {name: sha256(ROOT/name) for name in ['research_workspace/multilingual_index.py',
                  'research_workspace/multilingual_model.py', 'research_workspace/library.py', 'research_workspace/reader_search.py']}}
    (output/'queries.jsonl').open('x', encoding='utf-8').close()
    def check(name, passed):
        report['checks'].append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)
    before = main_snapshot()
    job_id = None
    index = None
    original_connect = socket.socket.connect
    def blocked(sock, address, *rest, **kwargs):
        # asyncio on Windows creates a loopback socket pair, never a provider.
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1', 'localhost'}:
            return original_connect(sock, address, *rest, **kwargs)
        report['external_connection_attempts'] += 1
        raise RuntimeError('External connections forbidden during local acceptance')
    try:
        with patch.object(socket.socket, 'connect', blocked):
            if args.reuse_artifact:
                previous = args.reuse_artifact.resolve()
                assert previous.is_relative_to(ROOT/'artifacts/multilingual-index') and previous != output
                shutil.copytree(previous/'workspace', output/'workspace')
                report['reused_public_workspace'] = str(previous.relative_to(ROOT))
            library = Library(output/'workspace')
            documents = {}
            for filename, source in manifest['sources'].items():
                path = ROOT/source['path']
                check('frozen original '+filename, sha256(path) == source['sha256'])
                documents[filename] = library.import_document(path.read_bytes(), filename)['document']['id']
            while library.process_next_job():
                pass
            blocks, snapshots, state = ReaderSearch(library).snapshot()
            check('entire three-paper canonical snapshot', state == 'ready' and len(snapshots) == 3
                  and all(s['parse_status'] == 'completed' for s in snapshots))
            report['documents'], report['source_snapshots'] = documents, snapshots
            raw_before = main_snapshot(output/'workspace')
            facts = {f['id']: f for f in manifest['facts'] if f['split'] == args.split}
            expected = {}
            for fact_id, fact in facts.items():
                evidence = fact['evidence']
                matches = [b.block_id for b in blocks if b.document_id == documents[fact['source']]
                    and b.page == evidence['physical_page'] and normalized(evidence['anchor']) in normalized(b.text)]
                check('frozen source anchor resolves '+fact_id, len(matches) >= 1)
                expected[fact_id] = matches
            index = MultilingualIndex(library)
            started = time.monotonic()
            job = index.start_build(blocks)
            job_id = job['id']
            deadline = time.monotonic()+600
            while True:
                job = index.job_status(job_id)
                if job['status'] not in {'queued', 'building'} and not job['owned_live_thread']:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('Owned index build did not finish within local acceptance budget')
                time.sleep(.1)
            report['build_seconds'] = round(time.monotonic()-started, 3)
            report['build'] = job
            check('owned real background index completed', job['status'] == 'completed')
            print(json.dumps({'build_seconds': report['build_seconds'], 'chunks': job['detail']['chunks']}), flush=True)
            canonical = {b.block_id: b for b in blocks}
            for q in [q for q in manifest['queries'] if q['split'] == args.split]:
                started = time.monotonic()
                hits, metadata = asyncio.run(index.rank([q['query']], blocks, limit=20))
                check('exact source generation '+q['id'], metadata['status'] == 'applied'
                      and metadata['snapshot'] == job['detail']['snapshot'] and metadata['truncated_blocks'] == 0)
                for hit in hits:
                    original = canonical[hit.block_id]
                    check('canonical original '+q['id']+' '+hit.block_id,
                          hit.model_dump(exclude={'score'}) == original.model_dump(exclude={'score'}))
                    span = metadata['matches'][hit.block_id]
                    check('matched character span '+q['id']+' '+hit.block_id,
                          original.text[span['start']:span['end']] == span['chunk_text']
                          and hashlib.sha256(span['chunk_text'].encode()).hexdigest() == span['text_sha256'])
                report['queries'].append({'id': q['id'], 'language': q['language'], 'query': q['query'],
                    'expected_block_ids': expected[q['fact_id']],
                    'recall_at_5': any(h.block_id in expected[q['fact_id']] for h in hits[:5]),
                    'elapsed_seconds': round(time.monotonic()-started, 4),
                    'top20': [h.model_dump() for h in hits],
                    'matches': {h.block_id: metadata['matches'][h.block_id] for h in hits}})
                with (output/'queries.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(report['queries'][-1], ensure_ascii=False)+'\n')
                if len(report['queries']) % 16 == 0:
                    print(json.dumps({'queries': len(report['queries']), 'candidate_recall_at_5':
                        sum(r['recall_at_5'] for r in report['queries'])/len(report['queries'])}), flush=True)
            report['recall_at_5'] = {language: {'hits': sum(q['recall_at_5'] for q in report['queries'] if q['language'] == language),
                'total': sum(q['language'] == language for q in report['queries'])} for language in sorted({q['language'] for q in report['queries']})}
            check('all selected split questions evaluated', len(report['queries']) == (96 if args.split == 'development' else 64))
            check('all raw library rows and originals unchanged by indexing', raw_before == main_snapshot(output/'workspace'))
            check('no external connections attempted', report['external_connection_attempts'] == 0)
    except (Exception, KeyboardInterrupt) as error:
        report['errors'].append(type(error).__name__+': '+str(error))
    finally:
        if index and job_id:
            observed = index.job_status(job_id)
            if observed['owned_live_thread']:
                index.cancel(job_id)
                deadline = time.monotonic()+30
                while index.job_status(job_id)['owned_live_thread'] and time.monotonic() < deadline:
                    time.sleep(.1)
            report['owned_builder_stopped'] = not index.job_status(job_id)['owned_live_thread']
        report['main_unchanged'] = main_snapshot() == before
        report['runtime_unchanged'] = all(sha256(ROOT/name) == value for name, value in report['runtime_sha256'].items())
        report['passed'] = (not report['errors'] and len(report['queries']) == (96 if args.split == 'development' else 64)
            and all(c['passed'] for c in report['checks']) and report['main_unchanged'] and report['runtime_unchanged'] and report.get('owned_builder_stopped', False))
        # Recall remains a measured result, not an implicit pass gate. End-to-end
        # S3 quality gates also require hybrid thresholds and held-out evaluation.
        with (output/'report.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'output': str(output), 'invariants_passed': report['passed'], 'checks': len(report['checks']),
                          'errors': report['errors'], 'candidate_recall_at_5': report.get('recall_at_5')}, ensure_ascii=True), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
