"""Assemble verified native Jianying voice sources without another TTS request.

Retimes source audio without changing pitch/provider, places it at approved scene
starts, and estimates captions using punctuation and actual audio-energy valleys.
This is explicitly NOT ASR, forced alignment or word-level synchronization.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
CASE=ROOT/'artifacts/video-production-jianying-20261030-v1/narration-native'
RATE=48000
FONT='Microsoft YaHei'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command,**kwargs):
    return subprocess.run(command,check=True,**kwargs)


def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],stdout=subprocess.PIPE).stdout)


def decode(path):
    result=run(['ffmpeg','-hide_banner','-nostdin','-v','error','-i',str(path),'-vn','-ac','1','-ar',str(RATE),'-f','f32le','-'],stdout=subprocess.PIPE)
    return np.frombuffer(result.stdout,dtype='<f4').copy()


def units(text):
    return sum(.55 if c.isascii() and not c.isspace() else (0. if c.isspace() else 1.) for c in text)


def tokens(text):
    return re.findall(r'[A-Za-z]+(?:[0-9_.+/-]*[A-Za-z0-9]+)*|[0-9]+(?:\.[0-9]+)?|\s+|.',text)


def split_captions(text):
    ts=tokens(text); result=[]; current=[]
    while ts:
        token=ts.pop(0)
        if current and units(''.join(current)+token)>24:
            # Prefer a punctuation boundary after at least fourteen glyph units.
            positions=[i+1 for i,t in enumerate(current) if t in '，、：；。？！' and units(''.join(current[:i+1]))>=8]
            cut=positions[-1] if positions else len(current)
            result.append(''.join(current[:cut])); current=current[cut:]
        current.append(token)
        if token in '。？！；' and units(''.join(current))>=8:
            result.append(''.join(current));current=[]
    if current:result.append(''.join(current))
    if len(result)>1 and units(result[-1])<8 and units(result[-2]+result[-1])<=26:
        tail=result.pop()
        result[-1]=result[-1]+tail
    elif len(result)>1 and units(result[-1])<12:
        # Rebalance two neighbouring cues instead of flashing a dangling
        # two-character tail such as “结果。” for less than one second.
        combined=result[-2]+result[-1]; ts=tokens(combined);total=units(combined)
        choices=[]
        for cut in range(1,len(ts)):
            left=''.join(ts[:cut]);right=''.join(ts[cut:]);a,b=units(left),units(right)
            punctuation=ts[cut-1] in '，、：；。？！'
            if min(a,b)>=(8 if punctuation else 10) and max(a,b)<=26:
                penalty=abs(a-total/2)+(0. if punctuation else 100.)
                choices.append((penalty,left,right))
        if choices:
            _,left,right=min(choices,key=lambda c:c[0]);result[-2:]=[left,right]
    assert ''.join(result)==text
    assert all(units(t)<=26 for t in result)
    return result


def speech_units(text):
    total=0.
    for token in tokens(text):
        if re.fullmatch(r'[A-Za-z]+',token):
            total+=len(token) if token.isupper() and len(token)<=5 else max(1,len(re.findall(r'[aeiouy]+',token.lower())))
        elif token.isdigit():total+=len(token)
        else:total+=sum(1. if '\u4e00'<=c<='\u9fff' else .08 for c in token)
    return max(1.,total)


def energy_caption_times(audio,parts):
    hop=480
    padded=np.pad(audio,(0,(-len(audio))%hop))
    rms=np.sqrt(np.mean(padded.reshape(-1,hop)**2,axis=1))
    threshold=max(.0012,float(np.percentile(rms,90))*.045)
    voiced=np.flatnonzero(rms>threshold)
    if not len(voiced):raise ValueError('A source segment has no measurable speech')
    first=max(0,int(voiced[0])-4);last=min(len(rms)-1,int(voiced[-1])+10)
    activity=np.maximum(rms-threshold,0.)**.6
    activity[first:last+1]+=max(float(activity.max())*.012,1e-8)
    activity[:first]=0;activity[last+1:]=0
    cdf=np.cumsum(activity)
    weights=np.array([speech_units(t) for t in parts]); fractions=np.cumsum(weights)/sum(weights)
    begin=first*.01;end=min(len(audio)/RATE,(last+1)*.01)
    boundaries=[begin];minima=[max(.9,units(part)/12) for part in parts]
    for i,fraction in enumerate(fractions[:-1]):
        ideal=int(np.searchsorted(cdf,cdf[-1]*fraction))
        minimum=max(first,round((boundaries[-1]+minima[i])*100))
        maximum=min(last,round((end-sum(minima[i+1:]))*100))
        lo=max(minimum,ideal-32);hi=min(maximum,ideal+32)
        if hi<=lo:position=max(minimum,min(maximum,ideal))
        else:
            ix=np.arange(lo,hi+1)
            local=np.convolve(rms,np.ones(5)/5,mode='same')[ix]
            score=local/(float(np.percentile(rms,90))+1e-9)+.20*np.abs(ix-ideal)/32
            position=int(ix[int(np.argmin(score))])
        boundaries.append(position*.01)
    boundaries.append(end)
    assert all(b>a for a,b in zip(boundaries,boundaries[1:]))
    return list(zip(boundaries,boundaries[1:])),{'speech_threshold_rms':threshold,'speech_begin_seconds':begin,'speech_end_seconds':end}


def srt_time(t):
    ms=int(round(t*1000));h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def ass_time(t):
    cs=int(round(t*100));h,cs=divmod(cs,360000);m,cs=divmod(cs,6000);s,cs=divmod(cs,100)
    return f'{h}:{m:02}:{s:02}.{cs:02}'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=CASE/'assembly-v1')
    args=parser.parse_args();out=args.output_dir.resolve()
    if not out.is_relative_to(CASE.resolve()):raise ValueError('Output must remain in the project native narration directory')
    if out.exists():raise FileExistsError('Preserve old output; choose a new version')
    mapping_path=CASE/'audio-map-v1.json';mapping=json.loads(mapping_path.read_text(encoding='utf-8'))
    snapshot_path=CASE/'approved-script-snapshot.json';snapshot=json.loads(snapshot_path.read_text(encoding='utf-8'))
    script=snapshot.get('script',snapshot);shots=script['shots'];by_scene={r['nearest_scene']:r for r in mapping['sources']}
    if not mapping['all_mappings_confident'] or len(by_scene)!=23:raise ValueError('Need all 23 independently mapped native recordings')
    for row in by_scene.values():
        if sha(ROOT/row['copy'])!=row['source_sha256']:raise ValueError('Native copy has changed')
    assert shots[0]['start']==0 and shots[-1]['end']==380
    out.mkdir(exist_ok=False);processed=out/'processed';processed.mkdir()
    master=np.zeros(380*RATE,dtype=np.float32);records=[];captions=[]
    for shot in shots:
        sid=shot['id'];source=ROOT/by_scene[sid]['copy'];original=decode(source)
        source_seconds=len(original)/RATE;slot=shot['end']-shot['start'];maximum=slot-.35
        tempo=max(.8,source_seconds/maximum)
        target=processed/(sid+'_retimed.wav')
        # Applying a short fade to the reversed tail avoids assuming an output
        # duration before atempo has actually rendered. No speech is trimmed.
        af=f'atempo={tempo:.12f},afade=t=in:d=0.020,areverse,afade=t=in:d=0.020,areverse,aresample={RATE}'
        run(['ffmpeg','-hide_banner','-nostdin','-v','error','-n','-i',str(source),'-vn','-ac','1',
             '-af',af,'-c:a','pcm_f32le',str(target)])
        audio=decode(target);seconds=len(audio)/RATE
        if seconds>maximum+.025:raise ValueError(f'{sid}: actual retimed duration exceeds safe scene boundary')
        start=round(shot['start']*RATE);stop=start+len(audio)
        if records and records[-1]['actual_end_seconds']>shot['start']:
            raise ValueError('Narration overlap is forbidden')
        if stop>len(master):raise ValueError('Narration exceeds 380-second movie')
        master[start:stop]=audio
        parts=split_captions(shot['narration']);timing,energy=energy_caption_times(audio,parts)
        for text,(a,b) in zip(parts,timing):
            captions.append({'scene':sid,'text':text,'start':round(shot['start']+a,3),'end':round(shot['start']+b,3),
                             'glyph_units':round(units(text),2),'timing_status':'punctuation and energy estimated, not ASR/word-aligned'})
        row={'scene':sid,'approved_start_seconds':shot['start'],'approved_end_seconds':shot['end'],
             'source_copy':str(source.relative_to(ROOT)),'source_sha256':sha(source),
             'source_decoded_seconds':source_seconds,'playback_tempo':tempo,'pitch_preserved':True,
             'actual_start_seconds':shot['start'],'actual_end_seconds':round(stop/RATE,6),
             'processed_duration_seconds':seconds,'processed_path':str(target.relative_to(ROOT)),
             'processed_sha256':sha(target),'safe_gap_before_scene_end_seconds':round(shot['end']-stop/RATE,6),
             'fade_in_seconds':.02,'fade_out_seconds':.02,'caption_count':len(parts),'energy':energy}
        records.append(row)
        print(json.dumps({'scene':sid,'tempo':round(tempo,8),'voice_seconds':round(seconds,4),
                          'scene_end_gap_seconds':row['safe_gap_before_scene_end_seconds']},ensure_ascii=False),flush=True)
    peak=float(np.max(np.abs(master)));gain=min(1.,.94/max(peak,1e-9));master*=gain
    final=out/'final-narration-v1.wav'
    run(['ffmpeg','-hide_banner','-nostdin','-v','error','-n','-f','f32le','-ar',str(RATE),'-ac','1','-i','-',
         '-c:a','pcm_s16le',str(final)],input=master.astype('<f4').tobytes())
    assert all(b['start']>=a['end'] for a,b in zip(captions,captions[1:]))
    assert all(r['safe_gap_before_scene_end_seconds']>=.325 for r in records)
    for shot in shots:
        text=''.join(c['text'] for c in captions if c['scene']==shot['id'])
        assert text==shot['narration'],shot['id']
    srt=[]
    for i,caption in enumerate(captions,1):
        srt += [str(i),srt_time(caption['start'])+' --> '+srt_time(caption['end']),caption['text'],'']
    (out/'captions-energy-estimated-v1.srt').write_text('\n'.join(srt),encoding='utf-8-sig')
    ass=['[Script Info]','Title: 研序 原生剪映旁白字幕（能量估时）','ScriptType: v4.00+','PlayResX: 1920','PlayResY: 1080','WrapStyle: 2','ScaledBorderAndShadow: yes','',
         '[V4+ Styles]','Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
         f'Style: Narration,{FONT},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2.2,0.6,2,96,96,68,1','',
         '[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for caption in captions:
        escaped=caption['text'].replace('\\','\\\\').replace('{','\\{').replace('}','\\}')
        ass.append(f"Dialogue: 0,{ass_time(caption['start'])},{ass_time(caption['end'])},Narration,,0,0,0,,{escaped}")
    (out/'captions-energy-estimated-v1.ass').write_text('\n'.join(ass)+'\n',encoding='utf-8-sig')
    # Full decode and independent FFmpeg signal check of the final deliverable.
    with (out/'full-decode.log').open('xb') as log:
        result=run(['ffmpeg','-hide_banner','-nostdin','-v','error','-i',str(final),'-f','null','-'],stdout=log,stderr=subprocess.STDOUT)
    with (out/'volume-check.log').open('xb') as log:
        run(['ffmpeg','-hide_banner','-nostdin','-i',str(final),'-af','volumedetect','-f','null','-'],stdout=log,stderr=subprocess.STDOUT)
    volume=(out/'volume-check.log').read_text(encoding='utf-8',errors='replace')
    mean=re.search(r'mean_volume:\s*(-?[\d.]+) dB',volume);maximum=re.search(r'max_volume:\s*(-?[\d.]+) dB',volume)
    if not mean or not maximum:raise ValueError('Missing signal verification')
    info=probe(final);duration=float(info['format']['duration'])
    assert abs(duration-380)<1/RATE and result.returncode==0
    report={'created_at':datetime.now(timezone.utc).isoformat(),'voice':'解说小帅','voice_provider':'Jianying native UI, verified by parent screenshots/source origin',
            'new_tts_calls':0,'fallback_used':False,'source_audio_map_sha256':sha(mapping_path),
            'approved_snapshot_sha256':sha(snapshot_path),'input_master_preserved':True,'source_recordings_preserved':True,
            'final_path':str(final.relative_to(ROOT)),'final_sha256':sha(final),'final_probe':info,
            'seconds':duration,'segments':records,'no_cross_scene_overlap':True,
            'global_linear_gain':gain,'headroom_limit_peak':.94,'full_decode_exit_code':0,
            'mean_volume_db':float(mean.group(1)),'max_volume_db':float(maximum.group(1)),
            'captions':captions,'caption_timing_method':'Original approved words split on punctuation/glyph limits; estimated timing follows measured speech energy mass and nearby energy valleys. Not ASR, forced alignment or word-level verification.',
            'caption_alignment_accepted':False,'ass_style':{'font':FONT,'size_px':40,'outline_px':2.2,'alignment':'bottom-center','margin_bottom_px':68,'margin_sides_px':96},
            'manual_full_listening_performed':False}
    with (out/'narration-assembly-metadata-v1.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    (out/'README_音轨与字幕范围.txt').write_text(
        '23段旁白均来自本轮剪映原生“解说小帅”，原始随机文件与Sxx通过母带信号匹配，未按文件名或时间排序。\n'
        '母带与原声全部保留。本音轨只对现有原声做保音高变速、20ms首尾淡化及必要的线性音量留量，不调用新TTS或替换音色。\n'
        'S01至S23默认0.8倍播放；过长片段采用最小加速，镜头结束前留至少约0.35秒，最终音轨380秒，已检查无跨镜重叠。\n'
        '字幕文字与批准稿逐段一致；按标点、约14至24汉字宽拆分，每行不超过26汉字宽，英文术语不拆。\n'
        '字幕时间由本段真实音频能量和附近静音谷值估算，不是ASR/逐字强制对齐，仍需最终视听校准。ASS为1080p、微软雅黑40px、白字黑描边、底部68px安全边距。\n'
        '全片解码和音量检测已执行；没有进行人工完整试听，不把程序检查说成完整视听验收。\n',encoding='utf-8-sig')
    print(json.dumps({'output':str(final),'seconds':duration,'subtitles':len(captions),'mean_volume_db':report['mean_volume_db'],
                      'max_volume_db':report['max_volume_db'],'all_segments_nonoverlapping':True,
                      'sha256':report['final_sha256'],'subtitle_word_alignment':False},ensure_ascii=False))


if __name__=='__main__':main()
