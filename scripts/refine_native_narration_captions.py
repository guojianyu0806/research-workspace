"""Refine punctuation/energy-estimated captions; reuse the verified audio bytes."""
from datetime import datetime, timezone
import json
from pathlib import Path

from prepare_native_jianying_narration import CASE,ROOT,decode,energy_caption_times,split_captions,units,srt_time,ass_time,sha


def main():
    base=CASE/'assembly-v2'
    source=base/'narration-assembly-metadata-v1.json'
    original=json.loads(source.read_text(encoding='utf-8'))
    snapshot=json.loads((CASE/'approved-script-snapshot.json').read_text(encoding='utf-8'))
    script=snapshot.get('script',snapshot)
    if sha(ROOT/original['final_path'])!=original['final_sha256']:raise ValueError('Verified voice master changed')
    by_scene={s['scene']:s for s in original['segments']}
    out=CASE/'captions-final-v1';out.mkdir(exist_ok=False)
    cues=[]
    for shot in script['shots']:
        segment=by_scene[shot['id']];path=ROOT/segment['processed_path']
        if sha(path)!=segment['processed_sha256']:raise ValueError('Processed source changed')
        parts=split_captions(shot['narration']);times,energy=energy_caption_times(decode(path),parts)
        for text,(a,b) in zip(parts,times):
            cues.append({'scene':shot['id'],'text':text,'start':round(shot['start']+a,3),'end':round(shot['start']+b,3),
                         'glyph_units':round(units(text),2)})
        assert ''.join(p for p in parts)==shot['narration']
    assert all(b['start']>=a['end'] for a,b in zip(cues,cues[1:]))
    assert all(c['end']-c['start']>=.89 for c in cues)
    assert all(c['glyph_units']<=26 for c in cues)
    srt=[]
    for i,c in enumerate(cues,1):srt += [str(i),srt_time(c['start'])+' --> '+srt_time(c['end']),c['text'],'']
    (out/'native-narration-captions-v1.srt').write_text('\n'.join(srt),encoding='utf-8-sig')
    previous=(base/'captions-energy-estimated-v1.ass').read_text(encoding='utf-8-sig')
    head=previous.split('[Events]')[0]
    ass=[head.rstrip(),'','[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for c in cues:
        escaped=c['text'].replace('\\','\\\\').replace('{','\\{').replace('}','\\}')
        ass.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},Narration,,0,0,0,,{escaped}")
    (out/'native-narration-captions-v1.ass').write_text('\n'.join(ass)+'\n',encoding='utf-8-sig')
    result={'created_at':datetime.now(timezone.utc).isoformat(),'voice_master':original['final_path'],
            'voice_master_sha256':original['final_sha256'],'voice_master_seconds':380,
            'reused_audio_unchanged':True,'base_audio_metadata_sha256':sha(source),
            'caption_count':len(cues),'max_glyph_units':max(c['glyph_units'] for c in cues),
            'minimum_cue_seconds':min(round(c['end']-c['start'],3) for c in cues),
            'all_original_words_reconstructed':True,'no_cross_scene_overlap':True,
            'method':'Original punctuation preferred; English tokens unbroken. Duration-weighted speech-energy CDF, snapping to nearby audio-energy valleys, minimum readable cue duration. Approximate, not ASR/word-aligned.',
            'word_alignment_accepted':False,'human_full_listening_performed':False,
            'ass_style':original['ass_style'],'cues':cues,
            'files':[{'name':p.name,'sha256':sha(p)} for p in sorted(out.glob('*')) if p.is_file()]}
    (out/'caption-timing-metadata-v1.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'README_使用这版字幕.txt').write_text(
        '本目录为最终估时字幕版本，请替代assembly-v2/v3中的初次字幕。旁白音频仍使用assembly-v2/final-narration-v1.wav，字节摘要不变。\n'
        '修正了“高低。”“结果。”等过短尾句及中文复合词跨字幕切断，优先采用实际标点。全部原词完整保留，英文术语不拆。\n'
        '每行不超过26汉字宽，多数约14至24字；少数自然语义分句略短。字幕按真实音频能量估时而非ASR/逐字强制对齐，仍需最终视听校准。\n',encoding='utf-8-sig')
    print(json.dumps({'output':str(out),'captions':len(cues),'min_seconds':result['minimum_cue_seconds'],
                      'max_glyph_units':result['max_glyph_units'],'audio_unchanged':True},ensure_ascii=False))


if __name__=='__main__':main()
