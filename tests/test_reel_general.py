"""General-purpose short-form factory: intent, language, assets, QC, cost."""
from __future__ import annotations

import unittest

from vidgen.reels.constants import BLOCKED_JOB_IDS, MAX_DURATION_SECONDS
from vidgen.reels.creative import plan_production
from vidgen.reels.factory import ReelFactory
from vidgen.reels.intent import classify_intent
from vidgen.reels.language import infer_language, is_garbled_caption
from vidgen.reels.qc import creative_qc, run_qc
from vidgen.reels.schemas import ReelJob, ReelRequest, ReelStatus
from vidgen.reels.watchability import score_watchability


def _req(idea: str, **kwargs) -> ReelRequest:
    data = dict(idea=idea, language="", duration_seconds=15.0, dry_run=True)
    data.update(kwargs)
    return ReelRequest(**data)


class TestIntent(unittest.TestCase):
    def test_comedy_interview(self):
        req = _req("একটা funny Bengali reel বানাও যেখানে একজন ছেলে interview দিতে গিয়ে সব প্রশ্নের উত্তর উল্টাপাল্টা দেয়")
        self.assertEqual(classify_intent(req), "COMEDY")

    def test_educational(self):
        self.assertEqual(classify_intent(_req("Explain black holes in a fascinating 25 second Bengali Reel")), "EDUCATIONAL")

    def test_story(self):
        self.assertEqual(classify_intent(_req("A mini story about a student before an exam")), "STORY")

    def test_meme(self):
        self.assertEqual(classify_intent(_req("Make a meme reel about office life")), "MEME")

    def test_lifestyle_not_ad(self):
        self.assertEqual(classify_intent(_req("Make a funny 20 second Reel about Dhaka traffic")), "COMEDY")

    def test_cinematic(self):
        self.assertEqual(classify_intent(_req("A cinematic short scene of rain on a Dhaka rooftop")), "CINEMATIC")

    def test_product_demo(self):
        self.assertEqual(classify_intent(_req("How to use this serum, a product demonstration")), "PRODUCT_DEMO")

    def test_ad(self):
        self.assertIn(classify_intent(_req("Create a 15 second Bengali advertisement for a perfume called X", product_name="X")), {"ADVERTISEMENT", "UGC"})


class TestLanguage(unittest.TestCase):
    def test_bengali_script(self):
        self.assertEqual(infer_language("", "একটা funny reel বানাও"), "bengali")

    def test_english(self):
        self.assertEqual(infer_language("", "Make a funny reel about office life"), "english")

    def test_banglish_word(self):
        self.assertEqual(infer_language("", "Make a Banglish reel about office adda"), "bengali")

    def test_garbled(self):
        self.assertTrue(is_garbled_caption("Youroscent. Neverer louded", "english"))


class TestAssetlessPlans(unittest.TestCase):
    def test_comedy_has_two_characters_no_product_cta(self):
        job = ReelJob(request=_req(
            "একটা funny Bengali Reel বানাও যেখানে একজন ছেলে interview দিতে গিয়ে confident ভাবে সব প্রশ্নের ভুল উত্তর দেয়",
            duration_seconds=15,
        ))
        plan_production(job)
        self.assertEqual(job.brief.creative_type, "COMEDY")
        self.assertEqual(job.brief.language, "bengali")
        self.assertFalse(job.brief.needs_cta)
        self.assertFalse(job.product_bible.required)
        self.assertGreaterEqual(len(job.character_bible), 2)
        self.assertTrue(any("\u0980" <= ch <= "\u09FF" for ch in job.script.full_text))
        self.assertNotIn("product truth", job.brief.narrative_structure)
        self.assertIn("punchline", " ".join(job.brief.strategy_beats))
        first = job.storyboard.shots[0]
        self.assertIn("face", first.action.lower())
        ids = {c.character_id for c in job.character_bible}
        for shot in job.storyboard.shots:
            self.assertTrue(set(shot.characters) <= ids)

    def test_educational_compose_and_no_cta(self):
        job = ReelJob(request=_req("Explain black holes in a fascinating 15 second Bengali Reel", duration_seconds=15))
        plan_production(job)
        self.assertEqual(job.brief.creative_type, "EDUCATIONAL")
        self.assertFalse(job.brief.needs_cta)
        self.assertTrue(any(s.generation_strategy == "compose" for s in job.storyboard.shots))

    def test_fictional_perfume_identity_locked(self):
        job = ReelJob(request=_req("Create a 15 second Bengali Reel about a perfume", duration_seconds=15))
        plan_production(job)
        self.assertTrue(job.product_bible.required)
        self.assertTrue(job.product_bible.fictional)
        self.assertTrue(job.product_bible.name)
        self.assertIn("atomizer", job.product_bible.shape.lower())
        name = job.product_bible.name
        for shot in job.storyboard.shots:
            if job.brief.needs_product and name.lower() in (shot.action or "").lower():
                break
        else:
            self.assertTrue(any("product" in s.purpose for s in job.storyboard.shots) or name)

    def test_named_product_not_replaced(self):
        job = ReelJob(request=_req("Make a Reel about Nike shoes", product_name="Nike"))
        plan_production(job)
        self.assertIn("nike", job.product_bible.name.lower())
        self.assertFalse(job.product_bible.fictional)


class TestDryRunAndCost(unittest.TestCase):
    def test_comedy_dry_run_zero_expensive(self):
        factory = ReelFactory()
        job = factory.create(_req(
            "একটা funny Bengali reel বানাও যেখানে একজন ছেলে interview দিতে গিয়ে সব প্রশ্নের উত্তর উল্টাপাল্টা দেয়",
            duration_seconds=20,
        ))
        job = factory.run(job, dry_run=True)
        self.assertEqual(job.status, ReelStatus.COMPLETE)
        self.assertEqual(job.ledger.total_calls, 0)
        self.assertEqual(job.dry_run_manifest.expensive_calls_made, 0)
        self.assertEqual(job.dry_run_manifest.creative_type, "COMEDY")
        self.assertEqual(job.dry_run_manifest.language, "bengali")
        self.assertLessEqual(job.dry_run_manifest.estimated_veo_calls, 3)
        self.assertEqual(job.dry_run_manifest.estimated_image_calls, 0)
        self.assertTrue(job.dry_run_manifest.script)
        self.assertTrue(job.production_manifest)

    def test_blocked_historical_job(self):
        self.assertIn("9237d967-2507-4a55-b98d-d8609db37e0d", BLOCKED_JOB_IDS)
        factory = ReelFactory()
        job = factory.create(_req("funny office reel"))
        job.job_id = "9237d967-2507-4a55-b98d-d8609db37e0d"
        from vidgen.reels.safety import PermanentGenerationError
        with self.assertRaises(PermanentGenerationError):
            factory.run(job, dry_run=True)


class TestWatchabilityAndQC(unittest.TestCase):
    def test_comedy_plan_scores(self):
        job = ReelJob(request=_req("একটা funny Bengali reel interview ভুল উত্তর", duration_seconds=15))
        plan_production(job)
        score = score_watchability(job)
        self.assertTrue(score.passed, score.notes)
        self.assertGreaterEqual(score.hook_strength, 0.5)
        qc = creative_qc(job)
        self.assertTrue(qc["passed"], qc["issues"])

    def test_bad_language_fails(self):
        self.assertTrue(is_garbled_caption("Alwaysatheree louded", "bengali"))

    def test_duration_cap(self):
        self.assertEqual(MAX_DURATION_SECONDS, 30.0)


if __name__ == "__main__":
    unittest.main()
