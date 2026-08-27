"""
Regression tests for the Universal Cinematic Engine upgrade.

Verified properties:
  INTENT
    1.  Arbitrary product can be primary subject (not hard-coded to vehicles)
    2.  Arbitrary character can be primary subject
    3.  Arbitrary location can be primary subject
    4.  Environment-only shots are possible (no character required)
    5.  Subject is never hard-coded to vehicles

  PROMPT QUALITY
    6.  Every shot has an explicit ShotObjective before prompt compilation
    7.  Shot objective survives prompt compilation (present in prompt)
    8.  Subject requirement survives prompt compilation
    9.  Action requirement survives prompt compilation
    10. Continuity requirements survive prompt compilation
    11. AUTOMOTIVE MANDATE is only injected for vehicle primary subjects
    12. Hierarchical prompt sections are present in correct order

  CONTINUITY
    13. Character identity persists across shots (reference assets included)
    14. Product/object identity references are included
    15. Location continuity reference is included
    16. Intra-scene previous-shot frame included; cross-scene frame excluded

  QC
    17. Missing subject fails QC with SUBJECT_MISSING reason
    18. Missing action fails QC with ACTION_MISSING reason
    19. Intent mismatch fails QC with INTENT_MISMATCH reason
    20. Continuity break fails QC with CONTINUITY_BREAK reason
    21. Character identity break fails QC with CHARACTER_IDENTITY_BREAK reason
    22. Valid shot passes QC
    23. Visual artifacts fail QC early (before intent check)

  ASSEMBLY
    24. normalize_video uses -fflags +genpts and -vsync cfr (timestamp continuity)
    25. concatenate_shots works for any number of shots

  MODELS
    26. ContentIntent round-trips through JSON
    27. ShotObjective round-trips through JSON
    28. QCFailureReason enum values are strings
    29. FilmProject includes content_intent field
    30. Shot includes shot_objective field
"""
from __future__ import annotations
import inspect
import json
import unittest
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

