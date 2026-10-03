"""Cost guards, idempotency, circuit breaker, and generation ledger."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from vidgen.config import settings
from vidgen.reels.schemas import (
    FailureClass,
    GenerationLedger,
    GenerationRecord,
    ReelJob,
    ReelStatus,
)
from vidgen.reels.state import transition
from vidgen.utils.retry import classify_error


class CostGuardTripped(RuntimeError):
    """Terminal: generation stopped to protect GCP credits."""


class PermanentGenerationError(RuntimeError):
    """Non-retryable provider or application failure."""


class DryRunViolation(RuntimeError):
    """Raised if an expensive call is attempted during dry-run."""


_EXPENSIVE_KINDS = {"veo", "image", "tts", "gemini"}


def request_hash(payload: Any) -> str:
    raw = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def elapsed_seconds(job: ReelJob) -> float:
    return max(0.0, (_now() - job.started_at).total_seconds())


def variant_budget_multiplier(job: ReelJob) -> int:
    return max(1, int(job.request.variant_count or 1))


def budget_limits(job: ReelJob) -> dict[str, int]:
    n = variant_budget_multiplier(job)
    veo_limit = settings.MAX_VEO_CALLS
    if getattr(job.request, "long_form", False):
        needed = max(veo_limit, int(job.request.duration_seconds // 4) + 2)
        veo_limit = min(int(settings.MAX_DIRECTOR_VEO_CALLS), needed)
    total_limit = settings.MAX_TOTAL_GENERATION_BUDGET
    if getattr(job.request, "long_form", False):
        total_limit = max(total_limit, veo_limit + 8)
    return {
        "veo": veo_limit * n,
        "image": settings.MAX_IMAGE_CALLS * n,
        "tts": settings.MAX_TTS_CALLS * n,
        "gemini": settings.MAX_GEMINI_CALLS * n,
        "total": total_limit * n,
        "pipeline_attempts": settings.MAX_PIPELINE_ATTEMPTS,
        "regen_per_shot": settings.MAX_REGENERATION_PER_SHOT,
        "runtime": settings.MAX_PIPELINE_RUNTIME,
    }


def trip(job: ReelJob, reason: str) -> None:
    job.failure_class = FailureClass.COST_GUARD.value
    job.last_error = reason
    job.message = reason
    try:
        transition(job, ReelStatus.FAILED_COST_GUARD, reason, progress=job.progress)
    except Exception:
        job.status = ReelStatus.FAILED_COST_GUARD
    raise CostGuardTripped(reason)


def check_runtime(job: ReelJob) -> None:
    if elapsed_seconds(job) > settings.MAX_PIPELINE_RUNTIME:
        trip(job, f"pipeline runtime exceeded {settings.MAX_PIPELINE_RUNTIME}s")


def check_pipeline_attempts(job: ReelJob) -> None:
    if job.pipeline_attempts > settings.MAX_PIPELINE_ATTEMPTS:
        trip(job, f"MAX_PIPELINE_ATTEMPTS={settings.MAX_PIPELINE_ATTEMPTS} exceeded")


def _count(ledger: GenerationLedger, kind: str) -> int:
    return {
        "veo": ledger.veo_calls,
        "image": ledger.image_calls,
        "tts": ledger.tts_calls,
        "gemini": ledger.gemini_calls,
    }.get(kind, 0)


def check_budget(job: ReelJob, kind: str) -> None:
    check_runtime(job)
    limits = budget_limits(job)
    if _count(job.ledger, kind) >= limits.get(kind, 0):
        trip(job, f"{kind} budget exhausted ({limits.get(kind)})")
    if job.ledger.total_calls >= limits["total"]:
        trip(job, f"total generation budget exhausted ({limits['total']})")


def check_shot_regen(job: ReelJob, shot_id: str) -> None:
    used = job.ledger.regenerations.get(shot_id, 0)
    if used >= settings.MAX_REGENERATION_PER_SHOT:
        trip(job, f"MAX_REGENERATION_PER_SHOT exceeded for {shot_id}")


def note_repeat_failure(job: ReelJob, signature: str) -> None:
    if signature and signature == job.last_error_signature:
        job.repeat_failure_count += 1
    else:
        job.last_error_signature = signature
        job.repeat_failure_count = 1
    if job.repeat_failure_count >= settings.CIRCUIT_BREAKER_REPEAT_FAILURES:
        trip(job, f"circuit breaker: repeated failure {signature[:120]}")


def classify_failure(exc: Exception) -> FailureClass:
    kind = classify_error(exc)
    if isinstance(exc, CostGuardTripped):
        return FailureClass.COST_GUARD
    if isinstance(exc, DryRunViolation):
        return FailureClass.APPLICATION
    msg = str(exc).lower()
    if "ffmpeg" in msg or "ffprobe" in msg:
        return FailureClass.APPLICATION
    from vidgen.reels.craft import CraftRejected
    if isinstance(exc, CraftRejected):
        return FailureClass.APPLICATION
    if "duration" in msg or "max_duration" in msg:
        return FailureClass.DURATION
    if kind == "deterministic":
        return FailureClass.PERMANENT
    if kind == "transient":
        return FailureClass.TRANSIENT
    return FailureClass.PERMANENT


def is_retryable_failure(exc: Exception) -> bool:
    return classify_failure(exc) == FailureClass.TRANSIENT


class IdempotencyStore:
    def __init__(self, root: Path):
        self.path = Path(root) / "idempotency.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except Exception:
                self._data = {}

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def put(self, record: GenerationRecord) -> None:
        self._data[record.request_hash] = record.model_dump(mode="json")
        self.path.write_text(json.dumps(self._data, indent=2, default=str))


def log_event(job: ReelJob, **event: Any) -> None:
    payload = {"ts": _now().isoformat(), "job_id": job.job_id, "stage": job.status.value, **event}
    job.ledger.events.append(payload)
    print(
        f"[REEL {job.job_id[:8]}] stage={job.status.value} "
        + " ".join(f"{k}={v}" for k, v in event.items() if k != "inputs")
    )


def execute_expensive(
    job: ReelJob,
    *,
    kind: str,
    operation: str,
    model: str,
    prompt: str,
    inputs: dict,
    store: IdempotencyStore,
    fn: Callable[[], str],
    dry_run: bool,
    shot_id: str = "",
) -> str:
    """
    validate → budget → duplicate → attempt → execute → persist.
    Dry-run never calls fn().
    """
    if kind not in _EXPENSIVE_KINDS:
        raise PermanentGenerationError(f"unknown expensive kind {kind}")
    if dry_run or settings.DRY_RUN:
        raise DryRunViolation(f"refusing expensive {kind}/{operation} during dry-run")

    check_budget(job, kind)
    if shot_id:
        check_shot_regen(job, shot_id)

    key = request_hash({
        "job_id": job.job_id,
        "kind": kind,
        "operation": operation,
        "model": model,
        "prompt": prompt,
        "inputs": inputs,
    })
    existing = store.get(key)
    if existing and existing.get("status") == "completed" and existing.get("result_uri"):
        log_event(job, event="idempotent_reuse", operation=operation, request_hash=key[:12])
        return existing["result_uri"]
    if existing and existing.get("status") == "failed":
        note_repeat_failure(job, existing.get("error") or key)
        raise PermanentGenerationError(
            f"identical generation request already failed: {existing.get('error')}"
        )

    record = GenerationRecord(
        request_hash=key,
        prompt_hash=prompt_hash(prompt),
        model=model,
        operation=operation,
        inputs=inputs,
        attempt=1,
        status="running",
    )
    t0 = time.monotonic()
    try:
        uri = fn()
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)[:400]
        record.completed_at = _now()
        store.put(record)
        job.ledger.records.append(record)
        _increment(job.ledger, kind)
        log_event(
            job, event="generation_failed", kind=kind, operation=operation,
            error=str(exc)[:160], elapsed=round(time.monotonic() - t0, 2),
        )
        klass = classify_failure(exc)
        if klass == FailureClass.PERMANENT:
            raise PermanentGenerationError(str(exc)) from exc
        raise

    record.status = "completed"
    record.result_uri = uri
    record.completed_at = _now()
    store.put(record)
    job.ledger.records.append(record)
    _increment(job.ledger, kind)
    if shot_id and "regen" in operation:
        job.ledger.regenerations[shot_id] = job.ledger.regenerations.get(shot_id, 0) + 1
    log_event(
        job, event="generation_ok", kind=kind, operation=operation,
        model=model, request_hash=key[:12], elapsed=round(time.monotonic() - t0, 2),
        generation_count=job.ledger.total_calls,
    )
    return uri


def _increment(ledger: GenerationLedger, kind: str) -> None:
    if kind == "veo":
        ledger.veo_calls += 1
    elif kind == "image":
        ledger.image_calls += 1
    elif kind == "tts":
        ledger.tts_calls += 1
    elif kind == "gemini":
        ledger.gemini_calls += 1
    ledger.total_calls += 1


def checkpoint(job: ReelJob, storage=None) -> Path:
    from vidgen.config import settings as cfg
    root = cfg.VIDGEN_WORK_ROOT / "reels" / job.job_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "job_state.json"
    path.write_text(job.model_dump_json(indent=2))
    if storage is not None:
        try:
            storage.upload(str(path), f"reels/{job.job_id}/state.json")
        except Exception as exc:
            print(f"[WARN] reel GCS checkpoint failed (local ok): {exc}")
    return path


def load_checkpoint(job_id: str, storage=None) -> Optional[ReelJob]:
    from vidgen.config import settings as cfg
    local = cfg.VIDGEN_WORK_ROOT / "reels" / job_id / "job_state.json"
    if local.exists():
        return ReelJob.model_validate_json(local.read_text())
    if storage is None:
        return None
    gcs = f"gs://{cfg.GCS_BUCKET}/reels/{job_id}/state.json"
    try:
        if storage.exists(gcs):
            local.parent.mkdir(parents=True, exist_ok=True)
            storage.download(gcs, str(local))
            return ReelJob.model_validate_json(local.read_text())
    except Exception:
        return None
    return None
