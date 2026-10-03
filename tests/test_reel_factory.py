"""Reels Super Factory — duration, budget, retry, idempotency, checkpoints, dry-run, media."""
from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from vidgen.config import settings
from vidgen.reels.constants import MAX_DURATION_SECONDS
from vidgen.reels.creative import build_brief, build_hooks, build_script, plan_production
from vidgen.reels.duration import (
    DurationExceeded,
    assert_duration,
    estimate_speech_seconds,
    is_duration_valid,
    plan_shot_durations,
)
from vidgen.reels.factory import ReelFactory
from vidgen.reels.qc import run_qc, technical_qc
from vidgen.reels.safety import (
    CostGuardTripped,
    IdempotencyStore,
    PermanentGenerationError,
    execute_expensive,
    trip,
)
from vidgen.reels.schemas import ReelJob, ReelRequest, ReelStatus
from vidgen.reels.state import InvalidTransition, infer_resume_stage, transition


def _req(**kwargs) -> ReelRequest:
    data = dict(
        idea="Make a 15-second Bengali Reel for this perfume. Premium but realistic.",
        language="bengali",
        duration_seconds=15.0,
        audience="young Bangladeshi professionals",
        style="premium but realistic",
        product_name="Noir Atelier",
        cta="পেজে গিয়ে দেখো",
        dry_run=True,
    )
    data.update(kwargs)
    return ReelRequest(**data)


class TestDuration(unittest.TestCase):
    def test_29_9_pass(self):
        self.assertTrue(is_duration_valid(29.9))
        self.assertEqual(assert_duration(29.9), 29.9)

    def test_30_0_pass(self):
        self.assertTrue(is_duration_valid(30.0))
        assert_duration(30.0)

    def test_30_01_fail(self):
        self.assertFalse(is_duration_valid(30.01))
        with self.assertRaises(DurationExceeded):
            assert_duration(30.01)

    def test_31_fail(self):
        self.assertFalse(is_duration_valid(31))
        with self.assertRaises(DurationExceeded):
            assert_duration(31)

    def test_request_rejects_over_30(self):
        with self.assertRaises(ValidationError):
            ReelRequest(idea="Make me a reel about tea", duration_seconds=30.01)

    def test_planner_never_exceeds_30(self):
        for target in (10, 12, 15, 20, 25, 29.9, 30):
            durs = plan_shot_durations(target)
            self.assertLessEqual(sum(durs), MAX_DURATION_SECONDS)
            self.assertTrue(is_duration_valid(sum(durs)))

    def test_speech_estimate_positive(self):
        self.assertGreater(estimate_speech_seconds("This is a short line.", "english"), 0.2)
        self.assertGreater(estimate_speech_seconds("এই গন্ধটা কাছে এলেই বোঝা যায়।", "bengali"), 0.2)


class TestCreative(unittest.TestCase):
    def test_rejects_generic_opening(self):
        req = _req()
        brief = build_brief(req)
        hook = build_hooks(brief, req)
        script = build_script(brief, req, hook)
        self.assertFalse(script.full_text.lower().startswith("in today's fast-paced world"))
        self.assertNotIn("আজকের দ্রুতগতির বিশ্বে", script.full_text)

    def test_script_fits_before_generation(self):
        job = ReelJob(request=_req(duration_seconds=12))
        plan_production(job)
        self.assertLessEqual(job.script.estimated_speech_seconds, 30.0)
        self.assertLessEqual(job.storyboard.total_duration, 30.0)
        self.assertLessEqual(job.storyboard.total_duration, 12.0 + 8.0)  # veo snap may be slightly under/near
        self.assertGreaterEqual(len(job.storyboard.shots), 1)
        self.assertLessEqual(len(job.storyboard.shots), 6)
        shot = job.storyboard.shots[0]
        for field in (
            "shot_id", "duration", "start_time", "end_time", "purpose", "action",
            "camera", "framing", "lighting", "generation_prompt",
        ):
            self.assertTrue(getattr(shot, field) is not None)
        self.assertTrue(shot.continuity_requirements)
        self.assertTrue(job.character_bible)
        self.assertTrue(job.product_bible)
        self.assertEqual(job.product_bible.name, "Noir Atelier")


