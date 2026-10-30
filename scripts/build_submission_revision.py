"""Create a dated technical document without replacing previous deliverables.

Use the Codex bundled document Python. PDF conversion and visual inspection
are separate stages. A historical regression is only used to verify hashes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

import build_submission_documents as base

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'docs/submission/20261030-v2/technical.md'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--draft', type=Path, required=True)
    args = parser.parse_args()
    draft = args.draft.resolve()
    assert draft.is_relative_to(ROOT/'artifacts/submission-revision-20261030')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    report = ROOT/'artifacts/regression-local/20261027T081810Z-c4f62f'
    manifest = json.loads((report/'manifest.json').read_text(encoding='utf-8'))
    summary = json.loads((report/'summary.json').read_text(encoding='utf-8'))
    changed = [name for name, value in manifest['source_sha256'].items()
               if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != value]
    assert not changed, changed
    assert summary['passed'] and summary['tests'] == 1277
    text = SOURCE.read_text(encoding='utf-8').replace('{{SOURCE_REV}}', revision).replace('{{SOURCE_REV_SHORT}}', revision[:7])
    assert '{{' not in text
    draft.mkdir(parents=True, exist_ok=False)
    figure = draft/'architecture.png'
    base.architecture(figure)
    old_style = base.style_document

    def style_v2(doc, kind):
        old_style(doc, kind)
        for name in ['Title', 'Heading 1', 'Heading 2', 'Header']:
            doc.styles[name].font.color.rgb = RGBColor(0, 0, 0)
        header = doc.sections[0].header.paragraphs[0]
        header.text = '研序 可溯源科研助手 Agent  技术文档'
        for run in header.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0, 0, 0)
        footer = doc.sections[0].footer.paragraphs[0]
        footer.clear()
        footer.add_run('郭剑宇  |  2026年10月30日 v2  |  ')
        field = OxmlElement('w:fldSimple')
        field.set(qn('w:instr'), 'PAGE')
        footer._p.append(field)
        for run in footer.runs:
            run.font.size = Pt(8)

    base.style_document = style_v2
    name = '研序_技术文档_郭剑宇_20261030_v2'
    (draft/(name+'.md')).write_text(text, encoding='utf-8')
    base.render_markdown(text, draft/(name+'.docx'), figure, '技术文档')
    info = {'source_revision': revision, 'document_source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'historical_regression_date': '2026-10-27', 'historical_tests': 1277,
            'historical_test_files': 97, 'historical_tested_revision': manifest['source_revision'],
            'current_application_and_test_files_hash_checked': len(manifest['source_sha256']),
            'changed_files': changed, 'current_full_regression_run': False, 'pro_calls': 0,
            'scope': 'Technical document revision; public deployment and current Pro quality remain unverified.'}
    (draft/'build-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(info, ensure_ascii=False))


if __name__ == '__main__':
    main()
