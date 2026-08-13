import unittest
from unittest.mock import MagicMock, patch
from vidgen.models import AssetReference, AssetType, GenerationJob
from vidgen.providers.video import VeoVideoGenerator, MockVideoGenerator
from vidgen.config import settings


class TestVeoVideoGenerator(unittest.TestCase):

    def test_mock_generator_returns_completed(self):
        """MockVideoGenerator should always return a completed job without real API calls."""
        gen = MockVideoGenerator()
        job = gen.generate_shot(
            prompt="A bustling city street at night",
            output_uri="gs://bucket/output/",
            shot_id="shot_001",
            project_id="test_project"
        )
        self.assertEqual(job.status, "completed")
        self.assertIn("mock_", job.artifact_uri)
        self.assertTrue(job.artifact_uri.endswith(".mp4"))

    def _make_veo_generator(self, mock_client, mock_storage):
        """Helper: build a VeoVideoGenerator with injected mocks (bypasses __init__)."""
        gen = VeoVideoGenerator.__new__(VeoVideoGenerator)
        gen.client = mock_client
        gen.model = 'veo-3.1-generate-001'
        gen.storage = mock_storage
        return gen

    def test_generate_shot_polls_until_done(self):
        """VeoVideoGenerator should poll operations until done=True then return the video URI."""
        mock_storage = MagicMock()
        mock_storage.upload.return_value = "gs://bucket/output/shot_001.mp4"

        mock_client = MagicMock()

        mock_op_not_done = MagicMock()
        mock_op_not_done.done = False

        mock_video = MagicMock()
        mock_video.uri = "gs://bucket/source/video.mp4"
        mock_video.video_bytes = None

        mock_generated_video = MagicMock()
        mock_generated_video.video = mock_video

        mock_op_done = MagicMock()
        mock_op_done.done = True
        mock_op_done.error = None
        mock_op_done.response = MagicMock()
        mock_op_done.response.generated_videos = [mock_generated_video]

        mock_client.models.generate_videos.return_value = mock_op_not_done
        mock_client.operations.get.return_value = mock_op_done

        with patch.object(settings, 'GCS_BUCKET', 'bucket'), \
             patch('time.sleep'):

            gen = self._make_veo_generator(mock_client, mock_storage)
            job = gen.generate_shot(
                prompt="A tech city at dawn",
                output_uri="gs://bucket/output/",
                shot_id="shot_001",
                project_id="test_project"
            )

        self.assertEqual(job.status, "completed")
        self.assertIsNotNone(job.artifact_uri)
        self.assertEqual(job.artifact_uri, "gs://bucket/source/video.mp4")
        mock_client.models.generate_videos.assert_called_once()
        mock_client.operations.get.assert_called_once()

    def test_generate_shot_fails_on_operation_error(self):
        """VeoVideoGenerator should return failed status when operation.error is set."""
        mock_storage = MagicMock()
        mock_client = MagicMock()

        mock_op_done = MagicMock()
        mock_op_done.done = True
        mock_op_done.error = "Generation failed due to safety filter"

        mock_client.models.generate_videos.return_value = mock_op_done
        # get() is called first since done is already True on first poll
        mock_client.operations.get.return_value = mock_op_done

        with patch.object(settings, 'GCS_BUCKET', 'bucket'), \
             patch('time.sleep'):

            gen = self._make_veo_generator(mock_client, mock_storage)
            job = gen.generate_shot(
                prompt="test",
                output_uri="gs://bucket/output/",
                shot_id="shot_001",
                project_id="test_project"
            )

        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.error)


if __name__ == "__main__":
    unittest.main()
