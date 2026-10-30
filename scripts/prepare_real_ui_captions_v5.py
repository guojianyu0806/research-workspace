"""Prepare ordinary v5 captions from unchanged native audio; estimated, not ASR.

Reads only the approved project plan, verified map and mapped audio. No GUI,
model, TTS, audio writes, speed adjustment, old-output overwrite or animation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'artifacts/video-real-ui-20261030-v5'
PLAN = ROOT / 'docs/submission/video-real-ui-20261030-v5/recording-plan.json'
ANCHORS = [0, 20, 60, 130, 180, 220, 255, 285, 350, 385]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=CASE / 'captions-estimated-v1')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if not out.is_relative_to(CASE.resolve()) or out.exists():
        raise ValueError('Choose a new output directory within the v5 artifact directory')
    spec = importlib.util.spec_from_file_location('v4_caption_functions', ROOT / 'scripts/prepare_native_jianying_narration.py')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    mapping_file = CASE / 'native-map-v1.json'
    mapping = json.loads(mapping_file.read_text(encoding='utf-8'))
    plan = json.loads(PLAN.read_text(encoding='utf-8'))
    scenes = plan['scenes']
    if len(scenes) != 10 or not mapping['all_mappings_confident'] or not mapping['unique_scene_assignment']:
        raise ValueError('Need ten approved scenes and an accepted native map')
    master = ROOT / mapping['master']
    master_digest = old.sha(master)
    if master_digest != mapping['master_sha256']:
        raise ValueError('Native master changed')
    by_scene = {s['scene']: s for s in mapping['sources']}
    cues, scene_records = [], []
    for i, scene in enumerate(scenes):
        row = by_scene[scene['id']]
        anchor = ANCHORS[i]
        scene_end = ANCHORS[i + 1] if i + 1 < len(ANCHORS) else float(plan['estimated_duration_seconds'])
        if row['srt_start_seconds'] != anchor or row['matching_speed'] != 1.0:
            raise ValueError('Caption anchor or matching speed changed')
        source = ROOT / row['copy']
        if not source.resolve().is_relative_to((CASE / 'mapped-native-v1').resolve()):
            raise ValueError('Caption input must remain in mapped v5 native media')
        if old.sha(source) != row['source_sha256']:
            raise ValueError('Mapped native audio changed')
        text = scene['narration']
        parts = old.split_captions(text)
        if ''.join(parts) != text:
            raise ValueError('Caption text reconstruction failed')
        boundaries, count = [], 0
        for part in parts[:-1]:
            count += len(part)
            boundaries.append(count)
        for word in re.finditer(r'[A-Za-z]+(?:[0-9_.+/-]*[A-Za-z0-9]+)*', text):
            if any(word.start() < b < word.end() for b in boundaries):
                raise ValueError('An English token crosses a cue boundary')
        audio = old.decode(source)
        times, energy = old.energy_caption_times(audio, parts)
        for part, (a, b) in zip(parts, times):
            cue = {'scene': scene['id'], 'text': part,
                   'start': round(anchor + a, 3), 'end': round(anchor + b, 3),
                   'glyph_units': round(old.units(part), 2),
                   'timing_status': 'estimated from punctuation and audio energy; pending listening calibration'}
            if not anchor <= cue['start'] < cue['end'] <= scene_end:
                raise ValueError('A caption crosses its scene boundary')
            cues.append(cue)
        scene_records.append({'scene': scene['id'], 'scene_start': anchor, 'scene_end': scene_end,
                              'audio': row['copy'], 'audio_sha256': row['source_sha256'],
                              'audio_seconds': len(audio) / old.RATE, 'speech_energy': energy,
                              'cue_count': len(parts), 'original_text_reconstructed': True,
                              'english_tokens_unbroken': True})
    if not all(b['start'] >= a['end'] for a, b in zip(cues, cues[1:])):
        raise ValueError('Captions overlap')
    if not all(c['end'] - c['start'] >= .89 and c['glyph_units'] <= 26 for c in cues):
        raise ValueError('Caption readability bound failed')
    if old.sha(master) != master_digest:
        raise ValueError('Audio master changed during caption preparation')
    for row in mapping['sources']:
        if old.sha(ROOT / row['copy']) != row['source_sha256']:
            raise ValueError('Source changed during caption preparation')
    out.mkdir(exist_ok=False)
    srt = []
    for i, cue in enumerate(cues, 1):
        srt += [str(i), old.srt_time(cue['start']) + ' --> ' + old.srt_time(cue['end']), cue['text'], '']
    srt_path = out / 'real-ui-v5-captions-estimated.srt'
    srt_path.write_text('\n'.join(srt), encoding='utf-8-sig')
    style = {'font': 'Microsoft YaHei', 'size_px': 38, 'outline_px': 2.2,
             'alignment': 'bottom-center', 'margin_bottom_px': 60, 'margin_sides_px': 80,
             'background_card': False, 'animation': False}
    ass = ['[Script Info]', 'Title: 研序真实页面演示 v5 普通估时字幕', 'ScriptType: v4.00+',
           'PlayResX: 1920', 'PlayResY: 1080', 'WrapStyle: 2', 'ScaledBorderAndShadow: yes', '',
           '[V4+ Styles]', 'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
           'Style: Narration,Microsoft YaHei,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&HFF000000,0,0,0,0,100,100,0,0,1,2.2,0.5,2,80,80,60,1', '',
           '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for cue in cues:
        escaped = cue['text'].replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')
        ass.append(f"Dialogue: 0,{old.ass_time(cue['start'])},{old.ass_time(cue['end'])},Narration,,0,0,0,,{escaped}")
    ass_path = out / 'real-ui-v5-captions-estimated.ass'
    ass_path.write_text('\n'.join(ass) + '\n', encoding='utf-8-sig')
    result = {'created_at': datetime.now(timezone.utc).isoformat(),
              'status': 'estimated captions prepared; pending listening calibration',
              'plan': str(PLAN.relative_to(ROOT)), 'plan_sha256': old.sha(PLAN),
              'map': str(mapping_file.relative_to(ROOT)), 'map_sha256': old.sha(mapping_file),
              'native_master': mapping['master'], 'native_master_sha256': master_digest,
              'audio_unchanged': True, 'audio_speed': 1.0, 'audio_written': False,
              'scene_anchors_seconds': ANCHORS, 'caption_count': len(cues),
              'method': 'Original punctuation and intact English tokens; measured speech-energy CDF with nearby energy valleys. No ASR or forced alignment.',
              'asr_used': False, 'forced_alignment_used': False, 'human_listening_calibrated': False,
              'all_original_characters_reconstructed': True, 'all_english_tokens_unbroken': True,
              'no_cue_overlap': True, 'no_cue_crosses_scene': True,
              'captions_only_over_measured_speech_span': True,
              'minimum_cue_seconds': round(min(c['end'] - c['start'] for c in cues), 3),
              'maximum_glyph_units': max(c['glyph_units'] for c in cues),
              'style': style, 'scenes': scene_records, 'cues': cues,
              'files': [{'path': str(p.relative_to(ROOT)), 'sha256': old.sha(p)} for p in [srt_path, ass_path]],
              'gui_calls': 0, 'model_calls': 0, 'tts_calls': 0}
    (out / 'caption-timing-metadata.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'README_估时字幕待试听校准.txt').write_text(
        '本目录为v5普通旁白估时字幕，按新原生1倍速音频的实际讲话范围分句显示。没有使用ASR或逐字强制对齐，尚待试听校准。\n'
        '字幕保留批准稿完整字符且不拆英文词，白字黑描边、微软雅黑38px、底部60px，无背景卡片、无动画。\n'
        '没有生成、覆盖、裁切或变速任何音频。不要将字幕贯穿整段20至70秒的镜头；静音阅读时不显示旁白字幕。\n', encoding='utf-8-sig')
    print(json.dumps({'output': str(out.relative_to(ROOT)), 'cues': len(cues),
                      'minimum_cue_seconds': result['minimum_cue_seconds'],
                      'maximum_glyph_units': result['maximum_glyph_units'],
                      'audio_unchanged': True, 'alignment_accepted': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
