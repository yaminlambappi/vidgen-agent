#!/usr/bin/env python3
"""Production runner — Ghost of Ithaca. Checkpoint-resumable."""
from __future__ import annotations
import os
import json, sys, time, shutil
from pathlib import Path
from vidgen.config import settings
from vidgen.models import FilmProject, FilmStatus
from vidgen.orchestrator import Orchestrator

TOPIC = (
    "GHOST OF ITHACA: A mythic psychological drama for international film festival submission. "
    "Odysseus returns to Ithaca after twenty years — but the kingdom he finds is not the one he left. "
    "Penelope has become a stranger guarded by silence. Telemachus bears the weight of a father who is both legend and ghost. "
    "The island itself seems to remember every betrayal.\n\n"
    "Three-act structure for a 48-second cinematic short (3 scenes × 2 shots × 8 seconds):\n"
    "* ACT I — Arrival: Odysseus steps ashore at dawn. The olive groves are unchanged; everything else is foreign. Haunted, restrained atmosphere.\n"
    "* ACT II — Recognition: Penelope and Telemachus confront the man at the threshold. Memory, doubt, and grief collide. Escalating emotional tension.\n"
    "* ACT III — The Ghost: Odysseus realizes he cannot fully return — he is changed by the sea, by war, by time. Final image: his silhouette against the Ithacan cliffs as the title GHOST OF ITHACA resolves.\n\n"
    "Premium theatrical quality. Consistent character identity, wardrobe, and location continuity throughout. "
    "Cinematic composition, motivated camera movement, controlled lighting, coherent color language, natural dialogue, layered ambience."
)

STATE_DIR = settings.VIDGEN_WORK_ROOT
CHECKPOINT = STATE_DIR / "active_project_id.txt"


def _restore_from_gcs(orc: Orchestrator, pid: str) -> FilmProject | None:
    """
    Restore a FilmProject from GCS or local cache.

    Priority:
      1. Local disk cache (already downloaded in a previous step this run)
      2. GCS state file

    Returns None only when the state cannot be found anywhere — meaning the
    project ID does not exist and cannot be resumed.  Never returns None when
    data is present.
    """
    gcs_uri = f"gs://{settings.GCS_BUCKET}/projects/{pid}/state.json"
    local = STATE_DIR / pid / "project_state.json"

    print(f"[GCS RESTORE] checking {gcs_uri}")

    # Fast-path: local cache already populated (e.g. downloaded earlier this run)
    if local.exists():
        p = FilmProject.model_validate_json(local.read_text())
        print(f"[GCS RESTORE] loaded from local cache — status={p.status.value}")
        return p

    # GCS path: check existence before attempting download
    if not orc.storage.exists(gcs_uri):
        print(f"[GCS RESTORE] not found: {gcs_uri}")
        return None

    local.parent.mkdir(parents=True, exist_ok=True)
    orc.storage.download(gcs_uri, str(local))
    p = FilmProject.model_validate_json(local.read_text())
    print(f"[GCS RESTORE] {pid} restored from GCS — status={p.status.value}")
    return p


def _load_or_new(orc: Orchestrator) -> FilmProject:
    if CHECKPOINT.exists():
        pid = CHECKPOINT.read_text().strip()
        sf = STATE_DIR / pid / "project_state.json"
        if not sf.exists():
            restored = _restore_from_gcs(orc, pid)
            if restored:
                if restored.status == FilmStatus.FAILED:
                    restored = _reset(restored)
                return restored
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
    elif p.character_bible and p.world_bible:
        safe = FilmStatus.STORYBOARDING
    elif p.story:
        safe = FilmStatus.QUEUED
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
    if not final.exists():
        fails.append("GATE8: final_film.mp4 does not exist locally")
    else:
        try:
            from vidgen.utils.ffmpeg import validate_video
            qc = validate_video(str(final))
            d = qc.get("duration", 0)
            print(f"[QC] {d:.1f}s {qc.get('width')}x{qc.get('height')} "
                  f"{qc.get('codec')} audio={qc.get('has_audio')}")
            expected = sum(sh.duration for sc in p.scenes for sh in sc.shots)
            if abs(d - expected) > 3.0:
                fails.append(f"GATE8: {d:.1f}s differs from planned {expected:.1f}s by >{3.0}s")
            if not qc.get("has_audio"): fails.append("GATE9: no audio")
        except Exception as e:
            fails.append(f"GATE10: ffprobe fail: {e}")
    return fails


