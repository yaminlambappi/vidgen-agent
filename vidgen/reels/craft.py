"""Watchability gate — a reel that nobody would stop for must not reach Veo."""
from __future__ import annotations

from typing import List

from vidgen.reels.constants import AD_LIKE_TYPES
from vidgen.reels.language import has_bengali, is_garbled_caption
from vidgen.reels.schemas import ReelJob
from vidgen.reels.watchability import score_watchability


class CraftRejected(ValueError):
    """Plan is technically valid and still not worth posting."""


_HOOK_TOKENS = (
    "face", "close-up", "closeup", "hook", "punch", "action", "reaction",
    "interview", "object", "conflict", "question", "first frame",
)


def critique_plan(job: ReelJob) -> List[str]:
    issues: List[str] = []
    brief = job.brief
    script = job.script
    board = job.storyboard
    product = job.product_bible
    if not brief:
        return ["missing brief"]
    if not script or not (script.hook_line or "").strip():
        issues.append("no hook line — first 0.7s has nothing to say")
    if script and is_garbled_caption(script.full_text, brief.language):
        issues.append("script is garbled or not in the requested language")
    if brief.language.startswith("bengali") and script and not has_bengali(script.full_text):
        issues.append("Bengali reel has no Bengali speech")
    if not board or not board.shots:
        issues.append("no storyboard")
    else:
        first = board.shots[0]
        blob = " ".join([first.action or "", first.camera or "", first.framing or "", first.purpose or ""]).lower()
        if not any(k in blob for k in _HOOK_TOKENS + ("weather", "rain", "street", "first frame")):
            issues.append("first shot does not open on a face or hook")
        if "back" in (first.action or "").lower() and "face" not in blob:
            issues.append("first shot can open on a back")
        bible_ids = {c.character_id for c in job.character_bible}
        used = set()
        for s in board.shots:
            used.update(s.characters)
        if used - bible_ids:
            issues.append("shot uses a character that is not in the bible")
        if brief.talking_head and not first.talking_head and first.generation_strategy != "compose":
            issues.append("talking-head reel but first shot is silent VO")
        if brief.creative_type == "COMEDY" and "product truth" in (brief.narrative_structure or ""):
            issues.append("comedy forced into an ad structure")
    if product and product.required:
        shape = (product.shape or "").lower()
        if "wine" in shape and "never" not in shape:
            issues.append("product bible allows a wine bottle")
        kind = (product.name or "").lower()
        perfume = "perfume" in kind or "atomizer" in shape or "fragrance" in kind
        if perfume and "atomizer" not in shape and "spray" not in shape:
            issues.append("perfume is not locked to an atomizer")
    if brief.talking_head and script:
        if not any(line.on_camera for line in script.body_lines):
            issues.append("talking-head script has no on-camera line")
    if not (brief.why_watch or "").strip():
        issues.append("brief has no reason to stop scrolling")
    if brief.needs_cta and script and not (script.cta_line or brief.cta):
        issues.append("ad missing CTA")
    if brief.creative_type in AD_LIKE_TYPES and brief.needs_product and product and not product.name:
        issues.append("ad has no product identity")
    return issues


def assert_watchable(job: ReelJob) -> None:
    issues = critique_plan(job)
    score = job.watchability or score_watchability(job)
    fatal = [
        i for i in issues
        if any(k in i for k in (
            "garbled", "no Bengali", "no hook", "wine", "not locked to an atomizer",
            "open on a face", "no storyboard", "missing brief", "ad structure",
        ))
    ]
    if not score.passed:
        fatal.append(f"watchability {score.total} below threshold")
    if fatal:
        raise CraftRejected("; ".join(fatal))
