"""Frozen three-way context experiment on public papers, development only."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_workspace.contextual_retrieval import source_part, make_input, target_spans, validate_part, VARIANTS
from research_workspace.e5_model import LocalE5Encoder
from research_workspace.intelligence import IntelligenceService
from research_workspace.layout import LayoutService
from research_workspace.library import Library
from research_workspace.reader_search import ReaderSearch, STOP, query_variants
from scripts.freeze_local_acceptance import main_snapshot


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_sources(library, blocks):
    canonical = {b.block_id: b for b in blocks}
    titles, headings = {}, {}
    service = LayoutService(library)
    for did, rid in sorted({(b.document_id, b.run_id) for b in blocks}):
        summary = service.get_document_summary(did, rid)
        group = [b for b in blocks if b.document_id == did and b.run_id == rid]
        titles[did, rid] = []
        for candidate in summary['metadata_candidates']['title']:
            matches = []
            for b in group:
                if b.page != 1:
                    continue
                start = b.text.find(candidate['text'])
                if start >= 0 and b.text.find(candidate['text'], start+1) < 0:
                    matches.append(source_part(b, 'title_candidate', start, start+len(candidate['text'])))
            if len(matches) == 1:
                titles[did, rid].append(matches[0])
        headings[did, rid] = summary['headings']
    with library._connection() as db:
        order = {r['id']: (r['page'], r['ordinal']) for r in db.execute('SELECT id,page,ordinal FROM blocks')}
    return canonical, titles, headings, order


def contexts(target, canonical, titles, headings, order, span):
    group = [b for b in canonical.values() if b.document_id == target.document_id and b.run_id == target.run_id]
    before = [h for h in headings[target.document_id, target.run_id]
              if h['evidence']['block_id'] in order and order[h['evidence']['block_id']] <= order[target.block_id]
              and (h['evidence']['block_id'] != target.block_id or h['evidence']['char_start'] < span['end'])]
    heading_parts = []
    if before:
        current = max(before, key=lambda h: (order[h['evidence']['block_id']], h['evidence']['char_start']))
        by_id = {h['id']: h for h in before}
        chain = []
        for _ in range(3):
            chain.insert(0, current)
            if current.get('parent_id') not in by_id:
                break
            current = by_id[current['parent_id']]
        for h in chain:
            for e in [h.get('number_evidence'), h['evidence']]:
                if e is not None:
                    b = canonical[e['block_id']]
                    part = source_part(b, 'heading', e['char_start'], e['char_end'])
                    if part['text'] != e['text'] or b.document_id != target.document_id or b.run_id != target.run_id:
                        raise ValueError('Heading does not resolve to its source')
                    heading_parts.append(part)
    candidates = []
    for b in group:
        if b.page != target.page or order[b.block_id] >= order[target.block_id] or len(IntelligenceService._search_terms(b.text)) < 8:
            continue
        a, t = b.bbox, target.bbox
        width = min(a[2]-a[0], t[2]-t[0])
        overlap = max(0, min(a[2], t[2])-max(a[0], t[0]))
        if width > 0 and overlap/width >= .5 and a[1] < t[1] and -.01 <= t[1]-a[3] <= .20:
            candidates.append(b)
    previous = source_part(max(candidates, key=lambda b: order[b.block_id]), 'previous') if candidates else None
    return titles[target.document_id, target.run_id], heading_parts, previous


def main(previous):
    previous = Path(previous).resolve()
    assert previous.is_relative_to(ROOT/'artifacts/multilingual-index')
    accepted = json.loads((previous/'report.json').read_text(encoding='utf-8'))
    assert accepted['passed'] and accepted['encoder_kind'] == 'e5' and accepted['split'] == 'development'
    output = ROOT/'artifacts/context-retrieval'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    print(json.dumps({'output': str(output)}), flush=True)
    shutil.copytree(previous/'workspace', output/'workspace')
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = os.environ['RESEARCH_OCR_ENABLED'] = '0'
    policy_path = ROOT/'docs/development/150-context-retrieval-policy-20261021.json'
    fixture_path = ROOT/'tests/fixtures/multilingual/20261020-v1/manifest.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    report = {'scope': 'All frozen development questions; score canonical target blocks only, never additional context as another recalled result.',
              'policy_sha256': sha(policy_path), 'fixture_sha256': sha(fixture_path), 'driver_sha256': sha(Path(__file__)),
              'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'runtime_sha256': {p: sha(ROOT/p) for p in ['research_workspace/contextual_retrieval.py', 'research_workspace/e5_model.py',
                                                        'research_workspace/intelligence.py', 'research_workspace/layout.py', 'research_workspace/structure.py']},
              'variants': {}, 'checks': [], 'errors': [], 'external_attempts': 0, 'paid_model_calls': 0}
    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        assert condition, name
    def denied(*args, **kwargs):
        report['external_attempts'] += 1
        raise RuntimeError('Offline context experiment forbids networking')
    before, main_before = main_snapshot(output/'workspace'), main_snapshot()
    started = time.monotonic()
    try:
        with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied):
            library = Library(output/'workspace')
            blocks, snapshots, state = ReaderSearch(library).snapshot()
            check('entire three-paper source snapshot', state == 'ready' and len(snapshots) == 3)
            encoder = LocalE5Encoder()
            report['model'] = encoder.status()
            canonical, titles, headings, order = prepare_sources(library, blocks)
            report['title_parts'] = {did+':'+rid: parts for (did, rid), parts in titles.items()}
            questions = [q for q in fixture['queries'] if q['split'] == 'development']
            query_vectors = np.stack([encoder.encode([q['query']], query=True)[0] for q in questions])
            np.save(output/'query-vectors.npy', query_vectors, allow_pickle=False)
            expected = {q['id']: set(q['expected_block_ids']) for q in accepted['queries']}
            lexical = IntelligenceService._lexical_index(blocks)
            plan = []
            for b in blocks:
                for span in target_spans(b.text, encoder.tokenizer):
                    if not span['text'].strip():
                        continue
                    title, heading, previous = contexts(b, canonical, titles, headings, order, span)
                    plan.append((b, span, title, heading, previous))
            report['source_blocks'], report['target_spans'] = len(blocks), len(plan)
            for variant in VARIANTS:
                entries = [make_input(b, span, canonical, encoder.tokenizer, report['model']['fingerprint'], variant=variant,
                                      title=title, heading=heading, previous=previous) for b, span, title, heading, previous in plan]
                for entry in entries:
                    b = canonical[entry['target']['block_id']]
                    for p in entry['parts']:
                        validate_part(p, canonical, b)
                    check('exact source parts '+variant+' '+entry['cache_key'], entry['parts'][-1] == entry['target'] and len(encoder.tokenizer.encode('passage: '+entry['text']).ids) <= 512)
                with (output/(variant+'-inputs.json')).open('x', encoding='utf-8') as stream:
                    json.dump(entries, stream, ensure_ascii=False)
                unique = sorted({e['text'] for e in entries}, key=lambda t: (len(t), t))
                vectors = {}
                for offset in range(0, len(unique), 24):
                    batch = unique[offset:offset+24]
                    vectors.update(zip(batch, encoder.encode(batch)))
                    if offset % 480 == 0:
                        print(json.dumps({'variant': variant, 'encoded': min(offset+24, len(unique)), 'total': len(unique)}), flush=True)
                matrix = np.stack([vectors[e['text']] for e in entries])
                np.save(output/(variant+'-vectors.npy'), matrix, allow_pickle=False)
                run = {'queries': [], 'unique_inputs': len(unique), 'entries': len(entries)}
                report['variants'][variant] = run
                for q, vector in zip(questions, query_vectors):
                    scores = (matrix@vector[:, None]).max(axis=1)
                    best = {}
                    for entry, score in zip(entries, scores):
                        bid = entry['target']['block_id']
                        if bid not in best or float(score) > best[bid][0]:
                            best[bid] = (float(score), entry['cache_key'])
                    raw_ids = sorted(best, key=lambda bid: (-best[bid][0], canonical[bid].document_id, canonical[bid].page, bid))
                    quality_ids = sorted(best, key=lambda bid: (-best[bid][0]*IntelligenceService._evidence_quality(canonical[bid], q['query'], lexical[3]), bid))
                    query_parts, _ = query_variants(q['query'])
                    lexical_query = ' '.join(t for t in IntelligenceService._search_terms(' '.join(query_parts)) if t not in STOP)
                    lex = IntelligenceService._rank_lexical(lexical_query, blocks, lexical)
                    ranks = {}
                    for ids in [quality_ids[:300], [b.block_id for b in lex[:100]]]:
                        for i, bid in enumerate(ids, 1):
                            ranks[bid] = ranks.get(bid, 0)+1/(60+i)
                    hybrid_ids = sorted(ranks, key=lambda bid: (-ranks[bid], -best.get(bid, (-1, ''))[0], bid))
                    row = {'id': q['id'], 'language': q['language'], 'query': q['query'], 'expected_block_ids': sorted(expected[q['id']]), 'modes': {}}
                    for name, ids in [('raw', raw_ids), ('quality', quality_ids), ('hybrid', hybrid_ids)]:
                        row['modes'][name] = {'hit_at_5': bool(set(ids[:5]) & expected[q['id']]),
                                             'top20': [{'block_id': bid, 'cosine': best[bid][0], 'input_key': best[bid][1]} for bid in ids[:20]]}
                    run['queries'].append(row)
                    with (output/(variant+'-queries.jsonl')).open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                run['results'] = {m: {lang: {'hits': sum(q['modes'][m]['hit_at_5'] for q in run['queries'] if q['language'] == lang),
                                                   'total': sum(q['language'] == lang for q in run['queries'])}
                                          for lang in sorted({q['language'] for q in run['queries']})} for m in ['raw', 'quality', 'hybrid']}
                print(json.dumps({'variant': variant, 'hits': {m: sum(r['hits'] for r in scores.values()) for m, scores in run['results'].items()}}), flush=True)
    except (Exception, KeyboardInterrupt) as error:
        report['errors'].append(type(error).__name__+': '+str(error))
    report.update(workspace_unchanged=before == main_snapshot(output/'workspace'), main_unchanged=main_before == main_snapshot(),
                  runtime_unchanged=all(sha(ROOT/p) == h for p, h in report['runtime_sha256'].items()), elapsed_seconds=round(time.monotonic()-started, 3))
    report['invariants_passed'] = not report['errors'] and len(report['variants']) == 3 and all(len(v['queries']) == 96 for v in report['variants'].values()) and all(c['passed'] for c in report['checks']) and report['external_attempts'] == 0 and all(report[k] for k in ['workspace_unchanged', 'main_unchanged', 'runtime_unchanged'])
    report['quality_passed'] = {v: {m: report['invariants_passed'] and sum(r['hits'] for r in scores.values())/96 >= .9 and all(r['hits']/r['total'] >= .8 for r in scores.values()) for m, scores in value.get('results', {}).items()} for v, value in report['variants'].items()}
    with (output/'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'invariants_passed': report['invariants_passed'], 'quality_passed': report['quality_passed'], 'errors': report['errors']}), flush=True)
    return 0 if report['invariants_passed'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
