"""Voice, music, foley, and captions for Reels. Deterministic assembly; TTS is gated."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

from vidgen.config import settings
from vidgen.reels.constants import MAX_DURATION_SECONDS
from vidgen.reels.duration import estimate_speech_seconds
from vidgen.reels.language import infer_language, is_garbled_caption
from vidgen.reels.schemas import AudioCue, AudioPlan, EditPlan, ReelJob, ScriptLine


def select_voice(job: ReelJob) -> Tuple[str, str, float, float]:
    """Returns (voice_name, language_code, rate, pitch)."""
    lang = infer_language(
        (job.brief.language if job.brief else "") or (job.request.language if job.request else ""),
        job.request.idea if job.request else "",
    )
    gender = (job.request.voice_gender or "").lower()
    if lang.startswith("bengali"):
        if gender in {"male", "man"}:
            return settings.TTS_VOICE_BN_MALE, "bn-IN", 0.96, -1.0
        return settings.TTS_VOICE_BN, "bn-IN", 0.96, -0.5
    if gender in {"female", "woman"}:
        return "en-US-Neural2-F", "en-US", 0.95, -1.0
    if gender in {"male", "man"}:
        return "en-US-Neural2-J", "en-US", 0.92, -2.0
    return settings.TTS_VOICE, "en-US", 0.94, -1.5


def build_audio_plan(job: ReelJob) -> AudioPlan:
    assert job.script and job.brief and job.storyboard
    voice, _, rate, pitch = select_voice(job)
    cues: List[AudioCue] = []
    for line in job.script.body_lines:
        if not line.text.strip():
            continue
        if line.on_camera:
            # Visible speech uses native Veo audio — do not overlay a second voice.
            continue
        cues.append(AudioCue(
            kind="dialogue",
            text=line.text,
            start_seconds=line.start_seconds,
            duration_seconds=line.estimated_seconds or estimate_speech_seconds(line.text, job.script.language, rate),
            voice_name=voice,
            speaking_rate=rate,
            pitch=pitch,
        ))
    foley: List[AudioCue] = []
    for shot in job.storyboard.shots:
        if "product" in shot.purpose or "contact" in (shot.sound or ""):
            foley.append(AudioCue(
                kind="product",
                text=shot.sound,
                start_seconds=shot.start_time + min(1.2, shot.duration * 0.35),
                duration_seconds=0.35,
            ))
    ctype = job.brief.creative_type if job.brief else ""
    mood = "none" if job.brief.talking_head or ctype in {"COMEDY", "SKIT", "MEME"} else {
        "EMOTIONAL": "warm sparse piano",
        "COMEDY": "light plucked rhythm",
        "DIRECT_RESPONSE_AD": "tight muted pulse",
        "STREET_STYLE": "soft urban bed",
        "CINEMATIC_COMMERCIAL": "low strings, restrained",
    }.get(job.brief.content_mode, "quiet analog pad")
    return AudioPlan(
        language=job.script.language,
        voice_name=voice,
        dialogue_cues=cues,
        music_mood=mood,
        foley=foley,
        duck_under_dialogue=True,
    )


def synthesize_dialogue(job: ReelJob, root: Path, tts_fn=None) -> AudioPlan:
    plan = job.audio_plan or build_audio_plan(job)
    root.mkdir(parents=True, exist_ok=True)
    for i, cue in enumerate(plan.dialogue_cues):
        out = root / f"vo_{i:02d}.mp3"
        if tts_fn:
            tts_fn(cue.text, str(out), cue.voice_name, cue.speaking_rate, cue.pitch)
        else:
            _write_tone(str(out), max(0.4, cue.duration_seconds), kind="voice")
        cue.local_path = str(out)
    job.audio_plan = plan
    return plan


def is_playable_audio(path: str) -> bool:
    """Reject stub files written when ffmpeg was missing."""
    if not path:
        return False
    p = Path(path)
    if not p.exists() or p.stat().st_size < 64:
        return False
    head = p.read_bytes()[:16]
    if head.startswith(b"stub"):
        return False
    probe = shutil.which("ffprobe")
    if not probe:
        return False
    r = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    if r.returncode != 0:
        return False
    try:
        return float((r.stdout or "0").strip() or 0) > 0.05
    except ValueError:
        return False


def ensure_mixable_audio(job: ReelJob, root: Path) -> None:
    """Rebuild deterministic music/foley if a prior run wrote stub bytes. Never calls TTS/Veo."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    total = 12.0
    if job.storyboard and job.storyboard.total_duration:
        total = float(job.storyboard.total_duration)
    elif job.request:
        total = float(job.request.duration_seconds)
    mood = (job.audio_plan.music_mood if job.audio_plan else "") or ""
    if mood.strip().lower() not in {"", "none", "silent", "mute"}:
        music = (job.audio_plan.music_path if job.audio_plan and job.audio_plan.music_path
                 else str(root / "music.m4a"))
        if not is_playable_audio(music):
            print(f"[AUDIO] regenerating invalid score {music}")
            render_music(job, root, total)
    if job.audio_plan:
        for cue in job.audio_plan.foley:
            if cue.local_path and not is_playable_audio(cue.local_path):
                render_foley(job, root)
                break
        job.audio_plan.dialogue_cues = [
            c for c in job.audio_plan.dialogue_cues
            if not c.local_path or is_playable_audio(c.local_path)
        ]
        job.audio_plan.foley = [
            c for c in job.audio_plan.foley
            if not c.local_path or is_playable_audio(c.local_path)
        ]


