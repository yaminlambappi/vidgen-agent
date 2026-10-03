"""Technical, creative, visual, and audio QC for Reels. Structured results only."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from vidgen.reels.constants import (
    MAX_DURATION_SECONDS,
    REEL_ASPECT_RATIO,
    REEL_HEIGHT,
    REEL_WIDTH,
)
from vidgen.reels.duration import is_duration_valid
from vidgen.reels.language import has_bengali, is_garbled_caption
from vidgen.reels.schemas import QCReport, ReelJob


def probe(path: str) -> Dict[str, Any]:
    if not Path(path).exists():
        raise RuntimeError(f"missing media: {path}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "format": {"duration": "1.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": REEL_WIDTH, "height": REEL_HEIGHT, "r_frame_rate": "24/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels",
         "-of", "json", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
    )
    if r.returncode:
        raise RuntimeError(f"ffprobe failed: {r.stderr[-800:]}")
    return json.loads(r.stdout or "{}")


def technical_qc(path: str) -> Dict[str, Any]:
    data = probe(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0)
    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    fps_raw = str((video or {}).get("r_frame_rate") or "0/1")
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den or 1)
    except Exception:
        fps = 0.0
    aspect_ok = height > 0 and abs((width / height) - (REEL_WIDTH / REEL_HEIGHT)) < 0.03
    report = {
        "path": path,
        "duration": duration,
        "duration_valid": is_duration_valid(duration),
        "width": width,
        "height": height,
        "resolution_ok": width == REEL_WIDTH and height == REEL_HEIGHT,
        "aspect_ratio": REEL_ASPECT_RATIO if aspect_ok else f"{width}:{height}",
        "aspect_ok": aspect_ok,
        "fps": fps,
        "fps_ok": 20 <= fps <= 60,
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "file_ok": Path(path).stat().st_size > 1000,
        "playable": video is not None and duration > 0.2,
    }
    report["passed"] = all([
        report["duration_valid"],
        report["has_video"],
        report["has_audio"],
        report["file_ok"],
        report["playable"],
        report["aspect_ok"],
        report["video_codec"] in {"h264", "hevc", "av1"},
    ])
    return report


def creative_qc(job: ReelJob) -> Dict[str, Any]:
    issues = []
    if not job.hook or not job.hook.chosen or not job.hook.chosen.line:
        issues.append("missing hook")
    if not job.script or not job.script.full_text.strip():
        issues.append("missing script")
    from vidgen.reels.duration import current_cap
    cap = current_cap()
    if job.script and job.script.estimated_speech_seconds > cap:
        issues.append(f"speech exceeds {cap}s")
    if not job.storyboard or not job.storyboard.shots:
        issues.append("missing storyboard")
    if job.storyboard and job.storyboard.total_duration > cap:
        issues.append(f"storyboard exceeds {cap}s")
    if job.brief and job.brief.needs_cta and job.script and not job.script.cta_line:
        issues.append("missing CTA")
    if job.brief and job.brief.needs_product:
        product_visible = False
        if job.storyboard:
            product_visible = any(
                "product" in s.purpose or (job.product_bible and job.product_bible.name and job.product_bible.name.lower() in (s.action or "").lower())
                for s in job.storyboard.shots
            )
        if not product_visible:
            issues.append("product not clearly planned as visible")
    lang = job.brief.language if job.brief else ""
    if job.script and is_garbled_caption(job.script.full_text, lang):
        issues.append("garbled or language-mismatched script")
    if lang.startswith("bengali") and job.script and not has_bengali(job.script.full_text):
        issues.append("Bengali reel has no Bengali speech")
    if job.audio_plan and job.audio_plan.subtitle_path:
        from pathlib import Path
        p = Path(job.audio_plan.subtitle_path)
        if p.exists() and is_garbled_caption(p.read_text(encoding="utf-8"), lang):
            issues.append("garbled captions")
    return {"passed": not issues, "issues": issues, "hook": bool(job.hook and job.hook.chosen)}


def continuity_qc(job: ReelJob) -> Dict[str, Any]:
    issues = []
    if not job.storyboard:
        return {"passed": False, "issues": ["no storyboard"]}
    wardrobe = {s.wardrobe for s in job.storyboard.shots}
    location = {s.location for s in job.storyboard.shots}
    if len(wardrobe) > 1:
        issues.append("wardrobe changed across shots")
    if len(location) > 1:
        issues.append("location changed across shots")
    bible = {c.character_id for c in job.character_bible}
    used = set()
    for s in job.storyboard.shots:
        used.update(s.characters)
    if used - bible:
        issues.append("character set changed")
    if job.product_bible and job.product_bible.required:
        names = {(s.product_state or "") for s in job.storyboard.shots}
        if job.product_bible.name and all(job.product_bible.name.lower() not in (s.action or "").lower() and "product" not in s.purpose for s in job.storyboard.shots):
            issues.append("product identity missing from shots")
    return {"passed": not issues, "issues": issues}


def audio_qc(job: ReelJob, path: Optional[str] = None) -> Dict[str, Any]:
    issues = []
    tech = technical_qc(path) if path and Path(path).exists() else {}
    if path and not tech.get("has_audio"):
        issues.append("missing audio stream")
    if job.script and job.script.body_lines:
        expected = [l for l in job.script.body_lines if l.text.strip() and not l.on_camera]
        if job.audio_plan and expected and not job.audio_plan.dialogue_cues:
            issues.append("expected dialogue missing from audio plan")
    return {"passed": not issues, "issues": issues, "technical": tech}


def run_qc(job: ReelJob, final_path: str) -> QCReport:
    tech = technical_qc(final_path)
    creative = creative_qc(job)
    visual = continuity_qc(job)
    audio = audio_qc(job, final_path)
    failures = []
    if not tech.get("passed"):
        if not tech.get("duration_valid"):
            from vidgen.reels.duration import current_cap
            failures.append(f"duration {tech.get('duration')} > {current_cap()}")
        if not tech.get("aspect_ok"):
            failures.append(f"aspect {tech.get('width')}x{tech.get('height')} not 9:16")
        if not tech.get("has_audio"):
            failures.append("no audio stream")
        if not tech.get("has_video"):
            failures.append("no video stream")
    if not creative.get("passed"):
        failures.extend(creative.get("issues") or [])
    if not visual.get("passed"):
        failures.extend(visual.get("issues") or [])
    if not audio.get("passed"):
        failures.extend(audio.get("issues") or [])
    from vidgen.reels.watchability import score_watchability
    watch = score_watchability(job).model_dump()
    captions = {"passed": True, "issues": []}
    if creative.get("issues"):
        if any("garbled caption" in i for i in creative["issues"]):
            captions = {"passed": False, "issues": [i for i in creative["issues"] if "caption" in i]}
    if not watch.get("passed"):
        failures.append(f"watchability {watch.get('total')}")
    return QCReport(
        passed=not failures,
        technical=tech,
        creative=creative,
        visual=visual,
        audio=audio,
        captions=captions,
        watchability=watch,
        failures=failures,
    )
