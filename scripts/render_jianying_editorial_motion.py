"""Render silent, data-grounded explainer motion for the approved Jianying video.

This is a local drawing/encoding tool, not application automation or model TTS.
One explicit scene per invocation. Default is a three-second sample, never the
whole film. Existing output folders are refused.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT/'artifacts/video-production-jianying-20261030-v1'
BOARD = ROOT/'docs/submission/video-redesign-20261030-v1/storyboard.json'
EVIDENCE = PRODUCTION/'media-preparation/evidence'
SCENES = ('S02','S05','S07','S09','S11','S18','S22','S23')
SCAN = ROOT/'artifacts/initial-equations/20261018T080641508898Z'
COMPARE = ROOT/'artifacts/model-retest-20261016-qa-compare-draft-accepted/compare.json'
W,H,FPS = 1920,1080,30
BG,INK,MUTED,BLUE,TEAL,AMBER = '#f5f7fc','#10233f','#566b85','#315bff','#087f86','#ad6a00'
FONT = Path('C:/Windows/Fonts/msyh.ttc')
BOLD = Path('C:/Windows/Fonts/msyhbd.ttc')
_fonts: dict[tuple[int,bool],ImageFont.FreeTypeFont] = {}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def font(size: int, bold=False):
    key=(size,bold)
    if key not in _fonts:
        _fonts[key]=ImageFont.truetype(str(BOLD if bold else FONT),size)
    return _fonts[key]


def rgb(color):
    return tuple(int(color[i:i+2],16) for i in (1,3,5))


def mix(color, alpha, background=BG):
    a=max(0.,min(1.,alpha));return tuple(round(b+(v-b)*a) for v,b in zip(rgb(color),rgb(background)))


def ease(p):
    p=max(0.,min(1.,p));return p*p*(3.-2.*p)


def enter(p,start,span=.11):
    return ease((p-start)/span)


class Frame:
    def __init__(self, scene, title, label):
        self.image=Image.new('RGB',(W,H),BG)
        self.draw=ImageDraw.Draw(self.image)
        self.boxes=[]
        self.text('研序  /  '+scene,96,52,600,30,bold=True,color=BLUE,metadata=True)
        self.text(label,960,52,864,28,color=MUTED,metadata=True)
        self.text(title,96,126,1728,56,bold=True)
        self.draw.line((96,222,1824,222),fill='#d7e1ef',width=2)

    def wrap(self,text,width,size,bold=False):
        f=font(size,bold);lines=[];current=''
        for token in re.findall(r'[A-Za-z0-9_./:+-]+|[^\x00-\x7f]|\s+|.',str(text)):
            if '\n' in token:
                lines.append(current.rstrip());current='';continue
            if current and self.draw.textlength(current+token,font=f)>width:
                lines.append(current.rstrip());current=''
            if self.draw.textlength(token,font=f)>width:
                for ch in token:
                    if current and self.draw.textlength(current+ch,font=f)>width:
                        lines.append(current);current=''
                    current+=ch
            else:current+=token
        if current:lines.append(current.rstrip())
        return lines

    def text(self,text,x,y,width,size=36,bold=False,color=INK,alpha=1.,leading=None,metadata=False):
        if alpha<=0:return y
        if not metadata and size<32:raise ValueError('Critical drawing text must be at least 32 px')
        leading=leading or round(size*1.42)
        for line in self.wrap(text,width,size,bold):
            bbox=self.draw.textbbox((x,y),line,font=font(size,bold),anchor='lt')
            if bbox[0]<80 or bbox[1]<32 or bbox[2]>1840 or bbox[3]>984:
                raise ValueError(f'Text outside safe bounds: {line}: {bbox}')
            self.draw.text((x,y),line,font=font(size,bold),fill=mix(color,alpha),anchor='lt')
            self.boxes.append({'text':line,'bbox':bbox,'size':size,'metadata':metadata})
            y+=leading
        return y

    def card(self,x,y,w,h,alpha=1.,fill='#ffffff',accent=None):
        if alpha<=0:return
        self.draw.rounded_rectangle((x,y,x+w,y+h),radius=22,fill=mix(fill,alpha),outline=mix('#d5deec',alpha),width=2)
        if accent:self.draw.rounded_rectangle((x,y,x+6,y+h),radius=3,fill=mix(accent,alpha))

    def arrow(self,x1,y1,x2,y2,progress=1.,color=BLUE,width=4):
        progress=max(0.,min(1.,progress))
        if progress<=0:return
        xe=x1+(x2-x1)*progress;ye=y1+(y2-y1)*progress
        self.draw.line((x1,y1,xe,ye),fill=color,width=width)
        angle=math.atan2(y2-y1,x2-x1);length=13
        points=[(xe,ye),(xe-length*math.cos(angle-.48),ye-length*math.sin(angle-.48)),(xe-length*math.cos(angle+.48),ye-length*math.sin(angle+.48))]
        self.draw.polygon(points,fill=color)

    def footer(self,text):
        self.draw.line((96,910,1824,910),fill='#d7e1ef',width=2)
        self.text(text,96,934,1728,28,color=MUTED,metadata=True)

    def source_image(self,path,x,y,width,alpha=1.):
        if alpha<=0:return
        with Image.open(path) as source:
            source=source.convert('RGB')
            source=source.resize((width,round(source.height*width/source.width)),Image.Resampling.LANCZOS)
        if y+source.height>980:raise ValueError('Source crop enters subtitle safe area')
        if alpha<1:source=Image.blend(Image.new('RGB',source.size,BG),source,alpha)
        self.image.paste(source,(x,y))


def load_data(scene=None):
    board=read(BOARD)
    data={'board':board,'shots':{s['id']:s for s in board['shots']}}
    for name,file in [('audit','S09-audit-case.json'),('events','S07-tool-events.json'),('filter','S18-filter-case.json')]:
        data[name]=read(EVIDENCE/file)
    assert data['audit']['before']['review']['verdict']=='unsupported'
    assert data['audit']['after']['review']['verdict']=='supported'
    ids=[c.get('quote_id') for c in data['audit']['after']['citation_metadata']]
    assert ids==['Q1','Q2','Q3','Q5'],ids
    assert [d['status'] for d in data['filter']['directions']]==['fully_addressed','partially_addressed','evidence_insufficient']
    assert [d['excluded'] for d in data['filter']['directions']]==[True,False,False]
    assert data['events']['job_status']=='completed'
    if scene=='S05':
        original=read(SCAN/'real-376-r0.json');erased=read(SCAN/'erased-first-x.json')
        first=original['formulas'][1];negative=erased['formulas'][1]
        assert first['id']==negative['id']=='scan-formula-38'
        assert first['multiline_audit']['source_row_count']==3
        assert first['initial_expression']['latex'].endswith('x')
        assert negative['initial_expression']['latex']==first['initial_expression']['latex'][:-1]
        assert first['needs_review'] and negative['needs_review']
        assert first['multiline_audit']['mathematical_equivalence_verified'] is False
        data['scan']={'original':original,'erased':erased,'first':first,'negative':negative}
    if scene=='S11':
        historical=read(COMPARE)
        assert historical['status']=='completed'
        message=historical['result']['message']
        exact='此外，所提供的片段未提供可引用的 Transformer 优化损失细节。'
        assert exact in message
        assert message==historical['result']['grounding']['response_coverage'][0]['response_message']
        data['compare']={'historical':historical,'exact_limitation':exact}
    return data


def s05_original(p,data):
    f=Frame('S05',data['shots']['S05']['title'],'解析样本对照 · 原图与真实候选')
    a=enter(p,.01)
    f.card(96,270,840,594,a,accent=BLUE)
    f.text('原扫描位置',128,300,776,38,True,color=BLUE,alpha=a)
    f.source_image(SCAN/'real-376-r0-initial-1.png',128,386,776,a)
    f.text('教材物理第376页 · 局部原图',128,582,776,34,True,alpha=a)
    f.text('288DPI栅格开发样本',128,643,776,32,color=MUTED,alpha=a)
    f.text('位置、文献版本与解析记录一起保留',128,718,776,32,color=MUTED,alpha=enter(p,.25))
    a=enter(p,.23)
    f.card(984,270,840,594,a,accent=TEAL)
    f.text('已保存的 LaTeX 候选（原样）',1016,300,776,36,True,color=TEAL,alpha=a)
    f.text(data['scan']['first']['initial_expression']['latex'],1016,383,776,32,color=INK,alpha=a,leading=46)
    a=enter(p,.54)
    f.text('起始式 + 2条等式续行，共3行',1016,653,776,34,True,color=TEAL,alpha=a)
    f.text('仍需原页复核 · 未验证数学等价',1016,731,776,32,color=AMBER,alpha=a)
    f.footer('历史公开教材开发样本 · 真实原图与解析导出对照')
    return f


def s05_negative(p,data):
    f=Frame('S05',data['shots']['S05']['title'],'解析样本对照 · 受控擦除负例')
    cards=[(96,'原始局部','real-376-r0-initial-1.png',BLUE),(984,'仅擦去末尾 x 的负例','erased-first-x-initial-1.png',AMBER)]
    for x,label,image_name,color in cards:
        f.card(x,270,840,340,accent=color)
        f.text(label,x+32,300,776,38,True,color=color)
        # The supplied negative crop ends before the erased glyph. Keep its
        # source-pixel scale identical to the original, rather than stretching
        # the shorter negative to fill the same width.
        image_width=776 if image_name.startswith('real-') else round(481*776/504)
        f.source_image(SCAN/image_name,x+32,400,image_width)
    a=enter(p,.10)
    f.card(96,652,1728,212,a,fill='#e9f4f3',accent=TEAL)
    f.text('真实候选没有补回 x',128,684,1640,44,True,color=TEAL,alpha=a)
    f.text('保留待核对状态；不从后续行反推缺失字符',128,766,1640,36,color=INK,alpha=a)
    # Focus the actual erased image boundary, not a reconstructed glyph.
    highlight=enter(p,.28)
    if highlight:
        f.draw.rounded_rectangle((1758,400,1802,538),radius=9,outline=mix(AMBER,highlight),width=4)
    f.footer('历史受控负例 · 擦除图、候选与审核记录分别保留')
    return f


def s05(p,data):
    # Two linked evidence beats rather than a single dense static plate.
    if p<.54:return s05_original(p/.54,data)
    second=s05_negative((p-.54)/.46,data)
    blend=enter(p,.54,.10)
    if blend<1:second.image=Image.blend(s05_original(1.,data).image,second.image,blend)
    return second


def s11(p,data):
    f=Frame('S11',data['shots']['S11']['title'],'历史比较导出解读 · 2026-10-16')
    a=enter(p,.01)
    f.card(96,286,540,407,a,accent=BLUE)
    f.text('已有回答范围',128,320,476,40,True,color=BLUE,alpha=a)
    f.text('模型结构',128,423,476,36,True,alpha=a)
    f.text('训练任务与预测内容',128,498,476,34,alpha=a)
    f.text('分别使用各自的原文引用',128,594,476,32,color=MUTED,alpha=a)
    f.arrow(668,485,742,485,enter(p,.20),TEAL)
    a=enter(p,.25)
    f.card(774,286,1050,407,a,fill='#fff8ec',accent=AMBER)
    f.text('本次检索片段的证据缺口',806,320,986,38,True,color=AMBER,alpha=a)
    f.text('“'+data['compare']['exact_limitation']+'”',806,419,986,40,True,alpha=a,leading=64)
    f.text('保留待补充标记',806,616,986,34,True,color=AMBER,alpha=enter(p,.47))
    a=enter(p,.62)
    f.text('缺少本次片段依据  ≠  原论文没有信息',159,787,1640,42,True,color=TEAL,alpha=a)
    f.footer('真实 compare.json · result.message 与覆盖审核记录保留同一缺口说明')
    return f


def s02(p,data):
    f=Frame('S02',data['shots']['S02']['title'],'研究目标说明 · 非软件界面')
    f.text('为可追溯的科研问答，准备研究材料',96,270,1728,46,bold=True,color=TEAL,alpha=enter(p,.02))
    cards=[('Transformer','结构与并行性','Attention Is All You Need'),('BERT','预训练与双向编码','Pre-training of Deep Bidirectional Transformers for Language Understanding'),('RAG','检索与外部知识','Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks')]
    for i,(name,role,title) in enumerate(cards):
        a=enter(p,.14+i*.11);y=378+round((1-a)*14);x=96+i*592
        f.card(x,y,544,292,a,accent=BLUE if i<2 else TEAL)
        f.text(name,x+28,y+28,488,44,True,color=BLUE,alpha=a)
        f.text(role,x+28,y+94,488,34,True,alpha=a)
        f.text(title,x+28,y+158,488,32,color=MUTED,alpha=a,leading=42)
    a=enter(p,.64)
    f.arrow(960,704,960,750,a,TEAL)
    f.text('有出处的比较   /   文献关系   /   待验证方案',242,787,1440,42,True,color=TEAL,alpha=a)
    f.footer('研究任务示意 · 多个真实样例分别保留来源')
    return f


def s07(p,data):
    f=Frame('S07',data['shots']['S07']['title'],'历史完成记录回看 · 非正在运行')
    f.text('回看一次已完成的真实任务',96,271,1728,38,True,color=TEAL)
    labels=[('下载原件','公开原文已保存'),('固定解析','解析版本已固定'),('检索准备','检索词准备'),('回答保存','回答已保存'),('支持与覆盖','逐条支持与覆盖检查')]
    for i,(label,detail) in enumerate(labels):
        x=96+i*352;a=enter(p,.10+i*.12);y=420+round((1-a)*14)
        f.card(x,y,320,278,a,accent=TEAL)
        f.text(str(i+1).zfill(2),x+26,y+24,268,36,True,color=TEAL,alpha=a)
        f.text(label,x+26,y+88,268,40,True,alpha=a)
        f.text(detail,x+26,y+164,268,28,color=MUTED,alpha=a,metadata=True,leading=38)
        f.text('历史已保存',x+26,y+232,268,28,color=TEAL,alpha=a,metadata=True)
        if i<4:f.arrow(x+326,y+139,x+347,y+139,enter(p,.20+i*.12),TEAL,3)
    f.text('关注实际工具、输出摘要与检查点',96,790,1728,42,True,color=BLUE,alpha=enter(p,.76))
    f.footer('历史任务 acbb2ec3 · 2026-10-17 · 已完成记录')
    return f


def s09(p,data):
    f=Frame('S09',data['shots']['S09']['title'],'任务导出对照 · 历史结果')
    left_a=enter(p,.02);left_y=295+round((1-left_a)*14)
    f.text('首次审核',96,left_y,410,40,True,color=MUTED,alpha=left_a)
    f.text('未通过',96,left_y+78,410,56,True,color=AMBER,alpha=left_a)
    f.text('引句缺少\n“循环模型”主语',96,left_y+190,410,42,True,alpha=left_a)
    center_a=enter(p,.22)
    f.text('补齐同条证据',666,295,600,40,True,color=TEAL,alpha=center_a)
    supporting=[('Q1','具名“循环模型”'),('Q2','隐藏状态依赖'),('Q3','并行与批处理限制'),('Q5','顺序计算根本约束')]
    for i,(qid,scope) in enumerate(supporting):
        a=enter(p,.25+i*.11);y=390+i*86+round((1-a)*12)
        f.card(666,y,600,70,a,accent=TEAL)
        f.text(qid,690,y+17,70,34,True,color=TEAL,alpha=a)
        f.text(scope,778,y+17,462,34,alpha=a)
    f.arrow(526,492,634,492,enter(p,.20),BLUE)
    f.arrow(1298,492,1386,492,enter(p,.71),TEAL)
    right_a=enter(p,.73);right_y=295+round((1-right_a)*14)
    f.text('再次审核',1416,right_y,408,40,True,color=MUTED,alpha=right_a)
    f.text('支持',1416,right_y+78,408,56,True,color=TEAL,alpha=right_a)
    f.text('四处原句\n共同支持\n完整声明',1416,right_y+188,408,42,True,alpha=right_a)
    f.text('Q1只支持主语；完整声明仍需其余支持片段',96,830,1728,36,True,color=BLUE,alpha=enter(p,.81))
    f.footer('历史任务 bbbf1c9c · 2026-10-16 · ce89267 · 任务导出对照')
    return f


def s18(p,data):
    f=Frame('S18',data['shots']['S18']['title'],'受控条件解释 · 非软件界面')
    f.text('先对照三个预设条件，再看原文覆盖范围',96,270,1728,40,True,color=TEAL,alpha=enter(p,.01))
    rows=[('充分覆盖','已报告的模型组合','线性插值与表2结果','移出重点候选',TEAL),
          ('部分覆盖','再加一个条件：\n无需训练任何模型','原文实际训练多个模型','保留，继续核验',BLUE),
          ('证据不足','跨句长上下文\n验证与定量改进','基准不适用于跨句依赖','保留，继续核验',AMBER)]
    for i,(status,condition,evidence,decision,color) in enumerate(rows):
        x=96+i*592;a=enter(p,.10+i*.10);y=368+round((1-a)*14)
        f.card(x,y,544,482,a,accent=color)
        f.text('条件 '+str(i+1),x+28,y+28,488,30,True,color=MUTED,alpha=a,metadata=True)
        f.text(condition,x+28,y+91,488,40,True,alpha=a)
        ev=enter(p,.36+i*.06)
        f.text(evidence,x+28,y+230,488,32,color=MUTED,alpha=ev)
        f.text(status,x+28,y+312,488,42,True,color=color,alpha=enter(p,.56+i*.06))
        f.text(decision,x+28,y+399,488,36,True,color=color,alpha=enter(p,.75+i*.025))
    f.footer('受控历史样例 766095e5 · 固定条件，非实时查新 · 69754c3')
    return f


def s22(p,data):
    f=Frame('S22',data['shots']['S22']['title'],'机制解说 · 非任务运行画面')
    rows=[('固定来源','文献、解析、页块'),('混合检索','稠密与稀疏检索'),('生成','按来源组织回答'),('本条审核','检查结论支持范围')]
    for i,(name,sub) in enumerate(rows):
        x=96+i*448;a=enter(p,.06+i*.14);y=385+round((1-a)*14)
        f.card(x,y,384,268,a,accent=BLUE if i<3 else TEAL)
        f.text(name,x+28,y+46,328,48,True,color=BLUE if i<3 else TEAL,alpha=a)
        f.text(sub,x+28,y+143,328,34,alpha=a)
        if i<3:f.arrow(x+394,y+134,x+438,y+134,enter(p,.17+i*.14),BLUE)
    a=enter(p,.67)
    f.arrow(1632,694,1632,755,a,TEAL)
    f.arrow(1632,755,288,755,a,TEAL)
    f.arrow(288,755,288,694,a,TEAL)
    f.text('模型组织语言；本地工具保存资料、位置和数据',96,824,1728,40,True,color=TEAL,alpha=enter(p,.77))
    f.footer('机制解说 · 系统架构来源：研序技术文档 c2e291b')
    return f


def s23(p,data):
    f=Frame('S23','把结论连回依据，让研究有据可查','作品收束')
    labels=['答案','自身引句','文献关系','写作草稿']
    for i,label in enumerate(labels):
        x=96+i*448;a=enter(p,.02+i*.09);y=295+round((1-a)*14)
        f.card(x,y,384,126,a,accent=BLUE if i<2 else TEAL)
        f.text(label,x+28,y+40,328,38,True,alpha=a)
    a=enter(p,.42)
    f.text('研序',96,516,1728,96,True,color=BLUE,alpha=a)
    f.text('可溯源科研助手 Agent',96,660,1728,48,True,alpha=a)
    f.text('郭剑宇  /  个人参赛  /  无指导老师',96,784,1728,36,color=MUTED,alpha=enter(p,.65))
    f.footer('Pro、官方资料与公网等验收仍待完成')
    return f


RENDER={'S02':s02,'S05':s05,'S07':s07,'S09':s09,'S11':s11,'S18':s18,'S22':s22,'S23':s23}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene',required=True,choices=SCENES)
    parser.add_argument('--duration',type=float,default=3.,help='Measured/assigned scene seconds; default is only a 3-second sample')
    parser.add_argument('--out',type=Path,required=True,help='New output directory inside the current production artifact scope')
    parser.add_argument('--stills-only',action='store_true',help='Render early/middle/final style keyframes, no video')
    args=parser.parse_args()
    if not math.isfinite(args.duration) or not .5<=args.duration<=120:
        raise ValueError('Scene duration must be between 0.5 and 120 seconds')
    out=args.out.resolve()
    if not out.is_relative_to(PRODUCTION.resolve()) or out.exists():
        raise ValueError('Use a NEW directory inside artifacts/video-production-jianying-20261030-v1')
    if not FONT.is_file() or not BOLD.is_file():raise ValueError('Installed Microsoft YaHei fonts required')
    data=load_data(args.scene);render=RENDER[args.scene]
    # Draw and bounds-check all style keys before creating any output directory.
    keys=[('early',.24),('middle',.59),('final',1.)]
    preview=[(name,progress,render(progress,data)) for name,progress in keys]
    out.mkdir(parents=True,exist_ok=False)
    for name,progress,frame in preview:frame.image.save(out/(name+'.png'))
    measured=None
    if not args.stills_only:
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise ValueError('Existing FFmpeg/FFprobe required')
        movie=out/(args.scene+'-editorial-motion.mp4')
        count=max(1,round(args.duration*FPS))
        # A genuine final reading hold is part of this explainer, not a repeated
        # storyboard used to pad the film. Full-duration scenes reserve 2-2.5s;
        # the default three-second style sample reserves only 0.75s.
        result_hold=min(2.5,args.duration*.25)
        active_seconds=args.duration-result_hold
        command=['ffmpeg','-hide_banner','-nostdin','-n','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','pipe:0','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(movie)]
        with (out/'ffmpeg.log').open('xb') as log:
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for i in range(count):
                    semantic_progress=min(1.,(i/FPS)/active_seconds)
                    process.stdin.write(render(semantic_progress,data).image.tobytes())
                process.stdin.close();code=process.wait()
            except BaseException:
                process.kill();process.wait();raise
        if code:raise RuntimeError('FFmpeg failed; inspect the project-local log')
        measured=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(movie)]))
        video=next(s for s in measured['streams'] if s['codec_type']=='video')
        assert video['width']==W and video['height']==H and video['r_frame_rate']=='30/1'
        assert abs(float(measured['format']['duration'])-count/FPS)<.05
        with (out/'ffprobe.json').open('x',encoding='utf-8') as stream:json.dump(measured,stream,ensure_ascii=False,indent=2)
    source_files=[BOARD,EVIDENCE/'S09-audit-case.json',EVIDENCE/'S07-tool-events.json',EVIDENCE/'S18-filter-case.json',Path(__file__).resolve()]
    if args.scene=='S05':source_files.extend(SCAN/name for name in ['real-376-r0.json','erased-first-x.json','real-376-r0-initial-1.png','erased-first-x-initial-1.png','results.json','final-acceptance.json'])
    if args.scene=='S11':source_files.append(COMPARE)
    metadata={'created_at':datetime.now(timezone.utc).isoformat(),'scene':args.scene,'duration_seconds_requested':args.duration,'fps':FPS,'pixels':[W,H],
              'kind':'editorial_data_explanation_not_application_UI','new_model_calls':0,'native_voice_generated_by_this_script':False,'silent_clip':True,
              'video_created':not args.stills_only,'is_three_second_sample':args.duration==3.,'no_existing_output_overwritten':True,
              'animation':'Semantic groups and relationships revealed in order, eased entry, then result hold; no fake product progress or model generation.',
              'planned_final_reading_hold_seconds':min(2.5,args.duration*.25),
              'subtitle_safe_region':'Bottom 100 pixels contain no core drawing information; source captions end above y=984.',
              'keyframe_progress':'Semantic reveal progress; it is not a fraction of the final encoded wall-clock duration.',
              'source_files':[{'file':p.relative_to(ROOT).as_posix(),'sha256':digest(p)} for p in source_files],
              'keyframes':[{'file':name+'.png','progress':progress,'text_bounds_checked':True,'sha256':digest(out/(name+'.png')),'text':frame.boxes} for name,progress,frame in preview]}
    if args.scene=='S09':
        metadata['historical_identity']={
            'job_id':data['audit']['job_id'],
            'before_claim_id':data['audit']['before']['claim_id'],
            'after_claim_id':data['audit']['after']['claim_id'],
            'after_quote_ids':[c['quote_id'] for c in data['audit']['after']['citation_metadata']],
            'identity_display':'Full identities retained here, not emphasized in the editorial frame.'}
    if args.scene=='S05':
        source=data['scan']['original'];negative=data['scan']['erased'];candidate=data['scan']['first']
        metadata['evidence_mapping']={
            'original_document_id':source['document_id'],'original_run_id':source['run_id'],
            'negative_document_id':negative['document_id'],'negative_run_id':negative['run_id'],
            'sample_pdf_page':source['page'],'source_textbook_physical_page':376,
            'formula_id':candidate['id'],'formula_bbox':candidate['bbox'],
            'coordinate_system':source['coordinate_system'],'original_candidate_latex':candidate['initial_expression']['latex'],
            'erased_candidate_latex':data['scan']['negative']['initial_expression']['latex'],
            'source_rows':candidate['multiline_audit']['source_row_count'],
            'needs_review':candidate['needs_review'],'mathematical_equivalence_verified':False,
            'controlled_negative':'Developer erased the final x. The actual candidate does not reconstruct x.',
            'crop_display':'Both supplied source crops displayed at the same source-pixel scale, without synthetic reconstruction.',
            'stages':'First original crop beside exact stored LaTeX; then original versus controlled erased crop and the preserved review state.'}
    if args.scene=='S11':
        metadata['historical_identity']={
            'job_id':data['compare']['historical']['id'],
            'exact_limitation_quote':data['compare']['exact_limitation'],
            'quote_paths':['result.message','result.grounding.response_coverage[0].response_message'],
            'field_correction':'The actual record has result.message; it has no result.limitations field.',
            'meaning':'Retrieval evidence gap in supplied snippets, not absence of the information from the source paper.'}
    with (out/'motion-manifest.json').open('x',encoding='utf-8') as stream:json.dump(metadata,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'scene':args.scene,'out':str(out),'video_created':not args.stills_only,'duration':args.duration,'frames':round(args.duration*FPS) if not args.stills_only else 0},ensure_ascii=False))


if __name__=='__main__':main()
