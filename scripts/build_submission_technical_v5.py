"""Create the new technical DOCX with current field 5/6/7 instructions.

Use the bundled document Python. PDF export and page review follow separately.
Prior DOCX/PDF/source versions remain untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

import build_submission_documents as base

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', required=True, type=Path)
    args = parser.parse_args()
    draft = args.draft.resolve()
    if not draft.is_relative_to(ROOT / 'artifacts/submission-pack-v5') or draft.exists():
        raise ValueError('Use a new project-local technical draft directory')
    source = ROOT / 'docs/submission/20261031-v5/technical.md'
    prose = source.read_text(encoding='utf-8')
    assert '当前表单第5项' in prose and '此前300字AI创作说明对应旧表单' in prose
    draft.mkdir(parents=True, exist_ok=False)
    figure = draft / 'architecture.png'
    shutil.copy2(ROOT / 'docs/submission/20261031-v5/architecture.png', figure)
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
        header.text = '研序 可溯源科研助手 Agent 技术文档'
        footer = doc.sections[0].footer.paragraphs[0]
        footer.clear()
        footer.add_run('郭剑宇  |  2026年10月31日 v4  |  ')
        field = OxmlElement('w:fldSimple')
        field.set(qn('w:instr'), 'PAGE')
        footer._p.append(field)
        for paragraph in [header, footer]:
            for run in paragraph.runs:
                run.font.size = Pt(8)
                run.font.color.rgb = RGBColor(0, 0, 0)

    base.style_document = style_document
    stem = '研序_技术文档_郭剑宇_20261031_v4'
    docx = draft / (stem + '.docx')
    base.render_markdown(prose, docx, figure, '技术文档')
    with (draft / (stem + '.md')).open('x', encoding='utf-8') as stream:
        stream.write(prose)
    info = {'application_baseline': '064dc581c20f2357a9b8df786c0284af9565904d',
            'authoring_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'frozen_revision': 'Recorded in the final package manifest after local source commit',
            'source': source.relative_to(ROOT).as_posix(),
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'docx': docx.name, 'docx_sha256': hashlib.sha256(docx.read_bytes()).hexdigest(),
            'current_form_fields': [5, 6, 7], 'pdf_export_and_visual_review': 'pending separate execution',
            'new_model_calls': 0, 'old_finished_files_overwritten': []}
    with (draft / 'build-info.json').open('x', encoding='utf-8') as stream:
        json.dump(info, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'draft': str(draft), **info}, ensure_ascii=False))


if __name__ == '__main__':
    main()
