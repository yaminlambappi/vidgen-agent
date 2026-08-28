"""
Regression tests for the three production blockers fixed:
  1. Veo referenceType must be ASSET or STYLE — never SUBJECT
  2. Reference assets are passed correctly and not silently dropped
  3. Duration planning respects p.duration_seconds
"""
from __future__ import annotations
import unittest
from unittest.mock import MagicMock, patch
from typing import List

from vidgen.models import (
    FilmProject, Shot, Scene, Character, Location,
    AssetReference, AssetType, CharacterBible, WorldBible,
    CinematicBible, StorySpec, ContentIntent, ProductionMode,
)
from vidgen.agents import build_veo_generation_package
from vidgen.providers.video import VeoVideoGenerator


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_char(char_id: str, uri: str) -> Character:
    return Character(
        character_id=char_id, name=f"Char-{char_id}",
        canonical_visual_assets=[AssetReference(
            asset_type=AssetType.IMAGE,
            uri=uri,
            metadata={"role": "character_identity", "mime_type": "image/png"})],
    )


def _make_loc(loc_id: str, uri: str) -> Location:
    return Location(
        location_id=loc_id, name=f"Loc-{loc_id}",
        canonical_visual_assets=[AssetReference(
            asset_type=AssetType.IMAGE,
            uri=uri,
            metadata={"role": "location_identity", "mime_type": "image/png"})],
    )


def _make_project(chars=None, locs=None, mode=ProductionMode.SHORT_FILM,
                  primary_subject_type="character",
                  duration_seconds=60) -> FilmProject:
    intent = ContentIntent(
        primary_subject="Test subject",
        primary_subject_type=primary_subject_type,
        narrative_purpose="test", emotional_objective="test",
        visual_objective="test", genre="drama", tone="serious",
        target_audience="adults", realism_requirement="photorealistic",
        prohibited_outcomes=["subject missing"],
    )
    return FilmProject(
        topic="test",
        production_mode=mode,
        duration_seconds=duration_seconds,
        content_intent=intent,
        character_bible=CharacterBible(characters=chars or []),
        world_bible=WorldBible(locations=locs or []),
        cinematic_bible=CinematicBible(
            color_palette="muted", lighting="natural",
            camera_language="handheld", texture="grain",
            editing_rhythm="slow"),
        story=StorySpec(title="T", logline="L", theme="TH",
                        genre="G", three_act_structure="123"),
    )


# ── 1. Veo referenceType validation ──────────────────────────────────────────

