#!/usr/bin/env python3
"""Dhaka Documentary Production Runner with Comprehensive Auth Fix."""
import os
import sys
import shutil
import json
import subprocess
from pathlib import Path
from importlib import reload

# Set environment variables for the run BEFORE any imports that use them
os.environ["SHOTS_PER_SCENE"] = "8"
os.environ["VIDGEN_WORK_ROOT"] = "/home/yaminlambappi/vidgen-work"
os.environ["FILM_MODE"] = "production"
os.environ["ALLOW_REAL_GENERATION"] = "true"
os.environ["GOOGLE_CLOUD_PROJECT"] = "vidgen-504817"
os.environ["GCS_BUCKET"] = "vidgen-media-assets"

# Add the current directory to sys.path
sys.path.append(str(Path(__file__).parent))

import google.auth
import google.oauth2.credentials
from google.cloud import storage as gcs
from google import genai
from google.cloud import texttospeech

def get_gcloud_credentials():
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], 
            encoding="utf-8"
        ).strip()
        return google.oauth2.credentials.Credentials(token)
    except Exception as e:
        print(f"[AUTH] Failed to get gcloud token: {e}")
        return None

# Patching providers and agents before they are used
def patch_everything(creds):
    import vidgen.providers.storage
    import vidgen.providers.video
    import vidgen.agents

    # Update CloudStorageProvider
    def patched_storage_init(self):
        from vidgen.config import settings
        self._client = gcs.Client(project=settings.GOOGLE_CLOUD_PROJECT, credentials=creds)
    vidgen.providers.storage.CloudStorageProvider.__init__ = patched_storage_init

    # Update VeoVideoGenerator
    def patched_video_init(self):
        from vidgen.config import settings
        self.client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
            credentials=creds
        )
        self.model = settings.VEO_MODEL
    vidgen.providers.video.VeoVideoGenerator.__init__ = patched_video_init

    # Update BaseAgent (for all director/creative agents)
    def get_patched_client(self):
        if not getattr(self, "_client_instance", None):
            from vidgen.config import settings
            self._client_instance = genai.Client(vertexai=True,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.GOOGLE_CLOUD_LOCATION,
                credentials=creds)
        return self._client_instance
    vidgen.agents.BaseAgent.client = property(get_patched_client)

    # Update VoiceAgent
    def patched_voice_init(self):
        self.client = texttospeech.TextToSpeechClient(credentials=creds)
        self.voice_map = {}
    vidgen.agents.VoiceAgent.__init__ = patched_voice_init

# Get credentials and apply patch
creds = get_gcloud_credentials()
if creds:
    print("[AUTH] Using gcloud user credentials.")
    patch_everything(creds)
else:
    print("[AUTH] Falling back to default credentials (might fail).")

from vidgen.config import settings
from vidgen.models import FilmProject, FilmStatus
from vidgen.orchestrator import Orchestrator

TOPIC = (
    "DHAKA: THE RHYTHM OF TIME. A cinematic 3-minute documentary exploring the profound contrast between Old Dhaka (Puran Dhaka) and New Dhaka. "
    "Old Dhaka: The sensory explosion of Shankhari Bazar, the narrow winding lanes, the Mughal architecture of Ahsan Manzil, the chaos of Sadarghat, "
    "and the timeless heritage of rickshaws and street food. Warm, sepia-toned, high-texture visuals. "
    "New Dhaka: The sleek skyscrapers of Gulshan and Banani, the modern infrastructure, the vibrant nightlife, the bustling business districts, "
    "and the contemporary urban lifestyle. Cool, blue-toned, sharp, and polished visuals. "
    "Narrative: A journey through time, showing how the past and present coexist in a city of twenty million souls. "
    "Emotional Arc: Nostalgia for the old world transitioning into the ambition of the new.\n\n"
    "Three-act structure for a 180-second cinematic documentary (3 scenes × 8 shots × 7.5 seconds):\n"
    "* ACT I — The Roots (Old Dhaka): Exploring the heritage, the lanes, and the life of the ancient city. Focus on texture, history, and community.\n"
    "* ACT II — The Shift: The movement from old to new. The bridges, the growing skyline, the blend of cultures.\n"
    "* ACT III — The Future (New Dhaka): The modern metropolis. Glass, steel, lights, and the fast-paced energy of a global city.\n\n"
    "Premium documentary quality. Consistent character identity, wardrobe, and location continuity. "
    "Cinematic composition, motivated camera movement, drone-style perspectives, coherent color language, natural soundscapes, and evocative narration."
)

def main():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[ABORT] ffmpeg or ffprobe is not installed.")
        sys.exit(1)

    print("="*72)
    print("VIDGEN — Dhaka Documentary (Comprehensive Auth Patch)")
    print("="*72)
    print(f"  project     : {settings.GOOGLE_CLOUD_PROJECT}")
    print(f"  bucket      : {settings.GCS_BUCKET}")
    print(f"  shots/scene : {settings.SHOTS_PER_SCENE}")
    print(f"  work_root   : {settings.VIDGEN_WORK_ROOT}")
    print("="*72)

    settings.VIDGEN_WORK_ROOT.mkdir(parents=True, exist_ok=True)
    orc = Orchestrator()
    
    checkpoint = settings.VIDGEN_WORK_ROOT / "active_dhaka_project_id.txt"
    if checkpoint.exists():
        pid = checkpoint.read_text().strip()
        sf = settings.VIDGEN_WORK_ROOT / pid / "project_state.json"
        if sf.exists():
            p = FilmProject.model_validate_json(sf.read_text())
            print(f"[RESUME] {pid} status={p.status.value}")
        else:
            p = FilmProject(topic=TOPIC)
            checkpoint.write_text(p.project_id)
            print(f"[NEW] {p.project_id}")
    else:
        p = FilmProject(topic=TOPIC)
        checkpoint.write_text(p.project_id)
        print(f"[NEW] {p.project_id}")

    try:
        orc.run(p)
    except Exception as exc:
        print(f"\n[FATAL ERROR] {exc}")
        raise

if __name__ == "__main__":
    main()
