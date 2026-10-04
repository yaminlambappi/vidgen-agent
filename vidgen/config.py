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

    GEMINI_MODEL = os.getenv("GEMINI_MODEL") or os.getenv("DIRECTOR_MODEL", "gemini-2.5-flash")
    VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-generate-001")
    # Default only. Intimacy is judged on the synthesized audio, not on this id.
    TTS_VOICE = os.getenv("TTS_VOICE", "bn-IN-Wavenet-D")
    TTS_LANGUAGE = os.getenv("TTS_LANGUAGE", "bn-IN")
    TTS_SPEAKING_RATE = float(os.getenv("TTS_SPEAKING_RATE", "0.72"))
    TTS_PITCH = float(os.getenv("TTS_PITCH", "-6.0"))
    TTS_MAWLA_BREAK_MS = int(os.getenv("TTS_MAWLA_BREAK_MS", "70"))

    VIDGEN_WORK_ROOT = Path(os.getenv("VIDGEN_WORK_ROOT", "/tmp/vidgen"))

    # Veo 3.1 text-to-video accepts only these lengths. Each Sufi shot is
    # generated at 6 seconds and trimmed from the first frame to 5.
    VEO_VALID_DURATIONS = (4, 6, 8)
    VEO_NATIVE_SECONDS = 6
    VEO_TIMEOUT_SECONDS = int(os.getenv("VEO_TIMEOUT_SECONDS", "1800"))

    WIDTH = 1080
    HEIGHT = 1920
    FPS = 24

    # Six takes plus one reshoot each. Plan, critic, and two vision passes share the Gemini ceiling.
    MAX_VEO_CALLS = int(os.getenv("MAX_VEO_CALLS", "12"))
    MAX_TTS_CALLS = int(os.getenv("MAX_TTS_CALLS", "8"))
    MAX_GEMINI_CALLS = int(os.getenv("MAX_GEMINI_CALLS", "6"))

    VIDGEN_MAX_RETRIES = int(os.getenv("VIDGEN_MAX_RETRIES", "5"))
    VIDGEN_INITIAL_BACKOFF_SECONDS = float(os.getenv("VIDGEN_INITIAL_BACKOFF_SECONDS", "2.0"))
    VIDGEN_MAX_BACKOFF_SECONDS = float(os.getenv("VIDGEN_MAX_BACKOFF_SECONDS", "60.0"))
    VIDGEN_RETRY_JITTER = float(os.getenv("VIDGEN_RETRY_JITTER", "1.0"))

    YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
    YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
    YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "unlisted")
    SOCIAL_WEBHOOK_URL = os.getenv("SOCIAL_WEBHOOK_URL", "")
    NEY_BED_PATH = os.getenv("NEY_BED_PATH", "")

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
