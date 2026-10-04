"""Veo takes, voice, a quiet bed, and one 1080x1920 file of an exact length."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from vidgen.config import settings


class IntegrityError(RuntimeError):
    """The finished file is missing, the wrong shape, or the wrong length."""


def ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("ffmpeg is required")
    return binary


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode("utf-8", "replace")[-600:]
        raise RuntimeError(f"ffmpeg failed: {tail}") from exc


def write_plate(path: str, seconds: float) -> None:
    """Warm still plate used when Veo is not in production."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"color=c=0x1a1208:s={settings.WIDTH}x{settings.HEIGHT}:d={max(1, seconds)}:r={settings.FPS}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", path,
    ])


def fit_slot(src: str, dst: str, slot_seconds: int) -> None:
    """Hold a short Veo take for the whole slot. The slot length is exact."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-stream_loop", "-1", "-i", src,
        "-t", str(int(slot_seconds)),
        "-vf", (
            f"scale={settings.WIDTH}:{settings.HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={settings.WIDTH}:{settings.HEIGHT},fps={settings.FPS},format=yuv420p"
        ),
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", dst,
    ])


def concat_video(paths: list[str], dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"]
    for path in paths:
        cmd += ["-i", path]
    n = len(paths)
    filt = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    cmd += [
        "-filter_complex", filt, "-map", "[v]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", dst,
    ]
    _run(cmd)


def write_drone(path: str, seconds: int) -> None:
    """Soft low drone. A Ney recording at NEY_BED_PATH replaces it when present."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bed = settings.NEY_BED_PATH
    if bed and Path(bed).exists():
        _run([
            ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-stream_loop", "-1", "-i", bed, "-t", str(seconds),
            "-ac", "2", "-ar", "48000", "-c:a", "aac", "-b:a", "160k", path,
        ])
        return
    fade = max(0, seconds - 2)
    filt = (
        "[0:a]volume=0.16[a];[1:a]volume=0.08[b];"
        "[a][b]amix=inputs=2:duration=first:normalize=0,"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade}:d=1.5"
    )
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=146.8:sample_rate=48000:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
        "-filter_complex", filt, "-c:a", "aac", "-b:a", "160k", path,
    ])


def write_tone_voice(path: str, seconds: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=196:sample_rate=48000:duration={seconds}",
        "-af", "volume=0.05", "-c:a", "aac", "-b:a", "128k", path,
    ])


def cloud_tts(text: str, path: str) -> None:
    from google.cloud import texttospeech
    client = texttospeech.TextToSpeechClient()
    voice_name = settings.TTS_VOICE
    lang = "bn-IN" if voice_name.startswith("bn-") else "en-US"
    resp = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=lang, name=voice_name),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            sample_rate_hertz=48000,
            speaking_rate=0.9,
            pitch=-1.5,
        ),
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(resp.audio_content)


def mux(video: str, voice: str, bed: str, dst: str, seconds: int) -> None:
    """Voice in front, bed underneath, picture held to the exact master length."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    filt = (
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{seconds},asetpts=PTS-STARTPTS,volume=1.0[v];"
        f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{seconds},asetpts=PTS-STARTPTS,volume=0.12[b];"
        "[v][b]amix=inputs=2:duration=first:normalize=0[a]"
    )
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", video, "-i", voice, "-i", bed,
        "-filter_complex", filt,
        "-map", "0:v:0", "-map", "[a]",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst,
    ])


def probe(path: str) -> dict:
    binary = shutil.which("ffprobe")
    if not binary:
        raise IntegrityError("ffprobe is required")
    if not Path(path).exists():
        raise IntegrityError(f"missing file {path}")
    proc = subprocess.run(
        [
            binary, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of", "json", path,
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=False,
    )
    if proc.returncode != 0:
        raise IntegrityError(proc.stderr[-400:] or "ffprobe failed")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration": float((data.get("format") or {}).get("duration") or 0),
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "has_audio": audio is not None,
        "bytes": Path(path).stat().st_size,
    }


def assert_reel(path: str, duration: int) -> dict:
    info = probe(path)
    problems = []
    if info["width"] != settings.WIDTH or info["height"] != settings.HEIGHT:
        problems.append(f"aspect {info['width']}x{info['height']} is not {settings.WIDTH}x{settings.HEIGHT}")
    if not info["has_audio"]:
        problems.append("no audio stream")
    if info["bytes"] < 50_000:
        problems.append(f"file is only {info['bytes']} bytes")
    if abs(info["duration"] - duration) > 0.75:
        problems.append(f"duration {info['duration']:.2f}s is not {duration}s")
    if problems:
        raise IntegrityError("; ".join(problems))
    return info
