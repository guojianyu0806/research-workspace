"""Place ordinary estimated v5 captions at explicit actual edit positions.

Reads verified native audio without writing, stretching or regenerating it.
Timing uses punctuation and waveform energy, not ASR or listening acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import hashlib
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'artifacts/video-real-ui-20261030-v5'


def polish_existing(source_dir: Path, out: Path):
    """Move the known S04 leading period, with no timing or audio processing."""
    source_dir, out = source_dir.resolve(), out.resolve()
    if (not source_dir.is_relative_to(CASE.resolve()) or not source_dir.is_dir()
            or not out.is_relative_to(CASE.resolve()) or out.exists()):
        raise ValueError('Use an existing v5 caption directory and a new output directory')
    previous = '这里比较 Transformer 和 BERT 的模型结构与训练任务'
    following = '。查看比较结果后，再分别打开两篇论文的引用，'
    changes, texts = [], {}
    for name in ['real-ui-v5-placed.ass', 'real-ui-v5-placed.srt']:
        content = (source_dir / name).read_text(encoding='utf-8-sig')
        if content.count(previous) != 1 or content.count(following) != 1 or previous + '。' in content:
            raise ValueError('Expected exactly the known unpolished S04 text')
        polished = content.replace(previous, previous + '。', 1).replace(following, following[1:], 1)
        old_lines, new_lines = content.splitlines(), polished.splitlines()
        line_changes = [(i + 1, a, b) for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        if len(old_lines) != len(new_lines) or len(line_changes) != 2:
            raise ValueError('Patch must change exactly two existing text lines')
        if content.count('。') != polished.count('。'):
            raise ValueError('Period count changed')
        if name.endswith('.ass'):
            def narration(value):
                return [line.split(',', 9)[9] for line in value.splitlines()
                        if line.startswith('Dialogue:') and line.split(',', 9)[3] == 'Narration']
            def timing(value):
                return [line.split(',', 9)[:9] for line in value.splitlines() if line.startswith('Dialogue:')]
            if ''.join(narration(content)) != ''.join(narration(polished)) or timing(content) != timing(polished):
                raise ValueError('ASS narration characters/order or event timing changed')
        else:
            def srt_text(value):
                return ''.join(''.join(block.splitlines()[2:]) for block in re.split(r'\n\s*\n', value.strip()))
            if srt_text(content) != srt_text(polished):
                raise ValueError('SRT original character order changed')
            if re.findall(r'\d{2}:\d{2}:\d{2},\d{3} --> .*', content) != re.findall(r'\d{2}:\d{2}:\d{2},\d{3} --> .*', polished):
                raise ValueError('SRT timing changed')
        texts[name] = polished
        changes.append({'file': name, 'changed_line_numbers': [c[0] for c in line_changes],
                        'before_sha256': hashlib.sha256((source_dir / name).read_bytes()).hexdigest()})
    metadata = json.loads((source_dir / 'timing-metadata.json').read_text(encoding='utf-8'))
    changed_cues = []
    for i, cue in enumerate(metadata['cues'], 1):
        if cue['text'] == previous:
            cue['text'] += '。'
            cue['glyph_units'] += 1.0
            changed_cues.append(i)
        elif cue['text'] == following:
            cue['text'] = cue['text'][1:]
            cue['glyph_units'] -= 1.0
            changed_cues.append(i)
    if len(changed_cues) != 2:
        raise ValueError('Expected two corresponding metadata cues')
    metadata['punctuation_patch'] = {'one_period_transferred_to_previous_cue': True,
                                     'cue_indices_1_based': changed_cues, 'all_timing_unchanged': True,
                                     'audio_unchanged': True, 'reestimated': False}
    metadata['max_glyph_units'] = max(c['glyph_units'] for c in metadata['cues'])
    out.mkdir(parents=True, exist_ok=False)
    for name, content in texts.items():
        (out / name).write_text(content, encoding='utf-8-sig')
    for name in ['audio-placements.json', 'wait-notes.srt']:
        shutil.copyfile(source_dir / name, out / name)
    for row in metadata.get('files', []):
        if (out / row['name']).is_file():
            row['sha256'] = hashlib.sha256((out / row['name']).read_bytes()).hexdigest()
    (out / 'timing-metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in changes:
        row['after_sha256'] = hashlib.sha256((out / row['file']).read_bytes()).hexdigest()
    patch = {'created_at': datetime.now(timezone.utc).isoformat(), 'source_directory': str(source_dir.relative_to(ROOT)),
             'output_directory': str(out.relative_to(ROOT)), 'operation': 'Transfer exactly one leading Chinese period to preceding narration cue',
             'characters_and_order_preserved': True, 'period_total_preserved': True, 'all_timing_unchanged': True,
             'audio_placements_unchanged': (source_dir / 'audio-placements.json').read_bytes() == (out / 'audio-placements.json').read_bytes(),
             'wait_notes_unchanged': (source_dir / 'wait-notes.srt').read_bytes() == (out / 'wait-notes.srt').read_bytes(),
             'audio_read_or_written': False, 'energy_reestimated': False, 'gui_calls': 0, 'model_calls': 0, 'tts_calls': 0,
             'changes': changes, 'human_listening_calibrated': False}
    (out / 'punctuation-patch-metadata.json').write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(out.relative_to(ROOT)), 'exactly_one_period_transferred': True,
                      'all_timing_unchanged': True, 'audio_unchanged': True}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('placements', type=Path, nargs='?')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--polish-existing', type=Path,
                        help='Only transfer the known S04 period in existing captions; no audio reads or re-estimation')
    args = parser.parse_args()
    if args.polish_existing:
        if args.placements:
            raise ValueError('Polishing existing captions does not accept new placements')
        polish_existing(args.polish_existing, args.output_dir)
        return
    if not args.placements:
        raise ValueError('Placements are required unless --polish-existing is used')
    placement_path = args.placements.resolve()
    out = args.output_dir.resolve()
    if not placement_path.is_relative_to(CASE.resolve()) or not out.is_relative_to(CASE.resolve()) or out.exists():
        raise ValueError('Use project-local placements and a new v5 output directory')
    spec = importlib.util.spec_from_file_location('caption_functions', ROOT / 'scripts/prepare_native_jianying_narration.py')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    placements = json.loads(placement_path.read_text(encoding='utf-8-sig'))
    phrase_path = CASE / 'voice-phrase-groups-v1.json'
    phrases = json.loads(phrase_path.read_text(encoding='utf-8'))
    map_path = CASE / 'native-map-v1.json'
    native = json.loads(map_path.read_text(encoding='utf-8'))
    old_caption_path = CASE / 'captions-estimated-v1/caption-timing-metadata.json'
    original = json.loads(old_caption_path.read_text(encoding='utf-8'))
    expected = {g['id']: g for g in phrases['groups']}
    sources = {s['scene']: s for s in native['sources']}
    scene_ranges = {s['scene']: s for s in placements['scenes']}
    groups = placements['groups']
    if [g['id'] for g in groups] != [g['id'] for g in phrases['groups']]:
        raise ValueError('Phrase group membership or order changed')
    if sorted(i for g in groups for i in g['caption_indices']) != list(range(1, 45)):
        raise ValueError('All original 44 cue identities must appear exactly once')
    cache, cues, records, audio_list = {}, [], [], []
    for g in groups:
        base = expected[g['id']]
        for key in ['scene', 'source', 'audio_start', 'audio_end', 'text', 'caption_indices']:
            if g[key] != base[key]:
                raise ValueError(f'{g["id"]}: frozen phrase field changed: {key}')
        duration = g['audio_end'] - g['audio_start']
        if duration <= 0 or abs(g['global_end'] - g['global_start'] - duration) > .0001:
            raise ValueError('Only original 1.0x duration is allowed')
        sr = scene_ranges[g['scene']]
        if not sr['start'] <= g['global_start'] < g['global_end'] <= sr['end']:
            raise ValueError('Voice placement crosses scene')
        row = sources[g['scene']]
        source = ROOT / g['source']
        if not source.resolve().is_relative_to((CASE / 'mapped-native-v1').resolve()):
            raise ValueError('Source must be an existing mapped native voice')
        if str(source) not in cache:
            if old.sha(source) != row['source_sha256']:
                raise ValueError('Native audio hash changed')
            cache[str(source)] = old.decode(source)
        whole = cache[str(source)]
        audio = whole[round(g['audio_start'] * old.RATE):round(g['audio_end'] * old.RATE)]
        parts = old.split_captions(g['text'])
        boundaries, pos = [], 0
        for part in parts[:-1]:
            pos += len(part)
            boundaries.append(pos)
        if any(w.start() < b < w.end() for w in re.finditer(r'[A-Za-z]+(?:[0-9_.+/-]*[A-Za-z0-9]+)*', g['text']) for b in boundaries):
            raise ValueError('English token split')
        times, energy = old.energy_caption_times(audio, parts)
        for text, (a, b) in zip(parts, times):
            start = round(g['global_start'] + a, 3)
            end = round(min(g['global_end'], g['global_start'] + b), 3)
            if not g['global_start'] <= start < end <= g['global_end']:
                raise ValueError('Cue crosses its group')
            cues.append({'group': g['id'], 'scene': g['scene'], 'text': text, 'start': start,
                         'end': end, 'glyph_units': old.units(text)})
        if ''.join(parts) != g['text']:
            raise ValueError('Original characters changed')
        records.append({'group': g['id'], 'scene': g['scene'], 'source': g['source'],
                        'audio_start': g['audio_start'], 'audio_end': g['audio_end'],
                        'global_start': g['global_start'], 'global_end': g['global_end'],
                        'caption_indices': g['caption_indices'], 'energy': energy,
                        'caption_count': len(parts), 'text_complete': True})
        audio_list.append({'id': g['id'], 'scene': g['scene'], 'source': g['source'],
                           'source_start': g['audio_start'], 'duration': duration,
                           'global_start': g['global_start'], 'global_end': g['global_end'],
                           'native_speed': 1.0, 'post_speed': 1.0})
    if not all(b['start'] >= a['end'] for a, b in zip(cues, cues[1:])):
        raise ValueError('Narration captions overlap or are reordered')
    if ''.join(g['text'] for g in groups) != ''.join(c['text'] for c in original['cues']):
        raise ValueError('Original 44-cue text is not reconstructed')
    for sid, row in sources.items():
        gs = [g for g in groups if g['scene'] == sid]
        if gs[0]['audio_start'] != 0 or abs(gs[-1]['audio_end'] - row['source_duration_seconds']) > .0001:
            raise ValueError('Native source not fully covered')
        if not all(a['audio_end'] == b['audio_start'] for a, b in zip(gs, gs[1:])):
            raise ValueError('Native voice repeated or deleted')
        if old.sha(ROOT / row['copy']) != row['source_sha256']:
            raise ValueError('Source changed during placement')
    notes = []
    for note in placements.get('wait_notes', []):
        start, duration = float(note['start']), float(note['duration'])
        if duration != 2 or start < 0 or start + duration > placements['duration']:
            raise ValueError('Wait note must be a two-second interval inside movie')
        notes.append({'start': start, 'end': start + duration, 'text': '等待已省略'})
    if not all(b['start'] >= a['end'] for a, b in zip(notes, notes[1:])):
        raise ValueError('Wait notes overlap each other')
    out.mkdir(parents=True, exist_ok=False)
    def write_srt(path, items):
        lines = []
        for i, c in enumerate(items, 1):
            lines += [str(i), old.srt_time(c['start']) + ' --> ' + old.srt_time(c['end']), c['text'], '']
        path.write_text('\n'.join(lines), encoding='utf-8-sig')
    write_srt(out / 'real-ui-v5-placed.srt', cues)
    write_srt(out / 'wait-notes.srt', notes)
    ass = ['[Script Info]', 'Title: 研序真实页面v5普通字幕（估时待校准）', 'ScriptType: v4.00+',
           'PlayResX: 1920', 'PlayResY: 1080', 'WrapStyle: 2', 'ScaledBorderAndShadow: yes', '',
           '[V4+ Styles]', 'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
           'Style: Narration,Microsoft YaHei,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&HFF000000,0,0,0,0,100,100,0,0,1,2.2,0.5,2,80,80,60,1',
           'Style: WaitNote,Microsoft YaHei,24,&H00FFFFFF,&H00FFFFFF,&H00000000,&HFF000000,0,0,0,0,100,100,0,0,1,1.5,0.3,9,60,60,60,1', '',
           '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for item in sorted([dict(c, style='Narration', layer=0) for c in cues] + [dict(n, style='WaitNote', layer=1) for n in notes], key=lambda c: (c['start'], c['layer'])):
        text = item['text'].replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')
        ass.append(f"Dialogue: {item['layer']},{old.ass_time(item['start'])},{old.ass_time(item['end'])},{item['style']},,0,0,0,,{text}")
    ass_path = out / 'real-ui-v5-placed.ass'
    ass_path.write_text('\n'.join(ass) + '\n', encoding='utf-8-sig')
    (out / 'audio-placements.json').write_text(json.dumps({'duration': placements['duration'], 'placements': audio_list}, ensure_ascii=False, indent=2), encoding='utf-8')
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'Estimated timing; pending listening calibration',
              'placements': str(placement_path.relative_to(ROOT)), 'placements_sha256': old.sha(placement_path),
              'phrase_groups_sha256': old.sha(phrase_path), 'native_map_sha256': old.sha(map_path),
              'duration': placements['duration'], 'group_count': len(groups), 'caption_count': len(cues),
              'original_44_cues_complete': True, 'characters_complete': True, 'english_words_unbroken': True,
              'no_narration_cue_overlap': True, 'no_group_or_scene_crossing': True,
              'all_source_audio_covered_once_in_order': True, 'source_audio_unchanged': True,
              'native_speed': 1.0, 'post_speed': 1.0, 'audio_written': False, 'asr_used': False,
              'forced_alignment_used': False, 'human_listening_calibrated': False,
              'method': 'Per actual source interval: punctuation splitting and measured speech-energy CDF with nearby energy valleys; approximate, not ASR.',
              'style': {'font': 'Microsoft YaHei', 'size': 38, 'outline': 2.2, 'bottom_margin': 60, 'background_card': False, 'animation': False},
              'wait_note_policy': 'ASS right-top 24px ordinary two-second notes on a separate layer; main SRT contains narration only, wait-notes.srt is separate.',
              'groups': records, 'cues': cues, 'wait_notes': notes,
              'minimum_cue_seconds': round(min(c['end'] - c['start'] for c in cues), 3),
              'max_glyph_units': max(c['glyph_units'] for c in cues),
              'files': [{'name': p.name, 'sha256': old.sha(p)} for p in out.iterdir() if p.is_file()]}
    (out / 'timing-metadata.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ass': str(ass_path.relative_to(ROOT)), 'captions': len(cues), 'groups': len(groups),
                      'wait_notes': len(notes), 'duration': placements['duration'], 'characters_complete': True,
                      'audio_unchanged': True, 'human_listening_calibrated': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