def render_music(job: ReelJob, root: Path, duration: float) -> str:
    mood = (job.audio_plan.music_mood if job.audio_plan else "") or ""
    if mood.strip().lower() in {"", "none", "silent", "mute"}:
        if job.audio_plan:
            job.audio_plan.music_path = ""
        return ""
    path = root / "music.m4a"
    freqs = _mood_freqs(mood)
    from vidgen.reels.duration import current_cap
    _write_score(str(path), min(duration, current_cap()), freqs)
    if job.audio_plan:
        job.audio_plan.music_path = str(path)
    return str(path)


def render_foley(job: ReelJob, root: Path) -> List[AudioCue]:
    plan = job.audio_plan or build_audio_plan(job)
    for i, cue in enumerate(plan.foley):
        out = root / f"foley_{i:02d}.wav"
        _write_tone(str(out), cue.duration_seconds or 0.3, kind="foley")
        cue.local_path = str(out)
    job.audio_plan = plan
    return plan.foley


def captions_from_audio_plan(job: ReelJob) -> str:
    """Captions come from the spoken script, including on-camera native lines."""
    lang = (job.script.language if job.script else "") or (job.brief.language if job.brief else "")
    spoken: List[tuple] = []
    if job.script:
        for line in job.script.body_lines:
            text = (line.text or "").strip()
            if not text or is_garbled_caption(text, lang):
                continue
            spoken.append((line.start_seconds, max(0.6, line.estimated_seconds), text))
    if not spoken and job.audio_plan:
        for cue in job.audio_plan.dialogue_cues:
            text = (cue.text or "").strip()
            if not text or is_garbled_caption(text, lang):
                continue
            spoken.append((cue.start_seconds, max(0.6, cue.duration_seconds), text))
    idx, blocks = 1, []
    for start, dur, text in spoken:
        blocks += [str(idx), f"{_srt(start)} --> {_srt(start + dur)}", text, ""]
        idx += 1
    return "\n".join(blocks)


def write_captions(job: ReelJob, root: Path) -> str:
    text = captions_from_audio_plan(job)
    if not text.strip():
        if job.audio_plan:
            job.audio_plan.subtitle_path = ""
        return ""
    path = root / "captions.srt"
    path.write_text(text, encoding="utf-8")
    if job.audio_plan:
        job.audio_plan.subtitle_path = str(path)
    return str(path)


