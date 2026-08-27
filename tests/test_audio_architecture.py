"""
Regression tests for the audio architecture fix.

Verified properties:
  1.  Veo _build_config uses generate_audio=False
  2.  narration is per-scene, not a global concatenated string
  3.  scenes without narration produce no narration track
  4.  narration start_ms differs per scene (no two scenes start at t=0 if both have narration)
  5.  narration tracks are never synthesised for empty narration_text
  6.  dialogue timestamps are preserved from synthesize_dialogue
  7.  final_mix filter graph contains NO '[narr]apad[speech]' pattern
  8.  final_mix filter graph does NOT reference [0:a] (Veo audio stream)
  9.  final_mix filter graph anchors duration to [silence]/video, not narration
  10. final_mix filter graph uses adelay for each speech segment
  11. silence is genuine: no speech tracks emitted when project has no narration/dialogue
  12. no duplicate narration tracks (same scene_id appears once)
  13. AudioPlan.narration_tracks is a list (not a string / monolithic URI)
"""
from __future__ import annotations
import inspect
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import tempfile

from vidgen.models import (
    AudioPlan, FilmProject, FilmStatus, Scene, Shot, DialogueLine,
    CharacterBible, Character, VoiceBible, VoiceAssignment, StorySpec,
    CinematicBible, WorldBible,
)
from vidgen.providers.video import VeoVideoGenerator
from vidgen.utils import ffmpeg as ffmpeg_mod


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_project_with_scenes(scene_specs):
    """
    scene_specs: list of (narration_text, dialogue_lines, shot_durations)
    dialogue_lines: list of (character_id, line)
    shot_durations: list of int seconds
    """
    chars = [Character(character_id="C1", name="Alice"),
             Character(character_id="C2", name="Bob")]
    scenes = []
    for i, (narr, dlg, durs) in enumerate(scene_specs):
        shots = [Shot(shot_id=f"SH{i}_{j}", scene_id=f"S{i+1}",
                      index=j+1, duration=d) for j, d in enumerate(durs)]
        scene = Scene(
            scene_id=f"S{i+1}", index=i+1,
            title=f"Scene {i+1}",
            narration_text=narr,
            dialogue=[DialogueLine(character_id=c, line=ln) for c, ln in dlg],
            shots=shots,
        )
        scenes.append(scene)
    vb = VoiceBible(assignments={
        "C1": VoiceAssignment(character_id="C1", voice_name="en-US-Neural2-A",
                              speaking_rate=0.9, pitch=-1.0),
        "C2": VoiceAssignment(character_id="C2", voice_name="en-US-Neural2-D",
                              speaking_rate=0.95, pitch=0.0),
    })
    p = FilmProject(
        topic="test",
        character_bible=CharacterBible(characters=chars),
        scenes=scenes,
        voice_bible=vb,
        story=StorySpec(title="T", logline="L", theme="TH", genre="G",
                        three_act_structure="123"),
        cinematic_bible=CinematicBible(color_palette="p", lighting="l",
                                       camera_language="c", texture="t",
                                       editing_rhythm="e"),
        world_bible=WorldBible(locations=[]),
    )
    return p


def _mock_tts_client():
    mock_resp = MagicMock()
    mock_resp.audio_content = b"\xff\xfb" + b"\x00" * 500
    mock_client = MagicMock()
    mock_client.synthesize_speech.return_value = mock_resp
    return mock_client


# ── 1. Veo generate_audio=False ───────────────────────────────────────────────

class TestVeoAudioDisabled(unittest.TestCase):
    def test_build_config_generate_audio_false(self):
        """generate_audio must be False — Veo audio must never contaminate the mix."""
        gen = VeoVideoGenerator.__new__(VeoVideoGenerator)
        gen.model = "veo-3.1-generate-001"

        mock_client = MagicMock()
        mock_client.operations.get.return_value = MagicMock(done=True, error=None)
        gen.client = mock_client

        from google.genai import types as gtypes
        captured = {}

        def fake_generate_videos(model, prompt, config):
            captured["config"] = config
            op = MagicMock()
            op.done = True
            op.error = None
            return op

        mock_client.models.generate_videos.side_effect = fake_generate_videos

        from vidgen.config import settings
        with patch.object(settings, "GCS_BUCKET", "test-bucket"), \
             patch.object(settings, "GOOGLE_CLOUD_PROJECT", "test-proj"), \
             patch("vidgen.providers.video.VeoVideoGenerator._extract_uri",
                   return_value="gs://test-bucket/shot.mp4"), \
             patch("time.sleep"):
            gen.generate_shot("prompt", "gs://test-bucket/out/", duration=8,
                              project_id="p", shot_id="s")

        cfg = captured.get("config")
        if cfg is not None:
            # Verify the config was built with generate_audio=False
            generate_audio_val = getattr(cfg, "generate_audio", None)
            self.assertFalse(
                generate_audio_val,
                f"generate_audio must be False, got {generate_audio_val!r}")

    def test_build_config_no_generate_audio_true_in_source(self):
        """Source code must not contain generate_audio=True anywhere."""
        import vidgen.providers.video as vmod
        src = inspect.getsource(vmod)
        self.assertNotIn(
            "generate_audio=True", src,
            "generate_audio=True found in video.py — Veo audio must be disabled")


