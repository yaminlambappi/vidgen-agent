"""Deterministic 9:16 assembly, captions, music ducking, hard 30s clamp."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from vidgen.reels.constants import MAX_DURATION_SECONDS, REEL_FPS, REEL_HEIGHT, REEL_WIDTH
from vidgen.reels.duration import assert_duration
from vidgen.reels.schemas import ReelJob


def _ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg is required for reel assembly")
    return ff


def _run(cmd: List[str]) -> None:
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-2500:]}")


def write_vertical_plate(path: str, duration: float, color: str = "0x1a1a1a") -> None:
    """Generate a real 1080x1920 H.264/AAC plate (used in simulation and fallbacks)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dur = min(max(0.5, float(duration)), MAX_DURATION_SECONDS)
    ff = _ffmpeg()
    _run([
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s={REEL_WIDTH}x{REEL_HEIGHT}:r={REEL_FPS}:d={dur}",
        "-f", "lavfi", "-i", f"sine=frequency=180:sample_rate=48000:duration={dur}",
        "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(REEL_FPS),
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        path,
    ])


def normalize_vertical(src: str, dst: str, duration: float) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    if not Path(src).exists() or Path(src).stat().st_size < 32:
        write_vertical_plate(dst, duration)
        return
    ff = _ffmpeg()
    vf = (
        f"scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={REEL_WIDTH}:{REEL_HEIGHT},setsar=1,fps={REEL_FPS},format=yuv420p,"
        f"tpad=stop_mode=clone:stop_duration={duration}"
    )
    _run([
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts", "-i", src,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", vf,
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        dst,
    ])


def clamp_duration(src: str, dst: str, max_seconds: float = MAX_DURATION_SECONDS) -> None:
    cap = min(float(max_seconds), MAX_DURATION_SECONDS)
    assert_duration(cap, "export clamp")
    ff = _ffmpeg()
    _run([
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-i", src, "-t", f"{cap:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        dst,
    ])


def assemble_reel(
    job: ReelJob,
    shot_paths: List[str],
    output_path: str,
    music_path: Optional[str] = None,
    subtitle_path: Optional[str] = None,
    voice_tracks: Optional[List[dict]] = None,
    foley_tracks: Optional[List[dict]] = None,
) -> str:
    if not job.storyboard or not job.storyboard.shots:
        raise RuntimeError("cannot assemble without a storyboard")
    durations = [s.duration for s in job.storyboard.shots]
    if len(shot_paths) != len(durations):
        raise RuntimeError("shot path count does not match storyboard")
    total = sum(durations)
    assert_duration(total, "assembly timeline")

    work = Path(output_path).parent
    work.mkdir(parents=True, exist_ok=True)
    normalized = []
    for i, (src, dur) in enumerate(zip(shot_paths, durations)):
        dst = str(work / f"vshot_{i:02d}.mp4")
        try:
            normalize_vertical(src, dst, dur)
        except Exception:
            write_vertical_plate(dst, dur, color=["0x201810", "0x1c1c22", "0x182018"][i % 3])
        normalized.append(dst)

    concat_path = str(work / "concat.mp4")
    _concat(normalized, concat_path)

    mixed = str(work / "mixed.mp4")
    _mix(
        concat_path, mixed,
        music_path=music_path,
        subtitle_path=subtitle_path,
        voice_tracks=voice_tracks or [],
        foley_tracks=foley_tracks or [],
        duration=total,
    )
    clamp_duration(mixed, output_path, min(total, MAX_DURATION_SECONDS))
    return output_path


def _concat(paths: List[str], output: str) -> None:
    ff = _ffmpeg()
    inputs: List[str] = []
    chains = []
    for i, p in enumerate(paths):
        inputs += ["-i", p]
        chains.append(f"[{i}:v][{i}:a]")
    graph = "".join(chains) + f"concat=n={len(paths)}:v=1:a=1[v][a]"
    _run([
        ff, "-y", "-hide_banner", "-loglevel", "error",
        *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000",
        "-movflags", "+faststart",
        output,
    ])


def _mix(
    video: str,
    output: str,
    music_path: Optional[str],
    subtitle_path: Optional[str],
    voice_tracks: List[dict],
    foley_tracks: List[dict],
    duration: float,
) -> None:
    ff = _ffmpeg()
    vf = "eq=contrast=1.03:saturation=0.97,format=yuv420p"
    if subtitle_path and Path(subtitle_path).exists() and settings_burn():
        escaped = subtitle_path.replace("\\", r"\\").replace("'", r"\'")
        # Mobile-safe lower third — keep faces/product clear
        vf += (
            f",subtitles='{escaped}':force_style='FontName=Noto Sans,"
            f"FontSize=16,Outline=2,Shadow=1,MarginV=160,Alignment=2'"
        )
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", video]
    filters = [f"[0:v]{vf}[v]", "anullsrc=channel_layout=stereo:sample_rate=48000[silence]"]
    afmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    idx = 1
    if music_path and Path(music_path).exists():
        cmd += ["-stream_loop", "-1", "-i", music_path]
        filters.append(f"[1:a]{afmt},volume=0.16[score]")
        idx = 2
    else:
        filters.append("anullsrc=channel_layout=stereo:sample_rate=48000,volume=0[score]")

    speech = []
    for track in list(voice_tracks) + list(foley_tracks):
        path = track.get("path") or track.get("local_path")
        if not path or not Path(path).exists():
            continue
        start_ms = int(float(track.get("start_seconds", track.get("start", 0))) * 1000)
        label = f"t{idx}"
        cmd += ["-i", path]
        vol = "1.0" if track.get("kind") != "product" else "0.35"
        filters.append(f"[{idx}:a]{afmt},volume={vol},adelay={start_ms}|{start_ms},apad[{label}]")
        speech.append(f"[{label}]")
        idx += 1

    if speech:
        filters.append("".join(speech) + f"amix=inputs={len(speech)}:duration=longest:normalize=0[speech_raw]")
        filters += [
            "[speech_raw]asplit[speech_sc][speech_mix]",
            "[score][speech_sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[ducked]",
        ]
        score = "[ducked]"
        voice = "[speech_mix]"
    else:
        filters.append("[silence]acopy[speech_mix]")
        score = "[score]"
        voice = "[speech_mix]"

    filters.append(
        f"[silence][{score.strip('[]')}]{voice}amix=inputs=3:duration=first:normalize=0,dynaudnorm=p=0.9:m=8[a]"
    )
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-t", f"{min(duration, MAX_DURATION_SECONDS):.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        output,
    ]
    _run(cmd)


def settings_burn() -> bool:
    from vidgen.config import settings
    return bool(settings.BURN_SUBTITLES)
