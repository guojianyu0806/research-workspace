"""Build the short AI creation statement; use bundled document Python."""
import argparse
import hashlib
import json
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'docs/submission/20261029-pre-guidance-v1/ai-creation-statement.txt'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    output = parser.parse_args().output_dir.resolve()
    assert output.is_relative_to(ROOT), 'Output must remain in this project'
    lines = [line.strip() for line in SOURCE.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(lines) == 4, 'Expected title, author and two body paragraphs'
    all_count = len(''.join(''.join(lines).split()))
    body_count = len(''.join(''.join(lines[2:]).split()))
    assert all_count <= 300, 'The entire document, not only its body, must be within 300 characters'
    assert all(term in ''.join(lines[2:]) for term in ['Codex', 'DeepSeek', '提示词', '人工环节', '工作流'])
    output.mkdir(parents=True, exist_ok=False)
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = Inches(.9)
    section.bottom_margin = Inches(.85)
    section.left_margin = section.right_margin = Inches(.95)
    for style in doc.styles:
        if style.type == 1:
            style.font.color.rgb = RGBColor(0, 0, 0)
        for border in list(style.element.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    normal = doc.styles['Normal']
    normal.font.name = '宋体'
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), '宋体')
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(12)
    title = doc.styles['Title']
    title.font.name = '微软雅黑'
    title.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), '微软雅黑')
    title.font.size = Pt(22)
    title.font.bold = True
    title.font.color.rgb = RGBColor(0, 0, 0)
    title.paragraph_format.space_after = Pt(12)
    doc.add_paragraph(lines[0], 'Title')
    author = doc.add_paragraph(lines[1])
    author.paragraph_format.space_after = Pt(18)
    for run in author.runs:
        run.font.size = Pt(11)
    for value in lines[2:]:
        paragraph = doc.add_paragraph(value)
        paragraph.paragraph_format.first_line_indent = Pt(24)
    doc.core_properties.title = lines[0]
    doc.core_properties.author = '郭剑宇'
    doc.core_properties.subject = '预指导提交第6项 AI创作说明'
    path = output/'研序_AI创作说明_郭剑宇.docx'
    doc.save(path)
    saved = Document(path)
    assert [p.text for p in saved.paragraphs] == lines
    assert len(''.join(''.join(p.text for p in saved.paragraphs).split())) == all_count
    report = {'body_characters_including_punctuation_and_latin_letters': body_count,
              'entire_document_characters_excluding_whitespace': all_count,
              'max_characters': 300, 'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'filename': path.name, 'bytes': path.stat().st_size,
              'four_topics_covered': ['prompt design', 'AI tools', 'human feedback and optimization', 'human-AI workflow']}
    (output/'authoring-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