class TestVeoReferenceType(unittest.TestCase):

    def _build_config_with_refs(self, reference_assets: List[dict]) -> object:
        """Run _build_config and return the config, capturing reference_images."""
        gen = VeoVideoGenerator.__new__(VeoVideoGenerator)
        gen.model = "veo-3.1-generate-001"
        config = gen._build_config(8, "gs://bucket/out/", reference_assets)
        return config

    def test_no_subject_reference_type_ever(self):
        """SUBJECT must never appear as a referenceType in any Veo config."""
        from google.genai import types as gtypes

        ref_assets = [
            {"uri": "gs://b/char1.png",
             "metadata": {"role": "character_identity", "mime_type": "image/png"}},
            {"uri": "gs://b/loc1.png",
             "metadata": {"role": "location_identity", "mime_type": "image/png"}},
            {"uri": "gs://b/frame.png",
             "metadata": {"role": "previous_shot_continuity_frame", "mime_type": "image/png"}},
        ]
        config = self._build_config_with_refs(ref_assets)
        refs = getattr(config, "reference_images", None) or []
        for ref in refs:
            rt = getattr(ref, "reference_type", None)
            if rt is not None:
                rt_val = rt.value if hasattr(rt, "value") else str(rt)
                self.assertNotEqual(rt_val.upper(), "SUBJECT",
                                    f"SUBJECT is not a valid VideoGenerationReferenceType; got {rt_val!r}")

    def test_character_identity_uses_asset(self):
        """Character identity references must use ASSET, not STYLE or SUBJECT."""
        ref_assets = [
            {"uri": "gs://b/char.png",
             "metadata": {"role": "character_identity", "mime_type": "image/png"}},
        ]
        config = self._build_config_with_refs(ref_assets)
        refs = getattr(config, "reference_images", None) or []
        self.assertEqual(len(refs), 1)
        rt = getattr(refs[0], "reference_type", None)
        if rt is not None:
            rt_val = rt.value if hasattr(rt, "value") else str(rt)
            self.assertEqual(rt_val.upper(), "ASSET",
                             f"Character identity must use ASSET; got {rt_val!r}")

    def test_location_identity_uses_asset(self):
        """Location identity references must use ASSET."""
        ref_assets = [
            {"uri": "gs://b/loc.png",
             "metadata": {"role": "location_identity", "mime_type": "image/png"}},
        ]
        config = self._build_config_with_refs(ref_assets)
        refs = getattr(config, "reference_images", None) or []
        rt = getattr(refs[0], "reference_type", None)
        if rt is not None:
            rt_val = rt.value if hasattr(rt, "value") else str(rt)
            self.assertEqual(rt_val.upper(), "ASSET",
                             f"Location identity must use ASSET; got {rt_val!r}")

    def test_cinematic_style_uses_style(self):
        """Cinematic style references must use STYLE."""
        ref_assets = [
            {"uri": "gs://b/style.png",
             "metadata": {"role": "cinematic_style", "mime_type": "image/png"}},
        ]
        config = self._build_config_with_refs(ref_assets)
        refs = getattr(config, "reference_images", None) or []
        rt = getattr(refs[0], "reference_type", None)
        if rt is not None:
            rt_val = rt.value if hasattr(rt, "value") else str(rt)
            self.assertEqual(rt_val.upper(), "STYLE",
                             f"Cinematic style must use STYLE; got {rt_val!r}")

    def test_continuity_frame_uses_asset(self):
        """Previous-shot continuity frames must use ASSET."""
        ref_assets = [
            {"uri": "gs://b/frame.png",
             "metadata": {"role": "previous_shot_continuity_frame", "mime_type": "image/png"}},
        ]
        config = self._build_config_with_refs(ref_assets)
        refs = getattr(config, "reference_images", None) or []
        rt = getattr(refs[0], "reference_type", None)
        if rt is not None:
            rt_val = rt.value if hasattr(rt, "value") else str(rt)
            self.assertEqual(rt_val.upper(), "ASSET",
                             f"Continuity frame must use ASSET; got {rt_val!r}")

    def test_non_gcs_uri_raises_before_api_call(self):
        """Non-GCS URIs must raise ValueError before any Veo API call."""
        gen = VeoVideoGenerator.__new__(VeoVideoGenerator)
        gen.model = "veo-3.1-generate-001"
        with self.assertRaises(ValueError):
            gen._build_config(8, "gs://bucket/out/", [
                {"uri": "http://example.com/image.png",
                 "metadata": {"role": "character_identity"}}
            ])

    def test_only_valid_types_in_source(self):
        """Source code must not contain 'SUBJECT' as a reference type string."""
        import inspect
        import vidgen.providers.video as vmod
        src = inspect.getsource(vmod)
        # Find all reference_type assignments
        import re
        # Look for reference_type=... patterns — must not contain SUBJECT
        matches = re.findall(r'ref_type\s*=\s*["\'](\w+)["\']', src)
        for m in matches:
            self.assertNotEqual(m.upper(), "SUBJECT",
                                f"'SUBJECT' found as ref_type in video.py — not a valid SDK value")

    def test_multiple_references_all_valid_types(self):
        """When multiple reference assets are passed, all must have valid types."""
        ref_assets = [
            {"uri": "gs://b/char1.png",
             "metadata": {"role": "character_identity", "mime_type": "image/png"}},
            {"uri": "gs://b/char2.png",
             "metadata": {"role": "character_identity", "mime_type": "image/png"}},
            {"uri": "gs://b/loc1.png",
             "metadata": {"role": "location_identity", "mime_type": "image/png"}},
        ]
        config = self._build_config_with_refs(ref_assets)
        refs = getattr(config, "reference_images", None) or []
        self.assertEqual(len(refs), 3)
        for ref in refs:
            rt = getattr(ref, "reference_type", None)
            if rt is not None:
                rt_val = (rt.value if hasattr(rt, "value") else str(rt)).upper()
                self.assertIn(rt_val, ("ASSET", "STYLE"),
                              f"Invalid reference type {rt_val!r}")


# ── 2. Reference assets not silently dropped ──────────────────────────────────

