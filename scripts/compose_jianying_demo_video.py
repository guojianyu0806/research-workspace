"""Compose the approved 23-shot demo from real recordings and silent motion.

The default action only validates a JSON plan. No TTS, UI control, model call,
optical-flow interpolation, freeze padding, speed change or source overwrite.
Existing output files are always refused. Run --help for the explicit modes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
PRODUCTION=ROOT/'artifacts/video-production-jianying-20261030-v1'
BOARD=ROOT/'docs/submission/video-redesign-20261030-v1/storyboard.json'
DEFAULT_PLAN=PRODUCTION/'edit-plan-v1.json'
DEFAULT_OUT=PRODUCTION/'newfinal-v4'
FINAL_NAME='研序_科研主线演示_剪映配音_v4.mp4'
W,H,FPS=1920,1080,30
BG,INK,MUTED,BLUE,TEAL='#f5f7fc','#10233f','#566b85','#315bff','#087f86'
FONT=Path('C:/Windows/Fonts/msyh.ttc')
BOLD=Path('C:/Windows/Fonts/msyhbd.ttc')
# Raw source-pixel rectangles: x,y,width,height. These are not output masks.
RAW_1024_FORBIDDEN=[('desktop_pet',(760,0,200,125)),('codex_circle',(908,174,116,106))]
RAW_1854_FORBIDDEN=[('desktop_pet',(1590,0,200,125)),('codex_circle',(1748,174,106,106))]
LAYOUTS={'full_width':'full_width','center':'full_width','full':'full_width',
         'right':'right','split':'right','passthrough':'passthrough'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as f:
        result=hashlib.sha256()
        for block in iter(lambda:f.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def project_file(value):
    path=Path(value)
    if not path.is_absolute():path=ROOT/path
    path=path.resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError(f'Expected an existing file inside the project: {value}')
    return path


def write_new(path,value):
    with Path(path).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def run(command,log,cwd=None):
    with Path(log).open('xb') as f:
        result=subprocess.run(command,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError(f'Command failed ({result.returncode}); see {log}')


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],
                                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))


def overlap(a,b):
    x,y,w,h=a;xx,yy,ww,hh=b
    return max(x,xx)<min(x+w,xx+ww) and max(y,yy)<min(y+h,yy+hh)


def fixed_forbidden_regions(width,height):
    """Select the audited source-pixel map; never rescale the narrow map."""
    if (width,height)==(1024,866):return list(RAW_1024_FORBIDDEN)
    if (width,height)==(1854,866):return list(RAW_1854_FORBIDDEN)
    return []


def on_frame_grid(value):
    return abs(value*FPS-round(value*FPS))<1e-5


@dataclass
class Clip:
    path:Path
    start:float
    duration:float
    crop:tuple[int,int,int,int]|None
    layout:str
    label:str
    notes:list[str]
    source_probe:dict
    content_box:tuple[int,int,int,int]|None
    source_kind:str


def fitted(crop,layout,context_overview=False):
    area=(96,250,1728,680) if layout=='full_width' else (638,250,1186,680)
    x,y,w,h=area;_,_,cw,ch=crop
    factor=min(w/cw,h/ch)
    tw=max(2,int(cw*factor)//2*2);th=max(2,int(ch*factor)//2*2)
    minimum=.9 if context_overview else 1.25
    if factor<minimum:
        raise ValueError('UI crop would be too small. Crop the relevant facts more closely; do not shrink the whole workspace.')
    return (x+(w-tw)//2,y+(h-th)//2,tw,th)


def validate(plan_path,allow_partial=False):
    plan_path=project_file(plan_path);plan=read(plan_path);board=read(BOARD)
    shots={s['id']:s for s in board['shots']};items=plan.get('scenes',[])
    if not items:raise ValueError('Plan needs scenes')
    ids=[s['id'] for s in items]
    if len(set(ids))!=len(ids):raise ValueError('Scene ids must be unique')
    if not allow_partial and ids!=list(shots):raise ValueError('Final plan must contain the approved 23 scenes in order')
    if float(plan.get('duration_seconds',380))!=float(board['duration_seconds']):raise ValueError('Approved film duration is 380 seconds')
    cache={};prepared=[];report=[]
    for item in items:
        sid=item['id']
        if sid not in shots:raise ValueError(f'Unknown scene {sid}')
        shot=shots[sid];expected=float(shot['end']-shot['start'])
        if item.get('content_complete') is not True or not str(item.get('coverage_note','')).strip():
            raise ValueError(f'{sid}: declare content_complete:true and provide an evidence-specific coverage_note')
        if abs(float(item.get('duration',expected))-expected)>.0001:raise ValueError(f'{sid}: duration differs from approved script')
        clips=[]
        for n,entry in enumerate(item.get('clips',[]),1):
            name=f'{sid} clip {n}'
            for unsupported in ['freeze','hold','loop','tpad','time_scale','minterpolate','interpolate']:
                if unsupported in entry:raise ValueError(f'{name}: {unsupported} is forbidden')
            if float(entry.get('speed',1))!=1:raise ValueError(f'{name}: original playback speed is required')
            path=project_file(entry['path']);start=float(entry.get('start',0));duration=float(entry['duration'])
            if not all(math.isfinite(x) for x in [start,duration]) or start<0 or duration<=0 or not on_frame_grid(duration):
                raise ValueError(f'{name}: start/duration must be valid, and duration must lie on the 30fps frame grid')
            if path not in cache:cache[path]=probe(path)
            info=cache[path];videos=[x for x in info['streams'] if x['codec_type']=='video']
            if len(videos)!=1:raise ValueError(f'{name}: exactly one video stream required')
            video=videos[0];source_duration=float(video.get('duration',info['format']['duration']))
            if float(Fraction(video['avg_frame_rate']))<24:
                raise ValueError(f'{name}: source is below 24fps; low-rate screenshots cannot stand in for continuous recording')
            if start+duration>source_duration+.001:
                raise ValueError(f'{name}: source has only {source_duration-start:.3f}s remaining; requested {duration:.3f}s. Record/select more real footage.')
            try:layout=LAYOUTS[entry.get('layout','full_width')]
            except KeyError:raise ValueError(f'{name}: unknown layout') from None
            kind=entry.get('source_kind','editorial_motion' if layout=='passthrough' else 'continuous_ui')
            crop=None;box=None
            if layout=='passthrough':
                if (video['width'],video['height'])!=(W,H) or entry.get('crop') is not None:
                    raise ValueError(f'{name}: passthrough requires an uncropped 1920x1080 clip')
                if kind!='editorial_motion':raise ValueError(f'{name}: passthrough is for finished editorial motion only')
            else:
                value=entry.get('crop')
                if not isinstance(value,list) or len(value)!=4 or any(not isinstance(v,int) for v in value):
                    raise ValueError(f'{name}: provide explicit integer crop [x,y,width,height]')
                crop=tuple(value);x,y,w,h=crop
                if x<0 or y<0 or w<2 or h<2 or x+w>video['width'] or y+h>video['height']:
                    raise ValueError(f'{name}: crop is outside source pixels')
                forbidden=fixed_forbidden_regions(video['width'],video['height'])
                forbidden+= [(str(i),tuple(v)) for i,v in enumerate(entry.get('forbidden_regions',[]))]
                for label,rect in forbidden:
                    if overlap(crop,rect):raise ValueError(f'{name}: crop includes {label}; select a clean source rectangle instead of covering it')
                overview=entry.get('context_overview',False)
                if overview and (sid!='S20' or duration>5 or not entry.get('overview_reason')):
                    raise ValueError('Only the brief S20 whole-chart overview may use context scale; provide its reason and enlarge values separately')
                box=fitted(crop,layout,overview)
            label=str(entry.get('label',item.get('source_label',''))).strip()
            if not label:raise ValueError(f'{name}: a truthful short source label is required')
            notes=entry.get('notes',item.get('notes',[]))
            if not isinstance(notes,list) or any(not isinstance(x,str) for x in notes):raise ValueError(f'{name}: notes must be a list of text')
            clips.append(Clip(path,start,duration,crop,layout,label,notes,info,box,kind))
        if not clips or abs(sum(c.duration for c in clips)-expected)>.0001:
            raise ValueError(f'{sid}: real clips must fill exactly {expected:g}s; no freeze/loop padding is allowed')
        for clip in clips:
            if clip.layout!='passthrough':frame(item,shot,clip)
        prepared.append((item,shot,clips))
        report.append({'scene':sid,'duration_seconds':expected,'coverage_note':item['coverage_note'],
                       'clips':[{'path':c.path.relative_to(ROOT).as_posix(),'start':c.start,'duration':c.duration,'crop':c.crop,'layout':c.layout,
                                 'content_box':c.content_box,'source_kind':c.source_kind,
                                 'source_average_fps':next(v for v in c.source_probe['streams'] if v['codec_type']=='video')['avg_frame_rate'],
                                 'fixed_forbidden_regions':fixed_forbidden_regions(next(v for v in c.source_probe['streams'] if v['codec_type']=='video')['width'],next(v for v in c.source_probe['streams'] if v['codec_type']=='video')['height']),
                                 'conversion':'CFR30 by normal frame duplication/drop only; original speed; no AI interpolation'} for c in clips]})
    audio=None;captions=None
    if not allow_partial:
        audio=project_file(plan['narration']);captions=project_file(plan['subtitles'])
        if '剪映' not in str(plan.get('native_voice','')):raise ValueError('Native Jianying voice provenance label required')
        if abs(float(probe(audio)['format']['duration'])-380)>.04:raise ValueError('Native narration track must be 380s; fix the actual native audio assembly first')
        subtitle_text=captions.read_text(encoding='utf-8-sig')
        if captions.suffix.lower()!='.ass' or 'PlayResX: 1920' not in subtitle_text or 'PlayResY: 1080' not in subtitle_text:
            raise ValueError('Provide reviewed 1920x1080 ASS subtitles')
    result={'plan':str(plan_path),'plan_sha256':sha(plan_path),'validated_at':datetime.now(timezone.utc).isoformat(),
            'scene_count':len(items),'duration_seconds':sum(x['duration_seconds'] for x in report),'partial_plan':allow_partial,
            'scenes':report,'new_model_calls':0,'native_tts_generated_here':False,'source_speed_changed':False,
            'optical_flow_used':False,'freeze_padding_used':False,'desktop_overlays_excluded_by_crop':True}
    return plan,prepared,result,audio,captions


class Canvas:
    def __init__(self):
        self.image=Image.new('RGB',(W,H),BG);self.draw=ImageDraw.Draw(self.image);self.boxes=[]

    def text(self,text,x,y,width,size,bold=False,color=INK,line_limit=None):
        font=ImageFont.truetype(str(BOLD if bold else FONT),size)
        lines=[];current=''
        for token in re.findall(r'[A-Za-z0-9_./:+-]+|[^\x00-\x7f]|\s+|.',str(text)):
            if '\n' in token:
                lines.append(current);current='';continue
            if current and self.draw.textlength(current+token,font=font)>width:
                lines.append(current);current=''
            if self.draw.textlength(token,font=font)>width:raise ValueError(f'Unbreakable token too wide: {token}')
            current+=token
        if current:lines.append(current)
        if line_limit and len(lines)>line_limit:raise ValueError(f'Text must be shortened for frame: {text}')
        for line in lines:
            bbox=self.draw.textbbox((x,y),line,font=font,anchor='lt')
            if bbox[0]<80 or bbox[2]>1840 or bbox[3]>976:raise ValueError(f'Text leaves safe area: {line}')
            self.draw.text((x,y),line,font=font,fill=color,anchor='lt')
            self.boxes.append({'text':line,'bbox':bbox,'size':size})
            y+=round(size*1.45)
        return y


def frame(item,shot,clip):
    f=Canvas()
    f.text(shot['chapter'],96,48,1100,28,True,BLUE,1)
    f.text('研序  /  '+shot['id'],1460,48,364,28,True,MUTED,1)
    f.text(shot['title'],96,108,1728,52,True,INK,1)
    explanation=str(item.get('explanation','真实界面实录 · 保留当前任务的原文依据'))
    f.text(explanation,96,194,1728,32,False,TEAL,1)
    f.draw.rounded_rectangle((80,238,1840,932),radius=22,fill='#ffffff',outline='#d5deec',width=2)
    if clip.layout=='right':
        f.draw.rounded_rectangle((96,250,598,920),radius=15,fill='#eaf0ff')
        f.text('剪辑说明',124,280,446,32,True,BLUE,1)
        y=354
        if not clip.notes:raise ValueError(f'{shot["id"]}: right layout needs short, truthful editorial notes')
        for note in clip.notes:
            y=f.text(note,124,y,446,34,False,INK)+35
        if y>920:raise ValueError('Too many editorial notes')
    f.text(clip.label,96,940,1728,28,False,MUTED,1)
    return f


def preview(item,shot,clip,out,offset=0):
    if clip.layout=='passthrough':raise ValueError('Preview is intended for a framed real-UI crop')
    if not 0<=offset<clip.duration:raise ValueError('Preview offset must lie inside selected clip')
    out.mkdir(parents=True,exist_ok=False)
    raw=out/'source-frame.png'
    run(['ffmpeg','-hide_banner','-nostdin','-v','error','-n','-ss',str(clip.start+offset),'-i',str(clip.path),'-frames:v','1',str(raw)],out/'frame-extract.log')
    canvas=frame(item,shot,clip)
    x,y,w,h=clip.crop;ox,oy,ow,oh=clip.content_box
    with Image.open(raw) as image:cropped=image.crop((x,y,x+w,y+h)).convert('RGB').resize((ow,oh),Image.Resampling.LANCZOS)
    canvas.image.paste(cropped,(ox,oy))
    canvas.image.save(out/(shot['id']+'-composition-preview.png'))
    write_new(out/'preview-manifest.json',{'scene':shot['id'],'source':clip.path.relative_to(ROOT).as_posix(),
        'source_seconds':clip.start+offset,'source_crop':clip.crop,'content_box':clip.content_box,'text':canvas.boxes,
        'kind':'Real recording crop with editorial frame; not fabricated software UI','sha256_source_video':sha(clip.path),
        'desktop_overlays_excluded_by_crop':True,'new_model_calls':0})


def encode_clip(item,shot,clip,n,out):
    target=out/f'{shot["id"]}-clip-{n:02d}.mp4';log=out/f'{shot["id"]}-clip-{n:02d}.log'
    command=['ffmpeg','-hide_banner','-nostdin','-n']
    if clip.layout=='passthrough':
        command+=['-ss',str(clip.start),'-t',str(clip.duration),'-i',str(clip.path),'-an','-vf','setpts=PTS-STARTPTS,fps=30,format=yuv420p']
    else:
        background=out/f'{shot["id"]}-clip-{n:02d}-frame.png'
        frame(item,shot,clip).image.save(background)
        x,y,w,h=clip.crop;ox,oy,ow,oh=clip.content_box
        command+=['-loop','1','-framerate','30','-i',str(background),'-ss',str(clip.start),'-t',str(clip.duration),'-i',str(clip.path),
                  '-filter_complex',f'[1:v]setpts=PTS-STARTPTS,crop={w}:{h}:{x}:{y}:exact=1,scale={ow}:{oh}:flags=lanczos,fps=30[ui];[0:v][ui]overlay={ox}:{oy}:shortest=1,format=yuv420p[final]',
                  '-map','[final]','-an']
    command+=['-t',str(clip.duration),'-r','30','-c:v','libx264','-preset','fast','-crf','18','-threads','2','-pix_fmt','yuv420p','-video_track_timescale','15360','-movflags','+faststart',str(target)]
    run(command,log)
    verify_video(target,clip.duration)
    return target


def verify_video(path,duration):
    info=probe(path);v=next(s for s in info['streams'] if s['codec_type']=='video')
    if (v['width'],v['height'],v['avg_frame_rate'])!=(W,H,'30/1') or int(v.get('nb_frames',0))!=round(duration*30):
        raise ValueError(f'Encoded frame size/rate/count mismatch: {path}')
    if abs(float(info['format']['duration'])-duration)>.05:raise ValueError(f'Encoded duration mismatch: {path}')
    return info


def concatenate(files,target,log,listfile,cwd):
    with listfile.open('x',encoding='utf-8',newline='\n') as f:
        for file in files:
            relative=file.relative_to(cwd).as_posix()
            if "'" in relative:raise ValueError('Generated concat path contains unexpected quote')
            f.write(f"file '{relative}'\n")
    run(['ffmpeg','-hide_banner','-nostdin','-n','-f','concat','-safe','1','-i',listfile.name,'-c','copy','-an','-movflags','+faststart',target.name],log,cwd)


def render_scenes(prepared,report,out,reuse_from=None):
    out.mkdir(parents=True,exist_ok=False)
    write_new(out/'validated-plan.json',report)
    shutil.copyfile(report['plan'],out/'frozen-edit-plan.json')
    shutil.copyfile(__file__,out/'composer-source-snapshot.py')
    previous={}
    if reuse_from:
        previous={s['id']:s for s in read(reuse_from/'frozen-edit-plan.json')['scenes']}
    for item,shot,clips in prepared:
        directory=out/shot['id'];directory.mkdir()
        if previous.get(shot['id'])==item:
            source=reuse_from/shot['id']/(shot['id']+'.mp4')
            old=read(source.parent/'scene-manifest.json')
            if old['output_sha256']!=sha(source):raise ValueError('Reuse source hash mismatch')
            verify_video(source,float(shot['end']-shot['start']))
            target=directory/source.name
            shutil.copyfile(source,target)
            write_new(directory/'scene-manifest.json',{**old,'plan_sha256':report['plan_sha256'],
                      'reused_from':str(source.relative_to(ROOT)),
                      'reused_original_plan_sha256':old['plan_sha256'],
                      'reuse_basis':'Exact scene item equality and verified original output SHA-256; no picture change'})
            continue
        parts=[encode_clip(item,shot,c,i,directory) for i,c in enumerate(clips,1)]
        target=directory/(shot['id']+'.mp4')
        concatenate(parts,target,directory/'concat.log',directory/'clips.ffconcat',directory)
        verify_video(target,float(shot['end']-shot['start']))
        write_new(directory/'scene-manifest.json',{'scene':shot['id'],'duration':shot['end']-shot['start'],
                  'content_complete':True,'coverage_note':item['coverage_note'],'plan_sha256':report['plan_sha256'],
                  'source_sha256':[sha(c.path) for c in clips],'output_sha256':sha(target),
                  'normal_cfr_conversion_only':True,'new_model_calls':0})


def assemble(prepared,report,audio,captions,out):
    if len(prepared)!=23:raise ValueError('Cannot assemble a partial plan')
    frozen=out/'frozen-edit-plan.json'
    if not frozen.is_file() or sha(frozen)!=report['plan_sha256']:
        raise ValueError('Rendered scenes must match the exact frozen edit plan')
    files=[]
    for item,shot,clips in prepared:
        source=out/shot['id']/(shot['id']+'.mp4')
        verify_video(source,float(shot['end']-shot['start']))
        manifest=read(source.parent/'scene-manifest.json')
        if manifest['plan_sha256']!=report['plan_sha256'] or manifest['output_sha256']!=sha(source):
            raise ValueError('Scene manifest mismatch')
        files.append(source)
    silent=out/'assembled-silent-380s.mp4'
    concatenate(files,silent,out/'assemble-silent.log',out/'scenes.ffconcat',out)
    verify_video(silent,380.)
    local_ass=out/'captions.ass'
    if local_ass.exists():raise ValueError('Refusing to overwrite captions')
    shutil.copyfile(captions,local_ass)
    final=out/FINAL_NAME
    run(['ffmpeg','-hide_banner','-nostdin','-n','-i',silent.name,'-i',str(audio),'-map','0:v:0','-map','1:a:0',
         '-vf','ass=filename=captions.ass','-t','380','-r','30','-c:v','libx264','-preset','fast','-crf','18','-threads','2',
         '-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',final.name],out/'final-encode.log',out)
    verify_video(final,380.)
    run(['ffmpeg','-hide_banner','-nostdin','-v','error','-i',str(final),'-f','null','-'],out/'final-full-decode.log')
    write_new(out/'final-manifest.json',{'file':FINAL_NAME,'duration_seconds':380,'pixels':[W,H],'fps':30,
              'sha256':sha(final),'plan_sha256':report['plan_sha256'],'narration_sha256':sha(audio),
              'subtitles_sha256':sha(captions),'voice_provenance':'Native Jianying voice supplied by the producer, not generated by this script',
              'full_decode_passed':True,'new_model_calls':0,'optical_flow_used':False,'old_finished_files_overwritten':False})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=DEFAULT_PLAN)
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--reuse-from',type=Path,help='Reuse verified unchanged scene items from a prior local render; never overwrite it')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--validate',action='store_true',help='Validate only; the default')
    modes.add_argument('--preview',metavar='SCENE',help='Write a new PNG composition from the first selected real clip')
    modes.add_argument('--smoke',metavar='SCENE',help='Encode only two seconds from the first clip as a technical smoke check, never a full scene')
    modes.add_argument('--render-scenes',action='store_true',help='Explicitly render all planned silent scenes into a new output folder')
    modes.add_argument('--assemble',action='store_true',help='Assemble previously rendered matching scenes, native voice and ASS')
    modes.add_argument('--render',action='store_true',help='Explicitly render all scenes and the complete 380s final film')
    parser.add_argument('--allow-partial',action='store_true',help='Only for validation/preview or isolated scene rendering; never final assembly')
    parser.add_argument('--preview-offset',type=float,default=0)
    args=parser.parse_args()
    if args.allow_partial and (args.render or args.assemble):parser.error('A partial plan cannot render or assemble a complete film')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise ValueError('Existing local FFmpeg and FFprobe are required')
    if not FONT.is_file() or not BOLD.is_file():raise ValueError('Installed Microsoft YaHei fonts required')
    out=args.out.resolve()
    if not out.is_relative_to(PRODUCTION.resolve()):raise ValueError('Outputs must stay inside the new production directory')
    plan,prepared,report,audio,captions=validate(args.plan,args.allow_partial)
    reuse=args.reuse_from.resolve() if args.reuse_from else None
    if reuse and (not reuse.is_relative_to(PRODUCTION.resolve()) or not (reuse/'frozen-edit-plan.json').is_file()):
        raise ValueError('Reuse must name a previous render inside the new production directory')
    if args.preview:
        selected=next((v for v in prepared if v[1]['id']==args.preview),None)
        if selected is None:raise ValueError('Scene not found in plan')
        preview(selected[0],selected[1],selected[2][0],out,args.preview_offset)
    elif args.smoke:
        from dataclasses import replace
        selected=next((v for v in prepared if v[1]['id']==args.smoke),None)
        if selected is None:raise ValueError('Scene not found in plan')
        out.mkdir(parents=True,exist_ok=False)
        clip=replace(selected[2][0],duration=min(2.,selected[2][0].duration))
        movie=encode_clip(selected[0],selected[1],clip,1,out)
        run(['ffmpeg','-hide_banner','-nostdin','-v','error','-i',str(movie),'-f','null','-'],out/'smoke-full-decode.log')
        write_new(out/'smoke-report.json',{'scope':'Two-second encoder/crop/frame smoke only; not a complete scene or film',
                  'scene':args.smoke,'duration':clip.duration,'output':str(movie),'sha256':sha(movie),
                  'full_decode_passed':True,'new_model_calls':0,'source_speed_changed':False})
    elif args.render or args.render_scenes:
        render_scenes(prepared,report,out,reuse)
        if args.render:assemble(prepared,report,audio,captions,out)
    elif args.assemble:assemble(prepared,report,audio,captions,out)
    else:print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