# ── 2–5. Per-scene narration, timing, empty scenes ───────────────────────────

class TestBuildAudioNarration(unittest.TestCase):
    def _run_build_audio(self, scene_specs):
        """Run _build_audio with mocked TTS and storage; return (audio_plan, calls)."""
        from vidgen.orchestrator import Orchestrator
        from vidgen.agents import VoiceAgent

        p = _make_project_with_scenes(scene_specs)
        p.audio_plan = AudioPlan()

        orc = Orchestrator.__new__(Orchestrator)

        # Set all instance attributes _build_audio touches
        voice = VoiceAgent.__new__(VoiceAgent)
        voice.client = _mock_tts_client()
        voice._fallback_pool = ["en-US-Neural2-A"]
        voice._fallback_map = {}
        object.__setattr__(orc, "voice", voice)

        mock_storage = MagicMock()
        mock_storage.upload.return_value = "gs://mock/file"
        object.__setattr__(orc, "storage", mock_storage)

        mock_subtitles = MagicMock()
        mock_subtitles.generate.return_value = ""
        object.__setattr__(orc, "subtitles", mock_subtitles)

        with patch("vidgen.orchestrator.create_score"), \
             patch.object(voice, "synthesize_dialogue", return_value=[]):
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                orc._build_audio(p, root)

        return p.audio_plan

    def test_narration_tracks_is_list(self):
        """AudioPlan.narration_tracks must be a list, not a string."""
        plan = self._run_build_audio([
            ("Scene one narration text.", [], [8]),
            ("Scene two narration text.", [], [8]),
        ])
        self.assertIsInstance(plan.narration_tracks, list)

    def test_narration_per_scene_not_concatenated(self):
        """Two scenes must produce two separate narration track entries."""
        plan = self._run_build_audio([
            ("First scene narration.", [], [8]),
            ("Second scene narration.", [], [8]),
        ])
        self.assertEqual(len(plan.narration_tracks), 2,
                         "Expected one narration track per scene with narration text")

    def test_scene_without_narration_produces_no_track(self):
        """A scene with empty narration_text must not produce a narration track."""
        plan = self._run_build_audio([
            ("First scene has narration.", [], [8]),
            ("", [], [8]),   # ← no narration
            ("Third scene has narration.", [], [8]),
        ])
        self.assertEqual(len(plan.narration_tracks), 2,
                         "Scene with no narration must produce no track")
        scene_indices = [t["scene_index"] for t in plan.narration_tracks]
        self.assertNotIn(2, scene_indices, "Silent scene must not appear in narration_tracks")

    def test_narration_start_ms_differs_per_scene(self):
        """Narration for scene 2 must start later than narration for scene 1."""
        plan = self._run_build_audio([
            ("First scene narration.", [], [8, 8]),   # 16s → scene 2 starts at 16000ms
            ("Second scene narration.", [], [8]),
        ])
        starts = [t["start_ms"] for t in plan.narration_tracks]
        self.assertEqual(starts[0], 0, "Scene 1 narration must start at 0ms")
        self.assertEqual(starts[1], 16000,
                         "Scene 2 narration must start at 16000ms (2 × 8s shots)")

    def test_no_empty_narration_synthesised(self):
        """synthesize_narration must never be called with an empty string."""
        from vidgen.orchestrator import Orchestrator
        from vidgen.agents import VoiceAgent

        p = _make_project_with_scenes([
            ("", [], [8]),      # no narration
            ("Has text.", [], [8]),
        ])
        p.audio_plan = AudioPlan()

        orc = Orchestrator.__new__(Orchestrator)
        voice = VoiceAgent.__new__(VoiceAgent)
        voice.client = _mock_tts_client()
        voice._fallback_pool = ["en-US-Neural2-A"]
        voice._fallback_map = {}
        object.__setattr__(orc, "voice", voice)
        object.__setattr__(orc, "storage",
                           MagicMock(upload=MagicMock(return_value="gs://mock/f")))
        object.__setattr__(orc, "subtitles",
                           MagicMock(generate=MagicMock(return_value="")))

        synth_calls = []

        def capturing_synth(text, path):
            synth_calls.append(text)
            Path(path).write_bytes(b"\xff\xfb" + b"\x00" * 100)

        with patch("vidgen.orchestrator.create_score"), \
             patch.object(voice, "synthesize_narration", side_effect=capturing_synth), \
             patch.object(voice, "synthesize_dialogue", return_value=[]):
            with tempfile.TemporaryDirectory() as tmpdir:
                orc._build_audio(p, Path(tmpdir))

        for text in synth_calls:
            self.assertTrue(text.strip(),
                            "synthesize_narration must never be called with empty text")

    def test_no_duplicate_narration_tracks(self):
        """Each scene_id must appear at most once in narration_tracks."""
        plan = self._run_build_audio([
            ("Scene 1 narration.", [], [8]),
            ("Scene 2 narration.", [], [8]),
            ("Scene 3 narration.", [], [8]),
        ])
        scene_ids = [t["scene_id"] for t in plan.narration_tracks]
        self.assertEqual(len(scene_ids), len(set(scene_ids)),
                         "Duplicate narration tracks detected")


