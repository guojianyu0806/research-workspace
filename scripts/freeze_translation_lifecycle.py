"""Freeze real source positions and synthetic mixed-text lifecycle contracts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot, sha256

DEFAULT = ROOT/'tests/fixtures/translation_lifecycle/20261020-v1'


def target_text(ordinal):
    label = '【本地联动夹具；非模型译文】'
    examples = [
        '本段用于检查长短译文联动、字符保留及阅读位置，不作为语义翻译质量证据。'*70,
        '短段。',
        '公式字面量：\n\\sum_{i=1}^{n} \\frac{a_i^2}{\\sqrt{b_i+c_i}} = y\nα β ε ∑ √ 𝑥₂\n'*12,
        '代码字面量：\n```python\nfor i in range(3):\n    print("<tag>", i)\n```\n'*15,
        '跨行文本 A\n\n段落 B\t原样保存。\n'*32,
        'Mixed text: 日本語 한국어 العربية. 数值0.001，-12.5，2026-10-20。'*25,
        '<img src="https://invalid.example/never" onerror="window.fixtureXss=1">\n<script>window.fixtureXss=2</script>',
        'long_identifier_'+'x'*1200,
    ]
    return label+'\n'+examples[ordinal % len(examples)]


def records(workspace, manifest):
    result = []
    database = Path(workspace)/'library.sqlite3'
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for frozen in manifest['source_records']:
            row = db.execute('''SELECT b.*,r.document_id FROM blocks b
                JOIN parse_runs r ON b.run_id=r.id WHERE b.id=?''', (frozen['block_id'],)).fetchone()
            assert row and row['document_id'] == manifest['document_id'] and row['run_id'] == manifest['run_id']
            assert hashlib.sha256(row['text'].encode()).hexdigest() == frozen['source_text_sha256']
            assert row['page'] == frozen['page'] and json.loads(row['bbox']) == frozen['bbox']
            target = target_text(frozen['ordinal'])
            assert hashlib.sha256(target.encode()).hexdigest() == frozen['target_text_sha256']
            result.append({'document_id': row['document_id'], 'run_id': row['run_id'], 'block_id': row['id'],
                'page': row['page'], 'bbox': json.loads(row['bbox']), 'source_text': row['text'], 'translated_text': target})
    return result


def freeze():
    assert not DEFAULT.exists(), 'Preserve earlier frozen versions'
    workspace = ROOT/'artifacts/multilingual-index/20261020T031149270684Z/workspace'
    with sqlite3.connect((workspace/'library.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        doc = db.execute('SELECT id,sha256,storage_name FROM documents WHERE filename=?', ('1706.03762v7.pdf',)).fetchone()
        rows = db.execute('''SELECT b.* FROM blocks b JOIN parse_runs r ON b.run_id=r.id
            WHERE r.document_id=? ORDER BY b.page,b.ordinal LIMIT 260''', (doc['id'],)).fetchall()
        assert len(rows) == 260 and len({r['run_id'] for r in rows}) == 1
    source = workspace/'originals'/doc['storage_name']
    assert sha256(source) == doc['sha256'] == sha256(ROOT/'data/samples/1706.03762v7.pdf')
    manifest = {'version': 'translation-lifecycle-20261020-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'public_workspace': workspace.relative_to(ROOT).as_posix(), 'raw_snapshot': main_snapshot(workspace),
        'document_id': doc['id'], 'run_id': rows[0]['run_id'], 'source_pdf_sha256': doc['sha256'],
        'source_records': [{'ordinal': i, 'block_id': row['id'], 'page': row['page'], 'bbox': json.loads(row['bbox']),
            'source_text_sha256': hashlib.sha256(row['text'].encode()).hexdigest(), 'source_chars': len(row['text']),
            'target_text_sha256': hashlib.sha256(target_text(i).encode()).hexdigest(), 'target_chars': len(target_text(i))}
            for i, row in enumerate(rows)],
        'cases': [
            {'id': 'valid', 'slice': [8, 208], 'variant': 'unchanged', 'mutation': 'prepend_records_0_3', 'expected': 'restore_same_unique_block'},
            {'id': 'legacy', 'slice': [8, 208], 'variant': 'missing_block_ids', 'mutation': 'prepend_records_0_3', 'expected': 'do_not_guess_old_ordinal'},
            {'id': 'duplicate', 'slice': [8, 208], 'variant': 'duplicate_at_137_from_10', 'mutation': 'prepend_records_0_3', 'expected': 'no_ambiguous_restore_or_navigation'},
            {'id': 'progress', 'slice': [8, 88], 'variant': 'saved_checkpoints', 'mutation': 'append_then_replace_then_cancel', 'expected': 'preserve_saved_content_and_unique_anchor'},
        ],
        'gates': {'max_dom_per_column': 120, 'max_settled_error_viewport': .1, 'paid_model_calls': 0,
                  'ambiguous_source_navigation': False, 'original_rows_and_pdfs_retained': True},
        'scope': 'Real existing PDF positions; synthetic saved translation geometry, literal math/code and lifecycle. Not Pro or language-quality acceptance.'}
    DEFAULT.mkdir(parents=True, exist_ok=False)
    with (DEFAULT/'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'path': str(DEFAULT), 'records': len(rows), 'cases': len(manifest['cases']),
                      'sha256': sha256(DEFAULT/'manifest.json')}), flush=True)


if __name__ == '__main__':
    freeze()
