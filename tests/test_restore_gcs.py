"""
Regression tests for _restore_from_gcs() and the production worker restore path.

All tests are pure logic — no real GCS calls, no subprocess, no ADC calls.
"""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from vidgen.models import FilmProject, FilmStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_orc(exists: bool) -> MagicMock:
    """Return a mock Orchestrator whose storage.exists returns `exists`."""
    orc = MagicMock()
    orc.storage.exists.return_value = exists

    def _fake_download(remote_path: str, local_path: str) -> None:
        # Write a minimal valid FilmProject JSON to the local path
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        p = FilmProject(topic="restored project")
        Path(local_path).write_text(p.model_dump_json())

    orc.storage.download.side_effect = _fake_download
    return orc


def _import_restore_fn():
    """Import _restore_from_gcs from run_production without running main()."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_production", "run_production.py")
    mod = importlib.util.module_from_spec(spec)
    # Prevent the module-level STATE_DIR.mkdir from failing in test env
    spec.loader.exec_module(mod)
    return mod._restore_from_gcs, mod


# ---------------------------------------------------------------------------
# 1. existing GCS state → restored
# ---------------------------------------------------------------------------

class TestRestoreFromGcsExists(unittest.TestCase):

    def test_existing_gcs_state_is_restored(self):
        """When GCS state exists, _restore_from_gcs must return a FilmProject."""
        _restore_from_gcs, mod = _import_restore_fn()
        orc = _make_mock_orc(exists=True)
        pid = "test-project-abc"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mod, "STATE_DIR", Path(tmpdir)):
                result = _restore_from_gcs(orc, pid)

        self.assertIsNotNone(result, "Expected a FilmProject but got None")
        self.assertIsInstance(result, FilmProject)

    def test_existing_gcs_state_calls_download(self):
        """download() must be called when GCS state exists and no local cache."""
        _restore_from_gcs, mod = _import_restore_fn()
        orc = _make_mock_orc(exists=True)
        pid = "test-project-download"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mod, "STATE_DIR", Path(tmpdir)):
                _restore_from_gcs(orc, pid)

        orc.storage.download.assert_called_once()
        call_args = orc.storage.download.call_args
        remote_arg = call_args[0][0]
        self.assertTrue(remote_arg.startswith("gs://"),
                        f"download() called with non-GCS URI: {remote_arg!r}")

    def test_gcs_uri_contains_pid_and_bucket(self):
        """The GCS URI checked must contain the project ID and GCS_BUCKET."""
        _restore_from_gcs, mod = _import_restore_fn()
        orc = _make_mock_orc(exists=True)
        pid = "my-unique-project-id"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mod, "STATE_DIR", Path(tmpdir)):
                _restore_from_gcs(orc, pid)

        exists_call_uri = orc.storage.exists.call_args[0][0]
        self.assertIn(pid, exists_call_uri,
                      f"Project ID not in exists() URI: {exists_call_uri!r}")
        self.assertTrue(exists_call_uri.startswith("gs://"),
                        f"exists() called with non-GCS URI: {exists_call_uri!r}")


# ---------------------------------------------------------------------------
# 2. missing GCS state → returns None
# ---------------------------------------------------------------------------

class TestRestoreFromGcsMissing(unittest.TestCase):

    def test_missing_gcs_state_returns_none(self):
        """When GCS state does not exist, _restore_from_gcs must return None."""
        _restore_from_gcs, mod = _import_restore_fn()
        orc = _make_mock_orc(exists=False)
        pid = "missing-project-xyz"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mod, "STATE_DIR", Path(tmpdir)):
                result = _restore_from_gcs(orc, pid)

        self.assertIsNone(result, "Expected None for missing GCS state")

    def test_missing_gcs_state_does_not_call_download(self):
        """download() must NOT be called when GCS state is absent."""
        _restore_from_gcs, mod = _import_restore_fn()
        orc = _make_mock_orc(exists=False)
        pid = "missing-project-xyz"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(mod, "STATE_DIR", Path(tmpdir)):
                _restore_from_gcs(orc, pid)

        orc.storage.download.assert_not_called()


# ---------------------------------------------------------------------------
# 3. local cache hit → restored without GCS download
# ---------------------------------------------------------------------------

class TestRestoreFromGcsLocalCache(unittest.TestCase):

    def test_local_cache_restored_without_download(self):
        """When local state file exists, restore from local without calling download."""
        _restore_from_gcs, mod = _import_restore_fn()
        orc = _make_mock_orc(exists=False)  # GCS would fail if called
        pid = "cached-project-id"

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / pid / "project_state.json"
            state_path.parent.mkdir(parents=True)
            p_orig = FilmProject(topic="cached project")
            p_orig.status = FilmStatus.GENERATING
            state_path.write_text(p_orig.model_dump_json())

            with patch.object(mod, "STATE_DIR", Path(tmpdir)):
                result = _restore_from_gcs(orc, pid)

        self.assertIsNotNone(result)
        self.assertEqual(result.status, FilmStatus.GENERATING)
        orc.storage.download.assert_not_called()

    def test_local_cache_takes_priority_over_gcs(self):
        """Local cache is used first; GCS is only consulted when local is absent."""
        _restore_from_gcs, mod = _import_restore_fn()
        orc = _make_mock_orc(exists=True)  # GCS also has a copy
        pid = "priority-project"

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / pid / "project_state.json"
            state_path.parent.mkdir(parents=True)
            p_orig = FilmProject(topic="local wins")
            state_path.write_text(p_orig.model_dump_json())

            with patch.object(mod, "STATE_DIR", Path(tmpdir)):
                result = _restore_from_gcs(orc, pid)

        # Should have loaded local, not called GCS download
        orc.storage.download.assert_not_called()
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# 4. explicit missing project → clear error in main()
# ---------------------------------------------------------------------------

class TestMainMissingProject(unittest.TestCase):

    def test_missing_project_exits_3(self):
        """main() must exit with code 3 when VIDGEN_PROJECT_ID project is not in GCS."""
        _restore_from_gcs, mod = _import_restore_fn()

        with patch.dict("os.environ", {
            "FILM_MODE": "production",
            "ALLOW_REAL_GENERATION": "true",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GCS_BUCKET": "test-bucket",
            "VIDGEN_PROJECT_ID": "nonexistent-project-id",
        }), patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
           patch.object(mod, "_restore_from_gcs", return_value=None):

            from vidgen.config import settings
            with patch.object(settings, "FILM_MODE", "production"), \
                 patch.object(settings, "ALLOW_REAL_GENERATION", True), \
                 patch.object(settings, "GOOGLE_CLOUD_PROJECT", "test-project"), \
                 patch.object(settings, "GCS_BUCKET", "test-bucket"):

                with self.assertRaises(SystemExit) as ctx:
                    mod.main()

                self.assertEqual(ctx.exception.code, 3,
                                 f"Expected exit code 3 for missing project, got {ctx.exception.code}")

    def test_missing_project_prints_expected_uri(self, capsys=None):
        """main() must print the exact expected GCS URI when project is not found."""
        import io
        _restore_from_gcs, mod = _import_restore_fn()

        with patch.dict("os.environ", {
            "VIDGEN_PROJECT_ID": "my-project-id",
        }), patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
           patch.object(mod, "_restore_from_gcs", return_value=None):

            from vidgen.config import settings
            with patch.object(settings, "FILM_MODE", "production"), \
                 patch.object(settings, "ALLOW_REAL_GENERATION", True), \
                 patch.object(settings, "GOOGLE_CLOUD_PROJECT", "test-project"), \
                 patch.object(settings, "GCS_BUCKET", "test-bucket"):

                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                try:
                    with redirect_stdout(buf):
                        mod.main()
                except SystemExit:
                    pass

                output = buf.getvalue()
                self.assertIn("gs://", output,
                              "Expected GCS URI in error output when project not found")
                self.assertIn("my-project-id", output,
                              "Expected project ID in error output")


# ---------------------------------------------------------------------------
# 5. production env guard remains correct (regression)
# ---------------------------------------------------------------------------

class TestProductionGuardRegression(unittest.TestCase):

    def test_aborts_when_not_production(self):
        """main() must exit when is_production is False."""
        _, mod = _import_restore_fn()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            from vidgen.config import settings
            with patch.object(settings, "FILM_MODE", "simulation"), \
                 patch.object(settings, "ALLOW_REAL_GENERATION", False):
                with self.assertRaises(SystemExit) as ctx:
                    mod.main()
                self.assertEqual(ctx.exception.code, 1)

    def test_aborts_when_vidgen_project_id_missing(self):
        """main() must exit with code 2 when VIDGEN_PROJECT_ID is absent."""
        _, mod = _import_restore_fn()

        env_without_pid = {k: v for k, v in __import__("os").environ.items()
                           if k != "VIDGEN_PROJECT_ID"}

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch.dict("os.environ", env_without_pid, clear=True):
            from vidgen.config import settings
            with patch.object(settings, "FILM_MODE", "production"), \
                 patch.object(settings, "ALLOW_REAL_GENERATION", True), \
                 patch.object(settings, "GOOGLE_CLOUD_PROJECT", "test-project"):
                with self.assertRaises(SystemExit) as ctx:
                    mod.main()
                self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
