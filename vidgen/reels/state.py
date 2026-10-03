"""Explicit Reels state machine. Invalid transitions are rejected."""
from __future__ import annotations

from vidgen.reels.schemas import ReelJob, ReelStatus


class InvalidTransition(ValueError):
    pass


ALLOWED: dict[ReelStatus, set[ReelStatus]] = {
    ReelStatus.QUEUED: {
        ReelStatus.PLANNING, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.PLANNING: {
        ReelStatus.SCRIPT_READY, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.SCRIPT_READY: {
        ReelStatus.STORYBOARD_READY, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.STORYBOARD_READY: {
        ReelStatus.REFERENCES_READY, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.REFERENCES_READY: {
        ReelStatus.SHOTS_GENERATING, ReelStatus.COMPLETE,  # dry-run finishes here
        ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.SHOTS_GENERATING: {
        ReelStatus.SHOTS_READY, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.SHOTS_READY: {
        ReelStatus.AUDIO_READY, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.AUDIO_READY: {
        ReelStatus.EDIT_PLAN_READY, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.EDIT_PLAN_READY: {
        ReelStatus.ASSEMBLING, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.ASSEMBLING: {
        ReelStatus.QC, ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.QC: {
        ReelStatus.COMPLETE, ReelStatus.ASSEMBLING,  # bounded QC repair of assembly only
        ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.COMPLETE: set(),
    ReelStatus.FAILED: {
        ReelStatus.PLANNING, ReelStatus.SCRIPT_READY, ReelStatus.STORYBOARD_READY,
        ReelStatus.REFERENCES_READY, ReelStatus.SHOTS_GENERATING, ReelStatus.SHOTS_READY,
        ReelStatus.AUDIO_READY, ReelStatus.EDIT_PLAN_READY, ReelStatus.ASSEMBLING,
        ReelStatus.QC, ReelStatus.FAILED_COST_GUARD,
    },
    ReelStatus.FAILED_COST_GUARD: set(),
}

RESUME_PRIORITY = (
    ReelStatus.QC,
    ReelStatus.ASSEMBLING,
    ReelStatus.EDIT_PLAN_READY,
    ReelStatus.AUDIO_READY,
    ReelStatus.SHOTS_READY,
    ReelStatus.SHOTS_GENERATING,
    ReelStatus.REFERENCES_READY,
    ReelStatus.STORYBOARD_READY,
    ReelStatus.SCRIPT_READY,
    ReelStatus.PLANNING,
    ReelStatus.QUEUED,
)


def can_transition(current: ReelStatus, nxt: ReelStatus) -> bool:
    return nxt in ALLOWED.get(current, set())


def transition(job: ReelJob, nxt: ReelStatus, message: str = "", progress: int | None = None) -> ReelJob:
    if job.status == nxt:
        if message:
            job.message = message
        if progress is not None:
            job.progress = progress
        return job
    if not can_transition(job.status, nxt):
        raise InvalidTransition(f"invalid transition {job.status.value} -> {nxt.value}")
    job.status = nxt
    if message:
        job.message = message
    if progress is not None:
        job.progress = progress
    if nxt not in {ReelStatus.FAILED, ReelStatus.FAILED_COST_GUARD}:
        job.resume_stage = nxt
    return job


def infer_resume_stage(job: ReelJob) -> ReelStatus:
    """Resume from the latest completed artifact, never restart the whole pipeline."""
    if job.status == ReelStatus.FAILED_COST_GUARD:
        return ReelStatus.FAILED_COST_GUARD
    if job.final_video_path and job.qc and job.qc.passed:
        return ReelStatus.COMPLETE
    if job.final_video_path:
        return ReelStatus.QC
    if job.edit_plan and job.audio_plan and job.storyboard and all(
        s.generated_asset_uri or s.use_user_footage for s in (job.storyboard.shots if job.storyboard else [])
    ):
        return ReelStatus.ASSEMBLING
    if job.edit_plan:
        return ReelStatus.EDIT_PLAN_READY
    if job.audio_plan and job.audio_plan.dialogue_cues:
        return ReelStatus.AUDIO_READY
    if job.storyboard and all(s.generated_asset_uri or s.use_user_footage for s in job.storyboard.shots):
        return ReelStatus.SHOTS_READY
    if job.storyboard and any(s.generated_asset_uri for s in job.storyboard.shots):
        return ReelStatus.SHOTS_GENERATING
    if job.storyboard and (job.character_bible or job.product_bible):
        refs_ready = True
        if job.product_bible and job.product_bible.user_image_authoritative:
            refs_ready = bool(job.product_bible.reference_uris)
        if refs_ready:
            return ReelStatus.REFERENCES_READY
        return ReelStatus.STORYBOARD_READY
    if job.storyboard:
        return ReelStatus.STORYBOARD_READY
    if job.script:
        return ReelStatus.SCRIPT_READY
    if job.brief:
        return ReelStatus.PLANNING
    return ReelStatus.QUEUED