def default_edit_plan(job: ReelJob) -> EditPlan:
    assert job.storyboard
    decisions = []
    seq = []
    for i, shot in enumerate(job.storyboard.shots):
        seq.append(shot.shot_id)
        cut = "hard_cut"
        rationale = "motivated change of size / information"
        if i == 0:
            cut = "none"
            rationale = "open on hook"
        elif shot.purpose.startswith("product"):
            cut = "match_cut"
            rationale = "hand/product continuity"
        decisions.append({
            "shot_id": shot.shot_id,
            "cut_type": cut,
            "rationale": rationale,
            "punch_in": shot.purpose in {"product_truth", "product_cta", "product_and_cta"},
            "j_cut": i > 0 and bool(shot.dialogue),
            "l_cut": False,
            "speed": 1.0,
        })
    from vidgen.reels.schemas import EditDecision
    return EditPlan(
        sequence=seq,
        decisions=[EditDecision(**d) for d in decisions],
        caption_placement="lower_third_safe",
        music_duck_db=-12.0,
        color_treatment="natural, slightly warm, no teal-orange",
    )


def _srt(s: float) -> str:
    ms = int(round((s % 1) * 1000))
    total = int(s)
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _mood_freqs(mood: str) -> Tuple[float, float, float]:
    mood = (mood or "").lower()
    if "piano" in mood or "warm" in mood:
        return (196.0, 246.9, 293.7)
    if "pulse" in mood or "tight" in mood:
        return (110.0, 164.8, 220.0)
    if "urban" in mood:
        return (98.0, 146.8, 196.0)
    if "strings" in mood:
        return (130.8, 196.0, 261.6)
    if "pluck" in mood:
        return (220.0, 329.6, 440.0)
    return (174.6, 220.0, 261.6)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _write_tone(path: str, duration: float, kind: str = "voice") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()
    dur = max(0.25, float(duration))
    if not ff:
        raise RuntimeError("ffmpeg is required to render audio; install ffmpeg then resume the same job")
    freq = 180 if kind == "voice" else 420
    vol = "0.08" if kind == "voice" else "0.05"
    codec = ["-c:a", "libmp3lame", "-q:a", "5"] if path.endswith(".mp3") else ["-c:a", "pcm_s16le"]
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={dur}",
           "-af", f"volume={vol}", *codec, path]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        raise RuntimeError(f"ffmpeg tone render failed for {path}")


def _write_score(path: str, duration: float, freqs: Tuple[float, float, float]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()
    dur = max(1.0, float(duration))
    if not ff:
        raise RuntimeError("ffmpeg is required to render music; install ffmpeg then resume the same job")
    fade = max(0.0, dur - 1.2)
    filt = (
        f"[0:a]volume=0.018[a];[1:a]volume=0.012[b];[2:a]volume=0.008[c];"
        f"[a][b][c]amix=inputs=3,afade=t=in:st=0:d=0.8,afade=t=out:st={fade}:d=1.2"
    )
    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freqs[0]}:sample_rate=48000:duration={dur}",
        "-f", "lavfi", "-i", f"sine=frequency={freqs[1]}:sample_rate=48000:duration={dur}",
        "-f", "lavfi", "-i", f"sine=frequency={freqs[2]}:sample_rate=48000:duration={dur}",
        "-filter_complex", filt, "-c:a", "aac", "-b:a", "192k", path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        raise RuntimeError(f"ffmpeg score render failed for {path}")


def cloud_tts(text: str, output_path: str, voice_name: str, rate: float, pitch: float) -> None:
    from google.cloud import texttospeech
    lang = "bn-IN" if voice_name.startswith("bn-") else "en-US"
    client = texttospeech.TextToSpeechClient()
    resp = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=lang, name=voice_name),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            sample_rate_hertz=48000,
            speaking_rate=max(0.75, min(1.15, rate)),
            pitch=max(-8.0, min(8.0, pitch)),
        ),
    )
    Path(output_path).write_bytes(resp.audio_content)
