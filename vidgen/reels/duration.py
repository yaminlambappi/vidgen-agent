"""Hard duration enforcement and speech timing. Planning must fit before generation."""
from __future__ import annotations

import contextvars
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from vidgen.reels.constants import DIRECTOR_MAX_SECONDS, MAX_DURATION_SECONDS, VEO_VALID_DURATIONS

_DURATION_CAP: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "vidgen_duration_cap", default=None
)


class DurationExceeded(ValueError):
    """Raised when a planned or measured duration exceeds the active cap."""


def current_cap() -> float:
    bound = _DURATION_CAP.get()
    if bound is not None and bound > 0:
        return float(bound)
    return float(MAX_DURATION_SECONDS)


def bind_duration_cap(cap: float) -> contextvars.Token:
    return _DURATION_CAP.set(float(cap))


def reset_duration_cap(token: contextvars.Token) -> None:
    _DURATION_CAP.reset(token)


def job_duration_cap(long_form: bool) -> float:
    return float(DIRECTOR_MAX_SECONDS if long_form else MAX_DURATION_SECONDS)


def is_duration_valid(seconds: float) -> bool:
    """Under the reel cap: 29.9 and 30.0 pass; 30.01 fails. Director cap is higher."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return False
    return 0 < value <= current_cap()


def assert_duration(seconds: float, context: str = "timeline") -> float:
    value = float(seconds)
    if not is_duration_valid(value):
        raise DurationExceeded(
            f"{context} duration {value}s exceeds cap={current_cap()}"
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


def parse_prompt_duration(text: str) -> Optional[float]:
    """Read '45 second', '2 minute', '90s' from a prompt. None if absent."""
    raw = text or ""
    minute = re.search(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|মিনিট)\b", raw, flags=re.I)
    if minute:
        return min(float(minute.group(1)) * 60.0, DIRECTOR_MAX_SECONDS)
    second = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|সেকেন্ড)\b|(?<![A-Za-z])(\d+)\s*s\b",
        raw,
        flags=re.I,
    )
    if second:
        n = second.group(1) or second.group(2)
        return min(float(n), DIRECTOR_MAX_SECONDS)
    return None


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
    valid = tuple(VEO_VALID_DURATIONS) or (4, 6, 8)
    target = max(min(float(seconds), max(valid)), min(valid))
    # Prefer the longer legal length on a tie (5s → 6, not 4).
    return min(valid, key=lambda d: (abs(d - target), -d))


def fill_legal_timeline(target_seconds: float, cap: Optional[float] = None) -> List[int]:
    """Pack 4/6/8 takes until the requested length. This is how 'any length' works."""
    limit = min(float(target_seconds), float(cap if cap is not None else current_cap()))
    valid = sorted(tuple(VEO_VALID_DURATIONS) or (4, 6, 8))
    if limit <= 0:
        raise DurationExceeded("target duration must be positive")
    shots: List[int] = []
    left = limit
    smallest = min(valid)
    while left + 1e-6 >= smallest:
        pick = max(v for v in valid if v <= left + 1e-6)
        shots.append(int(pick))
        left -= pick
    if not shots:
        shots = [smallest]
    if sum(shots) > limit + 1e-6:
        raise DurationExceeded(f"filled timeline {sum(shots)}s exceeds {limit}s")
    return shots


def plan_shot_durations(target_seconds: float, preferred_shots: int = 0) -> List[int]:
    """
    Build a Veo-legal shot duration list whose sum is <= min(target, active cap).
    Short reels: few 6/8s takes. Longer director jobs: pack 8s takes to the request.
    """
    cap = min(float(target_seconds), current_cap())
    if cap <= 0:
        raise DurationExceeded("target duration must be positive")

    valid = sorted(tuple(VEO_VALID_DURATIONS) or (4, 6, 8))
    if cap > 30:
        return fill_legal_timeline(cap, cap)
    want = preferred_shots if preferred_shots > 0 else (2 if cap <= 16 else 3 if cap <= 24 else 4)
    # Few longer takes. Five 4s clips is how faces change and the reel feels empty.
    max_shots = min(4, int(cap // min(valid)) or 1, max(1, want) + 1)
    min_shots = 1
    want = max(min_shots, min(want, max_shots))

    best: List[int] = []
    best_diff = float("inf")
    for n in range(min_shots, max_shots + 1):
        # Prefer counts near `want`
        base = snap_veo_duration(cap / n)
        candidate = [base] * n
        total = sum(candidate)
        # Shrink from the tail using only legal Veo lengths (4/6/8), never 5 or 7.
        while total > cap and candidate:
            idx = len(candidate) - 1
            lower = [v for v in valid if v < candidate[idx]]
            if lower:
                candidate[idx] = max(lower)
            else:
                candidate.pop()
            total = sum(candidate)
        if not candidate:
            continue
        if sum(candidate) > current_cap():
            continue
        diff = (
            abs(sum(candidate) - cap)
            + abs(len(candidate) - want) * 1.6
            + candidate.count(min(valid)) * 0.45
        )
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
    limit = min(float(cap or current_cap()), current_cap())
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


def legalize_shot_durations(durations: Sequence[float], cap: float) -> List[int]:
    """Force every Veo shot onto 4/6/8 and keep the sum <= cap and <= 30."""
    limit = min(float(cap), current_cap())
    valid = set(tuple(VEO_VALID_DURATIONS) or (4, 6, 8))
    if durations and all(int(d) in valid for d in durations) and sum(durations) <= limit:
        return [int(d) for d in durations]
    return [int(d) for d in plan_shot_durations(limit, preferred_shots=len(list(durations)) or 0)]