class TestReferenceAssetPreservation(unittest.TestCase):

    def _pkg(self, shot, project):
        return build_veo_generation_package(shot, project)

    def test_character_reference_included_in_package(self):
        """Character canonical reference must appear in reference_assets."""
        char = _make_char("C1", "gs://b/c1.png")
        p = _make_project(chars=[char])
        shot = Shot(shot_id="SH1", scene_id="S1", index=1,
                    subject="C1 subject", action="walks",
                    character_ids=["C1"])
        pkg = self._pkg(shot, p)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://b/c1.png", uris)

    def test_product_reference_works_without_hardcoded_type(self):
        """Product references must work just like character references — no hard-coding."""
        char = _make_char("PROD1", "gs://b/product.png")
        p = _make_project(chars=[char], primary_subject_type="product")
        shot = Shot(shot_id="SH1", scene_id="S1", index=1,
                    subject="Product", action="reveals",
                    character_ids=["PROD1"])
        pkg = self._pkg(shot, p)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://b/product.png", uris)

    def test_vehicle_reference_works_without_hardcoded_type(self):
        """Vehicle references use the same ASSET mechanism as any other subject."""
        char = _make_char("VEH1", "gs://b/vehicle.png")
        p = _make_project(chars=[char], primary_subject_type="vehicle",
                          mode=ProductionMode.PREMIUM_AUTOMOTIVE_AD)
        shot = Shot(shot_id="SH1", scene_id="S1", index=1,
                    subject="Vehicle", action="drives",
                    character_ids=["VEH1"])
        pkg = self._pkg(shot, p)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://b/vehicle.png", uris)

    def test_location_reference_included(self):
        """Location reference must appear in reference_assets."""
        loc = _make_loc("L1", "gs://b/loc.png")
        p = _make_project(locs=[loc])
        shot = Shot(shot_id="SH1", scene_id="S1", index=1,
                    subject="S", action="A", location_id="L1")
        pkg = self._pkg(shot, p)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://b/loc.png", uris)

    def test_multiple_character_references_all_included(self):
        """All visible characters must have their references included."""
        char1 = _make_char("C1", "gs://b/c1.png")
        char2 = _make_char("C2", "gs://b/c2.png")
        p = _make_project(chars=[char1, char2])
        shot = Shot(shot_id="SH1", scene_id="S1", index=1,
                    subject="both", action="talk",
                    character_ids=["C1", "C2"])
        pkg = self._pkg(shot, p)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://b/c1.png", uris)
        self.assertIn("gs://b/c2.png", uris)

    def test_all_reference_uris_are_gcs(self):
        """Every reference asset URI in the package must be a GCS URI."""
        char = _make_char("C1", "gs://b/c1.png")
        loc = _make_loc("L1", "gs://b/loc.png")
        p = _make_project(chars=[char], locs=[loc])
        shot = Shot(shot_id="SH1", scene_id="S1", index=1,
                    subject="S", action="A",
                    location_id="L1", character_ids=["C1"])
        pkg = self._pkg(shot, p)
        for asset in pkg["reference_assets"]:
            self.assertTrue(asset["uri"].startswith("gs://"),
                            f"Non-GCS URI: {asset['uri']!r}")

    def test_non_gcs_character_uri_not_included(self):
        """Characters with non-GCS reference_image_uri must not pollute reference_assets."""
        char = Character(character_id="C_BAD", name="Bad",
                         reference_image_uri="http://example.com/bad.png")
        p = _make_project(chars=[char])
        shot = Shot(shot_id="SH1", scene_id="S1", index=1,
                    subject="S", action="A", character_ids=["C_BAD"])
        pkg = self._pkg(shot, p)
        for asset in pkg["reference_assets"]:
            self.assertFalse(asset["uri"].startswith("http"),
                             f"HTTP URI leaked into reference_assets: {asset['uri']!r}")


# ── 3. Duration planning ──────────────────────────────────────────────────────