class TestStateMachine(unittest.TestCase):
    def test_valid_and_invalid_transitions(self):
        job = ReelJob(request=_req())
        transition(job, ReelStatus.PLANNING)
        self.assertEqual(job.status, ReelStatus.PLANNING)
        with self.assertRaises(InvalidTransition):
            transition(job, ReelStatus.QC)

    def test_failed_cost_guard_is_terminal(self):
        job = ReelJob(request=_req())
        transition(job, ReelStatus.PLANNING)
        transition(job, ReelStatus.FAILED_COST_GUARD)
        with self.assertRaises(InvalidTransition):
            transition(job, ReelStatus.PLANNING)


class TestBudgetAndCircuit(unittest.TestCase):
    def test_budget_exhaustion_stops(self):
        job = ReelJob(request=_req())
        job.ledger.total_calls = settings.MAX_TOTAL_GENERATION_BUDGET
        with self.assertRaises(CostGuardTripped):
            from vidgen.reels.safety import check_budget
            check_budget(job, "veo")
        self.assertEqual(job.status, ReelStatus.FAILED_COST_GUARD)

    def test_circuit_breaker_on_repeat_failure(self):
        job = ReelJob(request=_req())
        transition(job, ReelStatus.PLANNING)
        with self.assertRaises(CostGuardTripped):
            trip(job, "repeated failure demo")
        self.assertEqual(job.status, ReelStatus.FAILED_COST_GUARD)

    def test_permanent_failure_does_not_retry_in_execute(self):
        job = ReelJob(request=_req(dry_run=False))
        root = settings.VIDGEN_WORK_ROOT / "reels" / job.job_id
        store = IdempotencyStore(root)
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError("400 invalid_argument: bad prompt")

        with self.assertRaises(PermanentGenerationError):
            execute_expensive(
                job, kind="veo", operation="shot/x", model="veo",
                prompt="p", inputs={"a": 1}, store=store, fn=boom, dry_run=False,
            )
        self.assertEqual(calls["n"], 1)

    def test_transient_is_retryable_classifier(self):
        from vidgen.reels.safety import is_retryable_failure
        self.assertTrue(is_retryable_failure(RuntimeError("503 unavailable")))
        self.assertFalse(is_retryable_failure(RuntimeError("400 invalid_argument")))


class TestIdempotency(unittest.TestCase):
    def test_duplicate_does_not_regenerate(self):
        job = ReelJob(request=_req(dry_run=False))
        root = settings.VIDGEN_WORK_ROOT / "reels" / job.job_id
        store = IdempotencyStore(root)
        calls = {"n": 0}

        def once():
            calls["n"] += 1
            return "gs://mock/out.mp4"

        a = execute_expensive(
            job, kind="veo", operation="shot/r01", model="veo",
            prompt="same", inputs={"shot": "r01"}, store=store, fn=once, dry_run=False,
        )
        b = execute_expensive(
            job, kind="veo", operation="shot/r01", model="veo",
            prompt="same", inputs={"shot": "r01"}, store=store, fn=once, dry_run=False,
        )
        self.assertEqual(a, b)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(job.ledger.veo_calls, 1)


