"""Compose a REAL browser-frame recording with offline Mandarin narration.

The input JSONL must contain continuously captured JPEG frames.  Example:
{"scene":"01","block":"01-1","file":"frames/000001.jpg","timestamp_ms":1234.5}

Only frame timestamps are retimed.  No page, answer, cursor or model output is
generated here.  Optional static document/history supplements are explicitly
labelled, never counted as captured browser frames and never upscaled.
Originals are retained; the output directory must be new.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import wave

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENES = ROOT/'docs/submission/20261030-v2/demo-scenes.json'
DEFAULT_AUDIO = ROOT/'artifacts/submission-video-20261030/narration-rate1-v2'
DEFAULT_CAPTIONS = ROOT/'artifacts/submission-video-20261030/narration-assembled-v2/研序_初排字幕_待试听校准.srt'
FOOTER = '历史模型结果回看｜本轮未调用Pro｜微软慧慧离线合成旁白｜实际网页画面已剪辑调整节奏'
WIDTH, HEIGHT, FOOTER_HEIGHT, FPS = 1920, 1080, 96, 30
STATIC_HEADER = 136
SUPPLEMENT_KINDS = {
    'historical_screenshot': '历史静态截图补充',
    'document_page': '文档静态页补充',
    'source_excerpt': '实际源码及Prompt静态摘录',
    'git_record': '真实Git记录静态摘录',
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_path(value: Path) -> Path:
    path = value.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Media and output paths must remain inside this project')
    return path


def media_path(take: Path, value: str) -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else take/path).resolve()
    if not path.is_relative_to(take) or not path.is_file():
        raise ValueError('A recorded frame is missing or outside its take directory')
    if path.suffix.lower() not in {'.jpg', '.jpeg'}:
        raise ValueError('Expected original browser JPEG frames')
    return path


def ffconcat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


def filter_path(path: Path) -> str:
    # Quoted filter values need a protected Windows drive colon.  This string
    # is one subprocess argument, never interpolated into a shell command.
    return path.resolve().as_posix().replace(':', r'\:').replace("'", r"\'")


@dataclass(frozen=True)
class Frame:
    path: Path
    ms: float
    line: int
    block: str


def load_frames(path: Path, expected_scenes: set[str], max_gap_ms: float,
                excluded_blocks: set[str] | None = None):
    take = path.parent.resolve()
    groups: dict[str, list[Frame]] = defaultdict(list)
    last_block, closed_blocks, seen_paths = None, set(), set()
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        # Metadata/event records are allowed but never used as picture frames.
        if not isinstance(item, dict) or 'file' not in item:
            continue
        scene = str(item['scene']).zfill(2)
        block = str(item.get('block', scene+'-continuous'))
        if block in (excluded_blocks or set()):
            continue
        if scene not in expected_scenes:
            raise ValueError(f'Unexpected scene at capture line {number}')
        key = (scene, block)
        if key != last_block:
            if key in closed_blocks:
                raise ValueError('A capture block was reopened; use a distinct block ID for another chunk')
            if last_block is not None:
                closed_blocks.add(last_block)
            last_block = key
        ms = float(item['timestamp_ms'])
        if not math.isfinite(ms):
            raise ValueError(f'Non-finite timestamp at capture line {number}')
        frame = Frame(media_path(take, str(item['file'])), ms, number, block)
        if frame.path in seen_paths:
            raise ValueError('A frame file was indexed more than once; repeated screenshots are not a recording')
        seen_paths.add(frame.path)
        if groups[scene] and groups[scene][-1].block == block and ms <= groups[scene][-1].ms:
            raise ValueError(f'Non-increasing timestamp in scene {scene}')
        groups[scene].append(frame)
    if set(groups) != expected_scenes:
        raise ValueError('Missing captured scenes: '+', '.join(sorted(expected_scenes-set(groups))))
    for scene, frames in groups.items():
        if len(frames) < 10:
            raise ValueError(f'Scene {scene} needs at least 10 genuine captured frames')
        blocks = split_blocks(frames)
        for block_frames in blocks:
            if len(block_frames) < 2:
                raise ValueError(f'Scene {scene}, block {block_frames[0].block} has no continuous frame interval')
            gaps = [b.ms-a.ms for a, b in zip(block_frames, block_frames[1:])]
            if max(gaps) > max_gap_ms:
                raise ValueError(f'Scene {scene}, block {block_frames[0].block} contains a capture interruption larger than {max_gap_ms:g} ms')
        for frame in frames:
            with Image.open(frame.path) as image:
                if image.format != 'JPEG' or image.width < WIDTH or image.height < HEIGHT:
                    raise ValueError(f'Scene {scene} has a frame below native 1920x1080; no upscaling is allowed')
    return groups


def split_blocks(frames: list[Frame]) -> list[list[Frame]]:
    blocks: list[list[Frame]] = []
    for frame in frames:
        if not blocks or blocks[-1][-1].block != frame.block:
            blocks.append([])
        blocks[-1].append(frame)
    return blocks


def scene_timing(frames: list[Frame]):
    """Keep only time captured WITHIN chunks, cutting tool-call waiting gaps.

    Every chunk's last image is exposed for one median sampling interval,
    capped at a second.  Cross-chunk gaps are never filled with frozen frames.
    """
    durations, metadata = [], []
    previous = None
    for block_frames in split_blocks(frames):
        gaps = [(b.ms-a.ms)/1000 for a, b in zip(block_frames, block_frames[1:])]
        tail = min(1., statistics.median(gaps))
        raw_gap = ((block_frames[0].ms-previous['last_timestamp_ms'])/1000
                   if previous is not None else None)
        trimmed = (max(0., raw_gap-previous['last_exposure_seconds'])
                   if raw_gap is not None else 0.)
        durations.extend([*gaps, tail])
        metadata.append({'block': block_frames[0].block, 'frame_count': len(block_frames),
                         'first_timestamp_ms': block_frames[0].ms,
                         'last_timestamp_ms': block_frames[-1].ms,
                         'captured_span_seconds': sum(gaps), 'last_exposure_seconds': tail,
                         'source_gap_since_previous_block_seconds': raw_gap,
                         'gap_trimmed': previous is not None,
                         'gap_trimmed_seconds': trimmed})
        previous = metadata[-1]
    return durations, metadata


def wave_seconds(path: Path) -> float:
    with wave.open(str(path), 'rb') as audio:
        if audio.getcomptype() != 'NONE' or not audio.getnframes():
            raise ValueError('Expected a nonempty uncompressed narration WAV')
        return audio.getnframes()/audio.getframerate()


def probe(path: Path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                            check=True, stdout=subprocess.PIPE, encoding='utf-8')
    return json.loads(result.stdout)


def run_ffmpeg(arguments: list[str], log: Path):
    with log.open('xb') as stream:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-n', *arguments],
                                stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'FFmpeg failed; inspect {log}')


def label(scene: dict) -> str:
    evidence = scene.get('evidence', {})
    old_version = evidence.get('source_revision')
    if scene.get('source_label'):
        origin = str(scene['source_label'])
    elif old_version:
        origin = '历史任务 旧源码'+str(old_version)
    elif 'historical' in str(evidence.get('kind', '')):
        origin = '独立历史证据 非当前演示库'
    else:
        origin = '当前浏览器操作'
    return f"{str(scene['id']).zfill(2)}  {scene['title']}  ·  {origin}"


def wrapped_caption(text: str, max_units=75, max_lines=2) -> str:
    lines, current, units = [], '', 0.
    for char in str(text).replace('\r', ''):
        if char == '\n':
            lines.append(current); current, units = '', 0.
            continue
        advance = .55 if char.isascii() else 1.
        if current and units+advance > max_units:
            lines.append(current); current, units = '', 0.
        current += char; units += advance
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        raise ValueError('Supplement caption is too long; shorten it without hiding its meaning')
    return '\n'.join(lines)


def load_supplements(path: Path | None, expected_scenes: set[str]):
    if path is None:
        return {}
    content = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(content, dict) or not isinstance(content.get('scenes'), dict):
        raise ValueError('Supplement spec must contain a scenes object')
    status = str(content.get('status', '')).lower()
    if status.startswith(('template_pending', 'pending', 'draft')):
        raise ValueError('Supplement spec is still marked pending/draft; confirm the final source cards first')
    result = {}
    for raw_id, value in content['scenes'].items():
        scene = str(raw_id).zfill(2)
        if scene not in expected_scenes:
            raise ValueError('Unexpected supplemented scene '+scene)
        if isinstance(value, list):
            position, items = 'after', value
        elif isinstance(value, dict):
            position, items = value.get('position', 'after'), value.get('items')
        else:
            raise ValueError('Each supplemented scene must be a list or a position/items object')
        if position not in {'before', 'after'} or not isinstance(items, list):
            raise ValueError('Supplement position must be before/after and items must be a list')
        prepared = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise ValueError('Each static supplement must be an object')
            candidate = Path(item['file'])
            image_path = project_path(candidate if candidate.is_absolute() else ROOT/candidate)
            if not image_path.is_file():
                raise ValueError('Static supplement image is missing')
            source_sha256 = sha(image_path)
            if item.get('expected_sha256') and str(item['expected_sha256']).lower() != source_sha256:
                raise ValueError('Static supplement does not match its approved frozen SHA-256')
            seconds = float(item['duration_seconds'])
            if not math.isfinite(seconds) or not .5 <= seconds <= 35:
                raise ValueError('Each labelled static supplement must be 0.5 to 35 seconds')
            kind = item.get('kind')
            if kind not in SUPPLEMENT_KINDS:
                raise ValueError('Static source kind must explicitly identify history/document/source/Git evidence')
            if not item.get('label') or not item.get('note'):
                raise ValueError('Static supplements need an explicit label and explanatory note')
            with Image.open(image_path) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('Render document pages to a raster image before supplementation')
                width, height = image.size
                image.verify()
            factor = min(1., WIDTH/width, (HEIGHT-FOOTER_HEIGHT-STATIC_HEADER)/height)
            # Even dimensions simplify chroma conversion; never exceed native size.
            shown_width = max(2, int(width*factor)//2*2)
            shown_height = max(2, int(height*factor)//2*2)
            if shown_width > width or shown_height > height:
                raise ValueError('Static supplement is too small to display without upscaling')
            title = wrapped_caption(SUPPLEMENT_KINDS[kind]+'  '+str(item['label']))
            note = wrapped_caption(str(item['note'])+'  非本轮页面交互', max_units=86)
            prepared.append({'index': index, 'file': str(image_path), 'source_sha256': source_sha256,
                             'duration_seconds': seconds, 'kind': kind, 'label': str(item['label']),
                             'note': str(item['note']), 'source_date': item.get('source_date'),
                             'source_revision': item.get('source_revision'),
                             'native_width': width, 'native_height': height,
                             'display_width': shown_width, 'display_height': shown_height,
                             'upscaled': False, 'current_interaction': False,
                             'header_title': title, 'header_note': note})
        result[scene] = {'position': position, 'items': prepared}
    return result


def footer_filter(font: Path, constant: Path, description: Path) -> str:
    return (
        f"drawtext=fontfile='{filter_path(font)}':textfile='{filter_path(constant)}':expansion=none:fontcolor=white:fontsize=22:x=36:y=996,"
        f"drawtext=fontfile='{filter_path(font)}':textfile='{filter_path(description)}':expansion=none:fontcolor=0xd5e6fa:fontsize=20:x=36:y=1037"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=Path, required=True, help='Timestamped browser capture JSONL')
    parser.add_argument('--scenes', type=Path, default=DEFAULT_SCENES)
    parser.add_argument('--audio-dir', type=Path, default=DEFAULT_AUDIO)
    parser.add_argument('--captions', type=Path, default=DEFAULT_CAPTIONS,
                        help='Existing estimated SRT is copied as a draft sidecar, not burned into the picture')
    parser.add_argument('--supplements', type=Path,
                        help='Explicitly labelled static history/document evidence spec; never raw capture')
    parser.add_argument('--output-dir', type=Path, required=True, help='NEW project-local directory')
    parser.add_argument('--name', default='研序_实际界面演示_1080p_v1.mp4')
    parser.add_argument('--max-gap-ms', type=float, default=5000.)
    parser.add_argument('--exclude-block', action='append', default=[],
                        help='Exclude an explicitly rejected capture block; repeat for multiple blocks')
    parser.add_argument('--max-slowdown', type=float, default=3., help='Maximum target/raw time ratio')
    parser.add_argument('--dry-run', action='store_true', help='Read/validate only; produce no video or files')
    args = parser.parse_args()
    frames_path, scenes_path, audio_dir = map(project_path, [args.frames, args.scenes, args.audio_dir])
    captions = project_path(args.captions) if args.captions else None
    output = project_path(args.output_dir)
    if output.exists():
        raise ValueError('Refuse to overwrite an existing output directory')
    if Path(args.name).name != args.name or not args.name.lower().endswith('.mp4'):
        raise ValueError('Choose a plain MP4 filename')
    if not 100 <= args.max_gap_ms <= 10000 or not 1 <= args.max_slowdown <= 3:
        raise ValueError('Capture gap/retiming safeguards are out of bounds')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('Existing FFmpeg and FFprobe are required; this script does not install them')
    scenes = json.loads(scenes_path.read_text(encoding='utf-8'))['scenes']
    scenes_sha256 = sha(scenes_path)
    capture_sha256 = sha(frames_path)
    ids = [str(scene['id']).zfill(2) for scene in scenes]
    if len(ids) != len(set(ids)):
        raise ValueError('Scene IDs must be unique')
    supplements_path = project_path(args.supplements) if args.supplements else None
    supplements_sha256 = sha(supplements_path) if supplements_path else None
    supplements = load_supplements(supplements_path, set(ids))
    groups = load_frames(frames_path, set(ids), args.max_gap_ms, set(args.exclude_block))
    if sha(frames_path) != capture_sha256:
        raise ValueError('Capture index changed while reading; finish/freeze the selected recording before composition')
    plans, position = [], 0.
    for scene, scene_id in zip(scenes, ids):
        frames = groups[scene_id]
        durations, blocks = scene_timing(frames)
        raw_duration = sum(durations)
        voice = audio_dir/f'{int(scene_id):02}.wav'
        target_duration = wave_seconds(voice)
        supplemented = supplements.get(scene_id, {'position': 'after', 'items': []})
        static_seconds = sum(item['duration_seconds'] for item in supplemented['items'])
        browser_seconds = target_duration-static_seconds
        if browser_seconds < 8:
            raise ValueError(f'Scene {scene_id} must retain at least 8 seconds of actual browser playback')
        ratio = browser_seconds/raw_duration
        if raw_duration < 8 or not .2 <= ratio <= args.max_slowdown:
            raise ValueError(f'Scene {scene_id} capture is too short or retiming too extreme; record more genuine interaction')
        browser_offset = static_seconds if supplemented['position'] == 'before' else 0.
        supplement_offset = 0. if supplemented['position'] == 'before' else browser_seconds
        for item in supplemented['items']:
            item['target_offset_seconds'] = supplement_offset
            supplement_offset += item['duration_seconds']
        plans.append({'id': scene_id, 'title': scene['title'], 'frame_count': len(frames),
                      'raw_duration': raw_duration, 'target_duration': target_duration,
                      'browser_target_duration': browser_seconds, 'browser_target_offset': browser_offset,
                      'static_supplement_seconds': static_seconds,
                      'supplement_position': supplemented['position'], 'supplements': supplemented['items'],
                      'target_start': position, 'time_ratio': ratio,
                      'voice': str(voice), 'voice_sha256': sha(voice),
                      'frame_durations': durations, 'label': label(scene),
                      'capture_blocks': blocks,
                      'gap_trimmed': len(blocks) > 1,
                      'gap_trimmed_seconds': sum(block['gap_trimmed_seconds'] for block in blocks),
                      'historical_evidence': scene.get('evidence', {})})
        position += target_duration
    if not 300 <= position <= 480:
        raise ValueError('Measured narration total must be within 300 to 480 seconds')
    if args.dry_run:
        print(json.dumps({'validated': True, 'total_seconds': round(position, 3),
                          'excluded_blocks': args.exclude_block,
                          'scenes': [{k: p[k] for k in ('id', 'frame_count', 'raw_duration', 'target_duration', 'browser_target_duration', 'static_supplement_seconds', 'supplement_position', 'time_ratio', 'gap_trimmed', 'gap_trimmed_seconds')} for p in plans],
                          'output_created': False}, ensure_ascii=False, indent=2))
        return
    output.mkdir(parents=True, exist_ok=False)
    clips = output/'clips'; clips.mkdir()
    constant = clips/'disclosure.txt'; constant.write_text(FOOTER, encoding='utf-8')
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if not font.is_file():
        raise ValueError('The installed Microsoft YaHei font was not found')
    provenance = []
    static_provenance = []
    for plan in plans:
        scene_id, frames = plan['id'], groups[plan['id']]
        if sha(Path(plan['voice'])) != plan['voice_sha256']:
            raise ValueError('A narration file changed after timing validation')
        concat = clips/f'{scene_id}.ffconcat'
        lines = ['ffconcat version 1.0']
        for frame, duration in zip(frames, plan['frame_durations']):
            lines += ["file '"+ffconcat_path(frame.path)+"'", f'duration {duration:.9f}']
            provenance.append({'scene': scene_id, 'file': str(frame.path.relative_to(frames_path.parent)),
                               'block': frame.block, 'timestamp_ms': frame.ms, 'sha256': sha(frame.path)})
        # FFconcat needs the last image once more to honor its final exposure.
        # This adds at most one observed sampling interval, never a long still.
        lines.append("file '"+ffconcat_path(frames[-1].path)+"'")
        concat.write_text('\n'.join(lines)+'\n', encoding='utf-8')
        description = clips/f'{scene_id}-label.txt'; description.write_text(plan['label'], encoding='utf-8')
        video_filter = (
            f"setpts=(PTS-STARTPTS)*{plan['time_ratio']:.12f},fps={FPS},"
            f'scale={WIDTH}:{HEIGHT-FOOTER_HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
            f'pad={WIDTH}:{HEIGHT}:(ow-iw)/2:0:color=0x101c2c,'
            +footer_filter(font, constant, description)
        )
        browser_clip = clips/f'{scene_id}-browser.mp4'
        run_ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(concat), '-vf', video_filter,
                    '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p',
                    '-fps_mode', 'cfr', '-t', f"{plan['browser_target_duration']:.9f}",
                    '-movflags', '+faststart', str(browser_clip)], clips/f'{scene_id}-browser.log')
        static_clips = []
        for item in plan['supplements']:
            image_path = Path(item['file'])
            if sha(image_path) != item['source_sha256']:
                raise ValueError('Static evidence changed after its provenance was recorded')
            prefix = f"{scene_id}-static-{item['index']:02}"
            title_file, note_file = clips/(prefix+'-title.txt'), clips/(prefix+'-note.txt')
            title_file.write_text(item['header_title'], encoding='utf-8')
            note_file.write_text(item['header_note'], encoding='utf-8')
            static_clip = clips/(prefix+'.mp4')
            static_filter = (
                f"scale={item['display_width']}:{item['display_height']},"
                f'pad={WIDTH}:{HEIGHT}:(ow-iw)/2:{STATIC_HEADER}+({HEIGHT-FOOTER_HEIGHT-STATIC_HEADER}-ih)/2:color=0x101c2c,'
                f"drawtext=fontfile='{filter_path(font)}':textfile='{filter_path(title_file)}':expansion=none:fontcolor=white:fontsize=24:line_spacing=6:x=36:y=18,"
                f"drawtext=fontfile='{filter_path(font)}':textfile='{filter_path(note_file)}':expansion=none:fontcolor=0xd5e6fa:fontsize=20:line_spacing=3:x=36:y=84,"
                +footer_filter(font, constant, description)
            )
            run_ffmpeg(['-loop', '1', '-framerate', str(FPS), '-i', str(image_path), '-vf', static_filter,
                        '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p',
                        '-fps_mode', 'cfr', '-t', f"{item['duration_seconds']:.9f}", '-movflags', '+faststart', str(static_clip)],
                       clips/(prefix+'.log'))
            static_clips.append(static_clip)
            static_provenance.append({'scene': scene_id, **item,
                                      'rendering_mode': 'Explicitly labelled static evidence, NOT current UI capture'})
        picture_order = ([*static_clips, browser_clip] if plan['supplement_position'] == 'before'
                         else [browser_clip, *static_clips])
        scene_concat = clips/f'{scene_id}-picture.ffconcat'
        scene_concat.write_text('ffconcat version 1.0\n'+'\n'.join("file '"+ffconcat_path(p)+"'" for p in picture_order)+'\n', encoding='utf-8')
        run_ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(scene_concat), '-i', plan['voice'],
                    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-af', 'aresample=48000',
                    '-c:a', 'aac', '-b:a', '160k', '-t', f"{plan['target_duration']:.9f}",
                    '-shortest', '-movflags', '+faststart', str(clips/f'{scene_id}.mp4')], clips/f'{scene_id}.log')
        print('Encoded scene '+scene_id+'; static evidence seconds '+str(plan['static_supplement_seconds']), flush=True)
    joined = output/'join.ffconcat'
    joined.write_text('ffconcat version 1.0\n'+'\n'.join("file '"+ffconcat_path(clips/(p['id']+'.mp4'))+"'" for p in plans)+'\n', encoding='utf-8')
    movie = output/args.name
    run_ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(joined), '-c', 'copy', '-movflags', '+faststart', str(movie)], output/'concat.log')
    details = probe(movie)
    if (sha(frames_path) != capture_sha256 or sha(scenes_path) != scenes_sha256
            or (supplements_path and sha(supplements_path) != supplements_sha256)):
        raise ValueError('Capture index or scene source changed during rendering; output cannot be accepted as the frozen take')
    video = next(stream for stream in details['streams'] if stream['codec_type'] == 'video')
    audio = next(stream for stream in details['streams'] if stream['codec_type'] == 'audio')
    duration = float(details['format']['duration'])
    if video['width'] < WIDTH or video['height'] < HEIGHT or not 300 <= duration <= 480:
        raise RuntimeError('Rendered dimensions or duration failed the competition format check')
    (output/'ffprobe.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'source-frame-provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'static-supplement-provenance.json').write_text(json.dumps(static_provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    subtitle = None
    if captions is not None and captions.is_file():
        subtitle_name = '旁白字幕_初排待试听校准.srt'
        shutil.copyfile(captions, output/subtitle_name)
        subtitle = {'file': subtitle_name, 'sha256': sha(captions), 'burned_in': False,
                    'timing': 'Estimated from measured narration scene durations; listening/video alignment not reviewed.'}
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'output': movie.name,
              'output_sha256': sha(movie), 'output_bytes': movie.stat().st_size,
              'frames_jsonl_sha256': capture_sha256, 'scenes_file_sha256': scenes_sha256,
              'supplements_spec_sha256': supplements_sha256,
              'excluded_blocks': args.exclude_block,
              'width': video['width'], 'height': video['height'], 'duration_seconds': duration,
              'video_codec': video['codec_name'], 'audio_codec': audio['codec_name'],
              'source_frame_count': len(provenance), 'source_frames_retained': True,
              'new_Pro_calls': 0, 'browser_portions_are_real_capture': True,
              'static_supplements_present': bool(static_provenance),
              'static_supplement_seconds': sum(plan['static_supplement_seconds'] for plan in plans),
              'rendered_browser_seconds': sum(plan['browser_target_duration'] for plan in plans),
              'human_review_passed': False,
              'status': 'Rendered format checks passed; complete visual/audio review is still required.',
              'pacing': 'Tool-call waiting gaps between browser capture blocks were cut, never filled with frozen frames. Within-block timestamps are real; captured browser time is retimed to the remaining narration duration. Explicitly labelled static supplements are held only for their specified seconds and are NOT represented as current interaction.',
              'gap_trimmed_seconds': sum(plan['gap_trimmed_seconds'] for plan in plans),
              'narration': 'Microsoft Huihui offline synthesis, not participant original voice.',
              'subtitle': subtitle,
              'plans': plans}
    (output/'render-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'README_剪辑与来源说明.txt').write_text(
        '本片浏览器操作部分使用Codex内置浏览器真实连续捕获的JPEG帧，原帧保留，未生成网页、模型答案或鼠标动作。\n'
        '如有文档或历史静态截图补充，其画面和来源说明单独标明，不冒称本轮交互，不计入浏览器捕获帧；保持原像素或仅缩小显示。\n'
        '每个录制块内部使用真实帧时间；工具调用之间没有录下的等待空隙被剪除，不复制末帧填补。\n'
        '各段已录画面再映射至该段离线旁白长度；等待和阅读节奏经过剪辑调整，画面页脚持续说明。\n'
        '模型相关结果是已保存历史任务，本轮未调用Pro。各段旧源码/证据保留在render-report.json。\n'
        '普通话为Microsoft Huihui离线合成，不是参赛者原声。\n'
        '格式探测通过不等于全部内容、字幕或软件质量验收；必须完整回看并另行记录人工检查结果。\n', encoding='utf-8-sig')
    print(json.dumps({'output': str(movie), 'seconds': duration, 'width': video['width'],
                      'height': video['height'], 'sha256': report['output_sha256'], 'human_review_required': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
