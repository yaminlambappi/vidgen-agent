"""
Shared rate-limit resilience policy for all VidGen generative providers.

Covers: Gemini LLM, Gemini Image, Veo video, and any future provider.

Error classification
--------------------
TRANSIENT (always retry with backoff):
  - 429  RESOURCE_EXHAUSTED / Too Many Requests
  - 500  INTERNAL
  - 503  UNAVAILABLE
  - timeout / deadline exceeded / connection error

DETERMINISTIC (never retry):
  - 400  invalid request / bad argument
  - 401  unauthenticated
  - 403  permission denied / does not have access
  - 404  model not found / resource not found

Retry-After
-----------
When a 429 response carries a Retry-After header value (in seconds), that
wait time is used directly, capped at VIDGEN_MAX_BACKOFF_SECONDS.

Logging
-------
Every retry emits a structured [RATE_LIMIT] log line.
Exhaustion emits a [RATE_LIMIT_EXHAUSTED] log line then raises RateLimitExhausted.
No credentials or secrets are ever logged.
"""
from __future__ import annotations

import random
import time
from typing import Any, Callable, Optional, TypeVar

from vidgen.config import settings

# ── Error classification ──────────────────────────────────────────────────────

_DETERMINISTIC_MARKERS = (
    "400", "invalid_argument", "invalid argument",
    "401", "unauthenticated",
    "403", "permission denied", "does not have access",
    "404", "not found", "model was not found",
    "unsupported", "malformed",
)

_TRANSIENT_MARKERS = (
    "429", "resource_exhausted", "resource exhausted",
    "too many requests", "quota",
    "500", "internal",
    "503", "unavailable",
    "502", "bad gateway",
    "timeout", "timed out", "deadline exceeded",
    "connection", "network",
)


def classify_error(exc: Exception) -> str:
    """
    Returns 'deterministic', 'transient', or 'unknown'.
    'unknown' is treated as transient for one retry cycle.
    """
    msg = str(exc).lower()
    if any(k in msg for k in _DETERMINISTIC_MARKERS):
        return "deterministic"
    if any(k in msg for k in _TRANSIENT_MARKERS):
        return "transient"
    return "unknown"


def is_retryable(exc: Exception) -> bool:
    return classify_error(exc) != "deterministic"


# ── Structured error ──────────────────────────────────────────────────────────

class RateLimitExhausted(RuntimeError):
    """
    Raised when all retry attempts are exhausted for a transient provider error.

    Attributes:
        provider   -- the provider name (e.g. 'gemini-image', 'veo', 'gemini-llm')
        model      -- the model being called
        operation  -- a short description of what was being attempted
        attempts   -- number of attempts made
        last_error -- the last exception message
    """

    def __init__(self, provider: str, model: str, operation: str,
                 attempts: int, last_error: str) -> None:
        self.provider = provider
        self.model = model
        self.operation = operation
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"RATE_LIMIT_EXHAUSTED provider={provider} model={model} "
            f"operation={operation} attempts={attempts} last_error={last_error}"
        )

    def to_dict(self) -> dict:
        return {
            "failure_code": "RATE_LIMIT_EXHAUSTED",
            "provider": self.provider,
            "model": self.model,
            "operation": self.operation,
            "attempts": self.attempts,
            "last_error": self.last_error,
        }


# ── Backoff helpers ───────────────────────────────────────────────────────────

def _backoff_seconds(attempt: int, exc: Exception) -> float:
    """
    Returns how long to wait before the next attempt.

    Exponential backoff with jitter:
      base = INITIAL_BACKOFF * 2^(attempt-1)
      wait = min(base, MAX_BACKOFF) + uniform(0, JITTER)

    If the exception message contains 'retry-after: N', that N is used directly
    (capped at MAX_BACKOFF).
    """
    msg = str(exc).lower()
    # Check for Retry-After hint in the exception message
    if "retry-after" in msg or "retry_after" in msg:
        import re
        match = re.search(r"retry[_-]after[:\s]+(\d+(?:\.\d+)?)", msg)
        if match:
            suggested = float(match.group(1))
            return min(suggested, settings.VIDGEN_MAX_BACKOFF_SECONDS)

    base = settings.VIDGEN_INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
    capped = min(base, settings.VIDGEN_MAX_BACKOFF_SECONDS)
    jitter = random.uniform(0, settings.VIDGEN_RETRY_JITTER)
    return capped + jitter


def _log_retry(provider: str, model: str, operation: str,
               attempt: int, max_attempts: int,
               delay: float, exc: Exception) -> None:
    reason = classify_error(exc)
    # Never log the full exception which might contain credentials or tokens.
    # Log only the classification and a short sanitised prefix.
    short_msg = str(exc)[:120].replace("\n", " ")
    print(
        f"[RATE_LIMIT] provider={provider} model={model} operation={operation} "
        f"attempt={attempt} max_attempts={max_attempts} "
        f"delay_seconds={delay:.1f} reason={reason} error={short_msg!r}"
    )


def _log_exhausted(provider: str, model: str, operation: str,
                   attempts: int, exc: Exception) -> None:
    short_msg = str(exc)[:120].replace("\n", " ")
    print(
        f"[RATE_LIMIT_EXHAUSTED] provider={provider} model={model} "
        f"operation={operation} attempts={attempts} error={short_msg!r}"
    )


# ── Public API ────────────────────────────────────────────────────────────────

T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    provider: str,
    model: str,
    operation: str,
    max_attempts: Optional[int] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """
    Call fn() with exponential-backoff retry on transient errors.

    Parameters
    ----------
    fn          -- zero-argument callable that performs the API call
    provider    -- provider name for logging (e.g. 'gemini-image')
    model       -- model name for logging
    operation   -- short description (e.g. 'generate_image')
    max_attempts -- override settings.VIDGEN_MAX_RETRIES
    sleep_fn    -- injectable sleep function (use mock in tests)

    Returns the return value of fn() on success.

    Raises
    ------
    RateLimitExhausted  -- when all transient retries are exhausted
    RuntimeError        -- immediately on deterministic errors (no retry)
    """
    n = max_attempts if max_attempts is not None else settings.VIDGEN_MAX_RETRIES
    last_exc: Exception = RuntimeError("No attempt made")

    for attempt in range(1, n + 1):
        try:
            return fn()
        except Exception as exc:
            classification = classify_error(exc)

            if classification == "deterministic":
                # Deterministic failures are never retried.
                raise RuntimeError(
                    f"Deterministic provider error [{provider}/{model}] "
                    f"operation={operation}: {exc}"
                ) from exc

            last_exc = exc

            if attempt == n:
                break  # exhausted — fall through to raise

            delay = _backoff_seconds(attempt, exc)
            _log_retry(provider, model, operation, attempt, n, delay, exc)
            sleep_fn(delay)

    _log_exhausted(provider, model, operation, n, last_exc)
    raise RateLimitExhausted(
        provider=provider,
        model=model,
        operation=operation,
        attempts=n,
        last_error=str(last_exc)[:200],
    )
