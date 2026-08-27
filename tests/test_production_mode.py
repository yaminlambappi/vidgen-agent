"""
Tests for ProductionMode, VoiceBible, VoiceDesignAgent, and mode-aware agents.

Correctness properties verified:
  1. ProductionMode round-trip JSON serialisation
  2. FilmProject preserves production_mode and voice_bible fields
  3. VoiceDesignAgent returns distinct voice_names (no duplicates)
  4. VoiceDesignAgent clamps speaking_rate to [0.75, 1.05]
  5. VoiceDesignAgent clamps pitch to [-4.0, 2.0]
  6. StoryArchitectAgent includes mode-context in SHORT_FILM prompt
  7. StoryArchitectAgent includes mode-context in PREMIUM_AUTOMOTIVE_AD prompt
  8. build_veo_generation_package includes AUTOMOTIVE MANDATE for automotive mode
  9. build_veo_generation_package does NOT include AUTOMOTIVE MANDATE for short-film mode
 10. VoiceAgent.synthesize_dialogue uses VoiceBible voice when present
 11. VoiceAgent.synthesize_dialogue falls back to pool when VoiceBible absent
 12. VoiceAgent.synthesize_dialogue timeline start values are monotonically non-decreasing
 13. Orchestrator instantiates VoiceDesignAgent as self.voice_design
"""
from __future__ import annotations
import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