class TestDurationPlanning(unittest.TestCase):

    def _budget(self, duration_seconds: int) -> tuple[int, int]:
        """Call _plan_shot_budget on a project with given duration_seconds."""
        from vidgen.orchestrator import Orchestrator
        orc = Orchestrator.__new__(Orchestrator)
        p = FilmProject(topic="t", duration_seconds=duration_seconds)
        return orc._plan_shot_budget(p)

    def test_30_second_plan(self):
        """30s requested → planned total within tolerance of 30s."""
        from vidgen.config import settings
        shots_per_scene, shot_dur = self._budget(30)
        total = 3 * shots_per_scene * shot_dur
        diff = abs(total - 30)
        self.assertLessEqual(diff, settings.DURATION_TOLERANCE_SECONDS,
                             f"30s plan produced {total}s (diff={diff}s)")

    def test_60_second_plan(self):
        """60s requested → planned total within tolerance."""
        from vidgen.config import settings
        shots_per_scene, shot_dur = self._budget(60)
        total = 3 * shots_per_scene * shot_dur
        diff = abs(total - 60)
        self.assertLessEqual(diff, settings.DURATION_TOLERANCE_SECONDS,
                             f"60s plan produced {total}s (diff={diff}s)")

    def test_10_second_plan(self):
        """10s requested → planned total within tolerance."""
        from vidgen.config import settings
        shots_per_scene, shot_dur = self._budget(10)
        total = 3 * shots_per_scene * shot_dur
        diff = abs(total - 10)
        self.assertLessEqual(diff, settings.DURATION_TOLERANCE_SECONDS,
                             f"10s plan produced {total}s (diff={diff}s)")

    def test_120_second_plan(self):
        """120s (2 min) requested → planned total within tolerance."""
        from vidgen.config import settings
        shots_per_scene, shot_dur = self._budget(120)
        total = 3 * shots_per_scene * shot_dur
        diff = abs(total - 120)
        self.assertLessEqual(diff, settings.DURATION_TOLERANCE_SECONDS,
                             f"120s plan produced {total}s (diff={diff}s)")

    def test_shot_duration_is_valid_veo_value(self):
        """shot_duration returned must be a valid Veo duration."""
        from vidgen.config import settings
        for target in (10, 20, 30, 45, 60, 90, 120):
            _, shot_dur = self._budget(target)
            self.assertIn(shot_dur, settings.VEO_VALID_DURATIONS,
                          f"shot_duration {shot_dur} not in VEO_VALID_DURATIONS for target={target}")

    def test_shots_per_scene_at_least_one(self):
        """shots_per_scene must always be >= 1."""
        for target in (5, 10, 15, 20, 30, 60):
            shots_per_scene, _ = self._budget(target)
            self.assertGreaterEqual(shots_per_scene, 1,
                                    f"shots_per_scene=0 for target={target}s")

    def test_plan_not_wildly_different_from_30s(self):
        """A 30s request must NOT produce 48s. That was the original bug."""
        shots_per_scene, shot_dur = self._budget(30)
        total = 3 * shots_per_scene * shot_dur
        self.assertNotEqual(total, 48,
                            "30s request produced 48s — duration planning bug not fixed")

    def test_zero_duration_uses_defaults(self):
        """When duration_seconds is 0, fall back to config defaults."""
        from vidgen.config import settings
        shots_per_scene, shot_dur = self._budget(0)
        self.assertEqual(shots_per_scene, settings.SHOTS_PER_SCENE)
        self.assertEqual(shot_dur, settings.DEFAULT_SHOT_DURATION)

    def test_design_shots_enforces_duration(self):
        """StoryboardAgent.design_shots must clamp all shot durations to shot_duration arg."""
        from vidgen.agents import StoryboardAgent
        from vidgen.models import Scene, Shot

        agent = StoryboardAgent.__new__(StoryboardAgent)
        agent.model = "gemini-test"

        # LLM returns shots with wrong durations
        class FakeOut:
            shots = [
                Shot(shot_id="SH1", scene_id="S1", index=1, duration=12,  # too long
                     subject="S", action="A"),
                Shot(shot_id="SH2", scene_id="S1", index=2, duration=4,   # already correct
                     subject="S", action="B"),
            ]

        agent.llm = MagicMock(return_value=FakeOut())

        scene = Scene(scene_id="S1", index=1, title="T", description="D", location_id="L")
        p = _make_project()

        result = agent.design_shots(scene, p, shots_per_scene=2, shot_duration=5)
        for shot in result:
            self.assertEqual(shot.duration, 5,
                             f"Shot {shot.shot_id} duration not clamped to 5 (got {shot.duration})")

    def test_storyboard_produces_correct_count(self):
        """design_shots must produce exactly shots_per_scene shots."""
        from vidgen.agents import StoryboardAgent
        from vidgen.models import Scene, Shot

        agent = StoryboardAgent.__new__(StoryboardAgent)
        agent.model = "gemini-test"

        class FakeOut:
            shots = [
                Shot(shot_id=f"SH{i}", scene_id="S1", index=i,
                     subject="S", action="A") for i in range(1, 4)
            ]  # LLM returns 3 shots

        agent.llm = MagicMock(return_value=FakeOut())

        scene = Scene(scene_id="S1", index=1, title="T", description="D", location_id="L")
        p = _make_project()

        result = agent.design_shots(scene, p, shots_per_scene=2, shot_duration=8)
        # Should be capped at 2 (shots_per_scene)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
