import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    from google.auth import default as _gauth
except Exception:
    _gauth = None


class Settings:
    FILM_MODE = os.getenv("FILM_MODE", "simulation")
    ALLOW_REAL_GENERATION = os.getenv("ALLOW_REAL_GENERATION", "false").lower() == "true"

    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    GCS_BUCKET = os.getenv("GCS_BUCKET", "vidgen-media-assets")

    # veo-3.1-generate-001 confirmed on vidgen-504817 / us-central1
    VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-generate-001")
    DIRECTOR_MODEL = os.getenv("DIRECTOR_MODEL", "gemini-2.5-flash")
    # Confirmed listed by the configured Vertex project; Imagen 3 is not.
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-2.5-flash-image")
    TTS_VOICE = os.getenv("TTS_VOICE", "en-US-Neural2-J")
    BURN_SUBTITLES = os.getenv("BURN_SUBTITLES", "true").lower() == "true"

    VIDGEN_WORK_ROOT = Path(os.getenv("VIDGEN_WORK_ROOT", "/tmp/vidgen"))

    FPS = 24
    DEFAULT_SHOT_DURATION = 8
    SHOTS_PER_SCENE = int(os.getenv("SHOTS_PER_SCENE", "2"))
    MAX_SHOTS = int(os.getenv("MAX_SHOTS", "42"))
    VEO_TIMEOUT_SECONDS = int(os.getenv("VEO_TIMEOUT_SECONDS", "1800"))
    RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))
    IMAGE_RETRY_ATTEMPTS = int(os.getenv("IMAGE_RETRY_ATTEMPTS", "8"))
    IMAGE_REQUEST_DELAY_SECONDS = float(os.getenv("IMAGE_REQUEST_DELAY_SECONDS", "3.0"))

    # ── Rate-limit resilience ────────────────────────────────────────────────
    # These control the shared retry policy used by all generative providers.
    VIDGEN_MAX_RETRIES = int(os.getenv("VIDGEN_MAX_RETRIES", "5"))
    VIDGEN_INITIAL_BACKOFF_SECONDS = float(os.getenv("VIDGEN_INITIAL_BACKOFF_SECONDS", "2.0"))
    VIDGEN_MAX_BACKOFF_SECONDS = float(os.getenv("VIDGEN_MAX_BACKOFF_SECONDS", "60.0"))
    VIDGEN_RETRY_JITTER = float(os.getenv("VIDGEN_RETRY_JITTER", "1.0"))

    # ── Duration planning ────────────────────────────────────────────────────
    # Tolerance (seconds) between requested and actual planned duration.
    # A planned duration within this tolerance of the target is accepted.
    DURATION_TOLERANCE_SECONDS = int(os.getenv("DURATION_TOLERANCE_SECONDS", "10"))
    # Valid Veo shot durations (provider constraint). Shots are snapped to nearest.
    VEO_VALID_DURATIONS = (4, 5, 6, 7, 8)

    @property
    def is_production(self) -> bool:
        return self.FILM_MODE == "production" and self.ALLOW_REAL_GENERATION

    def __init__(self):
        if not self.GOOGLE_CLOUD_PROJECT and _gauth:
            try:
                _, proj = _gauth()
                if proj:
                    self.GOOGLE_CLOUD_PROJECT = proj
            except Exception:
                pass


settings = Settings()
settings.VIDGEN_WORK_ROOT.mkdir(parents=True, exist_ok=True)
