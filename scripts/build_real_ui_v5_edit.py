"""Build the v5 real-recording edit and lossless-in-time voice placements.

No GUI, model, TTS or rendering is performed. Native speech is split only at
the measured phrase boundaries and at real video cuts, always at rate 1.0.
All output is exclusive creation in a new project-local directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'artifacts/video-real-ui-20261030-v5'
FPS = 30


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding='utf-8-sig'))
    groups = json.loads((CASE / 'voice-phrase-groups-v1.json').read_text(encoding='utf-8'))
    out = args.output_dir.resolve()
    if not out.is_relative_to(CASE.resolve()) or out.exists():
        raise ValueError('Use a new directory inside the v5 artifact root')
    scenes, clips, placed = [], [], []
    clock_frames = 0
    for scene in selection['scenes']:
        start_frames = clock_frames
        for i, clip in enumerate(scene['clips'], 1):
            n = round(clip['duration'] * FPS)
            if n <= 0 or abs(n / FPS - clip['duration']) > 1e-6:
                raise ValueError('Video duration must be on the 30fps frame grid')
            clips.append({**clip, 'id': f"{scene['id']}_{i:02d}",
                          'scene': scene['id'], 'frame_start': clock_frames,
                          'frame_end': clock_frames + n})
            clock_frames += n
        duration = (clock_frames - start_frames) / FPS
        current = [g for g in groups['groups'] if g['scene'] == scene['id']]
        if len(current) != len(scene['voice_starts']):
            raise ValueError('Each native phrase must have exactly one placement')
        for group, position in zip(current, scene['voice_starts']):
            if abs(round(position * FPS) / FPS - position) > 1e-6:
                raise ValueError('Placement start must be on the frame grid')
            if position < 0 or position + group['duration_seconds'] > duration + 1e-6:
                raise ValueError(f"{group['id']}: speech does not fit the real scene")
            placed.append({**group, 'global_start': start_frames / FPS + position,
                           'global_end': start_frames / FPS + position + group['duration_seconds']})
        scenes.append({'scene': scene['id'], 'start': start_frames / FPS,
                       'duration': duration, 'end': clock_frames / FPS})
    for a, b in zip(placed, placed[1:]):
        if a['global_end'] > b['global_start'] + 1e-6:
            raise ValueError('Voice phrases overlap')
    segments, coverage = [], {g['id']: [] for g in placed}
    for clip in clips:
        bounds = sorted({clip['frame_start'], clip['frame_end']} | {
            round(v['global_start'] * FPS) for v in placed
            if clip['frame_start'] < round(v['global_start'] * FPS) < clip['frame_end']})
        for i, (fa, fb) in enumerate(zip(bounds, bounds[1:]), 1):
            ta, tb = fa / FPS, fb / FPS
            voice = [v for v in placed if v['global_start'] < tb - 1e-7 and v['global_end'] > ta + 1e-7]
            if len(voice) > 1:
                raise ValueError('A render segment contains two voice starts')
            narration = None
            if voice:
                v = voice[0]
                a, b = max(ta, v['global_start']), min(tb, v['global_end'])
                audio_start = v['audio_start'] + a - v['global_start']
                narration = {'source': v['source'], 'source_start': audio_start,
                             'duration': b - a, 'offset': a - ta,
                             'native_speed': 1, 'provider': '剪映原生',
                             'evidence': 'artifacts/video-real-ui-20261030-v5/native-map-v1.json'}
                coverage[v['id']].append((audio_start, audio_start + b - a))
            segments.append({'id': f"{clip['id']}_p{i}", 'source': clip['source'],
                             'start': clip['start'] + (fa - clip['frame_start']) / FPS,
                             'duration': (fb - fa) / FPS, 'speed': 1,
                             'crop': clip.get('crop', [0, 0, 1854, 806]),
                             'narration': narration, 'evidence_note': clip['evidence']})
    for v in placed:
        ranges = coverage[v['id']]
        if not ranges or abs(ranges[0][0] - v['audio_start']) > 1e-6 or abs(ranges[-1][1] - v['audio_end']) > 1e-6:
            raise ValueError('Voice source coverage is incomplete')
        if any(abs(a[1] - b[0]) > 1e-6 for a, b in zip(ranges, ranges[1:])):
            raise ValueError('Voice source has a duplicate or missing interval')
    out.mkdir(parents=True, exist_ok=False)
    placements = {'groups': [{k: v[k] for k in ['id', 'scene', 'source', 'audio_start', 'audio_end', 'global_start', 'global_end', 'text', 'caption_indices']} for v in placed],
                  'wait_notes': selection.get('wait_notes', []), 'scenes': scenes,
                  'duration': clock_frames / FPS, 'all_native_audio_preserved_at_1x': True,
                  'human_listening_calibrated': False}
    plan = {'version': 1, 'final_name': '研序_真实页面演示_剪映正常配音_v5.mp4',
            'subtitles': selection.get('subtitles', str((out / 'captions' / 'real-ui-v5-placed.ass').relative_to(ROOT))),
            'segments': segments,
            'notes': ['All visuals are selected continuous screen-recording intervals at 1x.',
                      'No cards, explanatory animation, fabricated UI, image replacement, freezing or loops.',
                      'All new native Jianying 1x speech is retained exactly once in original source order.',
                      'Long inactive waits are hard-cut; ordinary short wait notes identify the omissions.']}
    for name, obj in [('voice-placements.json', placements), ('edit-plan.json', plan),
                      ('selected-footage.json', {'selection': selection, 'scenes': scenes, 'base_clips': clips})]:
        with (out / name).open('x', encoding='utf-8') as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps({'duration': clock_frames / FPS, 'base_clips': len(clips),
                      'render_segments': len(segments), 'voice_groups': len(placed),
                      'native_voice_coverage_verified': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