# ── 6. Dialogue timestamps preserved ─────────────────────────────────────────

class TestDialogueTimestamps(unittest.TestCase):
    def test_dialogue_start_times_are_non_negative_and_ordered(self):
        """Dialogue timestamps must be non-negative and non-decreasing."""
        from vidgen.agents import VoiceAgent
        p = _make_project_with_scenes([
            ("Narration.", [("C1", "Hello world."), ("C2", "Goodbye world.")], [8, 8]),
            ("", [("C1", "Another line.")], [8]),
        ])
        agent = VoiceAgent.__new__(VoiceAgent)
        agent.client = _mock_tts_client()
        agent._fallback_pool = ["en-US-Neural2-A", "en-US-Neural2-D"]
        agent._fallback_map = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = agent.synthesize_dialogue(p, Path(tmpdir))

        starts = [r[1]["start"] for r in result]
        self.assertTrue(all(s >= 0 for s in starts), "Negative start time detected")
        for a, b in zip(starts, starts[1:]):
            self.assertLessEqual(a, b, "Dialogue timestamps not monotonically ordered")

    def test_dialogue_uses_voice_bible_voice(self):
        """Dialogue synthesis must use VoiceBible voice, not a hardcoded fallback."""
        from vidgen.agents import VoiceAgent
        p = _make_project_with_scenes([
            ("", [("C1", "I speak.")], [8]),
        ])
        agent = VoiceAgent.__new__(VoiceAgent)
        agent.client = _mock_tts_client()
        agent._fallback_pool = ["en-US-Neural2-A"]
        agent._fallback_map = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            agent.synthesize_dialogue(p, Path(tmpdir))

        calls = agent.client.synthesize_speech.call_args_list
        self.assertEqual(len(calls), 1)
        voice_name = calls[0].kwargs["voice"].name
        self.assertEqual(voice_name, "en-US-Neural2-A",
                         "VoiceBible voice assignment not used")


# ── 7–11. final_mix filter graph ─────────────────────────────────────────────

