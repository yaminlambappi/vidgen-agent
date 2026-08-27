"""
Gemini-based image generation with rate-limit resilience.

Uses the shared retry policy (vidgen.utils.retry) for exponential backoff
on 429/500/503. Never fabricates a URI or silently degrades quality.

Model chain:
  Primary model (settings.IMAGE_MODEL) is tried with full retry budget.
  If it exhausts its budget on rate-limit errors, the next model in the
  confirmed fallback chain is tried.

Raises RateLimitExhausted when all models are exhausted.
"""
from __future__ import annotations

import time
from typing import Optional

from google import genai
from google.genai import types

from vidgen.config import settings
from vidgen.utils.retry import call_with_retry, RateLimitExhausted, classify_error

# Confirmed fallback chain — all are real Gemini models that support image output.
# Do NOT add models that are not confirmed to exist in the project.
_FALLBACK_CHAIN = [
    "gemini-2.5-flash-image",
    "gemini-2.0-flash-exp",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
]


class GeminiImageGenerator:
    """
    Generates an image using a Gemini model with full rate-limit resilience.

    Postconditions:
        - Returns image bytes on success
        - Raises RateLimitExhausted when all models exhaust their retry budgets
        - Never returns None on success (raises on failure)
        - Never fabricates a URI or creates a placeholder image
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.IMAGE_MODEL
        self._client: Optional[genai.Client] = None
        # Build deduplicated model chain: configured primary first, then fallbacks
        seen: set = set()
        chain = []
        for m in [self.model] + _FALLBACK_CHAIN:
            if m not in seen:
                seen.add(m)
                chain.append(m)
        self._model_chain = chain

    @property
    def client(self) -> genai.Client:
        if not self._client:
            self._client = genai.Client(
                vertexai=True,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.GOOGLE_CLOUD_LOCATION,
            )
        return self._client

    def _generate_with_model(self, model_name: str, prompt: str) -> Optional[bytes]:
        """
        Attempt to generate an image with a single model.
        Returns bytes on success, None if the model returned no image data.
        Raises the original exception on deterministic errors.
        """
        if model_name.startswith("imagen-"):
            r = self.client.models.generate_images(
                model=model_name, prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1, aspect_ratio="16:9",
                    output_mime_type="image/png"))
            for image in (getattr(r, "generated_images", None) or []):
                data = getattr(getattr(image, "image", image), "image_bytes", None)
                if data:
                    return data
        else:
            r = self.client.models.generate_content(
                model=model_name, contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"], temperature=0.4))
            for cand in (getattr(r, "candidates", None) or []):
                for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        return inline.data
        return None

    def generate(self, prompt: str) -> bytes:
        """
        Generate an image, retrying on rate-limit errors across the model chain.

        Algorithm:
          For each model in the chain:
            - Retry the model up to VIDGEN_MAX_RETRIES times on transient errors
            - If the model is exhausted due to rate limits, try the next model
            - If the model raises a deterministic error, skip to next model
            - If a model returns image bytes, return immediately

        Raises:
          RateLimitExhausted — if all models in the chain exhaust their retry budgets
          RuntimeError       — if no model in the chain produced image bytes (non-rate-limit failure)
        """
        exhausted_models: list[str] = []
        last_rate_limit_exc: Optional[RateLimitExhausted] = None

        for model_name in self._model_chain:
            try:
                image_bytes = call_with_retry(
                    fn=lambda m=model_name: self._generate_with_model(m, prompt),
                    provider="gemini-image",
                    model=model_name,
                    operation="generate_image",
                )
                if image_bytes:
                    return image_bytes
                # Model returned no data — try next (not a rate limit)
                print(f"[IMAGE] Model {model_name} returned no image data, trying next")
                continue

            except RateLimitExhausted as exc:
                exhausted_models.append(model_name)
                last_rate_limit_exc = exc
                print(f"[IMAGE] Model {model_name} rate-limit exhausted, trying next model")
                continue

            except RuntimeError as exc:
                # Deterministic error from this model — skip to next
                err_cls = classify_error(exc)
                if err_cls == "deterministic":
                    print(f"[IMAGE] Model {model_name} deterministic error, skipping: {str(exc)[:80]}")
                    continue
                # Non-deterministic non-rate-limit failure — raise
                raise

        if last_rate_limit_exc is not None:
            # All models were rate limited
            raise RateLimitExhausted(
                provider="gemini-image",
                model=f"all_models({','.join(exhausted_models)})",
                operation="generate_image",
                attempts=settings.VIDGEN_MAX_RETRIES * len(exhausted_models),
                last_error=str(last_rate_limit_exc),
            )

        raise RuntimeError(
            f"Image generation failed for all models: none produced image bytes. "
            f"Models tried: {self._model_chain}"
        )
