"""One Gemini write turns a reflection into a timed Sufi short plan."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

REQUIRED_TAGS = ("#Sufism", "#SpiritualReminders", "#Tasawwuf", "#SchoolOfSufi")
WORDS_PER_SECOND = 2.1
AESTHETIC = (
    "Cinematic 8k, dark atmospheric Sufi aesthetic, intense volumetric lighting, "
    "ultra slow motion, wajd atmosphere, vertical 9:16, no text, no watermarks"
)


class PlanError(ValueError):
    """The model returned a plan that cannot be filmed as specified."""


class SufiPlan(BaseModel):
    script_text: str
    veo_prompts: list[str]
    caption_and_hashtags: str
    duration_seconds: int
    slots: list[int] = Field(default_factory=list)


def choose_duration(thought: str) -> int:
    """Short reflections become 30s. Longer ones become 60s. Nothing in between."""
    words = len((thought or "").split())
    return 60 if words > 45 else 30


def word_budget(duration: int) -> int:
    return int(duration * WORDS_PER_SECOND)


def speech_seconds(text: str) -> float:
    words = len((text or "").split())
    if words == 0:
        return 0.0
    return words / WORDS_PER_SECOND


MOTIONS = (
    "Slow camera pan across a deep indigo night as swirling mist hides a lone seeker",
    "Macro drift through glowing embers while light particles whirl in the dark",
    "Slow dolly toward stars reflected on black water, mist moving over the surface",
    "Gentle crane rise as volumetric rays pierce an indigo sky above a waiting silhouette",
    "Tracking drift past a low fire, sparks turning like a whirling hem of light",
    "Slow tilt through darkness until one shaft of light finds a bowed figure",
)

_MOTION_TOKENS = (
    "pan", "dolly", "splash", "tracking", "tilt", "crane", "drift",
    "macro", "slow-motion", "slow motion", "sweep", "camera",
)


def prompt_count_for(duration: int) -> int:
    """30s asks for 6 pictures at 5s. 60s keeps a short pace with 10 pictures."""
    return 6 if duration == 30 else 10


def _allowed_counts(total: int) -> range:
    if total == 30:
        return range(4, 7)
    return range(8, 13)


def slot_durations(total: int, count: int) -> list[int]:
    """Split a 30s or 60s master into short integer slots that sum to the total.

    A 30s film uses 4, 5, or 6 pictures (about 5–8s each). A 60s film uses
    8–12 pictures at the same pace. Remainder seconds are handed out one at a
    time, so the sum cannot come up short.
    """
    if total not in (30, 60):
        raise PlanError(f"duration must be 30 or 60, got {total}")
    if count not in _allowed_counts(total):
        raise PlanError(f"a {total}s short cannot use {count} pictures")
    base, rem = divmod(total, count)
    slots = [base + (1 if i < rem else 0) for i in range(count)]
    if sum(slots) != total or any(s < 5 or s > 8 for s in slots):
        raise PlanError(f"slot split failed for {total}s x {count}: {slots}")
    return slots


def system_prompt(duration: int, prompt_count: int) -> str:
    words = word_budget(duration)
    return (
        "You are not a teacher or preacher. "
        "You are a humble Sufi Ashiq, in the spirit of the poetry sung by Abida Parveen, Amir Khusrau, and Rumi. "
        "You do NOT give advice or tell people what to do. "
        "You speak directly to Allah (Maula, Ya Rabb, Ya Lateef) in deep longing, secrecy, awe, and total surrender. "
        "The listener is overhearing a secret conversation, a munajat, not a lesson. "
        "Speak of divine attributes, secret longing (israr), tears of devotion, light over darkness, "
        "seeking Allah's glance (nazar), and total helplessness before His majesty. "
        "Language: poetic, rhythmic, intimate English with a few Urdu-transliterated whispers. "
        "No commands to the audience. No explanations. No selling. Do not invent quotations from scripture. "
        "Return only JSON with keys script_text, veo_prompts, caption_and_hashtags. "
        "script_text is the whisper itself: no stage directions, no speaker labels. "
        f"It must be readable aloud in about {duration} seconds, about {words} words, and no more. "
        f"veo_prompts is an array of exactly {prompt_count} strings, each one moving shot of about 5 seconds. "
        "Show deep indigo night, swirling mist, glowing embers, rays piercing darkness, "
        "stars on dark water, a lone seeker in longing, whirling particles of light. "
        "Name a camera move: pan, dolly, tilt, crane, tracking drift, or macro. "
        "No on-screen writing, no logos, no violence, no crowd. "
        "caption_and_hashtags is the quiet post text and must include "
        "#Sufism #SpiritualReminders #Tasawwuf #SchoolOfSufi."
    )


def user_prompt(thought: str, duration: int, prompt_count: int) -> str:
    return (
        f"Reflection:\n{thought.strip()}\n\n"
        f"Turn this into a {duration}-second secret whispered to Allah. "
        f"Use exactly {prompt_count} moving visual prompts. Do not advise the listener."
    )


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise PlanError("model did not return JSON") from None
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            raise PlanError("model JSON could not be parsed") from exc
    if not isinstance(data, dict):
        raise PlanError("model JSON must be an object")
    return data


def _ensure_tags(caption: str) -> str:
    text = (caption or "").strip()
    lowered = text.lower()
    missing = [tag for tag in REQUIRED_TAGS if tag.lower() not in lowered]
    if missing:
        text = (text + "\n\n" + " ".join(missing)).strip()
    return text


def _lock_prompt(prompt: str, index: int) -> str:
    body = " ".join((prompt or "").split())
    if not any(token in body.lower() for token in _MOTION_TOKENS):
        body = f"{MOTIONS[index % len(MOTIONS)]}. {body}"
    if AESTHETIC.lower() not in body.lower():
        body = f"{body} {AESTHETIC}"
    return body


def parse_plan(payload: str | dict, duration: int) -> SufiPlan:
    data = payload if isinstance(payload, dict) else _extract_json(payload)
    script = " ".join(str(data.get("script_text") or "").split())
    if len(script.split()) < 8:
        raise PlanError("script_text is empty or too short to speak")
    prompts = data.get("veo_prompts") or []
    if isinstance(prompts, str):
        prompts = [prompts]
    prompts = [_lock_prompt(str(p), i) for i, p in enumerate(prompts) if str(p).strip()]
    allowed = _allowed_counts(duration)
    if len(prompts) > allowed.stop - 1:
        prompts = prompts[: allowed.stop - 1]
    if len(prompts) not in allowed:
        raise PlanError(
            f"veo_prompts must contain {allowed.start} to {allowed.stop - 1} moving pictures"
        )
    if speech_seconds(script) > duration + 1:
        raise PlanError(
            f"script is {speech_seconds(script):.0f}s of speech for a {duration}s film"
        )
    caption = _ensure_tags(str(data.get("caption_and_hashtags") or script))
    return SufiPlan(
        script_text=script,
        veo_prompts=prompts,
        caption_and_hashtags=caption,
        duration_seconds=duration,
        slots=slot_durations(duration, len(prompts)),
    )


def offline_plan(thought: str, duration: int) -> SufiPlan:
    """Used only when generation is not in production. The live path calls Gemini."""
    core = " ".join((thought or "").split())
    script = (
        f"Ya Rabb, {core.rstrip('.')}. "
        "Maula, this secret is only for You. "
        "Ya Lateef, I have nothing but this longing and these quiet tears. "
        "If You glance once, the dark itself becomes light."
    )
    budget = word_budget(duration)
    words = script.split()
    if len(words) > budget:
        script = " ".join(words[:budget])
    scenes = (
        "indigo night and a lone silhouette in longing",
        "swirling mist over dark water holding stars",
        "glowing embers in an empty stone shrine",
        "volumetric rays piercing total darkness",
        "whirling motes of light around a bowed figure",
        "a low fire reflected in black water",
        "mist crossing an empty courtyard at night",
        "one lamp surviving inside a dark arch",
        "sparks rising like a slow whirling hem",
        "starlight trembling on a still pool",
    )
    count = prompt_count_for(duration)
    return parse_plan(
        {
            "script_text": script,
            "veo_prompts": [scenes[i % len(scenes)] for i in range(count)],
            "caption_and_hashtags": script,
        },
        duration,
    )
