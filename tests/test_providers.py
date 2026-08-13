import os
import unittest
from unittest.mock import patch, MagicMock
from vidgen.providers import get_video_generator, get_storage_provider
from vidgen.providers.video import MockVideoGenerator, VeoVideoGenerator
from vidgen.config import settings

class TestProviders(unittest.TestCase):
    def test_provider_selection_simulation(self):
        # Force simulation mode
        with patch("vidgen.config.settings.FILM_MODE", "simulation"), \
             patch("vidgen.config.settings.ALLOW_REAL_GENERATION", False):
            
            # Note: settings.is_production is a property on the class, 
            # but we patched it on the instance or class level?
            # settings is an instance of Settings.
            with patch.object(settings, 'FILM_MODE', 'simulation'), \
                 patch.object(settings, 'ALLOW_REAL_GENERATION', False):
                
                self.assertFalse(settings.is_production)
                video_gen = get_video_generator()
                self.assertIsInstance(video_gen, MockVideoGenerator)

    def test_provider_selection_production_guard(self):
        # production mode but ALLOW_REAL_GENERATION is False
        with patch.object(settings, 'FILM_MODE', 'production'), \
             patch.object(settings, 'ALLOW_REAL_GENERATION', False):
            
            self.assertFalse(settings.is_production)
            video_gen = get_video_generator()
            self.assertIsInstance(video_gen, MockVideoGenerator)

    def test_provider_selection_real_production(self):
        # Only this combo should allow real generation
        with patch.object(settings, 'FILM_MODE', 'production'), \
             patch.object(settings, 'ALLOW_REAL_GENERATION', True):
            
            self.assertTrue(settings.is_production)
            # Mocking google-genai so we don't actually import it and fail if not installed
            with patch("google.genai.Client", return_value=MagicMock()):
                 video_gen = get_video_generator()
                 self.assertIsInstance(video_gen, VeoVideoGenerator)

    def test_mock_video_generator_does_not_call_veo(self):
        mock_gen = MockVideoGenerator()
        # If MockVideoGenerator had internal calls to real APIs, we'd mock them and verify they aren't called.
        # For now, it's a pure mock.
        job = mock_gen.generate_shot("Test prompt", "gs://test/", shot_id="shot-1")
        self.assertEqual(job.status, "completed")
        self.assertIn("mock_", job.artifact_uri)
        self.assertTrue(job.artifact_uri.endswith(".mp4"))

    # def test_infer_image_mime_type_from_uri(self):
    #     self.assertEqual(VeoVideoGenerator._infer_image_mime_type("gs://bucket/hero.png"), "image/png")
    #     self.assertEqual(VeoVideoGenerator._infer_image_mime_type("gs://bucket/hero.jpg"), "image/jpeg")
    #     self.assertEqual(VeoVideoGenerator._infer_image_mime_type("gs://bucket/hero.webp"), "image/webp")
    #     self.assertIsNone(VeoVideoGenerator._infer_image_mime_type("gs://bucket/hero"))

if __name__ == "__main__":
    unittest.main()
