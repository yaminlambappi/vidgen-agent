"""Machine-readable watchability. Plan-time and post-render, not a vanity score."""
from __future__ import annotations

from typing import List

from vidgen.reels.constants import AD_LIKE_TYPES
from vidgen.reels.language import has_bengali, is_garbled_caption
from vidgen.reels.schemas import ReelJob, WatchabilityScore


_HOOK_TOKENS = (
    "face", "close-up", "closeup", "hook", "punch", "action", "reaction",
    "interview", "object", "conflict", "question", "surprise", "first frame",
)


def score_watchability(job: ReelJob) -> WatchabilityScore:
    notes: List[str] = []
    brief = job.brief
    script = job.script
    board = job.storyboard
    ctype = (brief.creative_type if brief else "") or ""

    hook = 0.2
    if script and (script.hook_line or "").strip():
        hook = 0.75
        if board and board.shots:
            blob = " ".join([
                board.shots[0].action or "", board.shots[0].camera or "",
                board.shots[0].framing or "", board.shots[0].purpose or "",
            ]).lower()
            if any(t in blob for t in _HOOK_TOKENS):
                hook = 0.92
            else:
                notes.append("first shot may not earn the first second")
                hook = 0.4
    else:
        notes.append("no hook line")

    clarity = 0.3
    if script and script.full_text.strip():
        clarity = 0.85 if len(script.body_lines) <= 6 else 0.6
        if brief and is_garbled_caption(script.full_text, brief.language):
            clarity = 0.1
            notes.append("script language/garbled")

    pacing = 0.5
    if board:
        total = board.total_duration or 0
        if 5.5 <= total <= 30.0:
            pacing = 0.8
        if brief and abs(total - float(brief.duration_seconds)) <= 8:
            pacing = 0.88

    visual = 0.7 if board and board.shots else 0.2
    payoff = 0.55
    if script and script.body_lines:
        last = (script.body_lines[-1].text or "").strip()
        if last and last != (script.hook_line or "").strip():
            payoff = 0.8
        if ctype in AD_LIKE_TYPES and brief and brief.needs_cta and not (script.cta_line or "").strip():
            payoff = 0.35
            notes.append("ad missing CTA")

    audio_q = 0.7
    if job.audio_plan and (job.audio_plan.music_mood or "").lower() in {"sine", "stub"}:
        audio_q = 0.1
        notes.append("placeholder music")
    if brief and brief.talking_head:
        audio_q = 0.82

    lang_q = 0.4
    if brief and script:
        if brief.language.startswith("bengali"):
            lang_q = 0.9 if has_bengali(script.full_text) else 0.05
        else:
            lang_q = 0.85 if not is_garbled_caption(script.full_text, brief.language) else 0.1

    consistency = 0.8
    if board and job.character_bible:
        ids = {c.character_id for c in job.character_bible}
        used = set()
        for s in board.shots:
            used.update(s.characters)
        if used - ids:
            consistency = 0.2
            notes.append("unknown character on a shot")
        wardrobes = {s.wardrobe for s in board.shots if s.wardrobe}
        if len(wardrobes) > len(job.character_bible):
            consistency = min(consistency, 0.45)
            notes.append("wardrobe not locked")

    social = 0.75 if ctype and ctype != "ADVERTISEMENT" else 0.7
    if brief and brief.narrative_structure and "product truth" in brief.narrative_structure and ctype == "COMEDY":
        social = 0.2
        notes.append("comedy forced into an ad structure")

    weights = (hook, clarity, pacing, visual, payoff, audio_q, lang_q, consistency, social)
    total = round(sum(weights) / len(weights), 3)
    score = WatchabilityScore(
        hook_strength=hook, clarity=clarity, pacing=pacing,
        visual_interest=visual, payoff=payoff, audio_quality=audio_q,
        language_quality=lang_q, asset_consistency=consistency,
        social_native_quality=social, total=total,
        passed=total >= 0.62 and hook >= 0.5 and lang_q >= 0.5,
        notes=notes,
    )
    job.watchability = score
    return score
