"""
Pre-flight capability check — validates every required API before film generation.
Gemini check: accepts any non-exception response (empty text is still a live endpoint).
Veo check: uses client.models.list() to confirm reachability, zero generation cost.
Image check: tries model chain, accepts any that returns image bytes.
"""
from __future__ import annotations
import subprocess
import uuid


class CapabilityError(RuntimeError):
    pass


def _ok(name: str) -> None:
    print(f"[CAPABILITY] {name:<20} OK")


def _fail(name: str, reason: str) -> None:
    print(f"[CAPABILITY] {name:<20} FAIL -- {reason[:120]}")


def check_ffmpeg() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            _ok("FFmpeg")
            return True
        _fail("FFmpeg", "non-zero exit")
        return False
    except Exception as exc:
        _fail("FFmpeg", str(exc))
        return False


def check_gcs() -> bool:
    try:
        from google.cloud import storage as gcs
        from vidgen.config import settings
        client = gcs.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = client.bucket(settings.GCS_BUCKET)
        probe = bucket.blob(f"_probe/{uuid.uuid4().hex}.txt")
        probe.upload_from_string(b"probe")
        probe.delete()
        _ok("GCS")
        return True
    except Exception as exc:
        _fail("GCS", str(exc))
        return False


def check_tts() -> bool:
    try:
        from google.cloud import texttospeech
        from vidgen.config import settings
        client = texttospeech.TextToSpeechClient()
        resp = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text="test"),
            voice=texttospeech.VoiceSelectionParams(
                language_code="en-US", name=settings.TTS_VOICE
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            ),
        )
        if resp.audio_content:
            _ok("TTS")
            return True
        _fail("TTS", "empty audio response")
        return False
    except Exception as exc:
        _fail("TTS", str(exc))
        return False


def check_gemini() -> bool:
    """Accept any non-exception response — empty text still means the API is live."""
    try:
        from google import genai
        from google.genai import types
        from vidgen.config import settings
        client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        r = client.models.generate_content(
            model=settings.DIRECTOR_MODEL,
            contents="Reply with one word: READY",
            config=types.GenerateContentConfig(max_output_tokens=10, temperature=0),
        )
        # Any response object (even with empty text) means the endpoint is reachable
        _ok(f"Gemini({settings.DIRECTOR_MODEL})")
        return True
    except Exception as exc:
        _fail("Gemini", str(exc))
        return False


def check_image() -> bool:
    """Try the model chain; accept the first that returns image bytes."""
    try:
        from google import genai
        from google.genai import types
        from vidgen.config import settings

        client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        chain = list(dict.fromkeys([
            settings.IMAGE_MODEL,
            "gemini-2.0-flash-exp",
            "gemini-2.5-flash",
            "gemini-1.5-flash",
        ]))
        deterministic = ("404", "not found", "403", "permission", "400", "unsupported")

        for model in chain:
            try:
                if model.startswith("imagen-"):
                    r = client.models.generate_images(
                        model=model, prompt="A solid blue square, no text.",
                        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="1:1", output_mime_type="image/png"))
                    if any(getattr(getattr(image, "image", image), "image_bytes", None)
                           for image in (getattr(r, "generated_images", None) or [])):
                        _ok(f"Image({model})")
                        return True
                    continue
                cfg = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    temperature=0.4,
                )
                r = client.models.generate_content(
                    model=model,
                    contents="A solid blue square, no text.",
                    config=cfg,
                )
                for cand in (getattr(r, "candidates", None) or []):
                    for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            _ok(f"Image({model})")
                            return True
            except Exception as exc:
                err = str(exc).lower()
                if any(k in err for k in deterministic):
                    continue
                _fail("Image", f"{model}: {str(exc)[:120]}")
                return False

        _fail("Image", f"no model returned image bytes: {chain}")
        return False
    except Exception as exc:
        _fail("Image", str(exc))
        return False


def check_veo() -> bool:
    """
    Verify Veo reachability via models.list() — zero generation cost.
    Falls back to a probe submission only if listing fails.
    """
    try:
        from google import genai
        from google.genai import types
        from vidgen.config import settings

        client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        candidates = list(dict.fromkeys([
            settings.VEO_MODEL,
            "veo-3.0-generate-001",
            "veo-2.0-generate-001",
        ]))
        deterministic = ("404", "not found", "unsupported", "does not have access", "403")

        # Try listing models first (free)
        try:
            listed = {m.name for m in client.models.list()}
            for model in candidates:
                if any(model in name or name.endswith(model) for name in listed):
                    _ok(f"Veo({model})")
                    settings.VEO_MODEL = model
                    return True
        except Exception:
            pass  # listing failed — fall through to probe

        # Probe: submit a real request and accept any non-404 response
        for model in candidates:
            try:
                args = dict(
                    aspect_ratio="16:9",
                    duration_seconds=4,
                    number_of_videos=1,
                    output_gcs_uri=f"gs://{settings.GCS_BUCKET}/_probe/{uuid.uuid4().hex}/",
                    generate_audio=False,
                )
                supported = set(types.GenerateVideosConfig.model_fields)
                config = types.GenerateVideosConfig(**{k: v for k, v in args.items() if k in supported})
                op = client.models.generate_videos(model=model, prompt="blue sky", config=config)
                if op is not None:
                    _ok(f"Veo({model})")
                    settings.VEO_MODEL = model
                    return True
            except Exception as exc:
                err = str(exc).lower()
                if any(k in err for k in deterministic):
                    print(f"[CAPABILITY]   Veo {model}: unavailable ({str(exc)[:80]})")
                    continue
                # Non-deterministic error still means API is reachable
                _ok(f"Veo({model}) [probe accepted with error: {str(exc)[:60]}]")
                settings.VEO_MODEL = model
                return True

        _fail("Veo", f"all candidates unavailable: {candidates}")
        return False
    except Exception as exc:
        _fail("Veo", str(exc))
        return False


def run_all(require_veo: bool = True) -> None:
    """Run all checks. Raises CapabilityError if any required capability fails."""
    print("\n" + "=" * 56)
    print("PRE-FLIGHT CAPABILITY CHECK")
    print("=" * 56)

    results = {
        "FFmpeg": check_ffmpeg(),
        "GCS":    check_gcs(),
        "TTS":    check_tts(),
        "Gemini": check_gemini(),
        "Image":  check_image(),
        "Veo":    check_veo(),
    }

    print("=" * 56)

    failed = [k for k, v in results.items() if not v]
    if not failed:
        print("[CAPABILITY] All systems GO\n")
        return

    hard = [f for f in failed if f != "Veo" or require_veo]
    if hard:
        raise CapabilityError(
            f"Required capabilities unavailable: {hard}. "
            "Fix configuration before starting film generation."
        )
    print(f"[CAPABILITY] WARNING — optional failed: {failed}\n")
