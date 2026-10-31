"""Build a new field-6 introduction from reviewed prose and real UI frames.

Run with the Codex bundled document Python after the document-operation marker.
No PDF export, model calls, network, installation or prior-file replacement.
Final builds require the separately recorded, user-confirmed publication and
project-origin facts; --allow-draft explicitly marks an incomplete internal draft.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image

import build_submission_documents as base

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'artifacts/submission-pack-v5'
SOURCE = ROOT / 'docs/submission/20261031-v5/project-introduction.md'
BASELINE = '064dc581c20f2357a9b8df786c0284af9565904d'
VIDEO_SHA = '869e3335c8f5144eba8a2854827d75051a586f42a84d8b51dc9069b884d72dd2'
IMAGE_SPECS = {
    'reading': '01-reading.png',
    'qa': '02-qa.png',
    'comparison': '03-comparison.png',
    'translation': '04-translation.png',
    'framework': '05-framework.png',
    'chart_or_graph': '06-chart-or-graph.png',
}
PENDING = re.compile(r'\{\{|待确认|待填写|待图片复核|尚未确认|\bTBD\b', re.IGNORECASE)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_text_new(path: Path, value: str) -> None:
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(value)


def facts_status(path: Path | None, prose: str) -> tuple[bool, dict]:
    if path is None:
        return False, {'status': 'No facts record supplied'}
    path = path.resolve()
    public_facts = (SOURCE.parent / 'confirmed-facts.json').resolve()
    if not (path.is_relative_to(CASE) or path == public_facts) or not path.is_file() or path.suffix.lower() != '.json':
        raise ValueError('Facts must be the professional confirmed source record or a local package-case JSON')
    facts = read_json(path)
    fields = ['publication_and_awards', 'origin_and_improvements']
    confirmed = facts.get('confirmed_by_user') is True
    for field in fields:
        value = facts.get(field)
        if not isinstance(value, str) or not value.strip() or PENDING.search(value):
            confirmed = False
        elif value.strip() not in prose:
            raise ValueError(f'Authored prose differs from the separately confirmed fact: {field}')
    return confirmed, {
        'record': path.relative_to(ROOT).as_posix(),
        'sha256': sha(path),
        'confirmed_by_user': confirmed,
        'confirmation_date': facts.get('confirmation_date'),
        'scope': fields,
        'other_contest_history': 'Not inferred from publication or award statements',
    }


def reviewed_images(directory: Path) -> tuple[list[dict], Path]:
    directory = directory.resolve()
    allowed_source = SOURCE.parent / 'images'
    if not (directory.is_relative_to(CASE) or directory == allowed_source.resolve()):
        raise ValueError('Use the reviewed local screenshot directory or committed introduction images')
    manifest_path = directory / 'manifest.json'
    manifest = read_json(manifest_path)
    if manifest.get('selected_video_sha256') != VIDEO_SHA:
        raise ValueError('Screenshot provenance does not identify the user-selected v5 video')
    frames = manifest.get('frames')
    if not isinstance(frames, list):
        raise ValueError('Screenshot manifest needs a frames list')
    frame_map = {row.get('id'): row for row in frames if isinstance(row, dict)}
    if len(frame_map) != len(frames) or set(frame_map) != set(IMAGE_SPECS):
        raise ValueError('Need exactly the six reviewed, uniquely identified picture frames')
    records = []
    for image_id, filename in IMAGE_SPECS.items():
        frame = frame_map[image_id]
        if frame.get('file') != filename:
            raise ValueError(f'Unexpected screenshot filename for {image_id}')
        path = (directory / filename).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError('Screenshot paths must stay inside the reviewed image directory')
        actual_sha = sha(path)
        if frame.get('sha256', frame.get('image_sha256')) != actual_sha:
            raise ValueError(f'Screenshot hash differs from its provenance: {image_id}')
        with Image.open(path) as image:
            if image.format != 'PNG' or image.size != (1920, 1080):
                raise ValueError('Use unmodified 1920x1080 PNG frames from the selected video')
            image.verify()
        time = frame.get('source_time_seconds')
        if not isinstance(time, (int, float)) or not 0 <= time < 334:
            raise ValueError(f'Invalid source video time for {image_id}')
        if not isinstance(frame.get('caption'), str) or not frame['caption'].strip():
            raise ValueError(f'Missing factual screenshot description: {image_id}')
        records.append({'id': image_id, 'file': filename, 'sha256': actual_sha,
                        'source_time_seconds': time, 'caption': frame['caption']})
    return records, manifest_path


def style_document(doc: Document) -> None:
    base.style_document(doc, '作品介绍文档')
    doc.styles['Normal'].paragraph_format.line_spacing = 1.22
    doc.styles['Normal'].paragraph_format.space_after = Pt(6)
    for name in ['Title', 'Heading 1', 'Heading 2', 'Header']:
        doc.styles[name].font.color.rgb = RGBColor(0, 0, 0)
    for name in ['Heading 1', 'Heading 2']:
        doc.styles[name].paragraph_format.space_before = Pt(12)
        doc.styles[name].paragraph_format.space_after = Pt(6)
    header = doc.sections[0].header.paragraphs[0]
    header.text = '研序 可溯源科研助手 Agent  作品介绍文档'
    footer = doc.sections[0].footer.paragraphs[0]
    footer.clear()
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('郭剑宇  |  2026年10月31日  |  ')
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    footer._p.append(field)
    for paragraph in [header, footer]:
        for run in paragraph.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0, 0, 0)
    doc.core_properties.author = '郭剑宇'
    doc.core_properties.subject = '提交表单第6项七项内容；真实页面与实际验证范围'


def render(prose: str, output: Path, images: Path, draft_mode: bool) -> None:
    if output.exists():
        raise FileExistsError('Prior introduction document must be preserved')
    doc = Document()
    style_document(doc)
    if draft_mode:
        p = doc.add_paragraph('内部待确认稿，不用于上传。事实及图片须确认后重新生成正式版本。')
        p.runs[0].font.bold = True
    lines = prose.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        match = re.fullmatch(r'!\[([^\]]+)\]\(images/([^()]+)\)', line)
        if match:
            filename = match.group(2)
            if filename not in IMAGE_SPECS.values():
                raise ValueError('Document references an unreviewed picture')
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(5)
            paragraph.paragraph_format.space_after = Pt(3)
            paragraph.paragraph_format.keep_with_next = True
            paragraph.add_run().add_picture(str(images / filename), width=Inches(6.7))
        elif line.startswith('|'):
            rows = [[cell.strip() for cell in line.strip('|').split('|')]]
            while index < len(lines) and lines[index].strip().startswith('|'):
                cells = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
                index += 1
                if all(re.fullmatch(r'[-: ]+', cell) for cell in cells):
                    continue
                rows.append(cells)
            base.add_table(doc, rows)
        elif line.startswith('# '):
            doc.add_paragraph(line[2:], 'Title')
            doc.core_properties.title = line[2:]
        elif line.startswith('## '):
            p = doc.add_paragraph(line[3:], 'Heading 1')
            if line.startswith('## 5 '):
                p.paragraph_format.page_break_before = True
        elif line.startswith('### '):
            doc.add_paragraph(line[4:], 'Heading 2')
        else:
            value = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
            paragraph = doc.add_paragraph(value[2:] if value.startswith('- ') else value,
                                          'List Bullet' if value.startswith('- ') else None)
            if re.match(r'^图[1-6]\s', value):
                paragraph.paragraph_format.space_after = Pt(9)
                paragraph.paragraph_format.line_spacing = 1.05
                for run in paragraph.runs:
                    run.font.size = Pt(9.5)
    for paragraph in doc.paragraphs:
        for border in list(paragraph._p.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    doc.save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', required=True, type=Path)
    parser.add_argument('--facts', type=Path)
    parser.add_argument('--screenshots-dir', type=Path, default=SOURCE.parent / 'images')
    parser.add_argument('--allow-draft', action='store_true',
                        help='Create a visibly marked internal draft; never distribute it')
    args = parser.parse_args()
    output = args.draft.resolve()
    if not output.is_relative_to(CASE) or output.exists():
        raise ValueError('Choose a new, nonexistent document directory under the v5 package artifacts')
    prose = SOURCE.read_text(encoding='utf-8-sig')
    confirmed, facts = facts_status(args.facts, prose)
    incomplete = not confirmed or bool(PENDING.search(prose))
    if incomplete and not args.allow_draft:
        raise ValueError('Final authoring requires confirmed matching facts and fully reviewed prose')
    images, screenshot_manifest = reviewed_images(args.screenshots_dir)
    expected_refs = ['images/' + filename for filename in IMAGE_SPECS.values()]
    if any(prose.count(reference) != 1 for reference in expected_refs):
        raise ValueError('The prose must reference each of the six reviewed images exactly once')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    output.mkdir(parents=True, exist_ok=False)
    image_output = output / 'images'
    image_output.mkdir(exist_ok=False)
    for frame in images:
        source = args.screenshots_dir.resolve() / frame['file']
        destination = image_output / frame['file']
        shutil.copyfile(source, destination)
        if sha(destination) != frame['sha256']:
            raise ValueError('Copied image differs from the reviewed original')
    shutil.copyfile(screenshot_manifest, image_output / 'manifest.json')
    stem = '研序_作品介绍文档_郭剑宇_20261031_v1' + ('_内部待确认稿' if incomplete else '')
    markdown = output / (stem + '.md')
    markdown_prose = ('内部待确认稿，不用于上传。事实及图片须确认后重新生成正式版本。\n\n' + prose
                      if incomplete else prose)
    write_text_new(markdown, markdown_prose)
    document = output / (stem + '.docx')
    render(prose, document, image_output, incomplete)
    info = {
        'document_kind': 'Current full submission form field 6: seven-item project introduction',
        'final_facts_confirmed': confirmed, 'distributable': not incomplete,
        'application_baseline': BASELINE, 'authoring_head': revision,
        'package_frozen_revision': 'Recorded separately in the package manifest',
        'source': SOURCE.relative_to(ROOT).as_posix(), 'source_sha256': sha(SOURCE),
        'user_confirmed_facts': facts, 'reviewed_screenshot_manifest_sha256': sha(screenshot_manifest),
        'selected_video_sha256': VIDEO_SHA, 'screenshots': images,
        'real_model_evidence': 'Four actual 2026-10-30 Flash tasks; 35 HTTP 200 responses; scope remains limited',
        'translation_audit_scope': 'Markers, numbers, target language and source association; not per-fact semantic audit',
        'new_model_calls': 0, 'new_pro_calls': 0, 'new_full_regression_run': False,
        'pro_retest': 'Canceled by user; corresponding quality scope remains unverified',
        'prior_outputs_overwritten': [], 'uploaded': False, 'published': False, 'submitted': False,
        'outputs': [{'file': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path)}
                    for path in [markdown, document]],
        'pdf_export_page_count_under_100mb_and_visual_review': 'Pending separate root execution',
    }
    write_text_new(output / 'build-info.json', json.dumps(info, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'distributable': not incomplete,
                      'docx': document.name, 'pictures': len(images)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