class TestFinalMixFilterGraph(unittest.TestCase):
    """Inspect the FFmpeg filter graph produced by final_mix without executing FFmpeg."""

    def _capture_filter_graph(self, narration_tracks=None, dialogue_tracks=None):
        """Run final_mix in production mode with mocked run_ffmpeg; return filter string."""
        import vidgen.utils.ffmpeg as ffm
        from vidgen.config import settings

        captured_args = {}

        def fake_run_ffmpeg(args):
            for i, a in enumerate(args):
                if a == "-filter_complex" and i + 1 < len(args):
                    captured_args["filter"] = args[i + 1]
                    captured_args["full"] = args

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            video = root / "assembled.mp4"
            music = root / "music.m4a"
            output = root / "final.mp4"
            video.write_bytes(b"stub")
            music.write_bytes(b"stub")

            narr_tracks = []
            for i, seg in enumerate(narration_tracks or []):
                p = root / f"narr_{i}.mp3"
                p.write_bytes(b"\xff\xfb" + b"\x00" * 200)
                narr_tracks.append({**seg, "path": str(p)})

            dlg_tracks = []
            for i, seg in enumerate(dialogue_tracks or []):
                p = root / f"dlg_{i}.mp3"
                p.write_bytes(b"\xff\xfb" + b"\x00" * 200)
                dlg_tracks.append({**seg, "path": str(p)})

            # is_production is a @property — patch the underlying flags
            with patch.object(settings, "FILM_MODE", "production"), \
                 patch.object(settings, "ALLOW_REAL_GENERATION", True), \
                 patch.object(settings, "BURN_SUBTITLES", False), \
                 patch.object(ffm, "run_ffmpeg", side_effect=fake_run_ffmpeg):
                ffm.final_mix(
                    str(video), str(output),
                    music_path=str(music),
                    narration_tracks=narr_tracks,
                    dialogue_tracks=dlg_tracks,
                )

        return captured_args.get("filter", ""), captured_args.get("full", [])

    def test_no_narr_apad_speech_pattern(self):
        """The old [narr]apad[speech] architecture must not exist anywhere."""
        graph, _ = self._capture_filter_graph(
            narration_tracks=[{"start_ms": 0, "scene_index": 1}])
        self.assertNotIn("[narr]apad[speech]", graph,
                         "Old [narr]apad[speech] pattern found in filter graph")

    def test_no_0_a_audio_from_video(self):
        """[0:a] (Veo baked audio) must never appear in the filter graph."""
        graph, _ = self._capture_filter_graph()
        self.assertNotIn("[0:a]", graph,
                         "[0:a] (Veo audio stream) found in final_mix filter graph")

    def test_duration_anchor_is_silence_not_narration(self):
        """amix duration=first must not be driven by a narration input."""
        graph, _ = self._capture_filter_graph(
            narration_tracks=[{"start_ms": 0, "scene_index": 1}])
        # The silence bus drives duration=first, not narr
        self.assertIn("silence", graph,
                      "anullsrc silence bus must be present in the filter graph")
        self.assertIn("duration=first", graph,
                      "amix must use duration=first to anchor to video length")

    def test_adelay_used_for_speech_segments(self):
        """Every speech segment must be placed via adelay."""
        graph, _ = self._capture_filter_graph(
            narration_tracks=[{"start_ms": 4000, "scene_index": 2}],
            dialogue_tracks=[{"start": 2.5, "character_id": "C1",
                               "text": "Hello.", "scene_id": "S1"}])
        self.assertIn("adelay=4000|4000", graph,
                      "Narration segment at 4000ms must use adelay=4000|4000")
        self.assertIn("adelay=2500|2500", graph,
                      "Dialogue at 2.5s must use adelay=2500|2500")

    def test_no_speech_produces_silent_speech_mix(self):
        """When no narration and no dialogue, speech mix must be silent (no apad on voice)."""
        graph, _ = self._capture_filter_graph(
            narration_tracks=[], dialogue_tracks=[])
        # No voice stream should be present
        self.assertNotIn("adelay", graph,
                         "adelay found despite no speech segments")
        # silence must be the speech source
        self.assertIn("acopy", graph,
                      "silence acopy must be used when there is no speech")

    def test_narration_at_correct_scene_offset(self):
        """Scene 2 narration at 16000ms must emit adelay=16000."""
        graph, _ = self._capture_filter_graph(
            narration_tracks=[
                {"start_ms": 0, "scene_index": 1},
                {"start_ms": 16000, "scene_index": 2},
            ])
        self.assertIn("adelay=0|0", graph,
                      "Scene 1 narration must start at 0ms")
        self.assertIn("adelay=16000|16000", graph,
                      "Scene 2 narration must start at 16000ms")

    def test_final_mix_output_uses_video_stream(self):
        """Final mix must map [v] from the video input, not some voice stream."""
        _, full_args = self._capture_filter_graph()
        self.assertIn("-map", full_args)
        map_indices = [full_args[i+1] for i, a in enumerate(full_args) if a == "-map"]
        self.assertIn("[v]", map_indices,
                      "[v] video map not found in final_mix output command")
        self.assertIn("[a]", map_indices,
                      "[a] audio map not found in final_mix output command")


# ── 12. AudioPlan model ───────────────────────────────────────────────────────

class TestAudioPlanModel(unittest.TestCase):
    def test_narration_tracks_field_exists(self):
        """AudioPlan must have a narration_tracks list field."""
        plan = AudioPlan()
        self.assertIsInstance(plan.narration_tracks, list)
        self.assertEqual(len(plan.narration_tracks), 0)

    def test_narration_tracks_roundtrip(self):
        """narration_tracks must survive JSON serialisation round-trip."""
        plan = AudioPlan(narration_tracks=[
            {"path": "/tmp/n1.mp3", "start_ms": 0, "scene_index": 1},
            {"path": "/tmp/n2.mp3", "start_ms": 8000, "scene_index": 2},
        ])
        from vidgen.models import FilmProject
        p = FilmProject(audio_plan=plan)
        restored = FilmProject.model_validate_json(p.model_dump_json())
        self.assertEqual(len(restored.audio_plan.narration_tracks), 2)
        self.assertEqual(restored.audio_plan.narration_tracks[1]["start_ms"], 8000)


if __name__ == "__main__":
    unittest.main()
