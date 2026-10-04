"""Picture, voice, and the gates a 30-second munajat must pass before it ships."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from vidgen.config import settings
from vidgen.sufi.plan import VisualBible, _bangla_count

# Gentle duck: a few dB under the voice, then a slow release so pauses stay alive.
DUCK = (
    "sidechaincompress=threshold=0.2:ratio=2:attack=20:release=400:"
    "makeup=1:knee=6:detection=rms"
)
VOICE_COLOR = (
    "highpass=f=70,"
    "equalizer=f=180:width_type=q:width=1:g=2.5,"
    "asoftclip=type=tanh:threshold=0.85:output=0.9,"
    "aecho=0.8:0.9:180|420:0.22|0.12,"
    "alimiter=limit=0.89"
)
_LATIN_MAWLA = re.compile(r"\b[Mm]a[uw]la\b")
_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?(?:\d+(?:\.\d+)?)|inf) dB")
_MAX_VOLUME = re.compile(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?)|inf) dB")


class IntegrityError(RuntimeError):
    """The finished file is missing, the wrong shape, or the wrong length."""


class QualityError(RuntimeError):
    """The pictures, the voice, or the master failed a gate."""


class PronunciationError(QualityError):
    """The heard line is not the Bangla that was written."""


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
    """A moving wash. A still color would fail the opening-motion check."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    length = max(1, seconds)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x243044:s=180x320:d={length}:r={settings.FPS}",
        "-f", "lavfi", "-i", f"color=c=0xe6c27a:s=48x320:d={length}:r={settings.FPS}",
        "-filter_complex",
        (
            "[0:v][1:v]overlay=x='8+90*t':y=0:shortest=1,"
            f"scale={settings.WIDTH}:{settings.HEIGHT}:flags=fast_bilinear,"
            f"fps={settings.FPS},format=yuv420p"
        ),
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path,
    ])


def write_solid(path: str, seconds: float, color: str = "0x334455") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=160x160:d={max(1, seconds)}:r=8",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", path,
    ])


def write_flicker(path: str, seconds: float = 1) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    expr = "if(eq(mod(N\\,2)\\,0)\\,16\\,240)"
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        (
            f"color=c=black:s=160x160:d={max(1, seconds)}:r=8,format=rgb24,"
            f"geq=r='{expr}':g='{expr}':b='{expr}'"
        ),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", path,
    ])


def fit_slot(src: str, dst: str, slot_seconds: int) -> None:
    """Keep the first seconds of the take. Do not loop a short clip."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", src,
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


def concat_audio(paths: list[str], dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"]
    for path in paths:
        cmd += ["-i", path]
    n = len(paths)
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    cmd += [
        "-filter_complex", filt, "-map", "[a]",
        "-c:a", "aac", "-b:a", "160k", dst,
    ]
    _run(cmd)


def fit_phrase(src: str, dst: str, seconds: float = 5) -> None:
    """Pad with silence or trim. Never time-stretch the speech."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", src,
        "-af", f"apad=whole_dur={seconds},atrim=0:{seconds},asetpts=PTS-STARTPTS",
        "-t", str(seconds),
        "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "128k", dst,
    ])


def write_drone(path: str, seconds: int) -> None:
    """A continuous bed. Ney when a file is configured, otherwise a low drone, plus a whisper of air."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    noise = f"anoisesrc=color=brown:sample_rate=48000:amplitude=0.15:duration={seconds}"
    bed = settings.NEY_BED_PATH
    if bed and Path(bed).exists():
        _run([
            ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-stream_loop", "-1", "-i", bed,
            "-f", "lavfi", "-i", noise,
            "-filter_complex",
            "[0:a]aformat=channel_layouts=stereo,volume=0.9[n];"
            "[1:a]aformat=channel_layouts=stereo,highpass=f=90,volume=0.04[h];"
            f"[n][h]amix=inputs=2:duration=first:normalize=0,atrim=0:{seconds}",
            "-t", str(seconds), "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "160k", path,
        ])
        return
    fade = max(0, seconds - 2)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=146.8:sample_rate=48000:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
        "-f", "lavfi", "-i", noise,
        "-filter_complex",
        "[0:a]volume=0.22[a];[1:a]volume=0.10[b];"
        "[2:a]highpass=f=90,volume=0.045[n];"
        "[a][b][n]amix=inputs=3:duration=first:normalize=0,"
        f"afade=t=in:st=0:d=1.2,afade=t=out:st={fade}:d=1.2",
        "-c:a", "aac", "-b:a", "160k", path,
    ])


def write_tone_voice(path: str, seconds: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=196:sample_rate=48000:duration={seconds}",
        "-af", "volume=0.08", "-c:a", "aac", "-b:a", "128k", path,
    ])


def write_pulsed_voice(path: str, seconds: int = 6) -> None:
    """Loud, then quiet, then loud. Used to prove the bed does not gate shut."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
        "-af", "volume='if(between(t,2,4),0.001,0.8)'",
        "-c:a", "aac", "-b:a", "160k", path,
    ])


