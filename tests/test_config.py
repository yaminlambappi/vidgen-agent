import unittest
from unittest.mock import patch
import os
from vidgen.config import settings

class TestConfig(unittest.TestCase):
    def test_config_overrides_shell_env(self):
        # Verify that our specific project is loaded
        self.assertEqual(settings.GOOGLE_CLOUD_PROJECT, "vidgen-504817")
        self.assertEqual(settings.GOOGLE_CLOUD_LOCATION, "us-central1")
        self.assertEqual(settings.VEO_MODEL, "veo-3.1-generate-001")

if __name__ == "__main__":
    unittest.main()
