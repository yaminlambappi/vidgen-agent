import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=False)  # Never override real deployment env vars (e.g. Cloud Run)

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

    # Configurable model IDs — never scatter hardcoded names in pipeline code.
    # GEMINI_MODEL / GEMINI_IMAGE_MODEL take precedence; DIRECTOR_MODEL / IMAGE_MODEL remain aliases.
    GEMINI_MODEL = os.getenv("GEMINI_MODEL") or os.getenv("DIRECTOR_MODEL", "gemini-2.5-flash")
    DIRECTOR_MODEL = GEMINI_MODEL
    GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL") or os.getenv("IMAGE_MODEL", "gemini-2.5-flash-image")
    IMAGE_MODEL = GEMINI_IMAGE_MODEL
    # veo-3.1-generate-001 confirmed on the configured Vertex project / us-central1
    VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-generate-001")
    TTS_MODEL = os.getenv("TTS_MODEL", "neural2")
    TTS_VOICE = os.getenv("TTS_VOICE", "en-US-Neural2-J")
    TTS_VOICE_BN = os.getenv("TTS_VOICE_BN", "bn-IN-Wavenet-A")
    TTS_VOICE_BN_MALE = os.getenv("TTS_VOICE_BN_MALE", "bn-IN-Wavenet-B")
    BURN_SUBTITLES = os.getenv("BURN_SUBTITLES", "true").lower() == "true"

    # ── Reels / Shorts factory ───────────────────────────────────────────────
    DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
    MAX_DURATION_SECONDS = float(os.getenv("MAX_DURATION_SECONDS", "30.0"))
    DIRECTOR_MAX_SECONDS = float(os.getenv("DIRECTOR_MAX_SECONDS", "180.0"))
    MAX_DIRECTOR_VEO_CALLS = int(os.getenv("MAX_DIRECTOR_VEO_CALLS", "24"))
    REEL_WIDTH = int(os.getenv("REEL_WIDTH", "1080"))
    REEL_HEIGHT = int(os.getenv("REEL_HEIGHT", "1920"))
    REEL_ASPECT_RATIO = os.getenv("REEL_ASPECT_RATIO", "9:16")
    REEL_FPS = int(os.getenv("REEL_FPS", "24"))
    DEFAULT_REEL_DURATION = float(os.getenv("DEFAULT_REEL_DURATION", "20.0"))

    # Conservative hard generation budgets — never unlimited.
    MAX_PIPELINE_ATTEMPTS = int(os.getenv("MAX_PIPELINE_ATTEMPTS", "2"))
    MAX_VEO_CALLS = int(os.getenv("MAX_VEO_CALLS", "8"))
    MAX_IMAGE_CALLS = int(os.getenv("MAX_IMAGE_CALLS", "4"))
    MAX_TTS_CALLS = int(os.getenv("MAX_TTS_CALLS", "6"))
    MAX_GEMINI_CALLS = int(os.getenv("MAX_GEMINI_CALLS", "12"))
    MAX_REGENERATION_PER_SHOT = int(os.getenv("MAX_REGENERATION_PER_SHOT", "1"))
    MAX_PIPELINE_RUNTIME = int(os.getenv("MAX_PIPELINE_RUNTIME", "3600"))
    MAX_TOTAL_GENERATION_BUDGET = int(os.getenv("MAX_TOTAL_GENERATION_BUDGET", "20"))
    MAX_REEL_SHOTS = int(os.getenv("MAX_REEL_SHOTS", "6"))
    CIRCUIT_BREAKER_REPEAT_FAILURES = int(os.getenv("CIRCUIT_BREAKER_REPEAT_FAILURES", "2"))
    MAX_REPAIR_ATTEMPTS = int(os.getenv("MAX_REPAIR_ATTEMPTS", "1"))
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

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
    # Veo 3.1 text_to_video: only 4, 6, 8. reference_to_video is 8s-only.
    VEO_VALID_DURATIONS = (4, 6, 8)
    # Planning quote only — not a Google invoice.
    VEO_USD_PER_SECOND = float(os.getenv("VEO_USD_PER_SECOND", "0.40"))
    FACTORY_LIST_PRICE_USD = float(os.getenv("FACTORY_LIST_PRICE_USD", "99"))
    FACTORY_MONTHLY_USD = float(os.getenv("FACTORY_MONTHLY_USD", "799"))
    FACTORY_MONTHLY_REELS = int(os.getenv("FACTORY_MONTHLY_REELS", "12"))
    AGENCY_COMPARABLE_USD = float(os.getenv("AGENCY_COMPARABLE_USD", "450"))

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
