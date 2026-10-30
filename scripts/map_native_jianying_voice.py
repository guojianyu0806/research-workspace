"""Map only this project's 23 native Jianying recordings by signal correlation.

Never reads draft JSON, app logs, accounts, IDs or internal TTS services. Random
source names and modification times are not used to infer scene identity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT/'artifacts/video-production-jianying-20261030-v1/narration-native'
RATE, HOP = 8000, 80


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe(path):
    p=subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],
                     check=True,stdout=subprocess.PIPE,encoding='utf-8')
    return json.loads(p.stdout)


def pcm(path):
    p=subprocess.run(['ffmpeg','-hide_banner','-nostdin','-v','error','-i',str(path),
                      '-vn','-ac','1','-ar',str(RATE),'-f','f32le','-'],
                     check=True,stdout=subprocess.PIPE)
    return np.frombuffer(p.stdout,dtype='<f4').copy()


def features(audio):
    # 25ms windows at 10ms steps; eight logarithmically spaced speech bands.
    window=200
    samples=np.pad(audio,(0,max(0,window-len(audio))))
    frames=np.lib.stride_tricks.sliding_window_view(samples,window)[::HOP]
    spectrum=np.abs(np.fft.rfft(frames*np.hanning(window),n=256,axis=1))**2
    frequencies=np.fft.rfftfreq(256,1/RATE)
    edges=[70,160,300,500,800,1200,1800,2600,4001]
    values=np.stack([np.log(np.maximum(spectrum[:,(frequencies>=a)&(frequencies<b)].sum(axis=1),1e-10))
                     for a,b in zip(edges[:-1],edges[1:])],axis=1)
    # Fixed source-independent scaling retains spectral fingerprints, while
    # per-band template centering and local NCC reject silent gaps.
    return values.astype(np.float64)


def warp(values,speed):
    target=max(2,int(round(len(values)/speed)))
    positions=np.arange(target)*speed
    return np.column_stack([np.interp(positions,np.arange(len(values)),values[:,i])
                            for i in range(values.shape[1])])


def ncc(master,template):
    n=len(template);m=len(master)
    template=template-template.mean(axis=0,keepdims=True)
    energy=(template**2).sum()
    length=1<<(m+n-1).bit_length()
    numerator=np.zeros(m-n+1)
    local_energy=np.zeros(m-n+1)
    for band in range(master.shape[1]):
        x=master[:,band]; y=template[:,band]
        dot=np.fft.irfft(np.fft.rfft(x,length)*np.conj(np.fft.rfft(y,length)),length)[:m-n+1]
        numerator+=dot
        sums=np.r_[0.,np.cumsum(x)]; squares=np.r_[0.,np.cumsum(x*x)]
        total=sums[n:]-sums[:-n]
        local_energy+=(squares[n:]-squares[:-n])-total*total/n
    scores=numerator/np.sqrt(np.maximum(energy*local_energy,1e-15))
    scores[~np.isfinite(scores)]=-1
    return scores


def peaks(scores,count=4,separation=1.):
    result=[];temp=scores.copy();radius=int(round(separation*RATE/HOP))
    for _ in range(count):
        idx=int(np.argmax(temp));value=float(temp[idx])
        result.append({'start_seconds':idx*HOP/RATE,'score':round(value,7)})
        temp[max(0,idx-radius):min(len(temp),idx+radius+1)]=-1
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True,
                        help='Exact existing folder containing only this project’s 23 native .wav files; no recursive scan')
    parser.add_argument('--analyze-only',action='store_true')
    parser.add_argument('--accept-analysis',type=Path,help='Copy from an already measured project-local map after rechecking all hashes')
    parser.add_argument('--output',type=Path,default=CASE/'audio-map-v1.json')
    parser.add_argument('--copy-dir',type=Path,default=CASE/'mapped-scenes-v1')
    args=parser.parse_args()
    try:
        source_dir=args.source_dir.resolve(strict=True)
    except OSError:
        raise ValueError('Source folder must already exist and be accessible') from None
    if not source_dir.is_dir():raise ValueError('Source must be an existing directory')
    sources=list(source_dir.iterdir())
    if len(sources)!=23 or any(p.is_symlink() or not p.is_file() or p.suffix.lower()!='.wav'
                               or p.resolve().parent!=source_dir for p in sources):
        raise ValueError('Source folder must contain exactly 23 regular .wav files and no other entries')
    out=args.output.resolve(); copydir=args.copy_dir.resolve()
    if not out.is_relative_to(CASE.resolve()) or not copydir.is_relative_to(CASE.resolve()):
        raise ValueError('All output must remain in the named project audio directory')
    if out.exists():raise FileExistsError('Preserve existing map; choose a new output name')
    master=CASE/'yanxu_native_voice_master_v1.wav'
    snapshot=CASE/'approved-script-snapshot.json'
    approved=json.loads(snapshot.read_text(encoding='utf-8'))
    shots=approved.get('script',approved)['shots']
    if len(sources)!=23 or len(shots)!=23:raise ValueError('Expected exactly this project’s 23 source recordings and scenes')
    master_hash=sha(master); source_hashes={str(p):sha(p) for p in sources}
    if args.accept_analysis:
        analysis=args.accept_analysis.resolve()
        if not analysis.is_relative_to(CASE.resolve()):raise ValueError('Analysis must be in this project audio directory')
        result=json.loads(analysis.read_text(encoding='utf-8'))
        if not result['all_mappings_confident'] or not result['unique_scene_assignment']:
            raise ValueError('Ambiguous audio analysis cannot be accepted')
        if master_hash!=result['master_sha256'] or sha(snapshot)!=result['source_snapshot_sha256']:
            raise ValueError('Frozen master or script changed')
        if {r['source_file'] for r in result['sources']}!={p.name for p in sources}:
            raise ValueError('Source membership changed')
        copydir.mkdir(exist_ok=False)
        for row in result['sources']:
            source=source_dir/row['source_file']
            if sha(source)!=row['source_sha256']:raise ValueError('Source bytes changed')
            suffix='.mp3' if row['detected_codec']=='mp3' else '.wav'
            target=copydir/(row['nearest_scene']+'_剪映解说小帅_原声源'+suffix)
            with source.open('rb') as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
            if sha(target)!=row['source_sha256']:raise ValueError('Copy bytes mismatch')
            row['copy']=str(target.relative_to(ROOT))
        result['accepted_analysis_sha256']=sha(analysis)
        result['copied_at']=datetime.now(timezone.utc).isoformat()
        result['status']='23 source recordings mapped by audio signal and copied without modification'
        with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps({'map':str(out),'copies':23,'copied_to':str(copydir),'all_confident':True},ensure_ascii=False))
        return
    master_probe=probe(master);master_audio=pcm(master); mf=features(master_audio)
    rows=[]
    first_sha=json.loads((CASE/'S01-native-origin.json').read_text(encoding='utf-8'))['sha256']
    for path in sources:
        info=probe(path); stream=next(s for s in info['streams'] if s['codec_type']=='audio')
        audio=pcm(path);feature=features(audio)
        candidates=[]
        speeds=[1.] if sha(path)==first_sha else [.8,1.]
        for speed in speeds:
            scores=ncc(mf,warp(feature,speed))
            candidate=peaks(scores,4)
            candidates.append({'playback_speed_candidate':speed,'peaks':candidate})
        ranked=sorted([(c['peaks'][0]['score'],c) for c in candidates],key=lambda x:x[0],reverse=True)
        winner=ranked[0][1];best=winner['peaks'][0]
        # Scene identity is a measured audio location, then compared with the
        # approved SRT scene anchors. No filename, directory or mtime ordering.
        scene=min(shots,key=lambda s:abs(float(s['start'])-best['start_seconds']))
        offset=best['start_seconds']-float(scene['start'])
        second=winner['peaks'][1]['score']
        rate=winner['playback_speed_candidate']
        duration=len(audio)/RATE
        rows.append({'source_file':path.name,'source_sha256':sha(path),'detected_codec':stream['codec_name'],
                     'source_duration_seconds':duration,'source_container':info['format']['format_name'],
                     'measured_start_seconds':best['start_seconds'],'spectral_ncc':best['score'],
                     'second_peak_score_1s_separated':second,'peak_margin':round(best['score']-second,7),
                     'matching_speed':rate,'effective_duration_seconds_estimate':duration/rate,
                     'nearest_scene':scene['id'],'approved_start_seconds':scene['start'],
                     'anchor_offset_seconds':round(offset,4),'candidates':candidates,
                     'confidence_passed':best['score']>=.65 and best['score']-second>=.08 and abs(offset)<=.65})
        print(json.dumps({k:rows[-1][k] for k in ['source_file','nearest_scene','measured_start_seconds','spectral_ncc','matching_speed','anchor_offset_seconds','confidence_passed']},ensure_ascii=False),flush=True)
    rows.sort(key=lambda r:r['measured_start_seconds'])
    scenes=[r['nearest_scene'] for r in rows]
    unique=len(set(scenes))==23
    passed=unique and all(r['confidence_passed'] for r in rows)
    for i,row in enumerate(rows):
        end=row['measured_start_seconds']+row['effective_duration_seconds_estimate']
        next_start=rows[i+1]['measured_start_seconds'] if i+1<len(rows) else len(master_audio)/RATE
        row['estimated_end_seconds']=round(end,4)
        row['overlap_with_next_seconds_estimate']=round(max(0,end-next_start),4)
    result={'created_at':datetime.now(timezone.utc).isoformat(),'voice_name':'解说小帅',
            'origin':'Parent verified actual Jianying 11.6 native UI generation; this script only maps existing audio.',
            'master':str(master.relative_to(ROOT)),'master_sha256':master_hash,
            'master_probe':master_probe,'source_snapshot_sha256':sha(snapshot),
            'mapping_method':'8-band log-spectral local normalized cross-correlation at 10ms frames; compare speed .8/1.0; then validate measured peak against approved scene anchor.',
            'file_order_used_for_identity':False,'sources':rows,'unique_scene_assignment':unique,
            'all_mappings_confident':passed,'source_files_changed':False,
            'new_model_or_tts_calls':0,'private_drafts_or_logs_read':False,
            'status':'analysis only' if args.analyze_only else 'unmapped confidence failure'}
    if sha(master)!=master_hash or any(sha(Path(p))!=v for p,v in source_hashes.items()):
        raise ValueError('Audio changed during mapping; output cannot be accepted')
    if not args.analyze_only and passed:
        copydir.mkdir(exist_ok=False)
        for row in rows:
            suffix={'mp3':'.mp3','pcm_s16le':'.wav','pcm_f32le':'.wav'}.get(row['detected_codec'])
            if suffix is None:raise ValueError('Unsupported source codec; preserve analysis only')
            target=copydir/(row['nearest_scene']+'_剪映解说小帅_原声源'+suffix)
            with (source_dir/row['source_file']).open('rb') as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
            if sha(target)!=row['source_sha256']:raise ValueError('Copied voice bytes mismatch')
            row['copy']=str(target.relative_to(ROOT))
        result['status']='23 source recordings mapped by audio signal and copied without modification'
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'map':str(out),'all_confident':passed,'unique_scenes':unique,
                      'copies_created':not args.analyze_only and passed,
                      'max_overlap_seconds':max(r['overlap_with_next_seconds_estimate'] for r in rows)},ensure_ascii=False))


if __name__=='__main__':main()
