"""Render a silent 12-second editorial reading of three actual exported files.

No UI, browser, model, service or database actions. Download originals remain
untouched. This is an explanatory composition, not simulated product UI.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image
import render_jianying_editorial_motion as style

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT/'artifacts/video-production-jianying-20261030-v1/export-files-v1'
OUT = FILES/'S21-editorial-v1'
STEM = 'chart-01b5e66333214da8b13554bf33c79b78'
FPS, DURATION = 30, 12
DATA = json.loads((FILES/(STEM+'.json')).read_text(encoding='utf-8'))
VERIFIED = json.loads((FILES/'verification.json').read_text(encoding='utf-8'))
SVG = Image.open(FILES/'actual-svg-render-v1.png').convert('RGB')
SVG.thumbnail((944, 430), Image.Resampling.LANCZOS)


def base():
    f = style.Frame('S21', '把文件带走，继续编辑与复现', '真实导出文件 · 剪辑解读')
    f.footer('本次实际下载 · 原文件保留 · BLEU为论文报告值')
    return f


def file_cards(t):
    f = base()
    for i, (extension, title, detail, color) in enumerate([
        ('SVG', '图形文件', '可以继续编辑的矢量图', style.BLUE),
        ('Python', '固定模板脚本', '标准库即可生成同一张图', style.TEAL),
        ('JSON', '数据与转换记录', '原值、行序与配置一起保留', style.AMBER),
    ]):
        a = style.ease((t-i*.18)/.45)
        x, y = 96+i*584, 290+round((1-a)*18)
        f.card(x,y,560,550,alpha=a,accent=color)
        f.text(extension,x+32,y+45,496,64,True,color=color,alpha=a)
        f.text(title,x+32,y+170,496,38,True,alpha=a)
        f.text(detail,x+32,y+260,496,32,color=style.MUTED,alpha=a)
        suffix = '.py' if extension == 'Python' else '.'+extension.lower()
        f.text('chart-01b5e663…'+suffix,x+32,y+400,496,28,
               color=style.MUTED,alpha=a,metadata=True)
    return f


def chart_values(t):
    f = base()
    a = style.ease((t-2.95)/.4)
    f.card(96,270,1008,580,accent=style.BLUE)
    f.text('SVG：实际导出图形',128,300,944,38,True,color=style.BLUE)
    picture = SVG
    if a < 1:
        picture = Image.blend(Image.new('RGB',SVG.size,'white'),SVG,a)
    f.image.paste(picture,(128,386))
    f.card(1152,270,672,580,accent=style.TEAL)
    f.text('6行原值，逐项核对',1184,300,608,38,True,color=style.TEAL)
    labels = DATA['spec']['labels']
    values = DATA['spec']['series'][0]['original_values']
    for i, (label,value) in enumerate(zip(labels,values)):
        a = style.ease((t-3.3-i*.13)/.3)
        yy = 390+i*68
        f.text(label,1184,yy,440,32,alpha=a)
        f.text(f'{value:g}',1664,yy-2,128,38,True,color=style.TEAL,alpha=a)
    return f


def code_records(t):
    f = base()
    for x, color in [(96,style.BLUE),(984,style.TEAL)]:
        f.card(x,270,840,580,accent=color)
    f.text('Python：实际脚本首尾节选',128,300,776,36,True,color=style.BLUE)
    code = ['# Standalone Python 3; standard library only.',
            'import html','import math','import json','from pathlib import Path',
            '…', 'stream.write(render_chart(spec))']
    y = 382
    for line in code:
        y = f.text(line,128,y,776,32,color=style.INK,leading=43)
    f.text('已运行：产出SVG与下载文件一致',128,779,776,32,True,color=style.TEAL)
    f.text('JSON：实际转换记录',1016,300,776,36,True,color=style.TEAL)
    text = json.dumps(DATA['transformations'],ensure_ascii=False,indent=2)
    f.text(text,1016,382,776,32,color=style.INK,leading=40)
    return f


def render(seconds):
    if seconds < 3.25:
        a = file_cards(seconds)
        if seconds >= 2.95:
            b = chart_values(seconds)
            a.image = Image.blend(a.image,b.image,style.ease((seconds-2.95)/.3))
        return a
    if seconds < 7.65:
        a = chart_values(seconds)
        if seconds >= 7.3:
            b = code_records(seconds)
            a.image = Image.blend(a.image,b.image,style.ease((seconds-7.3)/.35))
        return a
    return code_records(seconds)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert VERIFIED['python_output_svg_byte_equal_to_download']
    assert len(DATA['spec']['labels']) == 6
    if OUT.exists():
        raise ValueError('Use a new version; existing output may not be overwritten')
    keys = [(2.,render(2.)),(5.5,render(5.5)),(10.5,render(10.5))]
    OUT.mkdir(exist_ok=False)
    for sec, f in keys:
        f.image.save(OUT/f'key-{sec:g}.png')
    movie = OUT/'S21-真实导出文件解读-1080p30-v1.mp4'
    command = ['ffmpeg','-hide_banner','-nostdin','-n','-f','rawvideo',
               '-pix_fmt','rgb24','-s','1920x1080','-r','30','-i','pipe:0',
               '-an','-c:v','libx264','-threads','2','-preset','fast','-crf','18',
               '-pix_fmt','yuv420p','-movflags','+faststart',str(movie)]
    with (OUT/'encoding.log').open('xb') as log:
        p = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=log,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for n in range(FPS*DURATION):
                p.stdin.write(render(n/FPS).image.tobytes())
            p.stdin.close()
            status = p.wait()
        except BaseException:
            p.kill();p.wait();raise
    assert status == 0
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(movie)]))
    stream = probe['streams'][0]
    assert stream['width'] == 1920 and stream['height'] == 1080
    assert stream['r_frame_rate'] == '30/1' and stream['nb_frames'] == '360'
    assert float(probe['format']['duration']) == 12
    with (OUT/'decode.log').open('xb') as log:
        check = subprocess.run(['ffmpeg','-hide_banner','-v','error','-i',str(movie),'-f','null','-'],stdout=log,stderr=log)
    assert check.returncode == 0 and (OUT/'decode.log').stat().st_size == 0
    sources = [FILES/(STEM+ext) for ext in ['.svg','.py','.json']]
    manifest = {'kind':'Actual downloaded-file editorial explanation, not product UI',
                'video':str(movie.relative_to(ROOT)),'duration':12,'fps':30,'frames':360,
                'pixels':[1920,1080],'silent_clip':True,'source_chart_id':DATA['id'],
                'sources':[{'path':str(s.relative_to(ROOT)),'sha256':sha(s)} for s in sources],
                'verification':str((FILES/'verification.json').relative_to(ROOT)),
                'source_values_from':'spec.series[0].original_values; labels unmodified',
                'json_excerpt':'The complete transformations object, line wrapping only',
                'python_excerpt':'Actual first imports and final stream.write; omission explicitly marked',
                'svg_display':'Actual exported SVG rendered with PyMuPDF; not a newly invented chart',
                'subtitle_safe':'Bottom 100px contains no core content',
                'model_calls':0,'services_started':0,'ui_actions':0,'old_files_overwritten':False,
                'full_decode_exit':0,'video_sha256':sha(movie),
                'keyframes':[{'source_second':sec,'text_boxes':frame.boxes} for sec,frame in keys]}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'ffprobe.json').write_text(json.dumps(probe,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'video':str(movie),'seconds':12,'frames':360,'verified':True},ensure_ascii=False))


if __name__ == '__main__':
    main()
