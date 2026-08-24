"""Gemini-based image generation."""
from __future__ import annotations
import time, random
from typing import Optional
from google import genai
from google.genai import types
from vidgen.config import settings

class GeminiImageGenerator:
    """Generates an image using a Gemini model."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.IMAGE_MODEL
        self._client = None
        self._model_chain = [self.model]

    @property
    def client(self):
        if not self._client:
            self._client = genai.Client(
                vertexai=True,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.GOOGLE_CLOUD_LOCATION,
            )
        return self._client

    def generate(self, prompt: str) -> Optional[bytes]:
        """Generates an image from a text prompt.

        Returns:
            Image bytes if successful, None otherwise.
        """
        max_retries = 5
        for attempt in range(max_retries):
            try:
                # --- Existing generation logic begins ---
                last_exc = None
                for model_name in self._model_chain:
                    try:
                        if model_name.startswith("imagen-"):
                            r = self.client.models.generate_images(
                                model=model_name, prompt=prompt,
                                config=types.GenerateImagesConfig(
                                    number_of_images=1, aspect_ratio="16:9", output_mime_type="image/png"))
                            for image in (getattr(r, "generated_images", None) or []):
                                image_data = getattr(getattr(image, "image", image), "image_bytes", None)
                                if image_data:
                                    return image_data
                        else:
                            r = self.client.models.generate_content(
                                model=model_name, contents=prompt,
                                config=types.GenerateContentConfig(response_modalities=["IMAGE"], temperature=0.4))
                            for cand in (getattr(r, "candidates", None) or []):
                                for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
                                    inline = getattr(part, "inline_data", None)
                                    if inline and getattr(inline, "data", None):
                                        return inline.data
                    except Exception as exc:
                        last_exc = exc
                        print(f"[IMAGE] Model {model_name} failed: {exc}")
                        continue # Try next model in the chain
                if last_exc:
                    raise last_exc # Re-raise the last error from the model chain
                # --- Existing generation logic ends ---
            except Exception as e:
                error_str = str(e).upper()
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait_time = (2 ** attempt) * 5 + random.uniform(0, 1)
                    print(f"\n[RATE LIMIT] Hit 429 Resource Exhausted. Attempt {attempt + 1}/{max_retries}.")
                    print(f"[RATE LIMIT] Sleeping for {wait_time:.2f} seconds to clear the rate-limit window...\n")
                    time.sleep(wait_time)
                else:
                    raise e

        raise RuntimeError(f"Image generation failed after {max_retries} retries due to quota constraints.")
