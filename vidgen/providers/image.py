"""Gemini-based image generation."""
from __future__ import annotations
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
        cfg = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            temperature=0.4,
        )
        last_exc = None
        for model_name in self._model_chain:
            try:
                r = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=cfg,
                )
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
            raise RuntimeError(f"Image generation failed for all models: {last_exc}")

        return None