from vidgen.models import (
    FilmProject, FilmStatus, ProductionMode,
    VoiceAssignment, VoiceBible, CharacterBible, Character,
    StorySpec, CinematicBible, WorldBible, Scene, Shot, DialogueLine,
)
from vidgen.agents import (
    VoiceDesignAgent, StoryArchitectAgent, build_veo_generation_package,
    VoiceAgent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(mode: ProductionMode = ProductionMode.SHORT_FILM,
                  num_chars: int = 3) -> FilmProject:
    chars = [
        Character(
            character_id=f"CHAR_{i}",
            name=f"Character {i}",
            personality="complex, driven, fearful",
        )
        for i in range(num_chars)
    ]
    return FilmProject(
        topic="A test film",
        production_mode=mode,
        character_bible=CharacterBible(characters=chars),
        story=StorySpec(
            title="Test Film", logline="A test.", theme="Testing",
            genre="drama", three_act_structure="act1 act2 act3",
        ),
        cinematic_bible=CinematicBible(
            color_palette="muted greys",
            lighting="natural motivated light",
            camera_language="handheld, eye-level",
            texture="16mm grain",
            editing_rhythm="contemplative long takes",
        ),
        world_bible=WorldBible(locations=[]),
    )


# ---------------------------------------------------------------------------
# 1–2. ProductionMode / FilmProject serialisation
# ---------------------------------------------------------------------------

class TestProductionModeModel(unittest.TestCase):
    def test_roundtrip_short_film(self):
        p = FilmProject(production_mode=ProductionMode.SHORT_FILM)
        restored = FilmProject.model_validate_json(p.model_dump_json())
        self.assertEqual(restored.production_mode, ProductionMode.SHORT_FILM)

    def test_roundtrip_automotive(self):
        p = FilmProject(production_mode=ProductionMode.PREMIUM_AUTOMOTIVE_AD)
        restored = FilmProject.model_validate_json(p.model_dump_json())
        self.assertEqual(restored.production_mode, ProductionMode.PREMIUM_AUTOMOTIVE_AD)

    def test_voice_bible_preserved_in_roundtrip(self):
        vb = VoiceBible(
            assignments={"C1": VoiceAssignment(character_id="C1", voice_name="en-US-Neural2-A")},
            narrator_voice="en-US-Neural2-J",
        )
        p = FilmProject(voice_bible=vb)
        restored = FilmProject.model_validate_json(p.model_dump_json())
        self.assertIsNotNone(restored.voice_bible)
        self.assertEqual(restored.voice_bible.assignments["C1"].voice_name, "en-US-Neural2-A")

    def test_default_production_mode_is_short_film(self):
        p = FilmProject()
        self.assertEqual(p.production_mode, ProductionMode.SHORT_FILM)

    def test_default_voice_bible_is_none(self):
        p = FilmProject()
        self.assertIsNone(p.voice_bible)


# ---------------------------------------------------------------------------
# 3–5. VoiceDesignAgent
# ---------------------------------------------------------------------------

class TestVoiceDesignAgent(unittest.TestCase):
    def _make_agent_with_mock_llm(self, specs):
        """Return a VoiceDesignAgent whose LLM always returns `specs`."""
        agent = VoiceDesignAgent.__new__(VoiceDesignAgent)

        class _Out:
            pass

        class _Spec:
            def __init__(self, character_id, style, rate, pitch):
                self.character_id = character_id
                self.performance_style = style
                self.speaking_rate = rate
                self.pitch = pitch

        mock_out = _Out()
        mock_out.specs = [_Spec(*s) for s in specs]
        agent.llm = MagicMock(return_value=mock_out)
        return agent

    def test_returns_voice_bible(self):
        p = _make_project(num_chars=3)
        specs = [
            ("CHAR_0", "gravitas", 0.90, -2.0),
            ("CHAR_1", "terse",    0.95, -1.0),
            ("CHAR_2", "urgent",   1.00,  0.0),
        ]
        agent = self._make_agent_with_mock_llm(specs)
        vb = agent.design_voices(p)
        self.assertIsInstance(vb, VoiceBible)
        self.assertEqual(len(vb.assignments), 3)

    def test_voice_names_are_distinct(self):
        """Correctness property 3: no two characters share the same TTS voice."""
        p = _make_project(num_chars=3)
        specs = [
            ("CHAR_0", "gravitas", 0.90, -2.0),
            ("CHAR_1", "terse",    0.95, -1.0),
            ("CHAR_2", "urgent",   1.00,  0.0),
        ]
        agent = self._make_agent_with_mock_llm(specs)
        vb = agent.design_voices(p)
        names = [a.voice_name for a in vb.assignments.values()]
        self.assertEqual(len(names), len(set(names)), "Duplicate voice names detected")

    def test_speaking_rate_clamped(self):
        """Correctness property 4: speaking_rate is always within [0.75, 1.05]."""
        p = _make_project(num_chars=2)
        specs = [
            ("CHAR_0", "style", 0.10, 0.0),   # too slow → clamped to 0.75
            ("CHAR_1", "style", 9.99, 0.0),   # too fast → clamped to 1.05
        ]
        agent = self._make_agent_with_mock_llm(specs)
        vb = agent.design_voices(p)
        for a in vb.assignments.values():
            self.assertGreaterEqual(a.speaking_rate, 0.75)
            self.assertLessEqual(a.speaking_rate, 1.05)

    def test_pitch_clamped(self):
        """Correctness property 5: pitch is always within [-4.0, 2.0]."""
        p = _make_project(num_chars=2)
        specs = [
            ("CHAR_0", "style", 0.85, -99.0),  # too low → clamped to -4.0
            ("CHAR_1", "style", 0.85,  99.0),  # too high → clamped to 2.0
        ]
        agent = self._make_agent_with_mock_llm(specs)
        vb = agent.design_voices(p)
        for a in vb.assignments.values():
            self.assertGreaterEqual(a.pitch, -4.0)
            self.assertLessEqual(a.pitch, 2.0)


# ---------------------------------------------------------------------------
# 6–7. StoryArchitectAgent mode context
# ---------------------------------------------------------------------------

class TestStoryArchitectAgentMode(unittest.TestCase):
    def _captured_prompt(self, mode: ProductionMode) -> str:
        """Run design_story with a mocked LLM and return the prompt it received."""
        agent = StoryArchitectAgent.__new__(StoryArchitectAgent)
        captured = {}

        def fake_llm(prompt, system="", schema=None):
            captured["prompt"] = prompt
            return StorySpec(title="T", logline="L", theme="TH", genre="G",
                             three_act_structure="123")

        agent.llm = fake_llm
        agent.design_story("test topic", "test research", production_mode=mode)
        return captured.get("prompt", "")

    def test_short_film_mode_context_in_prompt(self):
        """Correctness property 6: SHORT_FILM prompt includes festival-film context."""
        prompt = self._captured_prompt(ProductionMode.SHORT_FILM)
        self.assertIn("SHORT FILM", prompt)

    def test_automotive_mode_context_in_prompt(self):
        """Correctness property 7: AUTOMOTIVE prompt includes brand/vehicle context."""
        prompt = self._captured_prompt(ProductionMode.PREMIUM_AUTOMOTIVE_AD)
        self.assertIn("AUTOMOTIVE", prompt)

    def test_modes_produce_different_prompts(self):
        sf = self._captured_prompt(ProductionMode.SHORT_FILM)
        aa = self._captured_prompt(ProductionMode.PREMIUM_AUTOMOTIVE_AD)
        self.assertNotEqual(sf, aa)


# ---------------------------------------------------------------------------
# 8–9. build_veo_generation_package — automotive mandate
# ---------------------------------------------------------------------------

class TestBuildVeoGenerationPackage(unittest.TestCase):
    def _make_shot(self) -> Shot:
        return Shot(
            shot_id="SH01", scene_id="S1", index=1,
            subject="A figure", action="walks forward",
            shot_type="medium", duration=8,
        )

    def test_automotive_mandate_present_for_automotive_mode(self):
        """Correctness property 8: AUTOMOTIVE MANDATE block in every automotive prompt."""
        p = _make_project(mode=ProductionMode.PREMIUM_AUTOMOTIVE_AD)
        shot = self._make_shot()
        pkg = build_veo_generation_package(shot, p)
        self.assertIn("AUTOMOTIVE MANDATE", pkg["prompt"])

    def test_automotive_mandate_absent_for_short_film_mode(self):
        """Correctness property 9: no AUTOMOTIVE MANDATE block in short-film prompts."""
        p = _make_project(mode=ProductionMode.SHORT_FILM)
        shot = self._make_shot()
        pkg = build_veo_generation_package(shot, p)
        self.assertNotIn("AUTOMOTIVE MANDATE", pkg["prompt"])

    def test_no_mutation_of_inputs(self):
        """build_veo_generation_package must not mutate shot or project."""
        p = _make_project()
        shot = self._make_shot()
        original_action = shot.action
        original_topic = p.topic
        build_veo_generation_package(shot, p)
        self.assertEqual(shot.action, original_action)
        self.assertEqual(p.topic, original_topic)

    def test_feedback_included_in_prompt(self):
        p = _make_project()
        shot = self._make_shot()
        pkg = build_veo_generation_package(shot, p, feedback="Fix the hands.")
        self.assertIn("Fix the hands.", pkg["prompt"])

    def test_prompt_is_non_empty(self):
        p = _make_project()
        shot = self._make_shot()
        pkg = build_veo_generation_package(shot, p)
        self.assertTrue(len(pkg["prompt"]) > 20)


# ---------------------------------------------------------------------------
# 10–12. VoiceAgent.synthesize_dialogue
# ---------------------------------------------------------------------------

class TestVoiceAgentDialogue(unittest.TestCase):
    def _make_project_with_dialogue(self, voice_bible=None) -> FilmProject:
        chars = [
            Character(character_id="C1", name="Alice"),
            Character(character_id="C2", name="Bob"),
        ]
        scene = Scene(
            scene_id="S1", index=1, title="Scene 1",
            location_id="L1",
            dialogue=[
                DialogueLine(character_id="C1", line="I see the light."),
                DialogueLine(character_id="C2", line="What light?"),
            ],
            shots=[Shot(shot_id="SH1", scene_id="S1", index=1, duration=8)],
        )
        p = FilmProject(
            topic="test",
            character_bible=CharacterBible(characters=chars),
            scenes=[scene],
            voice_bible=voice_bible,
        )
        return p

    def _make_mock_tts(self):
        """Returns a mock TTS client that writes minimal MP3 bytes."""
        mock_resp = MagicMock()
        mock_resp.audio_content = b"\xff\xfb" + b"\x00" * 200  # minimal fake MP3
        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = mock_resp
        return mock_client

    def test_uses_voice_bible_when_present(self):
        """Correctness property 10: VoiceBible assignment is used when present."""
        vb = VoiceBible(assignments={
            "C1": VoiceAssignment(character_id="C1", voice_name="en-US-Neural2-F",
                                  speaking_rate=0.80, pitch=-1.0),
            "C2": VoiceAssignment(character_id="C2", voice_name="en-US-Neural2-I",
                                  speaking_rate=0.95, pitch=0.5),
        })
        p = self._make_project_with_dialogue(voice_bible=vb)

        agent = VoiceAgent()
        agent.client = self._make_mock_tts()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = agent.synthesize_dialogue(p, root)

        self.assertEqual(len(result), 2)
        # Check that synthesize_speech was called with the VoiceBible voice names
        calls = agent.client.synthesize_speech.call_args_list
        used_voices = {c.kwargs["voice"].name for c in calls}
        self.assertIn("en-US-Neural2-F", used_voices)
        self.assertIn("en-US-Neural2-I", used_voices)

    def test_fallback_when_no_voice_bible(self):
        """Correctness property 11: Falls back to pool when VoiceBible absent."""
        p = self._make_project_with_dialogue(voice_bible=None)
        agent = VoiceAgent()
        agent.client = self._make_mock_tts()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = agent.synthesize_dialogue(p, Path(tmpdir))

        self.assertEqual(len(result), 2)
        agent.client.synthesize_speech.assert_called()

    def test_timeline_starts_are_monotonically_non_decreasing(self):
        """Correctness property 12: start times are non-negative and non-decreasing."""
        p = self._make_project_with_dialogue()
        agent = VoiceAgent()
        agent.client = self._make_mock_tts()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = agent.synthesize_dialogue(p, Path(tmpdir))

        starts = [item[1]["start"] for item in result]
        self.assertTrue(all(s >= 0 for s in starts), "Negative start time detected")
        for a, b in zip(starts, starts[1:]):
            self.assertLessEqual(a, b, "Start times not monotonically non-decreasing")


# ---------------------------------------------------------------------------
# 13. Orchestrator has voice_design attribute
# ---------------------------------------------------------------------------

class TestOrchestratorVoiceDesignIntegration(unittest.TestCase):
    def test_orchestrator_has_voice_design_agent(self):
        """Correctness property 13: Orchestrator instantiates VoiceDesignAgent."""
        from vidgen.orchestrator import Orchestrator
        orc = Orchestrator()
        self.assertIsInstance(orc.voice_design, VoiceDesignAgent)

    def test_orchestrator_calls_design_voices_during_planning(self):
        """Orchestrator calls voice_design.design_voices when voice_bible is absent."""
        from vidgen.orchestrator import Orchestrator
        from vidgen.models import (
            CharacterBible, WorldBible, CinematicBible, MusicPlan,
            GenerationJob,
        )

        orc = Orchestrator()

        project = FilmProject(topic="Voice integration test")
        project.voice_bible = None  # explicitly absent

        mock_vb = VoiceBible(
            assignments={"C1": VoiceAssignment(character_id="C1",
                                               voice_name="en-US-Neural2-A")})

        with patch.object(orc.story_arch, "design_story",
                          return_value=StorySpec(title="T", logline="L",
                                                 theme="TH", genre="G",
                                                 three_act_structure="123")), \
             patch.object(orc.world_design, "design_world",
                          return_value=WorldBible(locations=[])), \
             patch.object(orc.cinematog, "design_cinematics",
                          return_value=CinematicBible(
                              color_palette="p", lighting="l",
                              camera_language="c", texture="t",
                              editing_rhythm="e")), \
             patch.object(orc.char_design, "design_characters",
                          return_value=CharacterBible(characters=[])), \
             patch.object(orc.voice_design, "design_voices",
                          return_value=mock_vb) as mock_dv, \
             patch.object(orc.researcher, "ground", return_value="research"), \
             patch.object(orc.screenwriter, "write_scenes",
                          return_value=[Scene(index=1, title="S1",
                                              description="D", location_id="L1")]), \
             patch.object(orc.storyboarder, "design_shots",
                          return_value=[Shot(scene_id="S1", index=1,
                                             subject="S", action="A",
                                             location_id="L1")]), \
             patch.object(orc.music, "compose_plan",
                          return_value=MusicPlan()), \
             patch.object(orc.editor, "compile",
                          return_value=__import__(
                              "vidgen.models", fromlist=["EditPlan"]).EditPlan(
                              sequence=[])), \
             patch.object(orc.subtitles, "generate", return_value=""), \
             patch.object(orc.voice, "synthesize", return_value=None), \
             patch.object(orc.voice, "synthesize_dialogue", return_value=[]), \
             patch.object(orc.storage, "upload", return_value="gs://mock/file"), \
             patch.object(orc.storage, "download", return_value=None), \
             patch("vidgen.orchestrator.concatenate_shots"), \
             patch("vidgen.orchestrator.final_mix"), \
             patch("vidgen.orchestrator.validate_video",
                   return_value={"valid": True, "duration": 8.0,
                                 "width": 1920, "height": 1080,
                                 "codec": "h264", "has_audio": True}), \
             patch("vidgen.orchestrator.create_score"), \
             patch("vidgen.utils.ffmpeg.extract_frames"):

            orc.run(project)

        mock_dv.assert_called_once_with(project)
        self.assertIsNotNone(project.voice_bible)
        self.assertEqual(project.voice_bible.assignments["C1"].voice_name,
                         "en-US-Neural2-A")

    def test_orchestrator_skips_design_voices_when_voice_bible_already_set(self):
        """Idempotency: design_voices not called when voice_bible already present."""
        from vidgen.orchestrator import Orchestrator
        from vidgen.models import (
            CharacterBible, WorldBible, CinematicBible, MusicPlan,
        )

        orc = Orchestrator()
        project = FilmProject(topic="Idempotency test")
        # Pre-set voice bible
        project.voice_bible = VoiceBible(
            assignments={"C1": VoiceAssignment(character_id="C1",
                                               voice_name="en-US-Neural2-A")})

        with patch.object(orc.story_arch, "design_story",
                          return_value=StorySpec(title="T", logline="L",
                                                 theme="TH", genre="G",
                                                 three_act_structure="123")), \
             patch.object(orc.world_design, "design_world",
                          return_value=WorldBible(locations=[])), \
             patch.object(orc.cinematog, "design_cinematics",
                          return_value=CinematicBible(
                              color_palette="p", lighting="l",
                              camera_language="c", texture="t",
                              editing_rhythm="e")), \
             patch.object(orc.char_design, "design_characters",
                          return_value=CharacterBible(characters=[])), \
             patch.object(orc.voice_design, "design_voices",
                          return_value=VoiceBible()) as mock_dv, \
             patch.object(orc.researcher, "ground", return_value="research"), \
             patch.object(orc.screenwriter, "write_scenes",
                          return_value=[Scene(index=1, title="S1",
                                              description="D", location_id="L1")]), \
             patch.object(orc.storyboarder, "design_shots",
                          return_value=[Shot(scene_id="S1", index=1,
                                             subject="S", action="A",
                                             location_id="L1")]), \
             patch.object(orc.music, "compose_plan",
                          return_value=MusicPlan()), \
             patch.object(orc.editor, "compile",
                          return_value=__import__(
                              "vidgen.models", fromlist=["EditPlan"]).EditPlan(
                              sequence=[])), \
             patch.object(orc.subtitles, "generate", return_value=""), \
             patch.object(orc.voice, "synthesize", return_value=None), \
             patch.object(orc.voice, "synthesize_dialogue", return_value=[]), \
             patch.object(orc.storage, "upload", return_value="gs://mock/file"), \
             patch.object(orc.storage, "download", return_value=None), \
             patch("vidgen.orchestrator.concatenate_shots"), \
             patch("vidgen.orchestrator.final_mix"), \
             patch("vidgen.orchestrator.validate_video",
                   return_value={"valid": True, "duration": 8.0,
                                 "width": 1920, "height": 1080,
                                 "codec": "h264", "has_audio": True}), \
             patch("vidgen.orchestrator.create_score"), \
             patch("vidgen.utils.ffmpeg.extract_frames"):

            orc.run(project)

        mock_dv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
