"""Classify an idea into a creative type and a category-native strategy."""
from __future__ import annotations

from typing import List, Tuple

from vidgen.reels.constants import AD_LIKE_TYPES, CREATIVE_TYPES
from vidgen.reels.language import has_bengali
from vidgen.reels.schemas import CreativeType, ReelRequest


# Order matters: more specific intents win over generic product/lifestyle words.
_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    (CreativeType.COMEDY.value, (
        "funny", "comedy", "joke", "skit", "হাস্য", "মজার", "মজা", "comedy reel",
        "উল্টাপাল্টা", "ভুল উত্তর", "prank",
    )),
    (CreativeType.SKIT.value, ("skit", "sketch", "act out", "নাটক")),
    (CreativeType.MEME.value, ("meme", "relatable meme", "pov:", "pov ")),
    (CreativeType.EDUCATIONAL.value, (
        "explain", "explainer", "educational", "learn", "what is", "how does",
        "black hole", "science", "fact about", "বোঝাও", "ব্যাখ্যা", "শেখাও",
    )),
    (CreativeType.EXPLAINER.value, ("explainer", "breakdown", "in 20 seconds", "in 15 seconds explain")),
    (CreativeType.FACT.value, ("fun fact", "did you know", "fact reel", "তথ্য")),
    (CreativeType.PRODUCT_DEMO.value, ("demo", "demonstration", "how to use", "product demonstration", "unbox how")),
    (CreativeType.TUTORIAL.value, ("tutorial", "how to", "step by step", "diy", "শেখাও কিভাবে")),
    (CreativeType.STORY.value, ("story", "mini story", "short story", "গল্প", "kahani")),
    (CreativeType.MOTIVATIONAL.value, ("motivational", "inspire", "mindset", "উদ্যম")),
    (CreativeType.REACTION.value, ("reaction", "responds to", "reacts")),
    (CreativeType.NEWS_STYLE.value, ("news", "announcement", "breaking", "ঘোষণা")),
    (CreativeType.CINEMATIC.value, ("cinematic", "short scene", "film look")),
    (CreativeType.REVIEW.value, ("review", "honest review", "tried this")),
    (CreativeType.TESTIMONIAL.value, ("testimonial", "customer story")),
    (CreativeType.UGC.value, ("ugc", "selfie", "unboxing", "real people")),
    (CreativeType.PROMOTIONAL.value, ("promo", "promotional", "launch", "offer", "buy now", "order now")),
    (CreativeType.ADVERTISEMENT.value, (
        "advertisement", "ad for", "commercial", "বিজ্ঞাপন", "for this perfume",
        "for a premium", "product ad",
    )),
    (CreativeType.LIFESTYLE.value, ("lifestyle", "day in the life", "vlog")),
]


_STRATEGIES = {
    CreativeType.COMEDY.value: ["hook", "setup", "escalation", "punchline"],
    CreativeType.SKIT.value: ["hook", "setup", "escalation", "punchline"],
    CreativeType.MEME.value: ["hook", "setup", "recognizable", "punchline"],
    CreativeType.STORY.value: ["hook", "setup", "conflict", "payoff"],
    CreativeType.EDUCATIONAL.value: ["hook", "question", "explanation", "close"],
    CreativeType.EXPLAINER.value: ["hook", "question", "explanation", "close"],
    CreativeType.FACT.value: ["hook", "question", "explanation", "close"],
    CreativeType.TUTORIAL.value: ["hook", "step", "result", "close"],
    CreativeType.PRODUCT_DEMO.value: ["hook", "step", "result", "close"],
    CreativeType.ADVERTISEMENT.value: ["hook", "problem", "product", "cta"],
    CreativeType.PROMOTIONAL.value: ["hook", "problem", "product", "cta"],
    CreativeType.UGC.value: ["hook", "experience", "proof", "reaction"],
    CreativeType.REVIEW.value: ["hook", "claim", "proof", "take"],
    CreativeType.TESTIMONIAL.value: ["hook", "claim", "proof", "take"],
    CreativeType.CINEMATIC.value: ["hook", "event", "action", "payoff"],
    CreativeType.MOTIVATIONAL.value: ["hook", "tension", "turn", "line"],
    CreativeType.LIFESTYLE.value: ["hook", "moment", "texture", "hold"],
    CreativeType.REACTION.value: ["hook", "stimulus", "reaction", "button"],
    CreativeType.NEWS_STYLE.value: ["hook", "fact", "context", "close"],
    CreativeType.OTHER.value: ["hook", "moment", "turn", "close"],
}


def _blob(req: ReelRequest) -> str:
    return " ".join([
        req.idea or "", req.style or "", req.goal or "", req.content_mode or "",
        req.product_name or "",
    ]).lower()