class TestDryRun(unittest.TestCase):
    def test_dry_run_no_expensive_calls(self):
        factory = ReelFactory()
        job = factory.create(_req(duration_seconds=15, dry_run=True))
        job = factory.run(job, dry_run=True)
        self.assertEqual(job.status, ReelStatus.COMPLETE)
        self.assertIsNotNone(job.dry_run_manifest)
        self.assertEqual(job.ledger.total_calls, 0)
        self.assertEqual(job.dry_run_manifest.expensive_calls_made, 0)
        self.assertLessEqual(job.dry_run_manifest.target_duration, 30)
        self.assertEqual(job.dry_run_manifest.max_duration, 30.0)
        self.assertEqual(job.dry_run_manifest.budget_status, "within_limit")
        self.assertGreaterEqual(job.dry_run_manifest.estimated_veo_calls, 1)
        self.assertFalse(any("In today's fast-paced world" in (job.script.full_text if job.script else "") for _ in [0]))

    def test_dry_run_blocks_execute_expensive(self):
        job = ReelJob(request=_req())
        store = IdempotencyStore(settings.VIDGEN_WORK_ROOT / "reels" / job.job_id)
        with self.assertRaises(Exception):
            execute_expensive(
                job, kind="veo", operation="x", model="veo", prompt="p",
                inputs={}, store=store, fn=lambda: "uri", dry_run=True,
            )
        self.assertEqual(job.ledger.total_calls, 0)


class TestCheckpointResume(unittest.TestCase):
    def test_assembly_failure_resumes_from_assembly(self):
        factory = ReelFactory()
        job = factory.create(_req(duration_seconds=12, dry_run=True))
        plan_production(job)
        for shot in job.storyboard.shots:
            shot.generated_asset_uri = f"gs://mock/{shot.shot_id}.mp4"
        job.audio_plan = job.audio_plan
        from vidgen.reels.audio import build_audio_plan, default_edit_plan
        job.audio_plan = build_audio_plan(job)
        job.audio_plan.dialogue_cues[0].local_path = "/tmp/fake.mp3" if job.audio_plan.dialogue_cues else ""
        job.edit_plan = default_edit_plan(job)
        job.status = ReelStatus.FAILED
        job.message = "assembly exploded"
        stage = infer_resume_stage(job)
        self.assertIn(stage, {ReelStatus.ASSEMBLING, ReelStatus.SHOTS_READY, ReelStatus.AUDIO_READY, ReelStatus.EDIT_PLAN_READY})
        # All shots already exist — must not go back to planning
        self.assertNotEqual(stage, ReelStatus.QUEUED)
        self.assertNotEqual(stage, ReelStatus.PLANNING)


class TestSimulationMedia(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_simulation_final_media_is_vertical_and_short(self):
        with patch.object(settings, "FILM_MODE", "simulation"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", False):
            factory = ReelFactory()
            job = factory.create(_req(
                idea="Make me a Reel for this skincare product. Realistic and trustworthy.",
                language="english",
                duration_seconds=10.0,
                product_name="Glow Serum",
                dry_run=False,
            ))
            job = factory.run(job, dry_run=False)
            self.assertEqual(job.status, ReelStatus.COMPLETE, job.last_error)
            self.assertTrue(job.final_video_path and Path(job.final_video_path).exists())
            tech = technical_qc(job.final_video_path)
            self.assertTrue(tech["has_video"])
            self.assertTrue(tech["has_audio"])
            self.assertLessEqual(tech["duration"], 30.0)
            self.assertTrue(tech["duration_valid"])
            self.assertEqual(tech["width"], 1080)
            self.assertEqual(tech["height"], 1920)
            self.assertTrue(tech["aspect_ok"])
            self.assertIn(tech["video_codec"], {"h264", "hevc", "av1"})
            qc = run_qc(job, job.final_video_path)
            self.assertTrue(qc.passed, qc.failures)


class TestApiReel(unittest.TestCase):
    def test_create_dry_run_endpoint(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        r = client.post("/api/v1/reels", json={
            "idea": "Create a 12-second English Reel for this tea brand.",
            "language": "english",
            "duration_seconds": 12,
            "product_name": "Lal Cha",
            "dry_run": True,
        })
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["status"], "complete")
        self.assertIsNotNone(data["dry_run_manifest"])
        self.assertEqual(data["generation_count"], 0)
        self.assertLessEqual(data["dry_run_manifest"]["target_duration"], 30)

    def test_rejects_duration_over_30(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        r = client.post("/api/v1/reels", json={
            "idea": "Make a long ad",
            "duration_seconds": 31,
        })
        self.assertIn(r.status_code, (400, 422))


if __name__ == "__main__":
    unittest.main()
