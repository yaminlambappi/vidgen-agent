import unittest
from unittest.mock import patch, MagicMock
from vidgen.orchestrator import Orchestrator
from vidgen.models import FilmProject, FilmStatus, Scene, Shot, GenerationJob, StorySpec
from vidgen.config import settings
from pathlib import Path


class TestSimulation(unittest.TestCase):
    def setUp(self):
        settings.FILM_MODE = "simulation"
        settings.ALLOW_REAL_GENERATION = False

    def _mock_download_file(self, remote_path: str, local_path: str) -> None:
        """Mocks file download by creating a dummy file with some content."""
        with open(local_path, "w") as f:
            f.write("dummy video content")

    def _mock_extract_frame(self, video_path: str, frame_path: str) -> None:
        """Mocks frame extraction by creating a dummy image file with some content."""
        with open(frame_path, "w") as f:
            f.write("dummy image content")

    @patch("vidgen.providers.video.VeoVideoGenerator.generate_shot")
    @patch("vidgen.agents.BaseAgent.llm")
    def test_simulation_never_calls_veo(self, mock_llm, mock_veo):
        """CRITICAL: Prove that simulation mode never invokes the real Veo generator."""
        with patch("vidgen.agents.StoryArchitectAgent.design_story") as mock_story, \
             patch("vidgen.agents.ScreenwriterAgent.write_scenes") as mock_scenes, \
             patch("vidgen.agents.StoryboardAgent.design_shots") as mock_shots, \
             patch("vidgen.agents.CharacterDesignAgent.design_characters") as mock_chars, \
             patch("vidgen.agents.WorldDesignAgent.design_world") as mock_world, \
             patch("vidgen.agents.CinematographerAgent.design_cinematics") as mock_cine, \
             patch("vidgen.agents.MusicAgent.compose_plan") as mock_music_plan, \
             patch("vidgen.agents.VoiceAgent.synthesize") as mock_tts, \
             patch("vidgen.agents.SubtitleAgent.generate") as mock_subs, \
             patch("vidgen.orchestrator.validate_video", return_value={"valid": True, "duration": 8.0, "error": None}), \
             patch("vidgen.utils.ffmpeg.probe", return_value={"format": {"duration": "8.0"}}), \
             patch("vidgen.utils.ffmpeg.extract_frame", side_effect=self._mock_extract_frame), \
             patch("vidgen.qc.QCMAgent.check_character_consistency", return_value={"consistent": True, "score": 1.0, "note": "Mock consistent"}), \
             patch("vidgen.qc.QCMAgent.check_visual_artifacts", return_value={"artifact_free": True, "issues": []}), \
             patch("vidgen.qc.QCMAgent.check_cinematic_style", return_value={"style_adherent": True, "critique": ""}), \
             patch("vidgen.qc.QCMAgent.check_continuity", return_value={"continuity_ok": True, "errors": []}):

            from vidgen.models import CharacterBible, WorldBible, CinematicBible, MusicPlan
            mock_story.return_value = StorySpec(title="Test", logline="Test", theme="T", genre="Doc")
            mock_chars.return_value = CharacterBible(characters=[])
            mock_world.return_value = WorldBible(locations=[])
            mock_cine.return_value = CinematicBible()
            mock_scenes.return_value = [Scene(index=1, title="S1", description="D1", location_id="L1")]
            mock_shots.return_value = [Shot(scene_id="S1", index=1, subject="S", action="A", location_id="L1")]
            mock_music_plan.return_value = MusicPlan()
            mock_subs.return_value = ""

            orchestrator = Orchestrator()
            project = FilmProject(topic="Simulation Test")

            with patch.object(orchestrator.storage, "upload", return_value="gs://mock/state.json"), \
                 patch.object(orchestrator.storage, "download", side_effect=self._mock_download_file), \
                 patch("vidgen.orchestrator.concatenate_shots"), \
                 patch("vidgen.orchestrator.final_mix"):

                orchestrator.run(project)

                from vidgen.providers.video import MockVideoGenerator
                self.assertIsInstance(orchestrator.video_gen, MockVideoGenerator)
                mock_veo.assert_not_called()
                self.assertEqual(project.status, FilmStatus.COMPLETED)

    def test_resumability(self):
        """Verify that the pipeline can resume from a specific status."""
        orchestrator = Orchestrator()
        project = FilmProject(topic="Resumable Test")
        project.status = FilmStatus.GENERATING

        with patch.object(orchestrator.video_gen, "generate_shot") as mock_gen, \
             patch.object(orchestrator.storage, "upload", return_value="gs://mock/file"), \
             patch.object(orchestrator.storage, "download", side_effect=self._mock_download_file), \
             patch("vidgen.orchestrator.concatenate_shots"), \
             patch("vidgen.orchestrator.final_mix"), \
             patch("vidgen.agents.MusicAgent.compose_plan") as mock_music_plan, \
             patch("vidgen.agents.SubtitleAgent.generate") as mock_subs, \
             patch("vidgen.agents.VoiceAgent.synthesize") as mock_tts, \
             patch("vidgen.orchestrator.validate_video", return_value={"valid": True, "duration": 8.0, "error": None}), \
             patch("vidgen.utils.ffmpeg.probe", return_value={"format": {"duration": "8.0"}}), \
             patch("vidgen.utils.ffmpeg.extract_frame", side_effect=self._mock_extract_frame), \
             patch("vidgen.qc.QCMAgent.check_character_consistency", return_value={"consistent": True, "score": 1.0, "note": "Mock consistent"}), \
             patch("vidgen.qc.QCMAgent.check_visual_artifacts", return_value={"artifact_free": True, "issues": []}), \
             patch("vidgen.qc.QCMAgent.check_cinematic_style", return_value={"style_adherent": True, "critique": ""}), \
             patch("vidgen.qc.QCMAgent.check_continuity", return_value={"continuity_ok": True, "errors": []}):

            from vidgen.models import MusicPlan
            mock_gen.return_value = GenerationJob(
                project_id=project.project_id, status="completed", artifact_uri="gs://mock/shot.mp4"
            )
            mock_music_plan.return_value = MusicPlan()
            mock_subs.return_value = ""

            scene = Scene(index=1, title="S1", description="D1", location_id="L1")
            scene.shots = [Shot(scene_id="S1", index=1, subject="S", action="A", location_id="L1")]
            project.scenes = [scene]

            orchestrator.run(project)

            self.assertEqual(project.status, FilmStatus.COMPLETED)
            mock_gen.assert_called()

    def test_mock_storage_download_creates_valid_media(self):
        """Simulation storage should emit valid stub media so QC is not blocked by placeholder bytes."""
        from vidgen.providers.storage import MockStorageProvider

        target = Path(settings.VIDGEN_WORK_ROOT) / "mock_test" / "shot.mp4"
        MockStorageProvider().download("gs://mock/shot.mp4", str(target))

        self.assertTrue(target.exists())
        self.assertGreater(target.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