def render_ducked_bed(voice: str, bed: str, dst: str, seconds: int) -> None:
    """The bed alone, after the same duck the master uses."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    filt = (
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{seconds},"
        f"asetpts=PTS-STARTPTS[bed];"
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{seconds},"
        f"asetpts=PTS-STARTPTS[key];"
        f"[bed][key]{DUCK}[d]"
    )
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", bed, "-i", voice,
        "-filter_complex", filt, "-map", "[d]",
        "-t", str(seconds), "-c:a", "aac", "-b:a", "160k", dst,
    ])


def protect_mawla(text: str, break_ms: int | None = None) -> str:
    """Keep মাওলা from collapsing into মৌলা. The micro-break is necessary, not sufficient."""
    if "মৌলা" in (text or ""):
        raise PronunciationError("মৌলা is not permitted")
    spoken = _LATIN_MAWLA.sub("মাওলা", text or "")
    gap = settings.TTS_MAWLA_BREAK_MS if break_ms is None else int(break_ms)
    spoken = spoken.replace("মাওলা", f'মা<break time="{gap}ms"/>ওলা')
    if "মৌলা" in spoken:
        raise PronunciationError("মৌলা is not permitted")
    return spoken


def build_tts_request(text: str, break_ms: int | None = None, voice_name: str | None = None) -> dict:
    """The synthesis request. Rate and pitch are settings, not a property of the voice id."""
    gap = settings.TTS_MAWLA_BREAK_MS if break_ms is None else int(break_ms)
    body = protect_mawla(text, gap)
    rate = settings.TTS_SPEAKING_RATE
    pitch = settings.TTS_PITCH
    percent = max(1, int(round(rate * 100)))
    ssml = f'<speak><prosody rate="{percent}%" pitch="{pitch:g}st">{body}</prosody></speak>'
    return {
        "voice": voice_name or settings.TTS_VOICE,
        "language": settings.TTS_LANGUAGE,
        "speaking_rate": rate,
        "pitch": pitch,
        "break_ms": gap,
        "ssml": ssml,
    }


def _synthesize(request: dict, path: str) -> None:
    from google.cloud import texttospeech
    client = texttospeech.TextToSpeechClient()
    # Prosody in the SSML already carries rate and pitch. Leaving AudioConfig
    # at unity keeps those values from being applied a second time.
    resp = client.synthesize_speech(
        input=texttospeech.SynthesisInput(ssml=request["ssml"]),
        voice=texttospeech.VoiceSelectionParams(
            language_code=request["language"],
            name=request["voice"],
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            sample_rate_hertz=48000,
            speaking_rate=1.0,
            pitch=0.0,
        ),
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(resp.audio_content)


def cloud_tts(text: str, path: str, break_ms: int | None = None, voice_name: str | None = None) -> dict:
    request = build_tts_request(text, break_ms=break_ms, voice_name=voice_name)
    _synthesize(request, path)
    return request


def check_pronunciation(phrase: str, transcript: str) -> None:
    heard = transcript or ""
    if "মৌলা" in heard:
        raise PronunciationError("heard মৌলা")
    if _bangla_count(heard) == 0:
        raise PronunciationError("transcript has no Bangla")
    if "মাওলা" in (phrase or "") and "মাওলা" not in heard:
        raise PronunciationError("মাওলা was not heard")


def transcribe_bangla(path: str) -> str:
    wav = str(Path(path).with_suffix(".stt.wav"))
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", path, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav,
    ])
    from google.cloud import speech
    client = speech.SpeechClient()
    audio = speech.RecognitionAudio(content=Path(wav).read_bytes())
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code=settings.TTS_LANGUAGE,
    )
    response = client.recognize(config=config, audio=audio)
    parts = []
    for result in response.results:
        if result.alternatives:
            parts.append(result.alternatives[0].transcript)
    return " ".join(parts)


def _ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    hours, cs = divmod(cs, 360_000)
    minutes, cs = divmod(cs, 6_000)
    secs, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _parse_ass_time(value: str) -> float:
    hours, minutes, rest = value.split(":")
    secs, cs = rest.split(".")
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(cs) / 100


def write_captions(glyphs: list[str], path: str, slot: float = 5) -> str:
    """One quiet Bangla thought per beat. The spoken line stays in the voice."""
    font = _caption_font()
    events = []
    for index, glyph in enumerate(glyphs):
        start = index * slot
        end = start + slot
        text = (glyph or "").replace("{", "(").replace("}", ")")
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Whisper,,0,0,0,,"
            f"{{\\fad(480,640)\\move(540,1240,540,1160)}}{text}"
        )
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
        f"Style: Whisper,{font},42,&H00F2F2F2,&H000000FF,&H00101010,&H64000000,"
        "0,0,0,0,100,100,0,0,1,1,0,2,80,80,220,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(body, encoding="utf-8")
    return path


def caption_events(path: str) -> list[tuple[float, float, str]]:
    text = Path(path).read_text(encoding="utf-8")
    events = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        try:
            _prefix, start, end, _style, _name, _ml, _mr, _mv, _effect, body = line.split(",", 9)
        except ValueError:
            continue
        shown = body
        if shown.startswith("{") and "}" in shown:
            shown = shown.split("}", 1)[1]
        events.append((_parse_ass_time(start), _parse_ass_time(end), shown.strip()))
    return events


def caption_faults(path: str, glyphs: list[str], phrases: list[str] | None = None) -> list[str]:
    events = caption_events(path)
    faults = []
    if len(events) != len(glyphs):
        faults.append(f"expected {len(glyphs)} cues, found {len(events)}")
        return faults
    for index, glyph in enumerate(glyphs):
        start, end, shown = events[index]
        if abs(start - index * 5) > 0.05 or abs(end - (index + 1) * 5) > 0.05:
            faults.append(f"cue {index + 1} is mistimed")
        if glyph not in shown:
            faults.append(f"cue {index + 1} is missing its glyph")
        phrase = (phrases or [""])[index] if phrases else ""
        if phrase and phrase != glyph and phrase in shown:
            faults.append(f"cue {index + 1} burns the spoken line")
    return faults


def _caption_font() -> str:
    preferred = (
        "Noto Serif Bengali", "Noto Sans Bengali", "Noto Sans",
        "Montserrat", "Arial", "Liberation Sans", "DejaVu Sans",
    )
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
    """Voice in front, a shrine tail, and a bed that ducks a little and then returns."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    escaped = _filter_path(captions)
    filt = (
        f"[0:v]ass='{escaped}'[vid];"
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,{VOICE_COLOR},"
        f"apad,atrim=0:{seconds},asetpts=PTS-STARTPTS,asplit=2[voice][key];"
        f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{seconds},"
        f"asetpts=PTS-STARTPTS,volume=-12dB[bed];"
        f"[bed][key]{DUCK}[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
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


def volume_levels(path: str, start: float = 0, duration: float | None = None) -> tuple[float, float]:
    cmd = [ffmpeg(), "-hide_banner", "-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", path, "-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
    err = (proc.stderr or b"").decode("utf-8", "replace")

    def _parse(match: re.Match | None) -> float:
        if not match or match.group(1) == "inf":
            return -99.0
        return float(match.group(1))

    return _parse(_MEAN_VOLUME.search(err)), _parse(_MAX_VOLUME.search(err))


def frame_rgb(path: str, index: int) -> bytes:
    proc = subprocess.run(
        [
            ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", path,
            "-vf", f"select=eq(n\\,{int(index)})", "-vframes", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
    )
    if proc.returncode != 0:
        return b""
    return proc.stdout or b""


def mean_abs_diff(left: bytes, right: bytes) -> float:
    length = min(len(left), len(right))
    if length < 3:
        return 0.0
    total = 0
    count = 0
    for i in range(0, length - 2, 64):
        total += abs(left[i] - right[i])
        count += 1
    return total / max(count, 1)


def opening_is_static(path: str) -> bool:
    """Shot 1 must already be moving between ~0.1s and ~0.4s."""
    early = frame_rgb(path, 2)
    later = frame_rgb(path, 10)
    if not early or not later:
        return True
    return mean_abs_diff(early, later) < 2.0


def is_flicker(path: str) -> bool:
    previous = b""
    jumps = 0
    for index in range(8):
        raw = frame_rgb(path, index)
        if not raw:
            break
        if previous and mean_abs_diff(previous, raw) > 70:
            jumps += 1
        previous = raw
    return jumps >= 3


def blackframe_count(path: str) -> int:
    proc = subprocess.run(
        [
            ffmpeg(), "-hide_banner", "-i", path,
            "-vf", "blackframe=amount=98:threshold=32", "-f", "null", "-",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120,
    )
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return err.count("pblack:")


def _merge_findings(*groups: list[dict]) -> list[dict]:
    by: dict[int, list[str]] = {}
    for group in groups:
        for item in group:
            index = int(item.get("index", -1))
            if index < 0:
                continue
            codes = by.setdefault(index, [])
            for code in item.get("codes") or []:
                if code not in codes:
                    codes.append(code)
    return [{"index": index, "codes": codes} for index, codes in sorted(by.items()) if codes]


def inspect_shots(paths: list[str], reviewer=None, bible: VisualBible | dict | None = None) -> list[dict]:
    """Local frame checks, then an optional reviewer that can see identity across shots."""
    local = []
    for index, path in enumerate(paths):
        codes = []
        if blackframe_count(path) > 0:
            codes.append("black_frame")
        if is_flicker(path):
            codes.append("flicker")
        if index == 0 and opening_is_static(path):
            codes.append("static_opening")
        if codes:
            local.append({"index": index, "codes": codes})
    extra = []
    if reviewer is not None:
        identity = bible.model_dump() if isinstance(bible, VisualBible) else (bible or {})
        extra = list(reviewer(paths, identity) or [])
    return _merge_findings(local, extra)


def extract_jpg(path: str, seconds: float, dest: str) -> str:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{seconds:.3f}", "-i", path, "-frames:v", "1", dest,
    ])
    return dest


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


def assert_film(
    path: str,
    *,
    segments: list[str],
    voice_path: str,
    bed_path: str,
    captions_path: str,
    glyphs: list[str],
    phrases: list[str] | None = None,
) -> dict:
    """Refuse the master unless the picture, the voice, and the captions agree."""
    info = probe(path)
    problems = []
    if info["width"] != settings.WIDTH or info["height"] != settings.HEIGHT:
        problems.append(f"aspect {info['width']}x{info['height']} is not {settings.WIDTH}x{settings.HEIGHT}")
    if not info["has_audio"]:
        problems.append("no audio stream")
    if abs(info["duration"] - 30) > 0.15:
        problems.append(f"duration {info['duration']:.2f}s is not 30.0s")
    if len(segments) != 6:
        problems.append(f"expected six segments, found {len(segments)}")
    for index, segment in enumerate(segments, start=1):
        shot = probe(segment)
        if abs(shot["duration"] - 5) > 0.15:
            problems.append(f"segment {index} is {shot['duration']:.2f}s")
        if shot["width"] != settings.WIDTH or shot["height"] != settings.HEIGHT:
            problems.append(f"segment {index} is {shot['width']}x{shot['height']}")
    for index in range(6):
        mean, _peak = volume_levels(voice_path, start=index * 5, duration=1.2)
        if mean <= -50:
            problems.append(f"beat {index + 1} has no speech in its opening")
        bed_mean, _bed_peak = volume_levels(bed_path, start=index * 5, duration=5)
        if bed_mean <= -50:
            problems.append(f"the bed falls silent in beat {index + 1}")
        master_mean, _master_peak = volume_levels(path, start=index * 5, duration=5)
        if master_mean <= -55:
            problems.append(f"the master is silent in beat {index + 1}")
    _mean, peak = volume_levels(path)
    if peak > -0.5:
        problems.append(f"audio peaks at {peak:.1f} dBFS")
    if blackframe_count(path) > 0:
        problems.append("black frames in the master")
    problems.extend(caption_faults(captions_path, glyphs, phrases))
    if problems:
        raise QualityError("; ".join(problems))
    return info
