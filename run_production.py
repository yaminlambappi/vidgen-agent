#!/usr/bin/env python3
"""Production runner — Bangladesh Digital Dreams. Checkpoint-resumable."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from vidgen.config import settings
from vidgen.models import FilmProject, FilmStatus
from vidgen.orchestrator import Orchestrator

TOPIC = (
    "ODYSSEUS trailer: mythic, emotionally powerful, visually coherent, premium theatrical quality. "
    "Odysseus has spent years trying to return home after war. The sea, gods, monsters, and his own memories have transformed the journey into a psychological battle. "
    "The greatest battle is no longer against the sea—it is against what he has become.\n\n"
    "Use a compact three-act trailer structure:\n"
    "* 0–8s — Mystery: vast ancient sea, exhausted Odysseus on a damaged ship, haunting atmosphere, restrained dialogue.\n"
    "* 8–20s — Escalation: rapid but coherent flashes of danger—storm, enormous silhouette beneath the water, warriors/ruins, Odysseus fighting, Penelope/home as an emotional memory.\n"
    "* 20–30s — Payoff: extreme danger and emotional revelation, decisive final image, then title ODYSSEUS and a powerful final sound/music hit."
)

STATE_DIR = settings.VIDGEN_WORK_ROOT
CHECKPOINT = STATE_DIR / "active_project_id.txt"


def _load_or_new(orc: Orchestrator) -> FilmProject:
    if CHECKPOINT.exists():
        pid = CHECKPOINT.read_text().strip()
        sf = STATE_DIR / pid / "project_state.json"
        if sf.exists():
            p = FilmProject.model_validate_json(sf.read_text())
            print(f"[RESUME] {pid} status={p.status.value}")
            if p.status == FilmStatus.FAILED:
                p = _reset(p)
            return p
    p = FilmProject(topic=TOPIC)
    CHECKPOINT.write_text(p.project_id)
    print(f"[NEW] {p.project_id}")
    return p


def _reset(p: FilmProject) -> FilmProject:
    shots = [sh for sc in p.scenes for sh in sc.shots]
    n = sum(1 for sh in shots if sh.generated_asset_uri)
    audio_ok = bool(p.audio_plan and p.audio_plan.narration_uri and p.audio_plan.music_uri)
    if audio_ok and n == len(shots) and shots:
        safe = FilmStatus.MASTERING
    elif n == len(shots) and shots:
        safe = FilmStatus.EDITING
    elif n > 0:
        safe = FilmStatus.GENERATING
    elif p.scenes:
        safe = FilmStatus.STORYBOARDING
    else:
        safe = FilmStatus.QUEUED
    print(f"[RESET] scenes={len(p.scenes)} shots={len(shots)} done={n} -> {safe.value}")
    p.status = safe; p.message = f"Reset to {safe.value}"
    return p


def _qc(p: FilmProject) -> list:
    fails = []
    if not p.story or not p.story.title:
        fails.append("GATE1: story missing")
    shots = [sh for sc in p.scenes for sh in sc.shots]
    if not shots:
        fails.append("GATE2: no shots")
    else:
        m = [sh.shot_id for sh in shots if not sh.generated_asset_uri]
        if m: fails.append(f"GATE2: {len(m)}/{len(shots)} shots missing")
    ap = p.audio_plan
    if not ap or not ap.narration_uri: fails.append("GATE5: narration missing")
    if not ap or not ap.music_uri: fails.append("GATE6: music missing")
    if not ap or not ap.subtitle_uri: fails.append("GATE7: subtitles missing")
    if not p.final_manifest_uri: fails.append("GATE11: manifest URI missing")
    final = STATE_DIR / p.project_id / "final_film.mp4"
    if final.exists():
        try:
            from vidgen.utils.ffmpeg import validate_video
            qc = validate_video(str(final))
            d = qc.get("duration", 0)
            print(f"[QC] {d:.1f}s {qc.get('width')}x{qc.get('height')} "
                  f"{qc.get('codec')} audio={qc.get('has_audio')}")
            expected = sum(sh.duration for sc in p.scenes for sh in sc.shots)
            if abs(d - expected) > 2.5:
                fails.append(f"GATE8: {d:.1f}s differs from planned {expected:.1f}s")
            if not qc.get("has_audio"): fails.append("GATE9: no audio")
        except Exception as e:
            fails.append(f"GATE10: ffprobe fail: {e}")
    return fails


def _run(orc: Orchestrator, p: FilmProject) -> FilmProject:
    for attempt in range(3):
        try:
            orc.run(p); return p
        except Exception as exc:
            print(f"\n[ERROR] attempt {attempt+1}/3: {exc}")
            if attempt < 2:
                w = 4**attempt
                print(f"[RETRY] {w}s ...")
                time.sleep(w)
                sf = STATE_DIR / p.project_id / "project_state.json"
                if sf.exists():
                    p = FilmProject.model_validate_json(sf.read_text())
                    p = _reset(p)
            else:
                raise
    return p


def main():
    print("="*72)
    print("VIDGEN — ODYSSEUS Trailer")
    print("="*72)
    for k,v in [("project", settings.GOOGLE_CLOUD_PROJECT),
                ("bucket", settings.GCS_BUCKET),
                ("veo", settings.VEO_MODEL),
                ("director", settings.DIRECTOR_MODEL),
                ("shots/scene", settings.SHOTS_PER_SCENE)]:
        print(f"  {k:<12}: {v}")
    print("="*72)

    if not settings.is_production:
        print("[ABORT] Set FILM_MODE=production ALLOW_REAL_GENERATION=true")
        sys.exit(1)
    if not settings.GOOGLE_CLOUD_PROJECT:
        print("[ABORT] GOOGLE_CLOUD_PROJECT not set")
        sys.exit(1)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    orc = Orchestrator()
    p = _load_or_new(orc)

    if p.status == FilmStatus.COMPLETED:
        print("[STATUS] Already complete — QC only")
    else:
        print(f"[RUN] from {p.status.value}")
        p = _run(orc, p)

    print("\n"+"="*72+"QUALITY GATE\n"+"="*72)
    fails = _qc(p)
    if fails:
        print("[FAIL]")
        for f in fails: print(f"  x {f}")
        rp = STATE_DIR / p.project_id / "quality_report.json"
        rp.write_text(json.dumps({"failures": fails, "status": p.status.value,
            "final_manifest_uri": p.final_manifest_uri}, indent=2))
        sys.exit(2)

    d = p.qc_report.get("duration",0) if p.qc_report else 0
    mins, secs = divmod(int(d), 60)
    shots_done = sum(1 for sc in p.scenes for sh in sc.shots if sh.generated_asset_uri)

    rp = STATE_DIR / p.project_id / "final_report.json"
    rp.write_text(json.dumps({
        "project_id": p.project_id,
        "title": p.story.title if p.story else "",
        "status": "completed",
        "final_manifest_uri": p.final_manifest_uri,
        "duration_seconds": d,
        "scenes": len(p.scenes),
        "shots": shots_done,
        "qc": p.qc_report}, indent=2))

    print(f"\n{'='*56}")
    print("FILM PRODUCTION COMPLETE")
    print(f"{'='*56}")
    print(f"Project   : {p.project_id}")
    print(f"Title     : {p.story.title if p.story else 'N/A'}")
    print(f"Duration  : {mins}:{secs:02d} ({d:.1f}s)")
    qc = p.qc_report or {}
    print(f"Resolution: {qc.get('width','?')}x{qc.get('height','?')}")
    print(f"FPS       : {settings.FPS}")
    print(f"Scenes    : {len(p.scenes)}")
    print(f"Shots     : {shots_done}")
    print(f"Audio     : {'OK' if p.audio_plan and p.audio_plan.narration_uri else 'MISSING'}")
    print(f"Music     : {'OK' if p.audio_plan and p.audio_plan.music_uri else 'MISSING'}")
    print(f"Subtitles : {'OK' if p.audio_plan and p.audio_plan.subtitle_uri else 'MISSING'}")
    print(f"QC        : PASSED")
    print(f"FINAL MP4 : {p.final_manifest_uri}")
    print(f"{'='*56}")

if __name__ == "__main__":
    main()
