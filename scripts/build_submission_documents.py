"""Build editable submission documents from committed, reviewable prose.

Run using the bundled document Python. Draft folders are exclusive; no old
deliverable is overwritten. PDF conversion and visual review are separate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'docs/submission/20261027-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font(style, name, size, color='1C2833', bold=False):
    style.font.name = name
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor.from_string(color)
    style.font.bold = bold
    style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), name)


def architecture(path):
    im = Image.new('RGB', (1600, 570), 'white')
    draw = ImageDraw.Draw(im)
    face = 'C:/Windows/Fonts/msyh.ttc'
    title = ImageFont.truetype(face, 36)
    body = ImageFont.truetype(face, 32)
    nodes = [(30, ['浏览器工作台', '阅读 翻译 问答', '图谱 数据与绘图']),
             (430, ['FastAPI 接口', '账号与来源边界', '任务创建与查询']),
             (830, ['任务与领域工具', '解析 检索 生成 审核', '进度 取消 检查点']),
             (1230, ['数据与服务', 'SQLite 独立空间', '原PDF 本地向量 LLM'])]
    for x, lines in nodes:
        draw.rounded_rectangle((x, 50, x+340, 280), radius=16, fill='#EDF3F8', outline='#6A8BA3', width=3)
        for i, line in enumerate(lines):
            draw.text((x+22, 80+i*56), line, font=title if i == 0 else body, fill='#183A52')
        if x < 1200:
            draw.line((x+344, 166, x+390, 166), fill='#285D7E', width=5)
            draw.polygon([(x+390, 166), (x+377, 157), (x+377, 175)], fill='#285D7E')
    draw.line((195, 300, 195, 350, 1400, 350, 1400, 300), fill='#6A8BA3', width=3)
    draw.rounded_rectangle((140, 385, 1460, 510), radius=12, fill='#F6F8FA', outline='#A7B8C5', width=2)
    draw.text((175, 403), '固定来源对象贯穿各步骤', font=title, fill='#183A52')
    draw.text((175, 451), 'document_id   run_id   page   block_id   bbox   quote_id', font=body, fill='#334B5C')
    im.save(path)


def style_document(doc, kind):
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.72)
    section.left_margin = section.right_margin = Inches(.78)
    section.header_distance = section.footer_distance = Inches(.3)
    styles = doc.styles
    font(styles['Normal'], '宋体', 11.5)
    styles['Normal'].paragraph_format.line_spacing = 1.28
    styles['Normal'].paragraph_format.space_after = Pt(7)
    font(styles['Title'], '微软雅黑', 26, '000000', True)
    styles['Title'].paragraph_format.space_after = Pt(16)
    font(styles['Heading 1'], '微软雅黑', 15, '16384F', True)
    styles['Heading 1'].paragraph_format.space_before = Pt(17)
    styles['Heading 1'].paragraph_format.space_after = Pt(8)
    font(styles['Heading 2'], '微软雅黑', 12.5, '16384F', True)
    styles['Heading 2'].paragraph_format.space_before = Pt(11)
    styles['Heading 2'].paragraph_format.space_after = Pt(6)
    if kind == '录屏稿':
        styles['Normal'].paragraph_format.space_after = Pt(5)
        styles['Heading 1'].paragraph_format.space_before = Pt(12)
        styles['Heading 1'].paragraph_format.space_after = Pt(6)
    for style in ['Heading 1', 'Heading 2']:
        styles[style].paragraph_format.keep_with_next = True
    # The bundled default template can carry a title paragraph border. Strip
    # it from every inherited style as well as direct paragraph properties.
    for style in styles:
        for border in list(style.element.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    font(styles['List Bullet'], '宋体', 11.5)
    styles['List Bullet'].paragraph_format.space_after = Pt(5)
    header = section.header.paragraphs[0]
    header.text = '研序 科研助手 Agent  '+kind
    for run in header.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor.from_string('647484')
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('郭剑宇  |  2026年10月27日 v1  |  ')
    field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE')
    footer._p.append(field)
    for run in footer.runs:
        run.font.size = Pt(8)
    doc.core_properties.author = '郭剑宇'
    doc.core_properties.subject = '科研助手 Agent 本地参赛交付材料'


def add_table(doc, rows):
    table = doc.add_table(rows=1, cols=len(rows[0]))
    table.autofit = False
    widths = [1.4, 2.7, 2.8] if len(rows[0]) == 3 else [6.9/len(rows[0])]*len(rows[0])
    for i, width in enumerate(widths):
        table.columns[i].width = Inches(width)
    for row_index, values in enumerate(rows):
        row = table.rows[0] if row_index == 0 else table.add_row()
        no_split = OxmlElement('w:cantSplit'); row._tr.get_or_add_trPr().append(no_split)
        for i, value in enumerate(values):
            cell = row.cells[i]; cell.width = Inches(widths[i])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell.text = value
            props = cell._tc.get_or_add_tcPr()
            shade = OxmlElement('w:shd'); shade.set(qn('w:fill'), 'DDEAF3' if row_index == 0 else 'F4F7F9' if row_index%2 == 0 else 'FFFFFF'); props.append(shade)
            borders = OxmlElement('w:tcBorders')
            for edge in ['top', 'bottom', 'left', 'right', 'insideH', 'insideV']:
                item = OxmlElement('w:'+edge)
                for key, v in [('val', 'single'), ('sz', '5'), ('color', 'D9D9D9')]:
                    item.set(qn('w:'+key), v)
                borders.append(item)
            props.append(borders)
            margin = OxmlElement('w:tcMar')
            for edge, number in [('top', '80'), ('bottom', '80'), ('left', '95'), ('right', '95')]:
                item = OxmlElement('w:'+edge); item.set(qn('w:w'), number); item.set(qn('w:type'), 'dxa'); margin.append(item)
            props.append(margin)
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(2)
                p.paragraph_format.line_spacing = 1.16
                p.paragraph_format.keep_with_next = row_index == 0
                for run in p.runs:
                    run.font.size = Pt(10)
                    run.font.bold = row_index == 0
        if row_index == 0:
            repeat = OxmlElement('w:tblHeader'); row._tr.get_or_add_trPr().append(repeat)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def render_markdown(text, output, figure, kind):
    if output.exists():
        raise FileExistsError('Preserve prior document')
    doc = Document(); style_document(doc, kind)
    lines = text.splitlines(); index = 0; code = False
    while index < len(lines):
        line = lines[index].strip(); index += 1
        if not line:
            continue
        if line.startswith('```'):
            code = not code; continue
        if code:
            p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.line_spacing = 1
            run = p.add_run(line); run.font.name = 'Consolas'; run.font.size = Pt(9.5)
        elif line.startswith('![系统架构]'):
            doc.add_picture(str(figure), width=Inches(6.7))
        elif line.startswith('|'):
            rows = [[c.strip() for c in line.strip('|').split('|')]]
            while index < len(lines) and lines[index].strip().startswith('|'):
                cells = [c.strip() for c in lines[index].strip().strip('|').split('|')]; index += 1
                if all(re.fullmatch(r'[-: ]+', c) for c in cells):
                    continue
                rows.append(cells)
            add_table(doc, rows)
        elif line.startswith('# '):
            doc.add_paragraph(line[2:], 'Title')
            doc.core_properties.title = line[2:]
        elif line.startswith('## '):
            doc.add_paragraph(line[3:], 'Heading 1')
        elif line.startswith('### '):
            doc.add_paragraph(line[4:], 'Heading 2')
        else:
            value = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
            doc.add_paragraph(value[2:] if value.startswith('- ') else value, 'List Bullet' if value.startswith('- ') else None)
    for paragraph in doc.paragraphs:
        for border in list(paragraph._p.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    doc.save(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--regression', type=Path, required=True)
    args = parser.parse_args()
    draft, regression = args.draft.resolve(), args.regression.resolve()
    assert draft.is_relative_to(ROOT/'artifacts/submission-preparation-20261027')
    assert regression.is_relative_to(ROOT/'artifacts/regression-local')
    summary = json.loads((regression/'summary.json').read_text(encoding='utf-8'))
    test_manifest = json.loads((regression/'manifest.json').read_text(encoding='utf-8'))
    assert summary['passed'] and summary['tests'] == 1277 and summary['failures'] == summary['errors'] == summary['skipped'] == 0
    assert all(sha(ROOT/name) == value for name, value in test_manifest['source_sha256'].items()), 'Source differs from actual full regression'
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    draft.mkdir(parents=True, exist_ok=False)
    architecture(draft/'architecture.png')
    substitutions = {'{{SOURCE_REV}}': revision, '{{SOURCE_REV_SHORT}}': revision[:7], '{{TEST_COUNT}}': str(summary['tests']), '{{TEST_FILES}}': str(test_manifest['expected_files'])}
    for source, name, kind in [('technical.md', '01_技术文档_研序_郭剑宇_v1', '技术文档'), ('demo.md', '02_演示录屏稿_研序_郭剑宇_v1', '录屏稿')]:
        text = (SOURCE/source).read_text(encoding='utf-8')
        for key, value in substitutions.items():
            text = text.replace(key, value)
        assert '{{' not in text
        (draft/(name+'.md')).write_text(text, encoding='utf-8')
        render_markdown(text, draft/(name+'.docx'), draft/'architecture.png', kind)
    info = {'source_revision': revision, 'tested_revision': test_manifest['source_revision'],
            'test_report': regression.relative_to(ROOT).as_posix(), 'tests': summary['tests'], 'test_files': test_manifest['expected_files'],
            'current_tested_sources_match': True, 'author': '郭剑宇', 'project': '研序 可溯源科研助手 Agent',
            'scope': 'Local materials only; public repository, deployment and MP4 remain unfulfilled.'}
    (draft/'build-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'draft': str(draft), **info}, ensure_ascii=False))


if __name__ == '__main__':
    main()
