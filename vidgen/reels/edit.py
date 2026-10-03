"""Deterministic 9:16 assembly, captions, music ducking, hard 30s clamp."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from vidgen.reels.constants import MAX_DURATION_SECONDS, REEL_FPS, REEL_HEIGHT, REEL_WIDTH
from vidgen.reels.duration import assert_duration
from vidgen.reels.schemas import ReelJob


class FFmpegMissing(RuntimeError):
    """Local encoder missing — not a Veo/TTS failure. Resume assembly after install."""


def require_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    probe = shutil.which("ffprobe")
    if not ff or not probe:
        raise FFmpegMissing(
            "ffmpeg/ffprobe is required for reel assembly. "
            "Install with: sudo apt-get update && sudo apt-get install -y ffmpeg "
            "then resume the same job. Do not start a new live run."
        )
    return ff


def _ffmpeg() -> str:
    return require_ffmpeg()


def _run(cmd: List[str]) -> None:
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-2500:]}")


def _has_audio_stream(path: str) -> bool:
    probe = shutil.which("ffprobe")
    if not probe or not Path(path).exists():
        return False
    r = subprocess.run(
        [probe, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    return r.returncode == 0 and "audio" in (r.stdout or "")


def write_compose_card(path: str, duration: float, title: str, language: str = "") -> None:
    """Deterministic 9:16 graphic beat — no Veo."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    dur = min(max(0.5, float(duration)), MAX_DURATION_SECONDS)
    ff = _ffmpeg()
    text = (title or "").replace(":", "\\:").replace("'", "")[:90]
    font = "Noto Sans Bengali" if str(language).startswith("bengali") else "Noto Sans"
    vf = (
        f"drawtext=text='{text}':fontcolor=white:fontsize=42:"
        f"font='{font}':x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=12"
    )
    try:
        _run([
            ff, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0x141414:s={REEL_WIDTH}x{REEL_HEIGHT}:r={REEL_FPS}:d={dur}",
            "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000:d={dur}",
            "-vf", vf,
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(REEL_FPS),
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            path,
        ])
    except Exception:
        write_vertical_plate(path, duration)


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


def normalize_vertical(src: str, dst: str, duration: float, keep_audio: bool = False) -> None:
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
    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts", "-i", src,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", vf,
        "-map", "0:v:0",
    ]
    if keep_audio and _has_audio_stream(src):
        cmd += ["-map", "0:a:0"]
    else:
        cmd += ["-map", "1:a:0"]
    cmd += [
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        dst,
    ]
    _run(cmd)


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
    for i, (src, dur, shot) in enumerate(zip(shot_paths, durations, job.storyboard.shots)):
        dst = str(work / f"vshot_{i:02d}.mp4")
        try:
            normalize_vertical(src, dst, dur, keep_audio=bool(shot.native_audio or shot.talking_head))
        except FFmpegMissing:
            raise
        except Exception:
            write_vertical_plate(dst, dur, color=["0x201810", "0x1c1c22", "0x182018"][i % 3])
        normalized.append(dst)

    concat_path = str(work / "concat.mp4")
    _concat(normalized, concat_path)

    use_music = True
    if job.brief and job.brief.talking_head:
        use_music = False
    if job.audio_plan and (job.audio_plan.music_mood or "").lower() in {"none", "silent", "mute"}:
        use_music = False
    mixed = str(work / "mixed.mp4")
    _mix(
        concat_path, mixed,
        music_path=music_path,
        subtitle_path=subtitle_path if should_burn_subtitles(job) else None,
        voice_tracks=voice_tracks or [],
        foley_tracks=foley_tracks or [],
        duration=total,
        language=job.brief.language if job.brief else "",
        use_music=use_music,
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
    language: str = "",
    use_music: bool = True,
) -> None:
    ff = _ffmpeg()
    vf = "eq=contrast=1.03:saturation=0.97,format=yuv420p"
    if subtitle_path and Path(subtitle_path).exists() and settings_burn(language=language):
        escaped = subtitle_path.replace("\\", r"\\").replace("'", r"\'")
        font = "Noto Sans Bengali" if str(language).startswith("bengali") else "Noto Sans"
        # Lower third only — never cover the face that is the hook
        vf += (
            f",subtitles='{escaped}':force_style='FontName={font},"
            f"FontSize=17,Outline=2,Shadow=1,MarginV=200,Alignment=2'"
        )
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", video]
    afmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters = [f"[0:v]{vf}[v]", f"[0:a]{afmt},volume=1.0[native]"]
    idx = 1
    from vidgen.reels.audio import is_playable_audio
    have_score = False
    if use_music and music_path and Path(music_path).exists() and is_playable_audio(music_path):
        cmd += ["-stream_loop", "-1", "-i", music_path]
        filters.append(f"[{idx}:a]{afmt},volume=0.05[score]")
        idx += 1
        have_score = True

    speech = []
    for track in list(voice_tracks) + list(foley_tracks):
        path = track.get("path") or track.get("local_path")
        if not path or not Path(path).exists() or not is_playable_audio(path):
            continue
        start_ms = int(float(track.get("start_seconds", track.get("start", 0))) * 1000)
        label = f"t{idx}"
        cmd += ["-i", path]
        vol = "1.0" if track.get("kind") != "product" else "0.28"
        filters.append(f"[{idx}:a]{afmt},volume={vol},adelay={start_ms}|{start_ms},apad[{label}]")
        speech.append(f"[{label}]")
        idx += 1

    voices = ["[native]"]
    if speech:
        filters.append("".join(speech) + f"amix=inputs={len(speech)}:duration=longest:normalize=0[speech]")
        voices.append("[speech]")
    if len(voices) == 1:
        filters.append(f"{voices[0]}acopy[voices]")
    else:
        filters.append("".join(voices) + f"amix=inputs={len(voices)}:duration=first:normalize=0[voices]")
    if have_score:
        filters += [
            "[score][voices]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=400[ducked]",
            "[voices][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
        ]
    else:
        filters.append("[voices]alimiter=limit=0.95[a]")
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


def _has_bengali_font() -> bool:
    from shutil import which
    import subprocess
    if not which("fc-list"):
        return False
    try:
        out = subprocess.check_output(
            ["fc-list", ":lang=bn"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except Exception:
        return False
    return bool(out.strip())


def should_burn_subtitles(job: Optional[ReelJob] = None, language: str = "") -> bool:
    """Burned captions on talking-head/comedy cover the joke. Bengali tofu is worse."""
    from vidgen.config import settings
    brief = job.brief if job is not None else None
    if brief and (brief.talking_head or brief.creative_type in {"COMEDY", "SKIT", "MEME"}):
        return False
    if not bool(settings.BURN_SUBTITLES):
        return False
    lang = language or (brief.language if brief else "")
    if str(lang).startswith("bengali") and not _has_bengali_font():
        return False
    return True


def settings_burn(language: str = "") -> bool:
    return should_burn_subtitles(language=language)
