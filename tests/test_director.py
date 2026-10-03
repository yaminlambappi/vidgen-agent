"""Pro Director: prompt length is a contract, not an 8s Veo clip."""
from __future__ import annotations

import unittest

from vidgen.reels.director import plan_director, resolve_duration
from vidgen.reels.duration import (
    bind_duration_cap,
    fill_legal_timeline,
    parse_prompt_duration,
    reset_duration_cap,
)
from vidgen.reels.schemas import ReelRequest


class TestPromptDuration(unittest.TestCase):
    def test_seconds_phrase(self):
        self.assertEqual(parse_prompt_duration("Make a 45 second cinematic tea stall"), 45.0)

    def test_minutes_phrase(self):
        self.assertEqual(parse_prompt_duration("2 minute founder film about VidGen"), 120.0)

    def test_resolve_flag_wins(self):
        self.assertEqual(resolve_duration("Make a 45 second video", 20), 20.0)

    def test_resolve_from_idea(self):
        self.assertEqual(resolve_duration("Make a 90 second video about rain", None), 90.0)


class TestLongFormPlan(unittest.TestCase):
    def test_ninety_seconds_is_not_thirty(self):
        token = bind_duration_cap(180)
        try:
            shots = fill_legal_timeline(90, 180)
        finally:
            reset_duration_cap(token)
        self.assertGreaterEqual(sum(shots), 88)
        self.assertLessEqual(sum(shots), 90)
        self.assertGreater(len(shots), 4)
        self.assertTrue(all(d in {4, 6, 8} for d in shots))

    def test_director_dry_run_honors_prompt_length(self):
        job = plan_director("Make a 45 second UGC founder reel for VidGen", language="english", product="VidGen")
        self.assertTrue(job.request.long_form)
        self.assertEqual(job.request.duration_seconds, 45.0)
        self.assertGreaterEqual(job.storyboard.total_duration, 40.0)
        self.assertLessEqual(job.storyboard.total_duration, 45.0)
        self.assertTrue(job.dry_run_manifest.offer.get("duration_honored"))
        self.assertGreaterEqual(job.dry_run_manifest.offer.get("veo_shots"), 5)

    def test_monsoon_plan_is_not_an_apartment_slogan(self):
        job = plan_director("Make a 90 second cinematic film about monsoon in Dhaka", language="english")
        text = job.script.full_text.lower()
        self.assertNotIn("watch this for one second", text)
        self.assertNotIn("that's the whole thing", text)
        self.assertTrue("rain" in text or "dhaka" in text)
        places = " ".join(s.location for s in job.storyboard.shots).lower()
        self.assertNotIn("lived-in apartment", places)
        self.assertIn("rain", places + job.storyboard.shots[0].action.lower())
        self.assertGreaterEqual(len({s.location for s in job.storyboard.shots}), 4)
        self.assertGreaterEqual(job.storyboard.total_duration, 80.0)
        hooks = [c.line for c in job.hook.concepts]
        self.assertEqual(len(hooks), len(set(hooks)))

    def test_reel_request_still_rejects_31_without_long_form(self):
        with self.assertRaises(Exception):
            ReelRequest(idea="a reel about tea stall light", duration_seconds=31)


if __name__ == "__main__":
    unittest.main()
