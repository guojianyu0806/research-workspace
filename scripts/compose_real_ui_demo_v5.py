"""Validate or compose the real-UI v5 demo at original playback speed.

Only existing video/audio inputs are accepted. This program does not record UI,
call a model/TTS service, generate cards, animate, freeze, loop, or retime speech.
An optional static source crop can exclude edge overlays before normal fitting.
Relative paths are resolved against the repository root. Default: check only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, FPS, AUDIO_RATE = 1920, 1080, 30, 48000
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def existing_file(value: str) -> Path:
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file():
        raise ValueError(f"Input file does not exist: {path}")
    return path


def number(value, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"Invalid {label}: {value}")
    return result


def allowed_keys(value: dict, keys: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    extra = set(value) - keys
    if extra:
        raise ValueError(f"Unsupported {label} keys: {sorted(extra)}")


def probe(path: Path) -> dict:
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    return json.loads(subprocess.check_output(command, creationflags=CREATION_FLAGS))


def stream_duration(stream: dict, info: dict) -> float:
    return number(stream.get("duration", info.get("format", {}).get("duration", 0)),
                  "media duration", positive=True)


def media_info(path: Path, kind: str) -> dict:
    info = probe(path)
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == kind
               and not s.get("disposition", {}).get("attached_pic")]
    if not streams:
        raise ValueError(f"No {kind} stream: {path}")
    stream = streams[0]
    result = {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size,
              "codec": stream.get("codec_name"), "duration_seconds": stream_duration(stream, info)}
    if kind == "video":
        image_formats = {"image2", "image2pipe", "png_pipe", "jpeg_pipe", "gif", "apng"}
        if set(info.get("format", {}).get("format_name", "").split(",")) & image_formats:
            raise ValueError(f"Still-image/animated-image input is forbidden: {path}")
        if stream.get("codec_name") in {"png", "jpeg2000", "bmp", "gif", "webp", "apng"}:
            raise ValueError(f"Image codec is forbidden: {path}")
        rate = Fraction(stream.get("avg_frame_rate", "0/1"))
        if not 24 <= float(rate) <= 120:
            raise ValueError(f"Expected continuous video at 24-120 fps, got {rate}: {path}")
        count = stream.get("nb_frames")
        if not count or count == "N/A":
            counted = json.loads(subprocess.check_output(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                 "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                creationflags=CREATION_FLAGS))
            count = counted["streams"][0].get("nb_read_frames", "0")
        if int(count) < 2:
            raise ValueError(f"A single frame is not a continuous recording: {path}")
        result.update(width=int(stream["width"]), height=int(stream["height"]),
                      fps=float(rate), frame_count=int(count))
    else:
        result.update(sample_rate=int(stream.get("sample_rate", 0)), channels=int(stream.get("channels", 0)))
    return result


def validate(plan_path: Path) -> tuple[dict, dict]:
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    allowed_keys(plan, {"version", "final_name", "subtitles", "segments", "notes"}, "plan")
    if plan.get("version") != 1:
        raise ValueError("Expected plan version: 1")
    final_name = plan.get("final_name", "研序_真实页面演示_v5.mp4")
    if not isinstance(final_name, str) or Path(final_name).name != final_name or not final_name.lower().endswith(".mp4"):
        raise ValueError("final_name must be a bare MP4 filename")
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("A nonempty segments list is required")
    sources = {}
    prepared = []
    identifiers = set()
    timeline = 0.0

    def source_info(path: Path, kind: str) -> dict:
        key = (str(path), kind)
        if key not in sources:
            sources[key] = media_info(path, kind)
        return sources[key]

    for segment in segments:
        allowed_keys(segment, {"id", "source", "start", "duration", "speed", "crop", "narration", "evidence_note"}, "segment")
        ident = segment.get("id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident) or ident in identifiers:
            raise ValueError(f"Invalid/duplicate segment id: {ident}")
        identifiers.add(ident)
        if number(segment.get("speed", 1), f"{ident} speed") != 1:
            raise ValueError("Only original 1.0 playback speed is supported")
        source = existing_file(segment["source"])
        info = source_info(source, "video")
        start = number(segment.get("start", 0), f"{ident} start")
        duration = number(segment["duration"], f"{ident} duration", positive=True)
        frames = round(duration * FPS)
        if abs(duration * FPS - frames) > 0.00001:
            raise ValueError(f"{ident}: duration must be on the 1/{FPS} second frame grid")
        if start + duration > info["duration_seconds"] + 0.5 / FPS:
            raise ValueError(f"{ident}: video cut extends beyond its input")
        crop = segment.get("crop")
        if crop is not None:
            if (not isinstance(crop, list) or len(crop) != 4
                    or any(type(value) is not int for value in crop)):
                raise ValueError(f"{ident}: crop must be four integers [x,y,width,height]")
            x, y, crop_width, crop_height = crop
            if (x < 0 or y < 0 or crop_width < 1 or crop_height < 1
                    or x + crop_width > info["width"] or y + crop_height > info["height"]):
                raise ValueError(f"{ident}: crop must have positive dimensions and remain within the source")
        item = {"id": ident, "source": str(source), "start": start, "duration": frames / FPS,
                "frames": frames, "timeline_start": timeline, "crop": crop, "narration": None,
                "evidence_note": segment.get("evidence_note", "")}
        narration = segment.get("narration")
        if narration is not None:
            allowed_keys(narration, {"source", "offset", "source_start", "duration", "native_speed", "provider", "evidence"}, "narration")
            if number(narration.get("native_speed", 0), f"{ident} native speed") != 1:
                raise ValueError(f"{ident}: native_speed:1 is required; this program cannot undo a slow native voice")
            if narration.get("provider") != "剪映原生":
                raise ValueError(f"{ident}: narration provider must be declared as 剪映原生")
            audio = existing_file(narration["source"])
            audio_info = source_info(audio, "audio")
            audio_start = number(narration.get("source_start", 0), f"{ident} audio source_start")
            audio_duration = number(narration.get("duration", audio_info["duration_seconds"] - audio_start),
                                    f"{ident} narration duration", positive=True)
            offset = number(narration.get("offset", 0), f"{ident} narration offset")
            if audio_start + audio_duration > audio_info["duration_seconds"] + 0.001:
                raise ValueError(f"{ident}: narration cut extends beyond its input")
            if offset + audio_duration > item["duration"] + 0.001:
                raise ValueError(f"{ident}: narration does not fit; choose a longer real recording, not a speed change")
            evidence = narration.get("evidence")
            item["narration"] = {"source": str(audio), "source_start": audio_start,
                "duration": audio_duration, "offset": offset, "native_speed_declared": 1.0,
                "provider_declared": "剪映原生", "native_setting_independently_verified": False,
                "evidence": str(existing_file(evidence)) if evidence else None}
        prepared.append(item)
        timeline += item["duration"]
    subtitle = None
    if plan.get("subtitles"):
        subtitle = existing_file(plan["subtitles"])
        if subtitle.suffix.lower() != ".ass":
            raise ValueError("Only ordinary ASS subtitles are supported")
        content = subtitle.read_text(encoding="utf-8-sig")
        if not re.search(r"PlayResX:\s*1920\b", content) or not re.search(r"PlayResY:\s*1080\b", content):
            raise ValueError("ASS subtitles must use PlayResX:1920 and PlayResY:1080")
        if re.search(r"\\(?:move|t|fad|fade)\s*\(", content):
            raise ValueError("Animated subtitle tags are forbidden")
    report = {"mode": "check-only", "plan": str(plan_path), "plan_sha256": digest(plan_path),
              "duration_seconds": timeline, "frame_count": sum(s["frames"] for s in prepared),
              "resolution": [WIDTH, HEIGHT], "fps": FPS, "video_speed": 1.0, "audio_speed": 1.0,
              "segments": prepared, "sources": list(sources.values()),
              "subtitles": {"path": str(subtitle), "sha256": digest(subtitle)} if subtitle else None,
              "limitations": ["Media metadata checks do not prove that video content is a genuine UI recording; inspect recording provenance and frames.",
                              "native_speed and provider are producer declarations; verify the supplied native Jianying settings separately.",
                              "Check-only does not create a recording or final film."]}
    return {"final_name": final_name, "subtitles": subtitle, "segments": prepared, "duration": timeline}, report


def write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def run(command: list[str], log: Path, cwd: Path) -> None:
    with log.open("xb") as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=CREATION_FLAGS)
    if result.returncode:
        raise RuntimeError(f"FFmpeg failed ({result.returncode}); inspect {log}")


def render(prepared: dict, report: dict, output: Path) -> dict:
    if not any(s["narration"] for s in prepared["segments"]):
        raise ValueError("Rendering requires at least one actual native narration input; no test/fake voice is generated")
    if not output.is_relative_to(ROOT):
        raise ValueError("Output directory must be inside the project")
    # Atomic refusal also protects partial/failed previous exports from overwrite.
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "validated-plan.json", report)
    part_names = []
    for index, segment in enumerate(prepared["segments"], 1):
        name = f"part-{index:03d}.mp4"
        part_names.append(name)
        video_filter = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS}")
        if segment["crop"] is not None:
            x, y, crop_width, crop_height = segment["crop"]
            video_filter = f"crop={crop_width}:{crop_height}:{x}:{y}:exact=1," + video_filter
        run(["ffmpeg", "-hide_banner", "-nostdin", "-n", "-ss", str(segment["start"]),
             "-i", segment["source"], "-map", "0:v:0", "-an", "-vf", video_filter,
             "-frames:v", str(segment["frames"]), "-c:v", "libx264", "-threads", "4",
             "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", name],
            output / f"part-{index:03d}.log", output)
    with (output / "parts.ffconcat").open("x", encoding="ascii") as stream:
        stream.write("ffconcat version 1.0\n")
        for name in part_names:
            stream.write(f"file '{name}'\n")
    run(["ffmpeg", "-hide_banner", "-nostdin", "-n", "-f", "concat", "-safe", "1",
         "-i", "parts.ffconcat", "-c", "copy", "-an", "assembled-silent.mp4"],
        output / "concat.log", output)
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-n", "-i", "assembled-silent.mp4"]
    audio_filters = []
    labels = []
    for index, segment in enumerate([s for s in prepared["segments"] if s["narration"]], 1):
        voice = segment["narration"]
        command.extend(["-i", voice["source"]])
        delay_samples = round((segment["timeline_start"] + voice["offset"]) * AUDIO_RATE)
        audio_filters.append(
            f"[{index}:a:0]atrim=start={voice['source_start']}:duration={voice['duration']},"
            f"asetpts=PTS-STARTPTS,aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=mono,"
            f"adelay={delay_samples}S:all=1[a{index}]")
        labels.append(f"[a{index}]")
    audio_filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,"
                         f"apad=whole_dur={prepared['duration']},atrim=duration={prepared['duration']}[voice]")
    command.extend(["-filter_complex", ";".join(audio_filters), "-map", "0:v:0", "-map", "[voice]"])
    if prepared["subtitles"]:
        shutil.copyfile(prepared["subtitles"], output / "captions.ass")
        command.extend(["-vf", "subtitles=captions.ass", "-c:v", "libx264", "-threads", "4",
                        "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"])
    else:
        command.extend(["-c:v", "copy"])
    command.extend(["-c:a", "aac", "-b:a", "128k", "-ar", str(AUDIO_RATE), "-t", str(prepared["duration"]),
                    "-movflags", "+faststart", prepared["final_name"]])
    run(command, output / "mux.log", output)
    final = output / prepared["final_name"]
    video = media_info(final, "video")
    audio = media_info(final, "audio")
    if (video["width"], video["height"]) != (WIDTH, HEIGHT) or abs(video["fps"] - FPS) > 0.00001:
        raise RuntimeError("Final resolution/frame rate does not match the plan")
    if video["frame_count"] != report["frame_count"] or abs(video["duration_seconds"] - prepared["duration"]) > 1 / FPS:
        raise RuntimeError("Final video duration/frame count does not match the selected real footage")
    if abs(audio["duration_seconds"] - prepared["duration"]) > 0.1:
        raise RuntimeError("Final audio duration does not match the timeline")
    run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(final), "-f", "null", "-"],
        output / "full-decode.log", output)
    manifest = {**report, "mode": "rendered", "created_utc": datetime.now(timezone.utc).isoformat(),
                "output": str(final), "actual_video": video, "actual_audio": audio,
                "complete_decode_passed": True, "voice_retiming": False, "cards_or_animation": False,
                "human_listening_completed": False, "visual_review_completed": False}
    write_new(output / "final-manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true", help="Validate existing media and print hashes/metadata; default")
    modes.add_argument("--render", action="store_true", help="Explicitly render to a new project output directory")
    parser.add_argument("--output-dir", type=Path, help="Required for --render; existing directories are refused")
    parser.add_argument("--report", type=Path, help="Optionally save check-only report to a new JSON file")
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("Existing FFmpeg and FFprobe are required; no packages are installed")
    prepared, report = validate(existing_file(str(args.plan)))
    if args.render:
        if not args.output_dir or args.report:
            raise ValueError("--render requires --output-dir and cannot be combined with --report")
        output = args.output_dir.resolve()
        result = render(prepared, report, output)
    else:
        if args.output_dir:
            raise ValueError("--output-dir is only used with --render")
        result = report
        if args.report:
            destination = args.report.resolve()
            if not destination.is_relative_to(ROOT):
                raise ValueError("Report must be saved inside the project")
            write_new(destination, report)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
