"""Map the ten v5 native recordings to the exported mother track at 1.0x only.

Only an explicitly supplied ten-file media directory is read. No draft JSON,
account data, app logs, cache search, GUI automation, TTS, or speed change.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'artifacts/video-real-ui-20261030-v5'
MASTER = CASE / 'yanxu_native_v5_1x_master.wav'
SRT = CASE / 'v5-native-narration-staging.srt'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(path: Path) -> dict:
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                        '-of', 'json', str(path)], check=True,
                       stdout=subprocess.PIPE, encoding='utf-8')
    return json.loads(p.stdout)


def seconds(value: str) -> float:
    h, m, rest = value.split(':')
    return int(h) * 3600 + int(m) * 60 + float(rest.replace(',', '.'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    args = parser.parse_args()
    source_dir = args.source_dir.resolve(strict=True)
    if source_dir.name != 'textReading' or not source_dir.is_dir():
        raise ValueError('Expected the explicitly selected native textReading media directory')
    sources = sorted(source_dir.iterdir(), key=lambda p: p.name)
    if len(sources) != 10 or any(p.is_symlink() or not p.is_file() or
                                 p.suffix.lower() != '.wav' or p.resolve().parent != source_dir
                                 for p in sources):
        raise ValueError('Source must contain exactly ten regular .wav files and nothing else')
    out = CASE / 'native-map-v1.json'
    copies = CASE / 'mapped-native-v1'
    checks = CASE / 'native-decode-checks-v1'
    if out.exists() or copies.exists() or checks.exists():
        raise FileExistsError('Preserve existing v5 outputs; choose a new version explicitly')
    text = SRT.read_text(encoding='utf-8-sig').strip()
    scenes = []
    for block in re.split(r'\r?\n\s*\r?\n', text):
        lines = block.splitlines()
        a, b = lines[1].split(' --> ')
        scenes.append({'id': f'S{int(lines[0]):02}', 'start': seconds(a),
                       'end': seconds(b), 'text': ''.join(lines[2:])})
    if len(scenes) != 10 or [s['start'] for s in scenes] != [0, 20, 60, 130, 180, 220, 255, 285, 350, 385]:
        raise ValueError('Unexpected v5 SRT anchors')

    spec = importlib.util.spec_from_file_location('v4_signal_functions', ROOT / 'scripts/map_native_jianying_voice.py')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    master_sha, srt_sha = sha(MASTER), sha(SRT)
    master_audio = old.pcm(MASTER)
    master_features = old.features(master_audio)
    rows = []
    for path in sources:
        digest = sha(path)
        info = probe(path)
        stream = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        if stream['codec_name'] != 'mp3':
            raise ValueError('Actual codec changed; preserve original and inspect')
        audio = old.pcm(path)
        scores = old.ncc(master_features, old.features(audio))
        peaks = old.peaks(scores, count=4, separation=1.)
        first, second = peaks[:2]
        scene = min(scenes, key=lambda s: abs(s['start'] - first['start_seconds']))
        offset = first['start_seconds'] - scene['start']
        confidence = first['score'] >= .65 and first['score'] - second['score'] >= .08 and abs(offset) <= .25
        row = {'source_file': path.name, 'source_sha256': digest,
               'detected_codec': stream['codec_name'], 'sample_rate': stream['sample_rate'],
               'channels': stream['channels'], 'source_duration_seconds': len(audio) / old.RATE,
               'matching_speed': 1.0, 'measured_start_seconds': first['start_seconds'],
               'spectral_ncc': first['score'], 'second_peak_score_1s_separated': second['score'],
               'peak_margin': round(first['score'] - second['score'], 7),
               'scene': scene['id'], 'srt_start_seconds': scene['start'],
               'anchor_offset_seconds': round(offset, 4), 'all_peaks': peaks,
               'confidence_passed': confidence}
        rows.append(row)
        print(json.dumps({k: row[k] for k in ['scene', 'measured_start_seconds', 'spectral_ncc',
                                             'anchor_offset_seconds', 'confidence_passed']}, ensure_ascii=False), flush=True)
    rows.sort(key=lambda r: r['measured_start_seconds'])
    unique = len({r['scene'] for r in rows}) == 10
    if not unique or not all(r['confidence_passed'] for r in rows):
        raise ValueError('Ambiguous mapping; no copies or accepted map created')
    if master_sha != sha(MASTER) or srt_sha != sha(SRT):
        raise ValueError('Master or SRT changed during analysis')
    for row in rows:
        if sha(source_dir / row['source_file']) != row['source_sha256']:
            raise ValueError('Source bytes changed during analysis')
    copies.mkdir(exist_ok=False)
    checks.mkdir(exist_ok=False)
    decode_records = []
    for row in rows:
        path = source_dir / row['source_file']
        target = copies / (row['scene'] + '_剪映解说小帅_1x原声.mp3')
        with path.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        if sha(target) != row['source_sha256']:
            raise ValueError('Copy hash mismatch')
        row['copy'] = str(target.relative_to(ROOT))
        log = checks / (row['scene'] + '-decode.log')
        with log.open('xb') as stream:
            p = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error',
                                '-i', str(target), '-f', 'null', '-'], stdout=stream, stderr=subprocess.STDOUT)
        decode_records.append({'scene': row['scene'], 'exit_code': p.returncode,
                               'log_bytes': log.stat().st_size, 'copy_sha256': sha(target)})
        if p.returncode != 0 or log.stat().st_size:
            raise ValueError('Native segment full decode failed; inspect retained log')
    log = checks / 'master-decode.log'
    with log.open('xb') as stream:
        p = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error',
                            '-i', str(MASTER), '-f', 'null', '-'], stdout=stream, stderr=subprocess.STDOUT)
    if p.returncode != 0 or log.stat().st_size:
        raise ValueError('Mother track full decode failed')
    result = {'created_at': datetime.now(timezone.utc).isoformat(),
              'voice': '解说小帅', 'source_generation_speed': 1.0,
              'native_ui_speed_evidence': ['jianying-native-speed-1x-before.png', 'jianying-timeline-speed-proof.png'],
              'native_ui_verified_by': 'Parent agent; signal matching alone does not prove voice identity or generation settings',
              'master': str(MASTER.relative_to(ROOT)), 'master_sha256': master_sha,
              'master_probe': probe(MASTER), 'srt': str(SRT.relative_to(ROOT)), 'srt_sha256': srt_sha,
              'method': '8-band log-spectral local NCC at 10ms steps, 1.0x candidate only, followed by SRT-anchor validation',
              'filename_or_mtime_used_for_identity': False, 'speed_transform_applied': False,
              'unique_scene_assignment': unique, 'all_mappings_confident': True,
              'source_files_preserved': True, 'sources': rows,
              'full_decode_checks': decode_records, 'master_full_decode_exit_code': p.returncode,
              'new_tts_calls': 0, 'gui_calls': 0, 'private_drafts_or_logs_read': False,
              'caption_timing_status': 'No captions aligned here; SRT is staging anchors, not ASR or forced alignment'}
    with out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'map': str(out.relative_to(ROOT)), 'copies': 10, 'all_confident': True,
                      'full_decode_passed': True, 'matching_speed': 1.0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