def classify_intent(req: ReelRequest) -> str:
    if req.content_mode:
        key = req.content_mode.strip().upper().replace(" ", "_").replace("-", "_")
        if key in CREATIVE_TYPES:
            return key
        aliases = {
            "LIFESTYLE_COMMERCIAL": CreativeType.LIFESTYLE.value,
            "DIRECT_RESPONSE_AD": CreativeType.ADVERTISEMENT.value,
            "CINEMATIC_COMMERCIAL": CreativeType.CINEMATIC.value,
            "STORYTELLING": CreativeType.STORY.value,
            "FOUNDER_STYLE": CreativeType.UGC.value,
            "STREET_STYLE": CreativeType.LIFESTYLE.value,
            "INTERVIEW": CreativeType.SKIT.value,
            "PROBLEM_SOLUTION": CreativeType.ADVERTISEMENT.value,
            "BEFORE_AFTER": CreativeType.PRODUCT_DEMO.value,
            "PRODUCT_REVEAL": CreativeType.ADVERTISEMENT.value,
            "EMOTIONAL": CreativeType.STORY.value,
        }
        if key in aliases:
            return aliases[key]
    blob = _blob(req)
    for ctype, keys in _RULES:
        if any(k in blob for k in keys):
            return ctype
    if req.product_name or any(k in blob for k in ("perfume", "serum", "product", "পারফিউম", "সুগন্ধি")):
        return CreativeType.UGC.value
    return CreativeType.OTHER.value


def strategy_beats(creative_type: str, duration: float) -> List[str]:
    beats = list(_STRATEGIES.get(creative_type, _STRATEGIES[CreativeType.OTHER.value]))
    if duration <= 10:
        return [beats[0], beats[-1]] if len(beats) > 1 else beats[:1]
    if duration <= 15:
        if len(beats) > 3:
            return [beats[0], beats[1], beats[-1]]
        return beats
    return beats


def needs_product(creative_type: str, req: ReelRequest) -> bool:
    if req.product_name.strip():
        return True
    if creative_type in AD_LIKE_TYPES:
        blob = _blob(req)
        return any(k in blob for k in (
            "perfume", "product", "serum", "brand", "app", "shoe", "phone",
            "পারফিউম", "সুগন্ধি", "পণ্য",
        )) or bool(req.product_name)
    return False


def needs_cta(creative_type: str) -> bool:
    return creative_type in {
        CreativeType.ADVERTISEMENT.value, CreativeType.PROMOTIONAL.value,
        CreativeType.UGC.value, CreativeType.PRODUCT_DEMO.value,
    }


def content_mode_for(creative_type: str) -> str:
    mapping = {
        CreativeType.ADVERTISEMENT.value: "DIRECT_RESPONSE_AD",
        CreativeType.UGC.value: "UGC",
        CreativeType.COMEDY.value: "COMEDY",
        CreativeType.SKIT.value: "SKIT",
        CreativeType.STORY.value: "STORYTELLING",
        CreativeType.EDUCATIONAL.value: "EDUCATIONAL",
        CreativeType.EXPLAINER.value: "EXPLAINER",
        CreativeType.FACT.value: "FACT",
        CreativeType.TUTORIAL.value: "TUTORIAL",
        CreativeType.REVIEW.value: "REVIEW",
        CreativeType.REACTION.value: "REACTION",
        CreativeType.MEME.value: "MEME",
        CreativeType.LIFESTYLE.value: "STREET_STYLE",
        CreativeType.MOTIVATIONAL.value: "MOTIVATIONAL",
        CreativeType.CINEMATIC.value: "CINEMATIC_COMMERCIAL",
        CreativeType.NEWS_STYLE.value: "NEWS_STYLE",
        CreativeType.PRODUCT_DEMO.value: "PRODUCT_DEMO",
        CreativeType.TESTIMONIAL.value: "TESTIMONIAL",
        CreativeType.PROMOTIONAL.value: "PROMOTIONAL",
        CreativeType.OTHER.value: "UGC",
    }
    return mapping.get(creative_type, "UGC")


def talking_head_for(creative_type: str) -> bool:
    return creative_type in {
        CreativeType.UGC.value, CreativeType.COMEDY.value, CreativeType.SKIT.value,
        CreativeType.MEME.value, CreativeType.REVIEW.value, CreativeType.TESTIMONIAL.value,
        CreativeType.REACTION.value, CreativeType.EDUCATIONAL.value,
        CreativeType.EXPLAINER.value, CreativeType.FACT.value,
        CreativeType.MOTIVATIONAL.value, CreativeType.NEWS_STYLE.value,
        CreativeType.ADVERTISEMENT.value, CreativeType.PROMOTIONAL.value,
    }


def idea_has_locale(req: ReelRequest) -> bool:
    blob = _blob(req)
    return has_bengali(req.idea) or any(k in blob for k in ("bengali", "bangla", "dhaka", "bangladeshi", "বাংলা"))