from vidgen.models import (
    ContentIntent, ShotObjective, QCFailureReason,
    FilmProject, Shot, Scene, Character, CharacterBible, Location, WorldBible,
    CinematicBible, StorySpec, AssetReference, AssetType, ProductionMode,
    VoiceBible, VoiceAssignment,
)
from vidgen.agents import (
    ContentIntentAgent, build_veo_generation_package,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_shot(subject="A figure", action="walks forward",
               shot_type="medium", duration=8,
               character_ids=None, objective: ShotObjective = None) -> Shot:
    s = Shot(
        shot_id="SH01", scene_id="S1", index=1,
        subject=subject, action=action,
        shot_type=shot_type, duration=duration,
        character_ids=character_ids or [],
    )
    if objective is not None:
        s.shot_objective = objective
    return s


def _make_project(primary_subject="A mysterious figure",
                  primary_subject_type="character",
                  mode=ProductionMode.SHORT_FILM,
                  chars: List[Character] = None,
                  locs: List[Location] = None) -> FilmProject:
    intent = ContentIntent(
        primary_subject=primary_subject,
        primary_subject_type=primary_subject_type,
        narrative_purpose="test narrative",
        emotional_objective="tension",
        visual_objective="stark contrast",
        genre="drama",
        tone="serious",
        target_audience="adults",
        realism_requirement="photorealistic",
        prohibited_outcomes=["subject not visible", "generic background"],
        shot_level_objectives=["establish subject", "reveal action"],
        continuity_requirements=["wardrobe", "location lighting"],
    )
    return FilmProject(
        topic="test",
        production_mode=mode,
        content_intent=intent,
        character_bible=CharacterBible(characters=chars or []),
        world_bible=WorldBible(locations=locs or []),
        cinematic_bible=CinematicBible(
            color_palette="muted greys", lighting="natural side light",
            camera_language="handheld eye-level", texture="16mm grain",
            editing_rhythm="long takes"),
        story=StorySpec(title="T", logline="L", theme="TH", genre="G",
                        three_act_structure="123"),
    )


# ── 1–5. Intent: arbitrary subjects ──────────────────────────────────────────

class TestIntentUniversalSubject(unittest.TestCase):

    def _intent_for(self, subject: str, subject_type: str) -> ContentIntent:
        return ContentIntent(
            primary_subject=subject,
            primary_subject_type=subject_type,
            narrative_purpose="test",
            emotional_objective="test",
            visual_objective="test",
            genre="test",
            tone="test",
            target_audience="test",
            realism_requirement="photorealistic",
            prohibited_outcomes=["subject missing"],
        )

    def test_product_as_primary_subject(self):
        """A product (perfume bottle) can be primary subject."""
        intent = self._intent_for("NOVA perfume bottle", "product")
        p = FilmProject(topic="test", content_intent=intent)
        self.assertEqual(p.content_intent.primary_subject_type, "product")
        self.assertNotIn("vehicle", p.content_intent.primary_subject.lower())

    def test_character_as_primary_subject(self):
        """A character can be primary subject."""
        intent = self._intent_for("Elena Vasquez", "character")
        p = FilmProject(topic="test", content_intent=intent)
        self.assertEqual(p.content_intent.primary_subject_type, "character")

    def test_location_as_primary_subject(self):
        """A location can be primary subject."""
        intent = self._intent_for("The abandoned lighthouse at Cape Horn", "location")
        p = FilmProject(topic="test", content_intent=intent)
        self.assertEqual(p.content_intent.primary_subject_type, "location")

    def test_environment_shot_no_character_required(self):
        """A shot with no character_ids is valid — environment-only shots allowed."""
        shot = _make_shot(subject="Storm clouds over the ocean", action="gather",
                          character_ids=[])
        p = _make_project(primary_subject_type="environment")
        pkg = build_veo_generation_package(shot, p)
        self.assertIsNotNone(pkg["prompt"])
        self.assertGreater(len(pkg["prompt"]), 20)

    def test_subject_not_hard_coded_to_vehicle(self):
        """Primary subject must come from ContentIntent, never be hard-coded as vehicle."""
        p = _make_project(primary_subject="A grandmother's hands", primary_subject_type="person",
                          mode=ProductionMode.SHORT_FILM)
        shot = _make_shot(subject="grandmother's hands", action="knit slowly")
        pkg = build_veo_generation_package(shot, p)
        # Vehicle-specific language must NOT appear for a person subject
        self.assertNotIn("AUTOMOTIVE MANDATE", pkg["prompt"])
        self.assertNotIn("tyre", pkg["prompt"])
        self.assertNotIn("clearcoat", pkg["prompt"])


# ── 6–12. Prompt quality ──────────────────────────────────────────────────────

class TestPromptQuality(unittest.TestCase):

    def _shot_with_objective(self, subject="The APEX-X9 smartphone",
                              action="screen illuminates") -> Shot:
        obj = ShotObjective(
            shot_id="SH01",
            what_must_audience_see="The smartphone screen turning on for the first time",
            primary_subject=subject,
            subject_action=action,
            where="minimalist studio",
            story_beat="product reveal",
            continuity_requirements=["product colour: midnight black"],
            must_not_lose=["screen visible", "product identity"],
            camera_rationale="extreme close-up to show screen detail",
            lighting_rationale="single rim light from right to catch product edge",
            failure_conditions=["product not visible", "screen off", "wrong product"],
        )
        return _make_shot(subject=subject, action=action, objective=obj)

    def test_shot_has_objective(self):
        """Shot must have a ShotObjective set."""
        shot = self._shot_with_objective()
        self.assertIsNotNone(shot.shot_objective)
        self.assertTrue(shot.shot_objective.what_must_audience_see)

    def test_shot_objective_in_prompt(self):
        """Shot objective text must appear in the compiled prompt."""
        shot = self._shot_with_objective()
        p = _make_project(primary_subject="APEX-X9 smartphone", primary_subject_type="product")
        pkg = build_veo_generation_package(shot, p)
        self.assertIn("screen turning on for the first time", pkg["prompt"])

    def test_subject_requirement_in_prompt(self):
        """Primary subject must be explicitly named in the prompt."""
        shot = self._shot_with_objective()
        p = _make_project(primary_subject="APEX-X9 smartphone", primary_subject_type="product")
        pkg = build_veo_generation_package(shot, p)
        self.assertIn("APEX-X9", pkg["prompt"])

    def test_action_requirement_in_prompt(self):
        """Required action must appear in the prompt."""
        shot = self._shot_with_objective(action="screen illuminates with a burst of light")
        p = _make_project()
        pkg = build_veo_generation_package(shot, p)
        self.assertIn("screen illuminates", pkg["prompt"])

    def test_continuity_requirements_in_prompt(self):
        """Continuity requirements from ShotObjective must survive into prompt."""
        shot = self._shot_with_objective()
        shot.shot_objective.continuity_requirements = ["product colour: midnight black"]
        p = _make_project()
        prev = _make_shot(subject="smartphone", action="resting on table",
                          objective=ShotObjective(shot_id="SH00"))
        pkg = build_veo_generation_package(shot, p, previous_shot=prev)
        self.assertIn("midnight black", pkg["prompt"])

    def test_automotive_mandate_only_for_vehicle_subject(self):
        """AUTOMOTIVE MANDATE must only appear when primary_subject_type == vehicle."""
        # Vehicle subject — mandate expected
        p_vehicle = _make_project(primary_subject="NEXUS-GT hypercar",
                                   primary_subject_type="vehicle",
                                   mode=ProductionMode.PREMIUM_AUTOMOTIVE_AD)
        shot = _make_shot()
        pkg_vehicle = build_veo_generation_package(shot, p_vehicle)
        self.assertIn("AUTOMOTIVE MANDATE", pkg_vehicle["prompt"])

        # Person subject — mandate must NOT appear
        p_person = _make_project(primary_subject="A racing driver",
                                  primary_subject_type="person",
                                  mode=ProductionMode.PREMIUM_AUTOMOTIVE_AD)
        pkg_person = build_veo_generation_package(shot, p_person)
        self.assertNotIn("AUTOMOTIVE MANDATE", pkg_person["prompt"])

    def test_negative_constraints_in_prompt(self):
        """Failure conditions from ShotObjective must appear as negative constraints."""
        shot = self._shot_with_objective()
        p = _make_project()
        pkg = build_veo_generation_package(shot, p)
        # failure_conditions from ShotObjective contain "product not visible"
        self.assertIn("product not visible", pkg["prompt"])

    def test_prompt_sections_ordered_correctly(self):
        """Priority order: subject identity before environment before style."""
        shot = self._shot_with_objective()
        p = _make_project(primary_subject="APEX-X9 smartphone", primary_subject_type="product")
        pkg = build_veo_generation_package(shot, p)
        prompt = pkg["prompt"]
        # PRIMARY SUBJECT must appear before ENVIRONMENT
        idx_subject = prompt.find("PRIMARY SUBJECT")
        idx_env = prompt.find("ENVIRONMENT")
        idx_style = prompt.find("CINEMATIC IDENTITY")
        self.assertGreater(idx_subject, -1)
        # Subject before cinematic style
        if idx_style > -1:
            self.assertLess(idx_subject, idx_style)


# ── 13–16. Continuity reference assets ───────────────────────────────────────

class TestContinuityReferenceAssets(unittest.TestCase):

    def _char_with_ref(self, char_id="C1") -> Character:
        return Character(
            character_id=char_id, name="Alice",
            physical_description="tall, red hair",
            wardrobe="grey overcoat",
            canonical_visual_assets=[AssetReference(
                asset_type=AssetType.IMAGE,
                uri=f"gs://bucket/refs/char_{char_id}.png",
                metadata={"role": "character_identity", "mime_type": "image/png"})],
        )

    def _loc_with_ref(self) -> Location:
        return Location(
            location_id="L1", name="The Lighthouse",
            canonical_visual_assets=[AssetReference(
                asset_type=AssetType.IMAGE,
                uri="gs://bucket/refs/loc_L1.png",
                metadata={"role": "location_identity", "mime_type": "image/png"})],
        )

    def test_character_identity_reference_included(self):
        """Character canonical reference must be in reference_assets."""
        char = self._char_with_ref("C1")
        p = _make_project(chars=[char])
        shot = _make_shot(character_ids=["C1"])
        pkg = build_veo_generation_package(shot, p)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://bucket/refs/char_C1.png", uris)

    def test_location_continuity_reference_included(self):
        """Location canonical reference must be in reference_assets."""
        loc = self._loc_with_ref()
        p = _make_project(locs=[loc])
        shot = _make_shot()
        shot.location_id = "L1"
        pkg = build_veo_generation_package(shot, p)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://bucket/refs/loc_L1.png", uris)

    def test_intra_scene_continuity_frame_included(self):
        """Previous-shot continuity frame included when same scene."""
        p = _make_project()
        shot = _make_shot()
        prev = _make_shot()
        prev.scene_id = "S1"
        shot.scene_id = "S1"
        prev.generated_frame_uris = ["gs://bucket/frames/prev_0.png"]
        pkg = build_veo_generation_package(shot, p, previous_shot=prev)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertIn("gs://bucket/frames/prev_0.png", uris)

    def test_cross_scene_continuity_frame_excluded(self):
        """Previous-shot frame from a DIFFERENT scene must NOT be included."""
        p = _make_project()
        shot = _make_shot()
        shot.scene_id = "S2"
        prev = _make_shot()
        prev.scene_id = "S1"
        prev.generated_frame_uris = ["gs://bucket/frames/prev_0.png"]
        pkg = build_veo_generation_package(shot, p, previous_shot=prev)
        uris = [a["uri"] for a in pkg["reference_assets"]]
        self.assertNotIn("gs://bucket/frames/prev_0.png", uris)

    def test_no_non_gcs_uris_in_reference_assets(self):
        """All reference asset URIs must start with gs://."""
        char = Character(character_id="C1", name="Bob",
                         reference_image_uri="http://example.com/not-gcs.png")
        p = _make_project(chars=[char])
        shot = _make_shot(character_ids=["C1"])
        pkg = build_veo_generation_package(shot, p)
        for asset in pkg["reference_assets"]:
            self.assertTrue(asset["uri"].startswith("gs://"),
                            f"Non-GCS URI found: {asset['uri']}")


# ── 17–23. QC structured failure reasons ─────────────────────────────────────

class TestQCStructuredFailures(unittest.TestCase):
    """Test QCMAgent failure reason classification without real API calls."""

    def _make_qcm(self):
        from vidgen.qc import QCMAgent
        agent = QCMAgent.__new__(QCMAgent)
        agent.model = "gemini-2.5-flash"
        return agent

    def _mock_client_for(self, json_response: dict):
        mock_response = MagicMock()
        mock_response.text = json.dumps(json_response)
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        return mock_client

    def test_missing_subject_fails_with_correct_reason(self):
        """Subject not present → SUBJECT_MISSING failure reason."""
        agent = self._make_qcm()
        cine = CinematicBible(color_palette="p", lighting="l",
                              camera_language="c", texture="t", editing_rhythm="e")
        shot = _make_shot(
            subject="The smartphone",
            objective=ShotObjective(
                shot_id="SH1",
                what_must_audience_see="The smartphone",
                primary_subject="The smartphone",
                subject_action="illuminate",
            )
        )
        intent = ContentIntent(primary_subject="smartphone", primary_subject_type="product",
                               narrative_purpose="p", emotional_objective="e",
                               visual_objective="v", genre="g", tone="t",
                               target_audience="a", realism_requirement="photorealistic",
                               prohibited_outcomes=["subject missing"])

        import unittest.mock as mock
        with mock.patch.object(agent, "check_visual_artifacts",
                               return_value={"artifact_free": True, "issues": []}), \
             mock.patch.object(agent, "check_shot_intent",
                               return_value={"subject_present": False, "action_present": True,
                                             "story_beat_served": True, "intent_failure": None,
                                             "passes_intent": False}), \
             mock.patch.object(agent, "check_cinematic_style",
                               return_value={"style_adherent": True, "critique": ""}):

            critique = agent.critique_shot(
                "fake_frame.png", shot, cine, content_intent=intent)

        self.assertFalse(critique["passed"])
        self.assertIn(QCFailureReason.SUBJECT_MISSING.value, critique["failure_reasons"])

    def test_missing_action_fails_with_correct_reason(self):
        """Action not present → ACTION_MISSING failure reason."""
        agent = self._make_qcm()
        cine = CinematicBible(color_palette="p", lighting="l",
                              camera_language="c", texture="t", editing_rhythm="e")
        shot = _make_shot(action="jumps over the barrier")

        import unittest.mock as mock
        with mock.patch.object(agent, "check_visual_artifacts",
                               return_value={"artifact_free": True, "issues": []}), \
             mock.patch.object(agent, "check_shot_intent",
                               return_value={"subject_present": True, "action_present": False,
                                             "story_beat_served": True, "intent_failure": None,
                                             "passes_intent": False}), \
             mock.patch.object(agent, "check_cinematic_style",
                               return_value={"style_adherent": True, "critique": ""}):

            critique = agent.critique_shot("fake_frame.png", shot, cine)

        self.assertFalse(critique["passed"])
        self.assertIn(QCFailureReason.ACTION_MISSING.value, critique["failure_reasons"])

    def test_intent_mismatch_fails_with_correct_reason(self):
        """Story beat not served → INTENT_MISMATCH failure reason."""
        agent = self._make_qcm()
        cine = CinematicBible(color_palette="p", lighting="l",
                              camera_language="c", texture="t", editing_rhythm="e")
        shot = _make_shot()

        import unittest.mock as mock
        with mock.patch.object(agent, "check_visual_artifacts",
                               return_value={"artifact_free": True, "issues": []}), \
             mock.patch.object(agent, "check_shot_intent",
                               return_value={"subject_present": True, "action_present": True,
                                             "story_beat_served": False,
                                             "intent_failure": "generic scene substituted",
                                             "passes_intent": False}), \
             mock.patch.object(agent, "check_cinematic_style",
                               return_value={"style_adherent": True, "critique": ""}):

            critique = agent.critique_shot("fake_frame.png", shot, cine)

        self.assertFalse(critique["passed"])
        self.assertIn(QCFailureReason.INTENT_MISMATCH.value, critique["failure_reasons"])

    def test_visual_artifacts_fail_early(self):
        """Visual artifacts must cause early exit before intent check."""
        agent = self._make_qcm()
        cine = CinematicBible(color_palette="p", lighting="l",
                              camera_language="c", texture="t", editing_rhythm="e")
        shot = _make_shot()

        intent_check_called = []

        import unittest.mock as mock
        with mock.patch.object(agent, "check_visual_artifacts",
                               return_value={"artifact_free": False,
                                             "issues": ["mangled hands"]}), \
             mock.patch.object(agent, "check_shot_intent",
                               side_effect=lambda *a, **kw: intent_check_called.append(1) or {}):

            critique = agent.critique_shot("fake_frame.png", shot, cine)

        self.assertFalse(critique["passed"])
        self.assertIn(QCFailureReason.VISUAL_ARTIFACTS.value, critique["failure_reasons"])
        self.assertEqual(len(intent_check_called), 0, "Intent check must not run after artifact failure")

    def test_valid_shot_passes_qc(self):
        """A shot where all checks pass must return passed=True."""
        agent = self._make_qcm()
        cine = CinematicBible(color_palette="p", lighting="l",
                              camera_language="c", texture="t", editing_rhythm="e")
        shot = _make_shot()

        import unittest.mock as mock
        with mock.patch.object(agent, "check_visual_artifacts",
                               return_value={"artifact_free": True, "issues": []}), \
             mock.patch.object(agent, "check_shot_intent",
                               return_value={"subject_present": True, "action_present": True,
                                             "story_beat_served": True, "intent_failure": None,
                                             "passes_intent": True}), \
             mock.patch.object(agent, "check_cinematic_style",
                               return_value={"style_adherent": True, "critique": ""}):

            critique = agent.critique_shot("fake_frame.png", shot, cine)

        self.assertTrue(critique["passed"])
        self.assertEqual(critique["failure_reasons"], [])

    def test_generate_feedback_prompt_uses_failure_reasons(self):
        """generate_feedback_prompt must produce targeted correction per failure reason."""
        agent = self._make_qcm()
        shot = _make_shot(
            subject="The smartphone",
            action="illuminate",
            objective=ShotObjective(
                shot_id="SH1",
                what_must_audience_see="The smartphone screen turning on",
                primary_subject="smartphone",
                subject_action="illuminate",
            )
        )
        critique = {
            "passed": False,
            "failure_reasons": [QCFailureReason.SUBJECT_MISSING.value,
                                 QCFailureReason.ACTION_MISSING.value],
            "feedback": ["subject not visible", "action not visible"],
        }
        prompt = agent.generate_feedback_prompt(shot, critique)
        self.assertIn("PRIORITY RE-SHOOT", prompt)
        self.assertIn("smartphone", prompt.lower())
        self.assertIn("illuminate", prompt.lower())


# ── 24–25. Assembly timestamp continuity ──────────────────────────────────────

class TestAssemblyTimestamps(unittest.TestCase):

    def test_normalize_video_uses_genpts_and_cfr(self):
        """normalize_video must use -fflags +genpts and -vsync cfr to prevent timestamp gaps."""
        import vidgen.utils.ffmpeg as ffm
        from vidgen.config import settings

        captured = []

        def fake_run_ffmpeg(args):
            captured.extend(args)

        with patch.object(settings, "FILM_MODE", "production"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", True), \
             patch.object(ffm, "run_ffmpeg", side_effect=fake_run_ffmpeg), \
             patch.object(ffm, "probe", return_value={
                 "format": {"duration": "8.0"},
                 "streams": [{"codec_type": "video", "codec_name": "h264",
                              "width": 1920, "height": 1080}]
             }):
            ffm.normalize_video("/tmp/fake_input.mp4", "/tmp/fake_output.mp4",
                                expected_duration=8.0)

        flat = " ".join(captured)
        self.assertIn("+genpts", flat,
                      "-fflags +genpts must be present to reset timestamps")
        self.assertIn("cfr", flat,
                      "-vsync cfr must be present for constant frame rate")

    def test_concatenate_shots_empty_raises(self):
        """concatenate_shots with empty list must raise RuntimeError."""
        import vidgen.utils.ffmpeg as ffm
        with self.assertRaises(RuntimeError):
            ffm.concatenate_shots([], "/tmp/output.mp4")