def _run(orc: Orchestrator, p: FilmProject) -> FilmProject:
    """Runs the orchestrator once. The orchestrator has its own internal retry logic."""
    try:
        orc.run(p)
    except Exception as exc:
        print(f"\n[FATAL ERROR] The orchestrator failed and could not recover: {exc}")
        # The orchestrator already sets the project status to FAILED on exit.
        raise  # Re-raise the exception to stop the script.
    return p


def main():
    if "--from-scratch" in sys.argv:
        if CHECKPOINT.exists():
            pid = CHECKPOINT.read_text().strip()
            shutil.rmtree(STATE_DIR / pid, ignore_errors=True)
            CHECKPOINT.unlink()
            print(f"[CLEAN] Removed project {pid} and started from scratch.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[ABORT] ffmpeg or ffprobe is not installed or not in PATH.")
        print("Please install FFmpeg: sudo apt-get update && sudo apt-get install -y ffmpeg")
        sys.exit(1)

    print("="*72)
    print("VIDGEN — Ghost of Ithaca")
    print("="*72)
    for k,v in [("project", settings.GOOGLE_CLOUD_PROJECT),
                ("bucket", settings.GCS_BUCKET),
                ("veo", settings.VEO_MODEL),
                ("image", settings.IMAGE_MODEL),
                ("director", settings.DIRECTOR_MODEL),
                ("shots/scene", settings.SHOTS_PER_SCENE)]:
        print(f"  {k:<12}: {v}")
    print("="*72)

    # ── Startup diagnostic (never prints secrets) ────────────────────────────
    print(f"  {'FILM_MODE':<24}: {settings.FILM_MODE}")
    print(f"  {'ALLOW_REAL_GENERATION':<24}: {settings.ALLOW_REAL_GENERATION}")
    print(f"  {'is_production':<24}: {settings.is_production}")
    print(f"  {'GOOGLE_CLOUD_PROJECT':<24}: {settings.GOOGLE_CLOUD_PROJECT}")
    print(f"  {'GCS_BUCKET':<24}: {settings.GCS_BUCKET}")
    print(f"  {'VIDGEN_PROJECT_ID present':<24}: {bool(os.getenv('VIDGEN_PROJECT_ID'))}")
    print("="*72)

    if not settings.is_production:
        print("[ABORT] Set FILM_MODE=production ALLOW_REAL_GENERATION=true")
        sys.exit(1)
    # In production, require an explicit project id to avoid running a hardcoded or accidental project
    vidgen_pid = os.getenv("VIDGEN_PROJECT_ID")
    if not vidgen_pid:
        print("[ABORT] Production worker requires VIDGEN_PROJECT_ID environment variable")
        sys.exit(2)
    if not settings.GOOGLE_CLOUD_PROJECT:
        print("[ABORT] GOOGLE_CLOUD_PROJECT not set")
        sys.exit(1)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    orc = Orchestrator()

    # If VIDGEN_PROJECT_ID is supplied via environment, load that exact project
    # VIDGEN_PROJECT_ID is guaranteed present above in production mode
    p = _restore_from_gcs(orc, os.getenv("VIDGEN_PROJECT_ID"))
    if not p:
        expected_uri = f"gs://{settings.GCS_BUCKET}/projects/{os.getenv('VIDGEN_PROJECT_ID')}/state.json"
        print(f"[ERROR] Could not restore project {os.getenv('VIDGEN_PROJECT_ID')} from GCS")
        print(f"[ERROR] Expected state at: {expected_uri}")
        print(f"[ERROR] Verify the bucket exists and the project was checkpointed there.")
        sys.exit(3)

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
