"""Hard duration enforcement and speech timing. Planning must fit before generation."""
from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from vidgen.reels.constants import MAX_DURATION_SECONDS, VEO_VALID_DURATIONS


class DurationExceeded(ValueError):
    """Raised when a planned or measured duration is strictly greater than 30.0s."""


def is_duration_valid(seconds: float) -> bool:
    """29.9 and 30.0 pass; 30.01 and 31 fail."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return False
    return 0 < value <= MAX_DURATION_SECONDS


def assert_duration(seconds: float, context: str = "timeline") -> float:
    value = float(seconds)
    if not is_duration_valid(value):
        raise DurationExceeded(
            f"{context} duration {value}s exceeds MAX_DURATION_SECONDS={MAX_DURATION_SECONDS}"
        )
    return value


def normalize_language(language: str) -> str:
    raw = (language or "english").strip().lower()
    if raw in {"bn", "bangla", "bengali", "বাংলা"}:
        return "bengali"
    if raw in {"bengali+english", "bangla+english", "bengali_english", "bilingual", "banglish"}:
        return "bengali_english"
    return "english"


def infer_language(language: str = "", idea: str = "") -> str:
    from vidgen.reels.language import infer_language as _infer
    return _infer(language, idea)


def _word_count(text: str, language: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    words = re.findall(r"[\w\u0980-\u09FF']+", text, flags=re.UNICODE)
    if words:
        return len(words)
    # Unspaced Bengali fallback: ~5 chars per spoken token
    compact = re.sub(r"\s+", "", text)
    return max(1, len(compact) // 5)


def estimate_speech_seconds(
    text: str,
    language: str = "english",
    speaking_rate: float = 1.0,
) -> float:
    """Deterministic speech-duration estimate used before any video generation."""
    lang = normalize_language(language)
    words = _word_count(text, lang)
    if words == 0:
        return 0.0
    wpm = 138.0 if lang.startswith("bengali") else 152.0
    rate = max(0.75, min(1.15, float(speaking_rate) or 1.0))
    pauses = len(re.findall(r"[.!?।…]+", text))
    commas = len(re.findall(r"[,;،]", text))
    seconds = (words / wpm) * 60.0 / rate
    seconds += pauses * 0.32 + commas * 0.12
    return round(seconds, 3)


def snap_veo_duration(seconds: float) -> int:
    valid = tuple(VEO_VALID_DURATIONS) or (5, 6, 7, 8)
    target = max(min(float(seconds), max(valid)), min(valid))
    return min(valid, key=lambda d: abs(d - target))


def plan_shot_durations(target_seconds: float, preferred_shots: int = 0) -> List[int]:
    """
    Build a Veo-legal shot duration list whose sum is <= min(target, 30).
    Prefers few purposeful shots. Never exceeds MAX_DURATION_SECONDS.
    """
    cap = min(float(target_seconds), MAX_DURATION_SECONDS)
    if cap <= 0:
        raise DurationExceeded("target duration must be positive")

    valid = sorted(tuple(VEO_VALID_DURATIONS) or (5, 6, 7, 8))
    max_shots = min(6, int(cap // min(valid)) or 1)
    min_shots = 1
    want = preferred_shots if preferred_shots > 0 else (2 if cap <= 16 else 3 if cap <= 24 else 4)
    want = max(min_shots, min(want, max_shots))

    best: List[int] = []
    best_diff = float("inf")
    for n in range(min_shots, max_shots + 1):
        # Prefer counts near `want`
        base = snap_veo_duration(cap / n)
        candidate = [base] * n
        total = sum(candidate)
        # Shrink from the tail until under cap
        while total > cap and candidate:
            idx = len(candidate) - 1
            if candidate[idx] > min(valid):
                candidate[idx] -= 1
                if candidate[idx] not in valid:
                    candidate[idx] = max(v for v in valid if v <= candidate[idx])
            else:
                candidate.pop()
            total = sum(candidate)
        if not candidate:
            continue
        if sum(candidate) > MAX_DURATION_SECONDS:
            continue
        diff = abs(sum(candidate) - cap) + abs(len(candidate) - want) * 0.35
        if diff < best_diff:
            best_diff = diff
            best = candidate

    if not best:
        # Last resort: single shortest legal shot
        best = [min(valid)]
    assert_duration(sum(best), "planned shot timeline")
    return best


def assign_timeline(durations: Sequence[float]) -> List[Tuple[float, float, float]]:
    """Return (duration, start, end) tuples. Raises if the timeline exceeds 30s."""
    cursor = 0.0
    spans: List[Tuple[float, float, float]] = []
    for dur in durations:
        start = cursor
        end = cursor + float(dur)
        spans.append((float(dur), start, end))
        cursor = end
    assert_duration(cursor, "assigned timeline")
    return spans


def restructure_timeline(durations: Iterable[float], cap: float | None = None) -> List[float]:
    """If a timeline overflows, shrink from the end before any expensive generation."""
    limit = min(float(cap or MAX_DURATION_SECONDS), MAX_DURATION_SECONDS)
    values = [float(d) for d in durations]
    total = sum(values)
    if total <= limit:
        return values
    # Drop trailing shots first, then shrink remaining to legal Veo durations
    while values and sum(values) > limit:
        if len(values) > 1 and sum(values[:-1]) >= min(VEO_VALID_DURATIONS):
            values.pop()
            continue
        values[-1] = snap_veo_duration(max(min(VEO_VALID_DURATIONS), limit - sum(values[:-1])))
        if sum(values) > limit:
            values.pop()
    if not values:
        values = [float(min(VEO_VALID_DURATIONS))]
    assert_duration(sum(values), "restructured timeline")
    return values
