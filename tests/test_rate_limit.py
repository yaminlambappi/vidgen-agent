"""Rate-limit resilience tests for provider retries and Veo."""
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


