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


JOURNEY_VOICE = "en-US-Journey-D"
NEURAL_VOICE = "en-US-Neural2-D"


def _voice_unavailable(exc: Exception) -> bool:
    msg = str(exc).lower()
    if any(k in msg for k in ("429", "503", "timeout", "timed out", "resource_exhausted")):
        return False
    return any(k in msg for k in ("voice", "not found", "invalid", "unrecognized", "400", "journey"))


def _synthesize(text: str, path: str, voice_name: str) -> None:
    from google.cloud import texttospeech
    client = texttospeech.TextToSpeechClient()
    lang = "bn-IN" if voice_name.startswith("bn-") else "en-US"
    resp = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=lang, name=voice_name),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            sample_rate_hertz=48000,
            speaking_rate=0.85,
            pitch=-2.5,
        ),
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(resp.audio_content)


def cloud_tts(text: str, path: str, voice_name: str = JOURNEY_VOICE, ledger=None) -> str:
    """Deep, slow Journey voice. Neural2-D is used only when Journey is rejected."""
    try:
        _synthesize(text, path, voice_name)
        return voice_name
    except Exception as exc:
        if voice_name == NEURAL_VOICE or not _voice_unavailable(exc):
            raise
        if ledger is not None:
            ledger.charge("tts")
        _synthesize(text, path, NEURAL_VOICE)
        return NEURAL_VOICE


def _ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    hours, cs = divmod(cs, 360_000)
    minutes, cs = divmod(cs, 6_000)
    secs, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def write_captions(script: str, duration: int, path: str, words_per_cue: int = 3) -> str:
    """A few whispered lines, each fading in, timed across the whole master."""
    words = [w for w in (script or "").split() if w]
    if not words:
        words = ["..."]
    groups = [words[i:i + words_per_cue] for i in range(0, len(words), words_per_cue)]
    total = len(words)
    font = _caption_font()
    cursor = 0.0
    events = []
    for index, group in enumerate(groups):
        if index == len(groups) - 1:
            end = float(duration)
        else:
            end = duration * (sum(len(g) for g in groups[: index + 1]) / total)
        text = " ".join(group).replace("{", "(").replace("}", ")")
        events.append(
            f"Dialogue: 0,{_ass_time(cursor)},{_ass_time(end)},Whisper,,0,0,0,,{{\\fad(700,280)}}{text}"
        )
        cursor = end
    body = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {settings.WIDTH}",
        f"PlayResY: {settings.HEIGHT}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Whisper,{font},46,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
        "0,1,0,0,100,100,1,0,1,1,1,2,80,80,180,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(body, encoding="utf-8")
    return path


def _caption_font() -> str:
    preferred = ("Noto Sans", "Montserrat", "Arial", "Liberation Sans", "DejaVu Sans", "Inter")
    try:
        listed = subprocess.run(
            ["fc-list", ":family"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=10, check=False,
        ).stdout.lower()
    except Exception:
        listed = ""
    for name in preferred:
        if name.lower() in listed:
            return name
    return "Liberation Sans"


def _filter_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")


def mux(video: str, voice: str, bed: str, dst: str, seconds: int, captions: str) -> None:
    """A whispered prayer in an empty shrine, drone 20 dB under it, captions burned."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    escaped = _filter_path(captions)
    filt = (
        f"[0:v]ass='{escaped}'[vid];"
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{seconds},"
        f"asetpts=PTS-STARTPTS,aecho=0.8:0.88:50:0.4,volume=1.0[v];"
        f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{seconds},"
        f"asetpts=PTS-STARTPTS,volume=-20dB[b];"
        "[v][b]amix=inputs=2:duration=first:normalize=0[a]"
    )
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", video, "-i", voice, "-i", bed,
        "-filter_complex", filt,
        "-map", "[vid]", "-map", "[a]",
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
