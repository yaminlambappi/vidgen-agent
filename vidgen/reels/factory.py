"""Reels Super Factory — checkpointed state machine with hard cost and duration guards."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional

from vidgen.config import settings
from vidgen.providers import get_storage_provider, get_video_generator
from vidgen.reels.audio import (
    build_audio_plan,
    cloud_tts,
    default_edit_plan,
    ensure_mixable_audio,
    render_foley,
    render_music,
    synthesize_dialogue,
    write_captions,
)
from vidgen.reels.assets import apply_compose_strategy, plan_assets, resolve_assets
from vidgen.reels.constants import BLOCKED_JOB_IDS, MAX_DURATION_SECONDS
from vidgen.reels.creative import build_storyboard, plan_production
from vidgen.reels.watchability import score_watchability
from vidgen.reels.llm import maybe_polish_script
from vidgen.reels.duration import assert_duration, assign_timeline, legalize_shot_durations
from vidgen.reels.edit import assemble_reel, require_ffmpeg, write_compose_card, write_vertical_plate
from vidgen.reels.prompts import compile_shot_prompt
from vidgen.reels.qc import run_qc
from vidgen.reels.safety import (
    CostGuardTripped,
    DryRunViolation,
    IdempotencyStore,
    PermanentGenerationError,
    check_pipeline_attempts,
    checkpoint,
    classify_failure,
    execute_expensive,
    is_retryable_failure,
    load_checkpoint,
    log_event,
    note_repeat_failure,
    request_hash,
    trip,
)
from vidgen.reels.schemas import (
    DryRunManifest,
    FailureClass,
    ProductionManifest,
    ReelJob,
    ReelRequest,
    ReelStatus,
)
from vidgen.reels.state import infer_resume_stage, transition


class ReelFactory:
    def __init__(self, storage=None, video_gen=None, tts_fn: Optional[Callable] = None):
        self.storage = storage if storage is not None else get_storage_provider()
        self.video_gen = video_gen if video_gen is not None else get_video_generator()
        self.tts_fn = tts_fn

    def create(self, request: ReelRequest) -> ReelJob:
        assert_duration(request.duration_seconds, "request")
        fp = request_hash(request.model_dump())
        job = ReelJob(request=request, request_hash=fp)
        checkpoint(job, self.storage)
        return job

    def run(self, job: ReelJob, dry_run: Optional[bool] = None) -> ReelJob:
        if job.job_id in BLOCKED_JOB_IDS or str(job.job_id).startswith("9237d967"):
            raise PermanentGenerationError(
                f"Job {job.job_id} is a failed historical reel and must not be resumed. Start a new job."
            )
        dry = settings.DRY_RUN if dry_run is None else bool(dry_run)
        if dry_run is None:
            dry = dry or bool(job.request.dry_run)
        else:
            dry = bool(dry_run)
        job.request.dry_run = dry
        if not dry:
            require_ffmpeg()
        resume_only = job.status == ReelStatus.FAILED and infer_resume_stage(job) in {
            ReelStatus.ASSEMBLING, ReelStatus.QC, ReelStatus.EDIT_PLAN_READY, ReelStatus.AUDIO_READY,
        }
        if not resume_only:
            job.pipeline_attempts += 1
            check_pipeline_attempts(job)
        root = settings.VIDGEN_WORK_ROOT / "reels" / job.job_id
        root.mkdir(parents=True, exist_ok=True)
        store = IdempotencyStore(root)

        try:
            self._advance(job, dry, root, store)
        except CostGuardTripped:
            checkpoint(job, self.storage)
            return job
        except Exception as exc:
            klass = classify_failure(exc)
            job.failure_class = klass.value
            job.last_error = str(exc)[:500]
            if klass != FailureClass.APPLICATION:
                try:
                    note_repeat_failure(job, hashlib.sha256(str(exc).encode()).hexdigest())
                except CostGuardTripped:
                    checkpoint(job, self.storage)
                    return job
            if job.status != ReelStatus.FAILED_COST_GUARD:
                try:
                    transition(job, ReelStatus.FAILED, f"Pipeline failure: {exc}")
                except Exception:
                    job.status = ReelStatus.FAILED
                    job.message = str(exc)
            checkpoint(job, self.storage)
            if klass == FailureClass.TRANSIENT and is_retryable_failure(exc):
                raise
            raise
        checkpoint(job, self.storage)
        return job

    def _advance(self, job: ReelJob, dry: bool, root: Path, store: IdempotencyStore) -> None:
        resume = infer_resume_stage(job) if job.status in {ReelStatus.FAILED, ReelStatus.QUEUED} else job.status
        if job.status == ReelStatus.FAILED:
            transition(job, resume, f"resuming from {resume.value}")
        if job.status == ReelStatus.QUEUED:
            transition(job, ReelStatus.PLANNING, "Creative direction", 8)

        if job.status == ReelStatus.PLANNING:
            if not job.brief or not job.script:
                plan_production(job)
            maybe_polish_script(job, store, dry)
            if job.brief and job.script:
                job.storyboard = build_storyboard(job)
            assert job.script
            assert_duration(job.script.estimated_speech_seconds or 0.1, "script")
            transition(job, ReelStatus.SCRIPT_READY, "Script fitted to duration", 22)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.SCRIPT_READY:
            if not job.storyboard:
                plan_production(job)
            assert job.storyboard
            assert_duration(job.storyboard.total_duration, "storyboard")
            transition(job, ReelStatus.STORYBOARD_READY, f"{len(job.storyboard.shots)} shots planned", 35)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.STORYBOARD_READY:
            self._attach_user_references(job)
            plan_assets(job)
            apply_compose_strategy(job)
            resolve_assets(job, dry)
            transition(job, ReelStatus.REFERENCES_READY, "References resolved", 42)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.REFERENCES_READY:
            job.dry_run_manifest = self._manifest(job, dry)
            job.production_manifest = self._production_manifest(job)
            if dry:
                transition(job, ReelStatus.COMPLETE, "Dry-run complete — no expensive generation", 100)
                log_event(job, event="dry_run_complete", **job.dry_run_manifest.model_dump())
                return
            transition(job, ReelStatus.SHOTS_GENERATING, "Generating controlled Veo shots", 48)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.SHOTS_GENERATING:
            self._generate_shots(job, root, store, dry=False)
            transition(job, ReelStatus.SHOTS_READY, "Shots ready", 68)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.SHOTS_READY:
            job.audio_plan = build_audio_plan(job)
            self._build_audio(job, root, store)
            transition(job, ReelStatus.AUDIO_READY, "Voice / music / captions", 78)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.AUDIO_READY:
            job.edit_plan = job.edit_plan or default_edit_plan(job)
            transition(job, ReelStatus.EDIT_PLAN_READY, "Edit plan ready", 84)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.EDIT_PLAN_READY:
            transition(job, ReelStatus.ASSEMBLING, "Deterministic assembly", 88)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.ASSEMBLING:
            final = self._assemble(job, root)
            job.final_video_path = final
            transition(job, ReelStatus.QC, "Quality control", 94)
            checkpoint(job, self.storage)

        if job.status == ReelStatus.QC:
            report = run_qc(job, job.final_video_path)
            job.qc = report
            if not report.passed:
                raise PermanentGenerationError("QC failed: " + "; ".join(report.failures))
            if job.final_video_path:
                try:
                    job.final_video_uri = self.storage.upload(
                        job.final_video_path, f"reels/{job.job_id}/deliverables/final.mp4"
                    )
                except Exception as exc:
                    log_event(job, event="upload_warn", error=str(exc)[:160])
                    job.final_video_uri = job.final_video_path
            job.last_error = ""
            job.failure_class = ""
            job.repeat_failure_count = 0
            transition(job, ReelStatus.COMPLETE, f"Reel complete <= {MAX_DURATION_SECONDS}s", 100)

    def _legalize_veo_durations(self, job: ReelJob) -> None:
        """Veo text_to_video only accepts 4/6/8. Rewrite illegal 5s/7s plans before spend."""
        if not job.storyboard or not job.storyboard.shots:
            return
        veo_shots = [s for s in job.storyboard.shots if s.generation_strategy != "compose"]
        if not veo_shots:
            return
        cap = min(float(job.storyboard.target_duration or job.request.duration_seconds), MAX_DURATION_SECONDS)
        legal = legalize_shot_durations([s.duration for s in veo_shots], cap)
        if len(legal) != len(veo_shots):
            veo_shots = veo_shots[:len(legal)]
            compose = [s for s in job.storyboard.shots if s.generation_strategy == "compose"]
            job.storyboard.shots = veo_shots + compose
        for shot, dur in zip(veo_shots, legal):
            shot.duration = float(dur)
        all_shots = job.storyboard.shots
        spans = assign_timeline([s.duration for s in all_shots])
        for shot, (dur, start, end) in zip(all_shots, spans):
            shot.duration = dur
            shot.start_time = start
            shot.end_time = end
        job.storyboard.total_duration = sum(s.duration for s in all_shots)
        assert_duration(job.storyboard.total_duration, "legalized storyboard")

    def _lock_production_ref_durations(self, job: ReelJob) -> None:
        """Veo reference_to_video is 8s-only. Never let the locked timeline exceed 30s."""
        if not settings.is_production or not job.storyboard:
            return
        has_refs = bool(job.product_bible and any(u.startswith("gs://") for u in job.product_bible.reference_uris))
        has_refs = has_refs or any(c.reference_uri.startswith("gs://") for c in job.character_bible)
        if not has_refs:
            return
        kept = []
        total = 0.0
        for shot in job.storyboard.shots:
            if total + 8.0 > MAX_DURATION_SECONDS:
                break
            shot.duration = 8.0
            kept.append(shot)
            total += 8.0
        if not kept:
            raise PermanentGenerationError("reference-locked 8s shot would exceed 30s")
        from vidgen.reels.duration import assign_timeline
        spans = assign_timeline([s.duration for s in kept])
        cursor_shots = []
        for shot, (dur, start, end) in zip(kept, spans):
            shot.duration = dur
            shot.start_time = start
            shot.end_time = end
            cursor_shots.append(shot)
        job.storyboard.shots = cursor_shots
        job.storyboard.total_duration = total

    def _attach_user_references(self, job: ReelJob) -> None:
        if not job.product_bible:
            return
        uris = []
        for asset in job.request.assets:
            ref = asset.uri or asset.local_path
            if not ref:
                continue
            if asset.kind in {"product_image", "logo", "brand"}:
                uris.append(ref)
                job.product_bible.user_image_authoritative = True
            if asset.kind == "actor" and job.character_bible:
                job.character_bible[0].reference_uri = ref
            if asset.kind == "video" and job.storyboard and job.storyboard.shots:
                # Prefer real footage for the lived-moment shot instead of regenerating
                for shot in job.storyboard.shots:
                    if shot.purpose in {"lived_moment", "product_truth"}:
                        shot.use_user_footage = True
                        shot.user_footage_uri = ref
                        break
        if uris:
            job.product_bible.reference_uris = list(dict.fromkeys(job.product_bible.reference_uris + uris))

    def _manifest(self, job: ReelJob, dry: bool) -> DryRunManifest:
        shots = job.storyboard.shots if job.storyboard else []
        veo = sum(1 for s in shots if s.generation_strategy == "veo" and not s.use_user_footage)
        images = 0
        if job.asset_plan:
            images = sum(1 for n in job.asset_plan.needs if n.strategy == "generate_image")
        tts = 0
        if job.script:
            tts = sum(1 for l in job.script.body_lines if l.text and not l.on_camera)
        total = veo + images + tts
        limit = settings.MAX_TOTAL_GENERATION_BUDGET * max(1, job.request.variant_count)
        status = "within_limit" if total <= limit else "over_budget"
        needs = job.asset_plan.needs if job.asset_plan else []
        score = job.watchability or score_watchability(job)
        return DryRunManifest(
            target_duration=float(job.request.duration_seconds),
            max_duration=MAX_DURATION_SECONDS,
            estimated_veo_calls=veo,
            estimated_image_calls=images,
            estimated_tts_calls=tts,
            estimated_gemini_calls=0,
            estimated_stages=8,
            shot_count=len(shots),
            budget_status=status,
            content_mode=job.brief.content_mode if job.brief else "",
            language=job.brief.language if job.brief else "",
            talking_head=bool(job.brief.talking_head) if job.brief else False,
            expensive_calls_made=job.ledger.total_calls,
            creative_type=job.brief.creative_type if job.brief else "",
            creative_strategy=list(job.brief.strategy_beats) if job.brief else [],
            characters=[c.name for c in job.character_bible],
            assets_required=[f"{n.kind}:{n.name}" for n in needs],
            assets_to_generate=[n.name for n in needs if n.strategy in {"generate_image", "generate_in_prompt"}],
            assets_to_source=[n.name for n in needs if n.strategy == "source"],
            script=(job.script.full_text if job.script else "")[:500],
            storyboard=[f"{s.shot_id}:{s.purpose}:{s.generation_strategy}" for s in shots],
            voice_plan=(job.character_bible[0].speaking_style if job.character_bible else ""),
            audio_plan="dialogue first; music only if the category needs it",
            caption_plan="from final script, lower-third, language-locked",
            qc_gates=["creative", "language", "asset", "continuity", "timing", "cost", "watchability"],
            watchability=score.model_dump(),
        )

    def _production_manifest(self, job: ReelJob) -> ProductionManifest:
        shots = job.storyboard.shots if job.storyboard else []
        return ProductionManifest(
            creative_type=job.brief.creative_type if job.brief else "",
            language=job.brief.language if job.brief else "",
            duration=float(job.request.duration_seconds),
            strategy_beats=list(job.brief.strategy_beats) if job.brief else [],
            characters=[{"name": c.name, "role": c.role, "wardrobe": c.wardrobe} for c in job.character_bible],
            assets=[n.model_dump() for n in (job.asset_plan.needs if job.asset_plan else [])],
            shots=[{"id": s.shot_id, "purpose": s.purpose, "strategy": s.generation_strategy, "seconds": s.duration} for s in shots],
            script=job.script.full_text if job.script else "",
            audio={"talking_head": bool(job.brief.talking_head) if job.brief else False},
            captions={"language": job.brief.language if job.brief else "", "placement": "lower_third"},
            budget={"estimated_veo": job.dry_run_manifest.estimated_veo_calls if job.dry_run_manifest else 0},
            gates=["creative", "language", "asset", "continuity", "timing", "cost"],
        )

    def _generate_shots(self, job: ReelJob, root: Path, store: IdempotencyStore, dry: bool) -> None:
        assert job.storyboard
        self._legalize_veo_durations(job)
        self._lock_production_ref_durations(job)
        prev = None
        for shot in job.storyboard.shots:
            if shot.generated_asset_uri or (shot.use_user_footage and shot.user_footage_uri):
                if shot.use_user_footage and not shot.generated_asset_uri:
                    shot.generated_asset_uri = shot.user_footage_uri
                prev = shot
                continue
            local = root / f"{shot.shot_id}.mp4"
            if shot.generation_strategy == "compose":
                title = (shot.dialogue or (job.script.cta_line if job.script else "") or "—")[:80]
                write_compose_card(str(local), shot.duration, title, job.brief.language if job.brief else "")
                shot.generated_asset_uri = str(local)
                shot.local_path = str(local)
                prev = shot
                checkpoint(job, self.storage)
                continue
            pkg = compile_shot_prompt(shot, job, prev)
            shot.prompt_hash = hashlib.sha256(pkg["prompt"].encode()).hexdigest()
            local = root / f"{shot.shot_id}.mp4"

            def _do(s=shot, package=pkg, dest=local):
                if not settings.is_production:
                    write_vertical_plate(str(dest), s.duration)
                    return str(dest)
                out_uri = f"gs://{settings.GCS_BUCKET}/reels/{job.job_id}/shots/{s.shot_id}/"
                result = self.video_gen.generate_shot(
                    prompt=package["prompt"],
                    output_uri=out_uri,
                    duration=int(package["duration"]),
                    project_id=job.job_id,
                    shot_id=s.shot_id,
                    reference_assets=package.get("reference_assets") or [],
                    aspect_ratio=package.get("aspect_ratio") or "9:16",
                    generate_audio=package.get("generate_audio", False),
                )
                if getattr(result, "status", "") != "completed" or not getattr(result, "artifact_uri", ""):
                    raise RuntimeError(result.error if getattr(result, "error", "") else "Veo shot failed")
                self.storage.download(result.artifact_uri, str(dest))
                return result.artifact_uri

            try:
                uri = execute_expensive(
                    job, kind="veo", operation=f"shot/{shot.shot_id}",
                    model=settings.VEO_MODEL, prompt=pkg["prompt"],
                    inputs={"shot_id": shot.shot_id, "duration": shot.duration},
                    store=store, fn=_do, dry_run=dry, shot_id=shot.shot_id,
                )
            except DryRunViolation:
                raise
            shot.generated_asset_uri = uri
            shot.local_path = str(local)
            if not local.exists():
                write_vertical_plate(str(local), shot.duration)
            prev = shot
            checkpoint(job, self.storage)

    def _build_audio(self, job: ReelJob, root: Path, store: IdempotencyStore) -> None:
        audio_root = root / "audio"
        audio_root.mkdir(exist_ok=True)
        use_cloud = settings.is_production and self.tts_fn is None

        def _tts(text, path, voice, rate, pitch):
            def _do():
                if use_cloud:
                    cloud_tts(text, path, voice, rate, pitch)
                elif self.tts_fn:
                    self.tts_fn(text, path, voice, rate, pitch)
                else:
                    synthesize_dialogue(job, audio_root, tts_fn=None)
                return path

            if use_cloud or self.tts_fn:
                return execute_expensive(
                    job, kind="tts", operation="tts",
                    model=settings.TTS_MODEL, prompt=text,
                    inputs={"voice": voice}, store=store, fn=_do, dry_run=False,
                )
            _do()
            return path

        if use_cloud or self.tts_fn:
            synthesize_dialogue(job, audio_root, tts_fn=_tts)
        else:
            synthesize_dialogue(job, audio_root, tts_fn=None)
        total = job.storyboard.total_duration if job.storyboard else job.request.duration_seconds
        render_music(job, audio_root, total)
        render_foley(job, audio_root)
        write_captions(job, audio_root)

    def _assemble(self, job: ReelJob, root: Path) -> str:
        assert job.storyboard
        audio_root = root / "audio"
        ensure_mixable_audio(job, audio_root)
        paths = []
        for shot in job.storyboard.shots:
            local = shot.local_path or str(root / f"{shot.shot_id}.mp4")
            if not Path(local).exists():
                if shot.generated_asset_uri and shot.generated_asset_uri.startswith("gs://"):
                    self.storage.download(shot.generated_asset_uri, local)
                elif shot.user_footage_uri and Path(shot.user_footage_uri).exists():
                    local = shot.user_footage_uri
                else:
                    write_vertical_plate(local, shot.duration)
            paths.append(local)
        voice = []
        foley = []
        if job.audio_plan:
            voice = [{"path": c.local_path, "start_seconds": c.start_seconds, "kind": "dialogue"} for c in job.audio_plan.dialogue_cues]
            foley = [{"path": c.local_path, "start_seconds": c.start_seconds, "kind": c.kind} for c in job.audio_plan.foley]
        final = str(root / "final_reel.mp4")
        music = job.audio_plan.music_path if job.audio_plan else None
        if job.brief and job.brief.talking_head:
            music = None
        assemble_reel(
            job, paths, final,
            music_path=music,
            subtitle_path=job.audio_plan.subtitle_path if job.audio_plan else None,
            voice_tracks=voice,
            foley_tracks=foley,
        )
        return final


def dry_run(request: ReelRequest) -> ReelJob:
    factory = ReelFactory()
    job = factory.create(request)
    return factory.run(job, dry_run=True)
