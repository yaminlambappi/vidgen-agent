"""
Rate-limit resilience tests for VidGen.

Tests (all mock time.sleep so execution is fast):
  test_retry_on_429
  test_retry_on_503
  test_retry_on_500
  test_no_retry_on_403
  test_no_retry_on_404
  test_no_retry_on_400
  test_exponential_backoff
  test_retry_after_header
  test_retry_exhaustion_returns_structured_error
  test_image_reference_reused_when_existing
  test_image_reference_not_regenerated_in_process
  test_duplicate_reference_generation_prevented
  test_veo_retry_does_not_duplicate_shot
  test_veo_rate_limit_exhausted_returns_structured_job
  test_orchestrator_marks_rate_limit_failure
  test_resume_reuses_completed_references
  test_resume_does_not_regenerate_completed_shots
  test_llm_retries_on_429
  test_llm_no_retry_on_403
  test_rate_limit_exhausted_to_dict
  test_classify_error_transient
  test_classify_error_deterministic
  test_classify_error_unknown_treated_as_transient
"""
from __future__ import annotations
import unittest
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch, call
import tempfile

from vidgen.utils.retry import (
    call_with_retry, RateLimitExhausted, classify_error,
    is_retryable, _backoff_seconds,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _exc(msg: str) -> Exception:
    return RuntimeError(msg)


def _rate_then_success(n_failures: int, success_value=42):
    """Return a callable that fails n_failures times then returns success_value."""
    calls = []
    def fn():
        calls.append(len(calls) + 1)
        if len(calls) <= n_failures:
            raise RuntimeError("429 resource_exhausted quota exceeded")
        return success_value
    return fn, calls


# ── classify_error ─────────────────────────────────────────────────────────────

class TestClassifyError(unittest.TestCase):

    def test_classify_429(self):
        self.assertEqual(classify_error(_exc("429 resource_exhausted")), "transient")

    def test_classify_503(self):
        self.assertEqual(classify_error(_exc("503 unavailable")), "transient")

    def test_classify_500(self):
        self.assertEqual(classify_error(_exc("500 internal")), "transient")

    def test_classify_timeout(self):
        self.assertEqual(classify_error(_exc("deadline exceeded timeout")), "transient")

    def test_classify_403(self):
        self.assertEqual(classify_error(_exc("403 permission denied")), "deterministic")

    def test_classify_404(self):
        self.assertEqual(classify_error(_exc("404 not found")), "deterministic")

    def test_classify_400(self):
        self.assertEqual(classify_error(_exc("400 invalid_argument")), "deterministic")

    def test_classify_unknown_treated_as_transient(self):
        """Unknown errors get classified as 'unknown' and is_retryable returns True."""
        result = classify_error(_exc("some mysterious network glitch"))
        # 'unknown' is valid; retry.py also defaults unrecognized to 'transient' — both are retryable
        self.assertIn(result, ("unknown", "transient"),
                      "Unknown errors must be retryable")
        self.assertTrue(is_retryable(_exc("some mysterious network glitch")))


# ── call_with_retry core behaviour ────────────────────────────────────────────

class TestCallWithRetry(unittest.TestCase):

    def test_retry_on_429(self):
        """429 must be retried."""
        fn, calls = _rate_then_success(n_failures=2)
        result = call_with_retry(fn, "test", "m", "op",
                                 max_attempts=5, sleep_fn=lambda _: None)
        self.assertEqual(result, 42)
        self.assertEqual(len(calls), 3)  # 2 failures + 1 success

    def test_retry_on_503(self):
        """503 must be retried."""
        fn, calls = _rate_then_success(0)  # succeed immediately (503 variant)
        attempts = []

        def raising():
            attempts.append(1)
            if len(attempts) <= 1:
                raise RuntimeError("503 unavailable service temporarily")
            return "ok"

        result = call_with_retry(raising, "test", "m", "op",
                                 max_attempts=3, sleep_fn=lambda _: None)
        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 2)

    def test_retry_on_500(self):
        """500 must be retried."""
        attempts = []

        def fn():
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("500 internal server error")
            return "done"

        result = call_with_retry(fn, "test", "m", "op",
                                 max_attempts=3, sleep_fn=lambda _: None)
        self.assertEqual(result, "done")
        self.assertEqual(len(attempts), 2)

    def test_no_retry_on_403(self):
        """403 is deterministic — must raise immediately without retry."""
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("403 permission denied does not have access")

        with self.assertRaises(RuntimeError) as ctx:
            call_with_retry(fn, "test", "m", "op",
                            max_attempts=5, sleep_fn=lambda _: None)
        self.assertEqual(len(calls), 1)
        self.assertIn("Deterministic", str(ctx.exception))

    def test_no_retry_on_404(self):
        """404 is deterministic — must raise immediately."""
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("404 model was not found")

        with self.assertRaises(RuntimeError):
            call_with_retry(fn, "test", "m", "op",
                            max_attempts=5, sleep_fn=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_no_retry_on_400(self):
        """400 is deterministic — must raise immediately."""
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("400 invalid_argument bad request")

        with self.assertRaises(RuntimeError):
            call_with_retry(fn, "test", "m", "op",
                            max_attempts=5, sleep_fn=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_exponential_backoff(self):
        """Delay doubles each attempt (approx — jitter aside)."""
        from vidgen.config import settings
        delays = []
        calls = []

        def fn():
            calls.append(1)
            raise RuntimeError("429 resource_exhausted")

        with patch.object(settings, "VIDGEN_INITIAL_BACKOFF_SECONDS", 2.0), \
             patch.object(settings, "VIDGEN_MAX_BACKOFF_SECONDS", 1000.0), \
             patch.object(settings, "VIDGEN_RETRY_JITTER", 0.0):
            with self.assertRaises(RateLimitExhausted):
                call_with_retry(fn, "test", "m", "op",
                                max_attempts=4,
                                sleep_fn=lambda d: delays.append(d))

        # Delays should be approximately 2, 4, 8 (3 sleeps for 4 attempts)
        self.assertEqual(len(delays), 3)
        self.assertAlmostEqual(delays[0], 2.0, places=0)
        self.assertAlmostEqual(delays[1], 4.0, places=0)
        self.assertAlmostEqual(delays[2], 8.0, places=0)

    def test_retry_after_header(self):
        """Retry-After value in exception message is respected, capped at max."""
        from vidgen.config import settings
        delays = []

        def fn():
            raise RuntimeError("429 resource_exhausted retry-after: 30 seconds")

        with patch.object(settings, "VIDGEN_MAX_BACKOFF_SECONDS", 60.0), \
             patch.object(settings, "VIDGEN_RETRY_JITTER", 0.0):
            with self.assertRaises(RateLimitExhausted):
                call_with_retry(fn, "test", "m", "op",
                                max_attempts=2,
                                sleep_fn=lambda d: delays.append(d))

        self.assertEqual(len(delays), 1)
        self.assertEqual(delays[0], 30.0)

    def test_retry_exhaustion_returns_structured_error(self):
        """When all attempts fail, RateLimitExhausted carries structured metadata."""
        def fn():
            raise RuntimeError("429 resource_exhausted")

        with self.assertRaises(RateLimitExhausted) as ctx:
            call_with_retry(fn, "gemini-image", "test-model", "generate_image",
                            max_attempts=3, sleep_fn=lambda _: None)

        exc = ctx.exception
        self.assertEqual(exc.provider, "gemini-image")
        self.assertEqual(exc.model, "test-model")
        self.assertEqual(exc.operation, "generate_image")
        self.assertEqual(exc.attempts, 3)
        self.assertIn("resource_exhausted", exc.last_error.lower())

        d = exc.to_dict()
        self.assertEqual(d["failure_code"], "RATE_LIMIT_EXHAUSTED")
        self.assertEqual(d["provider"], "gemini-image")


# ── Image reference reuse ─────────────────────────────────────────────────────

class TestImageReferenceReuse(unittest.TestCase):

    def _make_char(self, char_id="C1"):
        from vidgen.models import Character
        return Character(character_id=char_id, name="Test")

    def test_image_reference_reused_when_existing(self):
        """If GCS already has the reference, image generation must NOT be called."""
        from vidgen.utils import references as refs_mod
        from vidgen.models import Character

        # Clear in-process cache
        with refs_mod._ref_lock:
            refs_mod._generated_refs.clear()

        char = self._make_char("EXIST_C")
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True  # GCS already has it
        mock_storage.download.return_value = None

        mock_image_gen = MagicMock()
        mock_image_gen.generate.return_value = b"\x89PNG" + b"\x00" * 100

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("vidgen.utils.references.settings") as mock_cfg:
                mock_cfg.VIDGEN_WORK_ROOT = Path(tmpdir)
                mock_cfg.GCS_BUCKET = "test-bucket"
                mock_cfg.IMAGE_REQUEST_DELAY_SECONDS = 0.0
                refs_mod.ensure_character_reference(
                    char, "proj1", mock_storage, mock_image_gen, "prompt")

        mock_image_gen.generate.assert_not_called()
        self.assertTrue(char.reference_image_uri.startswith("gs://"))

    def test_image_reference_not_regenerated_in_process(self):
        """Same reference must not be generated twice in one process run."""
        from vidgen.utils import references as refs_mod
        from vidgen.models import Character

        # Clear in-process cache
        with refs_mod._ref_lock:
            refs_mod._generated_refs.clear()

        generated_count = []

        def fake_generate(prompt):
            generated_count.append(1)
            return b"\x89PNG" + b"\x00" * 100

        mock_storage = MagicMock()
        mock_storage.exists.return_value = False  # first call: not in GCS
        mock_storage.upload.return_value = "gs://test-bucket/refs/char_C_DUPE.png"

        mock_image_gen = MagicMock(generate=fake_generate)

        char1 = Character(character_id="C_DUPE", name="Alice")
        char2 = Character(character_id="C_DUPE", name="Alice")  # same ID

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("vidgen.utils.references.settings") as mock_cfg:
                mock_cfg.VIDGEN_WORK_ROOT = Path(tmpdir)
                mock_cfg.GCS_BUCKET = "test-bucket"
                mock_cfg.IMAGE_REQUEST_DELAY_SECONDS = 0.0

                # First call: generates
                refs_mod.ensure_character_reference(
                    char1, "proj2", mock_storage, mock_image_gen, "prompt")

                # Simulate GCS now having the file (as upload returned a URI)
                mock_storage.exists.return_value = True

                # Second call for same entity_id: must reuse
                refs_mod.ensure_character_reference(
                    char2, "proj2", mock_storage, mock_image_gen, "prompt")

        # Must only have generated once
        self.assertEqual(len(generated_count), 1)

    def test_duplicate_reference_generation_prevented(self):
        """In-process cache prevents duplicate generation even without GCS."""
        from vidgen.utils import references as refs_mod
        from vidgen.models import Character

        with refs_mod._ref_lock:
            refs_mod._generated_refs.clear()

        calls = []

        def fake_generate(prompt):
            calls.append(prompt)
            return b"\x89PNG" + b"\x00" * 100

        mock_storage = MagicMock()
        mock_storage.exists.return_value = False
        mock_storage.upload.return_value = "gs://bucket/c.png"

        mock_image_gen = MagicMock(generate=fake_generate)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("vidgen.utils.references.settings") as mock_cfg:
                mock_cfg.VIDGEN_WORK_ROOT = Path(tmpdir)
                mock_cfg.GCS_BUCKET = "bucket"
                mock_cfg.IMAGE_REQUEST_DELAY_SECONDS = 0.0

                char_a = Character(character_id="C_NODUP", name="Alice")
                char_b = Character(character_id="C_NODUP", name="Alice")

                # First call generates
                refs_mod.ensure_character_reference(
                    char_a, "proj3", mock_storage, mock_image_gen, "p1")

                # Immediately second call for same ID — GCS still returns False
                # but in-process cache must prevent second generation
                mock_storage.exists.return_value = False
                refs_mod.ensure_character_reference(
                    char_b, "proj3", mock_storage, mock_image_gen, "p1")

        self.assertEqual(len(calls), 1, "Image generation called more than once for same entity")


# ── Veo rate-limit ─────────────────────────────────────────────────────────────

class TestVeoRateLimit(unittest.TestCase):

    def test_veo_retry_does_not_duplicate_shot(self):
        """Retry on 429 must reuse the same shot_id and not create a new one."""
        from vidgen.providers.video import VeoVideoGenerator

        gen = VeoVideoGenerator.__new__(VeoVideoGenerator)
        gen.model = "veo-3.1-generate-001"

        submitted_shot_ids = []
        call_count = [0]

        mock_op_success = MagicMock()
        mock_op_success.done = True
        mock_op_success.error = None
        mock_video = MagicMock()
        mock_video.uri = "gs://bucket/shot.mp4"
        mock_generated = MagicMock()
        mock_generated.video = mock_video
        mock_op_success.response = MagicMock(generated_videos=[mock_generated])

        def fake_generate_videos(model, prompt, config):
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("429 resource_exhausted quota exceeded")
            return mock_op_success

        mock_client = MagicMock()
        mock_client.models.generate_videos.side_effect = fake_generate_videos
        mock_client.operations.get.return_value = mock_op_success
        gen.client = mock_client

        delays = []
        with patch("vidgen.utils.retry.time.sleep", side_effect=lambda d: delays.append(d)):
            from vidgen.config import settings
            with patch.object(settings, "VIDGEN_MAX_RETRIES", 5), \
                 patch.object(settings, "VIDGEN_INITIAL_BACKOFF_SECONDS", 0.01), \
                 patch.object(settings, "VIDGEN_MAX_BACKOFF_SECONDS", 1.0), \
                 patch.object(settings, "VIDGEN_RETRY_JITTER", 0.0), \
                 patch("vidgen.providers.video.VeoVideoGenerator._extract_uri",
                       return_value="gs://bucket/shot.mp4"):
                job = gen.generate_shot(
                    prompt="test", output_uri="gs://bucket/out/",
                    shot_id="SHOT_01", project_id="p1")

        self.assertEqual(job.status, "completed")
        self.assertEqual(job.artifact_uri, "gs://bucket/shot.mp4")
        # Shot ID was never changed
        self.assertEqual(job.shot_id, "SHOT_01")
        # generate_videos was called 3 times (2 failures + 1 success)
        self.assertEqual(call_count[0], 3)

    def test_veo_rate_limit_exhausted_returns_structured_job(self):
        """When Veo exhausts retries, job status is rate_limit_exhausted."""
        from vidgen.providers.video import VeoVideoGenerator

        gen = VeoVideoGenerator.__new__(VeoVideoGenerator)
        gen.model = "veo-3.1-generate-001"

        mock_client = MagicMock()
        mock_client.models.generate_videos.side_effect = RuntimeError(
            "429 resource_exhausted quota exceeded")
        gen.client = mock_client

        from vidgen.config import settings
        with patch.object(settings, "VIDGEN_MAX_RETRIES", 2), \
             patch.object(settings, "VIDGEN_INITIAL_BACKOFF_SECONDS", 0.01), \
             patch.object(settings, "VIDGEN_MAX_BACKOFF_SECONDS", 0.1), \
             patch.object(settings, "VIDGEN_RETRY_JITTER", 0.0), \
             patch("vidgen.utils.retry.time.sleep"):
            job = gen.generate_shot(
                prompt="test", output_uri="gs://bucket/out/",
                shot_id="SHOT_RL", project_id="p1")

        self.assertEqual(job.status, "rate_limit_exhausted")
        self.assertIn("RATE_LIMIT_EXHAUSTED", job.error)


# ── Orchestrator rate-limit handling ──────────────────────────────────────────

class TestOrchestratorRateLimit(unittest.TestCase):

    def test_orchestrator_marks_rate_limit_failure(self):
        """When RateLimitExhausted propagates, orchestrator sets FAILED + structured info."""
        from vidgen.orchestrator import Orchestrator
        from vidgen.models import (
            FilmProject, FilmStatus, CharacterBible, WorldBible, CinematicBible,
            MusicPlan, Scene, Shot, StorySpec
        )
        from vidgen.utils.retry import RateLimitExhausted

        orc = Orchestrator.__new__(Orchestrator)
        object.__setattr__(orc, "storage", MagicMock(upload=MagicMock(return_value="gs://m/f"),
                                                       download=MagicMock()))
        object.__setattr__(orc, "video_gen", MagicMock())
        object.__setattr__(orc, "researcher", MagicMock(ground=MagicMock(return_value="r")))
        object.__setattr__(orc, "intent_agent", MagicMock())
        object.__setattr__(orc, "story_arch", MagicMock())
        object.__setattr__(orc, "screenwriter", MagicMock())
        object.__setattr__(orc, "char_design", MagicMock())
        object.__setattr__(orc, "world_design", MagicMock())
        object.__setattr__(orc, "cinematog", MagicMock())
        object.__setattr__(orc, "storyboarder", MagicMock())
        object.__setattr__(orc, "voice", MagicMock())
        object.__setattr__(orc, "voice_design", MagicMock())
        object.__setattr__(orc, "music", MagicMock())
        object.__setattr__(orc, "editor", MagicMock())
        object.__setattr__(orc, "subtitles", MagicMock())
        object.__setattr__(orc, "qcm_agent", MagicMock())

        from vidgen.models import ContentIntent
        mock_intent = ContentIntent(primary_subject="test", primary_subject_type="character",
                                     narrative_purpose="p", emotional_objective="e",
                                     visual_objective="v", genre="g", tone="t",
                                     target_audience="a", realism_requirement="photorealistic")
        orc.intent_agent.understand.return_value = mock_intent
        orc.story_arch.design_story.return_value = StorySpec(
            title="T", logline="L", theme="TH", genre="G", three_act_structure="123")
        orc.world_design.design_world.return_value = WorldBible(locations=[])
        orc.cinematog.design_cinematics.return_value = CinematicBible(
            color_palette="p", lighting="l", camera_language="c", texture="t", editing_rhythm="e")
        orc.char_design.design_characters.return_value = CharacterBible(characters=[])
        orc.voice_design.design_voices.return_value = MagicMock(assignments={})
        orc.screenwriter.write_scenes.return_value = [
            Scene(scene_id="S1", index=1, title="S", description="D", location_id="L")]
        orc.storyboarder.design_shots.return_value = [
            Shot(shot_id="SH1", scene_id="S1", index=1, subject="S", action="A")]
        orc.music.compose_plan.return_value = MusicPlan()
        orc.editor.compile.return_value = MagicMock(sequence=[])
        orc.subtitles.generate.return_value = ""
        orc.voice.synthesize.return_value = None
        orc.voice.synthesize_dialogue.return_value = []

        rl_exc = RateLimitExhausted(
            provider="veo", model="veo-3.1", operation="generate_shot/SH1",
            attempts=5, last_error="429 resource_exhausted")

        p = FilmProject(topic="rate limit test")

        with patch("vidgen.orchestrator.concatenate_shots"), \
             patch("vidgen.orchestrator.final_mix"), \
             patch("vidgen.orchestrator.validate_video",
                   return_value={"valid": True, "duration": 8.0,
                                 "width": 1920, "height": 1080,
                                 "codec": "h264", "has_audio": True}), \
             patch("vidgen.orchestrator.create_score"), \
             patch("vidgen.utils.ffmpeg.extract_frames"), \
             patch.object(orc, "_generate_and_critique_shot", side_effect=rl_exc):

            with self.assertRaises(RateLimitExhausted):
                orc.run(p)

        self.assertEqual(p.status, FilmStatus.FAILED)
        self.assertEqual(p.last_error_type, "RATE_LIMIT_EXHAUSTED")
        self.assertIn("veo", p.last_error_message)

    def test_resume_does_not_regenerate_completed_shots(self):
        """Shots with generated_asset_uri set must be skipped on resume."""
        from vidgen.orchestrator import Orchestrator
        from vidgen.models import (
            FilmProject, FilmStatus, Scene, Shot, EditPlan, AudioPlan, MusicPlan
        )

        orc = Orchestrator.__new__(Orchestrator)
        object.__setattr__(orc, "storage", MagicMock(
            upload=MagicMock(return_value="gs://m/f"),
            download=MagicMock()))
        object.__setattr__(orc, "video_gen", MagicMock())
        object.__setattr__(orc, "music", MagicMock(compose_plan=MagicMock(return_value=MusicPlan())))
        object.__setattr__(orc, "editor", MagicMock(
            compile=MagicMock(return_value=EditPlan(sequence=["SH1", "SH2"]))))
        object.__setattr__(orc, "subtitles", MagicMock(generate=MagicMock(return_value="")))
        object.__setattr__(orc, "voice", MagicMock(
            synthesize=MagicMock(), synthesize_dialogue=MagicMock(return_value=[]),
            synthesize_narration=MagicMock()))
        object.__setattr__(orc, "qcm_agent", MagicMock())

        shot1 = Shot(shot_id="SH1", scene_id="S1", index=1, duration=8,
                     subject="S", action="A", generated_asset_uri="gs://b/sh1.mp4")
        shot2 = Shot(shot_id="SH2", scene_id="S1", index=2, duration=8,
                     subject="S", action="A", generated_asset_uri="gs://b/sh2.mp4")
        scene = Scene(scene_id="S1", index=1, title="T", description="D",
                      location_id="L", shots=[shot1, shot2])

        p = FilmProject(topic="resume test", status=FilmStatus.GENERATING,
                        scenes=[scene])

        gen_calls = []

        with patch.object(orc, "_generate_and_critique_shot",
                          side_effect=lambda *a, **kw: gen_calls.append(1)), \
             patch("vidgen.orchestrator.concatenate_shots"), \
             patch("vidgen.orchestrator.final_mix"), \
             patch("vidgen.orchestrator.validate_video",
                   return_value={"valid": True, "duration": 16.0,
                                 "width": 1920, "height": 1080,
                                 "codec": "h264", "has_audio": True}), \
             patch("vidgen.orchestrator.create_score"):

            orc.run(p)

        # Both shots already have URIs — generate must never be called
        self.assertEqual(len(gen_calls), 0,
                         "_generate_and_critique_shot called for already-completed shots")
        self.assertEqual(p.status, FilmStatus.COMPLETED)

    def test_resume_reuses_completed_references(self):
        """GCS-existing references must not trigger image generation on resume."""
        from vidgen.utils import references as refs_mod

        with refs_mod._ref_lock:
            refs_mod._generated_refs.clear()

        from vidgen.models import Character

        char = Character(character_id="C_RESUME", name="Resume Test")
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True   # already in GCS
        mock_storage.download.return_value = None

        mock_image_gen = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("vidgen.utils.references.settings") as mock_cfg:
                mock_cfg.VIDGEN_WORK_ROOT = Path(tmpdir)
                mock_cfg.GCS_BUCKET = "bucket"
                mock_cfg.IMAGE_REQUEST_DELAY_SECONDS = 0.0
                refs_mod.ensure_character_reference(
                    char, "proj_resume", mock_storage, mock_image_gen, "p")

        mock_image_gen.generate.assert_not_called()
        self.assertIn("C_RESUME", char.reference_image_uri)


# ── LLM retry via BaseAgent ───────────────────────────────────────────────────

class TestLLMRetry(unittest.TestCase):

    def test_llm_retries_on_429(self):
        """BaseAgent.llm must retry on 429 using the shared policy."""
        from vidgen.agents import BaseAgent

        agent = BaseAgent.__new__(BaseAgent)
        agent.model = "gemini-test"

        calls = []

        def fake_generate(model, contents, config):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("429 resource_exhausted quota exceeded")
            resp = MagicMock()
            resp.text = "hello"
            resp.parsed = None
            return resp

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate
        agent._client = mock_client

        from vidgen.config import settings
        with patch.object(settings, "VIDGEN_MAX_RETRIES", 5), \
             patch.object(settings, "VIDGEN_INITIAL_BACKOFF_SECONDS", 0.01), \
             patch.object(settings, "VIDGEN_MAX_BACKOFF_SECONDS", 0.1), \
             patch.object(settings, "VIDGEN_RETRY_JITTER", 0.0), \
             patch("vidgen.utils.retry.time.sleep"):
            result = agent.llm("test prompt")

        self.assertEqual(result, "hello")
        self.assertEqual(len(calls), 3)

    def test_llm_no_retry_on_403(self):
        """BaseAgent.llm must raise immediately on 403 without retry."""
        from vidgen.agents import BaseAgent

        agent = BaseAgent.__new__(BaseAgent)
        agent.model = "gemini-test"

        calls = []

        def fake_generate(model, contents, config):
            calls.append(1)
            raise RuntimeError("403 permission denied does not have access")

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate
        agent._client = mock_client

        from vidgen.config import settings
        with patch.object(settings, "VIDGEN_MAX_RETRIES", 5), \
             patch("vidgen.utils.retry.time.sleep"):
            with self.assertRaises(RuntimeError):
                agent.llm("test prompt")

        self.assertEqual(len(calls), 1)

    def test_rate_limit_exhausted_to_dict(self):
        """RateLimitExhausted.to_dict returns all required machine-readable fields."""
        exc = RateLimitExhausted(
            provider="gemini-image",
            model="gemini-2.5-flash-image",
            operation="generate_image",
            attempts=5,
            last_error="429 resource_exhausted"
        )
        d = exc.to_dict()
        self.assertEqual(d["failure_code"], "RATE_LIMIT_EXHAUSTED")
        self.assertEqual(d["provider"], "gemini-image")
        self.assertEqual(d["model"], "gemini-2.5-flash-image")
        self.assertEqual(d["operation"], "generate_image")
        self.assertEqual(d["attempts"], 5)
        self.assertIn("429", d["last_error"])


if __name__ == "__main__":
    unittest.main()
