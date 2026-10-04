"""One Gemini write turns a reflection into a timed Sufi short plan."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

REQUIRED_TAGS = ("#Sufism", "#SpiritualReminders", "#Tasawwuf", "#SchoolOfSufi")
WORDS_PER_SECOND = 2.1
AESTHETIC = (
    "Volumetric light rays piercing soft atmosphere, golden hour lighting, "
    "subtle floating dust particles, sacred geometric aesthetic, 8k cinematic slow motion, "
    "vertical 9:16, no text, no watermarks, no logos, ultra peaceful Sufi mood."
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


def slot_durations(total: int, count: int) -> list[int]:
    """Split a 30s or 60s master into 2 or 3 integer slots that sum to the total.

    30 with 2 prompts is 15+15. 60 with 2 prompts is 30+30.
    Remainder seconds are handed out one at a time, so the sum cannot come up short.
    """
    if total not in (30, 60):
        raise PlanError(f"duration must be 30 or 60, got {total}")
    if count not in (2, 3):
        raise PlanError(f"a short uses 2 or 3 pictures, got {count}")
    base, rem = divmod(total, count)
    slots = [base + (1 if i < rem else 0) for i in range(count)]
    if sum(slots) != total or any(s <= 0 for s in slots):
        raise PlanError(f"slot split failed for {total}s x {count}")
    return slots


def system_prompt(duration: int, prompt_count: int) -> str:
    words = word_budget(duration)
    return (
        "You are the writer for the School of Sufi channel. "
        "Turn one personal reflection into a short video plan. "
        "Tone: quiet, respectful, spiritually uplifting. "
        "Rooted in tasawwuf: sincerity (ikhlas) and remembrance (dhikr). "
        "Do not preach. Do not attack other traditions. "
        "Do not invent quotations from scripture. Do not sell anything. "
        "Return only JSON with keys script_text, veo_prompts, caption_and_hashtags. "
        "script_text is spoken voiceover: plain sentences, no stage directions, no speaker labels. "
        f"It must be readable aloud in about {duration} seconds at a slow pace, about {words} words, and no more. "
        f"veo_prompts is an array of exactly {prompt_count} strings. "
        "Each describes one peaceful moving image: geometry, candlelight, water, a manuscript, a quiet courtyard, dusk. "
        "No on-screen writing, no logos, no violence. "
        "caption_and_hashtags is the social caption and must include "
        "#Sufism #SpiritualReminders #Tasawwuf #SchoolOfSufi."
    )


def user_prompt(thought: str, duration: int, prompt_count: int) -> str:
    return (
        f"Reflection:\n{thought.strip()}\n\n"
        f"Write the {duration}-second School of Sufi short. "
        f"Use exactly {prompt_count} visual prompts."
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


def _lock_prompt(prompt: str) -> str:
    body = " ".join((prompt or "").split())
    if AESTHETIC.lower() in body.lower():
        return body
    return f"{body} {AESTHETIC}"


def parse_plan(payload: str | dict, duration: int) -> SufiPlan:
    data = payload if isinstance(payload, dict) else _extract_json(payload)
    script = " ".join(str(data.get("script_text") or "").split())
    if len(script.split()) < 8:
        raise PlanError("script_text is empty or too short to speak")
    prompts = data.get("veo_prompts") or []
    if isinstance(prompts, str):
        prompts = [prompts]
    prompts = [_lock_prompt(str(p)) for p in prompts if str(p).strip()]
    if len(prompts) > 3:
        prompts = prompts[:3]
    if len(prompts) < 2:
        raise PlanError("veo_prompts must contain 2 or 3 pictures")
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
        f"{core.rstrip('.')}. "
        "Sincerity is quiet work. Remembrance is not a performance. "
        "Let the heart return, without hurry, to what is true."
    )
    budget = word_budget(duration)
    words = script.split()
    if len(words) > budget:
        script = " ".join(words[:budget])
    return parse_plan(
        {
            "script_text": script,
            "veo_prompts": [
                "Serene Islamic geometric tilework, warm candle light, subtle particle motion, golden hour, slow motion",
                "Calm river at twilight, illuminated manuscript art style, peaceful, atmospheric",
            ],
            "caption_and_hashtags": script,
        },
        duration,
    )
