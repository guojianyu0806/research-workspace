"""Join offline speech and prepare ESTIMATED captions, never a fake video."""
import argparse
import json
from pathlib import Path
import re
import wave


def stamp(seconds):
    milliseconds = round(seconds*1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}'


def chunks(text):
    result, current = [], ''
    for part in re.split(r'(?<=[。！？；，])', text):
        if current and len(current)+len(part) > 28:
            result.append(current); current = ''
        while len(part) > 28:
            result.append(part[:28]); part = part[28:]
        current += part
    if current:
        result.append(current)
    return result


def weight(text):
    return sum(.5 if char in '。！？；，、：' else .55 if char.isascii() else 1 for char in text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenes', type=Path, required=True)
    parser.add_argument('--audio-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    assert output.is_relative_to(root) and not output.exists()
    scenes = json.loads(args.scenes.read_text(encoding='utf-8'))['scenes']
    output.mkdir(parents=True, exist_ok=False)
    waveform = output/'研序_离线合成旁白_Huihui.wav'
    timeline, captions, params = [], [], None
    position = 0.
    with wave.open(str(waveform), 'wb') as joined:
        for index, scene in enumerate(scenes, 1):
            path = args.audio_dir/f'{index:02}.wav'
            with wave.open(str(path), 'rb') as voice:
                current = (voice.getnchannels(), voice.getsampwidth(), voice.getframerate(), voice.getcomptype(), voice.getcompname())
                if params is None:
                    params = current
                    joined.setnchannels(current[0]); joined.setsampwidth(current[1]); joined.setframerate(current[2])
                assert params == current, 'Speech segments must share a PCM format'
                frames = voice.getnframes(); duration = frames/voice.getframerate()
                joined.writeframes(voice.readframes(frames))
            start = position; position += duration
            segments = chunks(scene['narration']); total = sum(weight(part) for part in segments)
            cursor = start
            for part in segments:
                end = cursor+duration*weight(part)/total
                captions.append(f'{len(captions)+1}\n{stamp(cursor)} --> {stamp(end)}\n{part}\n')
                cursor = end
            timeline.append({'scene': index, 'title': scene['title'], 'start': round(start, 3),
                             'end': round(position, 3), 'duration': round(duration, 3),
                             'actions': scene['actions'], 'evidence': scene['evidence']})
    (output/'研序_初排字幕_待试听校准.srt').write_text('\n'.join(captions), encoding='utf-8-sig')
    report = {'duration_seconds': round(position, 3), 'voice': 'Microsoft Huihui Desktop - Chinese (Simplified)',
              'offline': True, 'actual_video_created': False, 'caption_timing': 'Estimated within measured scene audio boundaries; listening and video alignment remain required.',
              'segments': timeline}
    (output/'录制时间轴.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['研序旁白实测时间轴', '这是音频和录制准备，不是MP4；字幕仍需试听校准。', '']
    for item in timeline:
        lines.append(f"{stamp(item['start'])} 至 {stamp(item['end'])}  {item['scene']:02} {item['title']}")
        lines.extend('- '+action for action in item['actions']); lines.append('')
    (output/'手动录制时间表.txt').write_text('\n'.join(lines), encoding='utf-8-sig')
    print(json.dumps({'duration_seconds': round(position, 3), 'waveform': str(waveform), 'caption_entries': len(captions), 'actual_video_created': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
