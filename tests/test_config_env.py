"""
Regression tests for vidgen/config.py environment variable precedence.

All tests are pure logic — no subprocess, no module reload, no SDK calls.
The settings singleton is already loaded by pytest's session; we use
patch.object to temporarily override individual attributes and verify
the is_production property logic.
"""
import pathlib
import unittest
from unittest.mock import patch

# Read source once at module level — pure file I/O, no SDK
_CONFIG_SRC = pathlib.Path("vidgen/config.py").read_text()

# Import the already-loaded singleton (no re-init, no SDK call)
from vidgen.config import settings  # noqa: E402 (after source read)


class TestConfigEnvPrecedence(unittest.TestCase):

    # ── source-code checks ────────────────────────────────────────────────────

    def test_load_dotenv_uses_override_false(self):
        """load_dotenv must use override=False — never override Cloud Run env vars."""
        self.assertNotIn(
            "load_dotenv(override=True)", _CONFIG_SRC,
            "load_dotenv(override=True) found — Cloud Run env vars would be "
            "silently overridden by any .env file in the container"
        )
        self.assertIn(
            "override=False", _CONFIG_SRC,
            "load_dotenv must pass override=False"
        )

    def test_allow_real_generation_parsed_as_bool(self):
        """ALLOW_REAL_GENERATION env string 'true' must be parsed → boolean True."""
        self.assertIn(
            '.lower() == "true"', _CONFIG_SRC,
            "ALLOW_REAL_GENERATION must parse env string 'true' to True"
        )

    def test_default_film_mode_is_simulation(self):
        """Default FILM_MODE must be 'simulation' when no env var is set."""
        self.assertIn('"simulation"', _CONFIG_SRC,
                      "Default FILM_MODE must be 'simulation' in config source")

    def test_is_production_depends_on_both_flags(self):
        """is_production property must check both FILM_MODE and ALLOW_REAL_GENERATION."""
        self.assertIn('== "production"', _CONFIG_SRC)
        self.assertIn("ALLOW_REAL_GENERATION", _CONFIG_SRC)

    # ── is_production property logic ─────────────────────────────────────────

    def test_is_production_true_when_both_flags_set(self):
        """FILM_MODE=production + ALLOW_REAL_GENERATION=True → is_production True."""
        with patch.object(settings, "FILM_MODE", "production"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", True):
            self.assertTrue(settings.is_production)

    def test_is_production_false_simulation_mode(self):
        """FILM_MODE=simulation → is_production False even with ALLOW_REAL_GENERATION=True."""
        with patch.object(settings, "FILM_MODE", "simulation"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", True):
            self.assertFalse(settings.is_production)

    def test_is_production_false_allow_real_false(self):
        """ALLOW_REAL_GENERATION=False → is_production False."""
        with patch.object(settings, "FILM_MODE", "production"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", False):
            self.assertFalse(settings.is_production)

    def test_production_requires_both_flags(self):
        """Neither flag alone is sufficient."""
        with patch.object(settings, "FILM_MODE", "production"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", False):
            self.assertFalse(settings.is_production)
        with patch.object(settings, "FILM_MODE", "simulation"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", True):
            self.assertFalse(settings.is_production)

    def test_cloud_run_env_produces_is_production_true(self):
        """
        Exact Cloud Run env config (as in the bug report) must produce is_production=True.
        Expected CLI output: production True True vidgen-504817 vidgen-media-assets
        """
        with patch.object(settings, "FILM_MODE", "production"), \
             patch.object(settings, "ALLOW_REAL_GENERATION", True), \
             patch.object(settings, "GOOGLE_CLOUD_PROJECT", "vidgen-504817"), \
             patch.object(settings, "GCS_BUCKET", "vidgen-media-assets"):
            result = (
                f"{settings.FILM_MODE} "
                f"{settings.ALLOW_REAL_GENERATION} "
                f"{settings.is_production} "
                f"{settings.GOOGLE_CLOUD_PROJECT} "
                f"{settings.GCS_BUCKET}"
            )
        self.assertEqual(
            result,
            "production True True vidgen-504817 vidgen-media-assets",
            f"Cloud Run env config produced unexpected result: {result}"
        )
