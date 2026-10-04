"""School of Sufi path: exact duration, one plan, a real 9:16 file."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from vidgen.config import settings
from vidgen.sufi.engine import generate
from vidgen.sufi.ledger import CostGuardTripped, Ledger
from vidgen.sufi.plan import PlanError, choose_duration, parse_plan, slot_durations
from vidgen.sufi.publish import publish_video
from vidgen.sufi.render import assert_reel, write_plate


class TestDuration(unittest.TestCase):
    def test_slots_sum_to_the_master(self):
        cases = [
            (30, 5, [6, 6, 6, 6, 6]),
            (30, 4, [8, 8, 7, 7]),
            (30, 6, [5, 5, 5, 5, 5, 5]),
            (60, 10, [6, 6, 6, 6, 6, 6, 6, 6, 6, 6]),
        ]
        for total, count, expected in cases:
            slots = slot_durations(total, count)
            self.assertEqual(slots, expected)
            self.assertEqual(sum(slots), total)

    def test_short_thought_is_thirty_seconds(self):
        self.assertEqual(choose_duration("A quiet return to sincerity."), 30)

    def test_long_thought_is_sixty_seconds(self):
        self.assertEqual(choose_duration("word " * 80), 60)


class TestPlan(unittest.TestCase):
    def test_missing_tags_are_added(self):
        plan = parse_plan(
            {
                "script_text": "The heart grows quiet when remembrance is sincere and unhurried today.",
                "veo_prompts": [
                    "Candle over geometric tile.",
                    "A river at dusk.",
                    "A quiet courtyard.",
                    "An open manuscript.",
                    "A dome in warm light.",
                ],
                "caption_and_hashtags": "Return to sincerity.",
            },
            30,
        )
        self.assertIn("#SchoolOfSufi", plan.caption_and_hashtags)
        self.assertIn("#Tasawwuf", plan.caption_and_hashtags)
        self.assertEqual(plan.slots, [6, 6, 6, 6, 6])
        self.assertEqual(sum(plan.slots), 30)
        self.assertTrue(all("camera" in p.lower() or "pan" in p.lower() or "splash" in p.lower() or "dolly" in p.lower() or "tilt" in p.lower() or "crane" in p.lower() or "drift" in p.lower() or "macro" in p.lower() for p in plan.veo_prompts))

    def test_one_prompt_is_rejected(self):
        with self.assertRaises(PlanError):
            parse_plan(
                {
                    "script_text": "The heart grows quiet when remembrance is sincere and unhurried today.",
                    "veo_prompts": ["Only one picture."],
                    "caption_and_hashtags": "#Sufism",
                },
                30,
            )

    def test_oversized_script_is_rejected(self):
        with self.assertRaises(PlanError):
            parse_plan(
                {
                    "script_text": "word " * 200,
                    "veo_prompts": ["Candle light.", "Still water."],
                    "caption_and_hashtags": "x",
                },
                30,
            )


class TestLedger(unittest.TestCase):
    def test_veo_ceiling_trips_before_the_call(self):
        ledger = Ledger()
        with patch.object(settings, "MAX_VEO_CALLS", 1):
            ledger.charge("veo")
            with self.assertRaises(CostGuardTripped):
                ledger.charge("veo")


class TestPublish(unittest.TestCase):
    def test_unconfigured_targets_are_skipped(self):
        with patch.object(settings, "YOUTUBE_CLIENT_ID", ""), \
             patch.object(settings, "YOUTUBE_CLIENT_SECRET", ""), \
             patch.object(settings, "YOUTUBE_REFRESH_TOKEN", ""), \
             patch.object(settings, "SOCIAL_WEBHOOK_URL", ""):
            report = publish_video("/tmp/does-not-need-to-exist.mp4", "A quiet heart.", "caption", 30)
        self.assertEqual(report["youtube"]["status"], "skipped")
        self.assertEqual(report["webhook"]["status"], "skipped")


class TestReel(unittest.TestCase):
    def test_generate_writes_an_exact_vertical_short(self):
        thought = "Sincerity is the hidden root of every act of remembrance."
        with patch.object(settings, "FILM_MODE", "simulation"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", False), \
             patch.object(settings, "YOUTUBE_CLIENT_ID", ""), \
             patch.object(settings, "YOUTUBE_CLIENT_SECRET", ""), \
             patch.object(settings, "YOUTUBE_REFRESH_TOKEN", ""), \
             patch.object(settings, "SOCIAL_WEBHOOK_URL", ""):
            result = generate(thought, publish=True)
        self.assertEqual(result.duration_seconds, 30)
        self.assertEqual(result.slots, [5, 5, 5, 5, 5, 5])
        self.assertEqual(sum(result.slots), result.duration_seconds)
        self.assertEqual(result.plan_source, "offline_draft")
        self.assertEqual(result.ledger["veo"], 6)
        self.assertIn("Ya Rabb", result.script_text)
        self.assertTrue(result.gcs_video_uri.startswith("gs://"))
        self.assertEqual(result.ledger["tts"], 1)
        self.assertEqual(result.publish["youtube"]["status"], "skipped")
        info = assert_reel(result.video_path, 30)
        self.assertEqual(info["width"], 1080)
        self.assertEqual(info["height"], 1920)
        self.assertAlmostEqual(info["duration"], 30, delta=0.75)

    def test_tiny_file_fails_integrity(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mp4"
            path.write_bytes(b"not a video")
            with self.assertRaises(Exception):
                assert_reel(str(path), 30)

    def test_plate_is_vertical(self):
        import tempfile
        from pathlib import Path
        from vidgen.sufi.render import probe
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plate.mp4"
            write_plate(str(path), 1)
            info = probe(str(path))
        self.assertEqual((info["width"], info["height"]), (1080, 1920))


class TestApi(unittest.TestCase):
    def test_generate_route_returns_the_short(self):
        from fastapi.testclient import TestClient
        import main

        payload = {
            "job_id": "job",
            "thought": "A quiet heart.",
            "duration_seconds": 30,
            "script_text": "A quiet heart returns.",
            "veo_prompts": ["a", "b"],
            "caption_and_hashtags": "#Sufism #SpiritualReminders #Tasawwuf #SchoolOfSufi",
            "slots": [15, 15],
            "video_path": "/tmp/short.mp4",
            "plan_source": "llm",
            "voice_source": "tone_fallback",
            "shots": [],
            "ledger": {"veo": 2, "tts": 1, "gemini": 1, "events": []},
            "publish": {},
            "probe": {},
        }

        class _Result:
            def model_dump(self):
                return payload

        with patch("main.generate", return_value=_Result()):
            client = TestClient(main.app)
            response = client.post("/generate", json={"thought": "A quiet heart."})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["duration_seconds"], 30)
        self.assertEqual(sum(body["slots"]), 30)

    def test_empty_thought_is_rejected(self):
        from fastapi.testclient import TestClient
        import main
        client = TestClient(main.app)
        response = client.post("/generate", json={"thought": "no"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