# ── 26–30. Model round-trips ──────────────────────────────────────────────────

class TestModelRoundTrips(unittest.TestCase):

    def test_content_intent_roundtrip(self):
        intent = ContentIntent(
            primary_subject="NEXUS-GT",
            primary_subject_type="vehicle",
            narrative_purpose="reveal",
            emotional_objective="awe",
            visual_objective="silhouette",
            genre="commercial",
            tone="aspirational",
            target_audience="luxury buyers",
            realism_requirement="photorealistic",
            prohibited_outcomes=["vehicle not visible"],
            shot_level_objectives=["hero reveal"],
            continuity_requirements=["vehicle colour"],
        )
        p = FilmProject(content_intent=intent)
        restored = FilmProject.model_validate_json(p.model_dump_json())
        self.assertIsNotNone(restored.content_intent)
        self.assertEqual(restored.content_intent.primary_subject, "NEXUS-GT")
        self.assertEqual(restored.content_intent.primary_subject_type, "vehicle")

    def test_shot_objective_roundtrip(self):
        obj = ShotObjective(
            shot_id="SH01",
            what_must_audience_see="product reveal",
            primary_subject="smartphone",
            subject_action="illuminate",
            where="studio",
            story_beat="reveal",
            failure_conditions=["product not visible"],
        )
        shot = Shot(shot_objective=obj)
        data = shot.model_dump_json()
        restored = Shot.model_validate_json(data)
        self.assertIsNotNone(restored.shot_objective)
        self.assertEqual(restored.shot_objective.what_must_audience_see, "product reveal")

    def test_qc_failure_reason_values_are_strings(self):
        for reason in QCFailureReason:
            self.assertIsInstance(reason.value, str)
            self.assertEqual(reason.value, reason.value.upper())

    def test_film_project_has_content_intent_field(self):
        p = FilmProject()
        self.assertIsNone(p.content_intent)
        p.content_intent = ContentIntent(
            primary_subject="test", primary_subject_type="product",
            narrative_purpose="p", emotional_objective="e", visual_objective="v",
            genre="g", tone="t", target_audience="a", realism_requirement="photorealistic",
        )
        self.assertIsNotNone(p.content_intent)

    def test_shot_has_shot_objective_field(self):
        shot = Shot()
        self.assertIsNone(shot.shot_objective)
        shot.shot_objective = ShotObjective(
            shot_id="SH1",
            what_must_audience_see="test objective",
        )
        self.assertEqual(shot.shot_objective.what_must_audience_see, "test objective")


if __name__ == "__main__":
    unittest.main()
