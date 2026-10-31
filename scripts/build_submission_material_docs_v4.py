"""Build new editable submission documents from the 2026-10-31 prose.

Run with the Codex bundled document Python after the document-operation marker.
This builder creates a new draft directory and refuses every prior output.
PDF export, per-page rendering and visual verification are separate stages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

import build_submission_documents as base

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/submission/20261031-v4'
BASELINE = '064dc581c20f2357a9b8df786c0284af9565904d'
VIDEO = ROOT / 'artifacts/video-real-ui-20261030-v5/final-v5-r2/研序_真实页面演示_剪映正常配音_v5.mp4'
EXPECTED_VIDEO_SHA = '869e3335c8f5144eba8a2854827d75051a586f42a84d8b51dc9069b884d72dd2'


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=Path, required=True)
    args = parser.parse_args()
    draft = args.draft.resolve()
    if not draft.is_relative_to(ROOT / 'artifacts') or draft.exists():
        raise ValueError('Choose a new draft directory under this project artifacts folder')
    prose = (SOURCE / 'technical.md').read_text(encoding='utf-8')
    statement = (SOURCE / 'ai-creation-statement.txt').read_text(encoding='utf-8')
    count = len(re.sub(r'\s', '', statement))
    if '{{' in prose or '{{' in statement or count > 300:
        raise ValueError('Unresolved document placeholder or overlong AI statement')
    if sha(VIDEO) != EXPECTED_VIDEO_SHA:
        raise ValueError('The user-selected v5 video changed')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    history = ROOT / 'artifacts/regression-local/20261027T081810Z-c4f62f'
    old_manifest = json.loads((history / 'manifest.json').read_text(encoding='utf-8'))
    old_summary = json.loads((history / 'summary.json').read_text(encoding='utf-8'))
    changed = [name for name, value in old_manifest['source_sha256'].items()
               if not (ROOT / name).is_file() or sha(ROOT / name) != value]
    draft.mkdir(parents=True, exist_ok=False)
    figure = draft / 'architecture.png'
    existing_figure = ROOT / 'deliverables/研序_技术文档与录制准备_20261030_v2/01_技术文档/architecture.png'
    shutil.copyfile(existing_figure, figure)
    original_style = base.style_document

    def style_document(doc, kind):
        original_style(doc, kind)
        doc.styles['Normal'].paragraph_format.space_after = Pt(6)
        for name in ['Title', 'Heading 1', 'Heading 2', 'Header']:
            doc.styles[name].font.color.rgb = RGBColor(0, 0, 0)
        doc.styles['Heading 1'].paragraph_format.space_before = Pt(15)
        doc.styles['Heading 1'].paragraph_format.space_after = Pt(7)
        doc.styles['Heading 2'].paragraph_format.space_before = Pt(10)
        doc.styles['Heading 2'].paragraph_format.space_after = Pt(5)
        header = doc.sections[0].header.paragraphs[0]
        header.text = '研序 可溯源科研助手 Agent  ' + kind
        for run in header.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0, 0, 0)
        footer = doc.sections[0].footer.paragraphs[0]
        footer.clear()
        footer.add_run('郭剑宇  |  2026年10月31日  |  ')
        field = OxmlElement('w:fldSimple')
        field.set(qn('w:instr'), 'PAGE')
        footer._p.append(field)
        for run in footer.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0, 0, 0)

    base.style_document = style_document
    specs = [
        ('研序_技术文档_郭剑宇_20261031_v3', prose, '技术文档'),
        ('研序_AI创作说明_郭剑宇_20261031_v2', '# ' + statement, 'AI创作说明'),
    ]
    outputs = []
    for name, text, kind in specs:
        markdown = draft / (name + '.md')
        with markdown.open('x', encoding='utf-8') as stream:
            stream.write(text)
        document = draft / (name + '.docx')
        base.render_markdown(text, document, figure, kind)
        outputs.append({'path': document.name, 'sha256': sha(document), 'bytes': document.stat().st_size})
    with (draft / '研序_AI创作说明_郭剑宇_20261031_v2.txt').open('x', encoding='utf-8') as stream:
        stream.write(statement)
    info = {
        'application_baseline': BASELINE,
        'document_authoring_head': revision,
        'package_frozen_revision': 'Recorded separately in the final package manifest; no self-referential hash',
        'technical_source_sha256': sha(SOURCE / 'technical.md'),
        'ai_statement_source_sha256': sha(SOURCE / 'ai-creation-statement.txt'),
        'ai_statement_characters_no_whitespace': count,
        'user_selected_video_sha256': EXPECTED_VIDEO_SHA,
        'historical_regression_date': '2026-10-27',
        'historical_tested_revision': old_manifest['source_revision'],
        'historical_tests': old_summary['tests'],
        'historical_test_files': old_manifest['expected_files'],
        'historical_manifest_sources_checked': len(old_manifest['source_sha256']),
        'historical_manifest_changed_files': changed,
        'new_full_regression_run': False,
        'new_model_calls': 0,
        'pro_retest': 'cancelled; corresponding quality scope remains unverified',
        'uploaded': False, 'deployed': False, 'pushed': False, 'submitted': False,
        'old_document_files_overwritten': [], 'outputs': outputs,
        'pdf_export_and_visual_review': 'pending separate execution',
    }
    with (draft / 'build-info.json').open('x', encoding='utf-8') as stream:
        json.dump(info, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'draft': str(draft), **info}, ensure_ascii=False))


if __name__ == '__main__':
    main()
