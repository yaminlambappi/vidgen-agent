"""One Gemini write turns a reflection into one 30-second munajat."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from vidgen.config import settings

REQUIRED_TAGS = ("#Sufism", "#SpiritualReminders", "#Tasawwuf", "#SchoolOfSufi")
ROLES = ("arrival", "ache", "longing", "surrender", "nearness", "release")
SHOT_COUNT = 6
SLOT_SECONDS = 5
MASTER_SECONDS = 30
# Orthographic syllables per second at speaking_rate 1.0. Tuned so a short
# munajat line fits in a beat and a repeated lecture does not.
SYLLABLES_PER_SECOND = 7.5

RESOLVING = {
    "peace", "peaceful", "surrender", "surrendered", "hope", "hopeful",
    "silence", "silent", "closeness", "release", "acceptance", "accepted",
    "শান্তি", "সমর্পণ", "আশা", "নীরবতা", "নিকট",
}
_STILL_OPENING = (
    "fade from black", "fade-from-black", "fades from black", "fade in from black",
    "fades in", "fade in", "static establishing", "begins still", "starts still",
    "still opening", "complete stillness", "delayed action", "holds still",
)
_MOTION = (
    "pan", "dolly", "tilt", "crane", "drift", "tracking", "push-in", "push in",
    "orbit", "handheld", "rack focus", "swirl", "swirling", "falling", "rising",
    "moving", "sweeps", "sweep", "travels", "glides", "blows", "ripples",
    "rushes", "reveals",
)
_BANS = (
    "face of allah", "allah's face", "allahs face", "depiction of allah",
    "glowing calligraphy", "arabic calligraphy", "glowing arabic",
    "floating mosque", "floating quran", "quran floating",
    "divine human", "divine face",
)
_IDENTITY = (
    "environment", "lighting_direction", "palette", "subject",
    "camera_language", "atmosphere",
)
_HASANTA = "\u09cd"
_VOWEL_SIGNS = set("\u09be\u09bf\u09c0\u09c1\u09c2\u09c3\u09c7\u09c8\u09cb\u09cc\u09d7")


class PlanError(ValueError):
    """The model returned a plan that cannot be filmed as specified."""


class VisualBible(BaseModel):
    world: str
    time: str
    environment: str
    lighting: str
    lighting_direction: str
    palette: str
    texture: str
    atmosphere: str
    subject: str
    camera_language: str
    recurring_elements: list[str]
    spiritual_symbolism: str
    continuity_rules: list[str]


class Beat(BaseModel):
    index: int
    role: str
    phrase: str
    glyph: str
    emotion: str
    visual_intention: str
    veo_prompt: str


class SufiPlan(BaseModel):
    emotional_core: str
    spiritual_direction: list[str]
    visual_bible: VisualBible
    beats: list[Beat]
    script_text: str
    veo_prompts: list[str]
    caption_and_hashtags: str
    duration_seconds: int
    slots: list[int] = Field(default_factory=list)


def choose_duration(thought: str) -> int:
    """Every reflection becomes one 30-second film."""
    return MASTER_SECONDS


def prompt_count_for(duration: int) -> int:
    if duration != MASTER_SECONDS:
        raise PlanError(f"duration must be {MASTER_SECONDS}, got {duration}")
    return SHOT_COUNT


def slot_durations(total: int, count: int) -> list[int]:
    """The only legal film is six shots of five seconds."""
    if total != MASTER_SECONDS or count != SHOT_COUNT:
        raise PlanError(f"a short is {SHOT_COUNT} shots of {SLOT_SECONDS} seconds, got {total}s x {count}")
    return [SLOT_SECONDS] * SHOT_COUNT


def bangla_syllables(text: str) -> int:
    """Count orthographic syllables. A hasanta joins consonants into one cluster."""
    count = 0
    for ch in text or "":
        code = ord(ch)
        if ch == _HASANTA:
            if count > 0:
                count -= 1
            continue
        if ch in _VOWEL_SIGNS:
            continue
        independent = 0x0985 <= code <= 0x0994
        consonant = (
            0x0995 <= code <= 0x09B9
            or code in (0x09DC, 0x09DD, 0x09DF, 0x09F0, 0x09F1)
        )
        if independent or consonant:
            count += 1
    return count


def speech_seconds(text: str, rate: float | None = None) -> float:
    spoken = rate if rate is not None else settings.TTS_SPEAKING_RATE
    if spoken <= 0:
        raise PlanError("speaking rate must be positive")
    syllables = bangla_syllables(text)
    if syllables == 0:
        return 0.0
    return syllables / (SYLLABLES_PER_SECOND * spoken)


def _bangla_count(text: str) -> int:
    return sum(1 for ch in text or "" if "\u0980" <= ch <= "\u09ff")


def _latin_count(text: str) -> int:
    return sum(1 for ch in text or "" if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))


def _has_bangla(text: str) -> bool:
    return _bangla_count(text) > 0


def prompts_share_identity(prompts: list[str], bible: VisualBible) -> bool:
    """True only when every prompt carries the whole identity, not merely the world name."""
    keys = [getattr(bible, name) for name in _IDENTITY]
    keys.extend(bible.recurring_elements)
    if not keys or any(not str(key).strip() for key in keys):
        return False
    return all(all(str(key) in prompt for key in keys) for prompt in prompts)


def system_prompt(duration: int, prompt_count: int) -> str:
    return (
        "You are a humble Sufi lover speaking privately to Allah, in the spirit of the poetry "
        "sung by Abida Parveen, Amir Khusrau, and Rumi. You do not teach, preach, advise, or explain. "
        "The listener is overhearing one munajat. "
        "From the thought, decide one emotional_core, a spiritual_direction of exactly six steps, "
        "and one visual_bible. Then write exactly six beats that live inside that bible. "
        "The six beats are one emotional arc, in this order: "
        "arrival with motion already in the first frame, inner ache, longing, surrender that grows quieter, "
        "nearness, and a peaceful release. "
        "All six shots share one world, one time of day, one lighting direction, one palette, "
        "one subject, one camera language, one atmosphere, and the same recurring elements. "
        "Do not illustrate the arc with six unrelated motifs. "
        "Prefer light, silence, weather, water, cloth, architecture, and a distant human presence. "
        "If a person appears, show a silhouette, hands, a back, or a distant figure. No faces. "
        "No depiction of Allah, no glowing calligraphy, no floating mosque, no floating Quran, "
        "no divine human figure, no invented scripture. "
        "Language of each phrase: original spoken Bangla addressed to Allah. "
        "Use মাওলা, never মৌলা. Simple and intimate. Not a translation, not a lecture, not a slogan. "
        "About six to eight words is a hint, not a quota. A short line is welcome. "
        "Each phrase must be speakable in under five seconds at a slow pace. "
        "Each glyph is one to three Bangla words, a quiet thought, not the whole line. "
        "Each veo_prompt is a cinematic shot with physical motion and a camera move that serves the emotion: "
        "subject, action, motion, camera, light, atmosphere. "
        "Shot 1 must already be moving. No fade from black, no still opening, no delayed action. "
        "Shot 6 must settle into a calm final frame. "
        "Return only JSON with keys emotional_core, spiritual_direction, visual_bible, beats, caption_and_hashtags. "
        "visual_bible has world, time, environment, lighting, lighting_direction, palette, texture, "
        "atmosphere, subject, camera_language, recurring_elements (at least two), spiritual_symbolism, "
        "continuity_rules (at least one, tying the ending back toward the opening). "
        f"beats has exactly {prompt_count} objects with phrase, glyph, emotion, visual_intention, veo_prompt. "
        "Neighboring beats must not share the same emotion. "
        "caption_and_hashtags is the quiet post text and must include "
        "#Sufism #SpiritualReminders #Tasawwuf #SchoolOfSufi. "
        f"The film is {duration} seconds. No on-screen writing in the pictures."
    )


def user_prompt(thought: str, duration: int, prompt_count: int) -> str:
    return (
        f"Thought:\n{thought.strip()}\n\n"
        f"Make one {duration}-second Bangla munajat in one visual world. "
        f"Use exactly {prompt_count} beats of one continuous prayer. Do not advise the listener."
    )


def critic_system_prompt() -> str:
    return (
        "You review a Sufi short before any picture or voice is generated. "
        "Pass only if this is one film: the emotion moves, the six shots share one visual identity "
        "(environment, lighting direction, palette, subject, camera language, atmosphere, recurring elements), "
        "each shot advances the prayer, the last beat releases, the Bangla sounds spoken rather than translated, "
        "and nothing is a lecture, a slogan, or a visual cliché. "
        "Return only JSON with keys pass (boolean) and reasons (array of short strings). "
        "reasons is empty when pass is true."
    )


def critic_user_prompt(plan: SufiPlan) -> str:
    payload = {
        "emotional_core": plan.emotional_core,
        "spiritual_direction": plan.spiritual_direction,
        "visual_bible": plan.visual_bible.model_dump(),
        "beats": [beat.model_dump() for beat in plan.beats],
    }
    return json.dumps(payload, ensure_ascii=False)


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


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _as_list(value: Any, label: str) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        raise PlanError(f"{label} must be a list")
    return [_clean(item) for item in value if _clean(item)]


def _bible(data: dict[str, Any]) -> VisualBible:
    raw = data.get("visual_bible")
    if not isinstance(raw, dict):
        raise PlanError("visual_bible is required")
    fields = {
        "world": _clean(raw.get("world")),
        "time": _clean(raw.get("time")),
        "environment": _clean(raw.get("environment")),
        "lighting": _clean(raw.get("lighting")),
        "lighting_direction": _clean(raw.get("lighting_direction")),
        "palette": _clean(raw.get("palette")),
        "texture": _clean(raw.get("texture")),
        "atmosphere": _clean(raw.get("atmosphere")),
        "subject": _clean(raw.get("subject")),
        "camera_language": _clean(raw.get("camera_language")),
        "spiritual_symbolism": _clean(raw.get("spiritual_symbolism")),
    }
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise PlanError("visual identity is incomplete: " + ", ".join(missing))
    recurring = _as_list(raw.get("recurring_elements"), "recurring_elements")
    rules = _as_list(raw.get("continuity_rules"), "continuity_rules")
    if len(recurring) < 2:
        raise PlanError("visual identity needs at least two recurring elements")
    if not rules:
        raise PlanError("visual identity needs a continuity rule")
    return VisualBible(**fields, recurring_elements=recurring, continuity_rules=rules)


def _emotion_tokens(emotion: str) -> set[str]:
    return set(re.findall(r"[a-z]+", emotion.casefold()))


def _resolves(emotion: str) -> bool:
    tokens = _emotion_tokens(emotion)
    if tokens & RESOLVING:
        return True
    return any(word in emotion for word in RESOLVING if not word.isascii())


def _glyph_ok(glyph: str) -> bool:
    words = [word for word in glyph.split() if word]
    if not 1 <= len(words) <= 3:
        return False
    return all(_has_bangla(word) for word in words)


def _banned(text: str) -> str | None:
    lowered = text.casefold()
    for phrase in _BANS:
        if phrase in lowered:
            return phrase
    return None


def _still_opening(text: str) -> str | None:
    lowered = text.casefold()
    for phrase in _STILL_OPENING:
        if phrase in lowered:
            return phrase
    return None


def _has_motion(text: str) -> bool:
    lowered = text.casefold()
    return any(token in lowered for token in _MOTION)


def lock_veo_prompt(bible: VisualBible, action: str, index: int) -> str:
    """Carry the full visual identity into every shot. The world name alone is not continuity."""
    parts = [
        f"Environment: {bible.environment}.",
        f"Lighting direction: {bible.lighting_direction}.",
        f"Palette: {bible.palette}.",
        f"Subject: {bible.subject}.",
        f"Camera language: {bible.camera_language}.",
        f"Atmosphere: {bible.atmosphere}.",
        "Recurring elements: " + "; ".join(bible.recurring_elements) + ".",
        (
            f"World: {bible.world}. Time: {bible.time}. "
            f"Lighting: {bible.lighting}. Texture: {bible.texture}."
        ),
        action.strip(),
    ]
    if index == 1:
        parts.append("Visible motion is already happening in the first 400 milliseconds.")
    if index == 6:
        parts.append("The motion settles into a calm final frame.")
    parts.append("Vertical 9:16, no text, no logos, no watermark.")
    return " ".join(part for part in parts if part)


def parse_plan(payload: str | dict, duration: int) -> SufiPlan:
    if duration != MASTER_SECONDS:
        raise PlanError(f"duration must be {MASTER_SECONDS}, got {duration}")
    data = payload if isinstance(payload, dict) else _extract_json(payload)
    core = _clean(data.get("emotional_core"))
    if not core:
        raise PlanError("emotional_core is required")
    direction = _as_list(data.get("spiritual_direction"), "spiritual_direction")
    if len(direction) != SHOT_COUNT:
        raise PlanError(f"spiritual_direction must have {SHOT_COUNT} steps")
    bible = _bible(data)
    raw_beats = data.get("beats")
    if not isinstance(raw_beats, list):
        raise PlanError("beats must be a list of six")
    if len(raw_beats) != SHOT_COUNT:
        raise PlanError(f"beats must contain {SHOT_COUNT} pictures")

    beats: list[Beat] = []
    for index, raw in enumerate(raw_beats, start=1):
        if not isinstance(raw, dict):
            raise PlanError(f"beat {index} is not an object")
        phrase = _clean(raw.get("phrase"))
        glyph = _clean(raw.get("glyph"))
        emotion = _clean(raw.get("emotion"))
        intention = _clean(raw.get("visual_intention"))
        action = _clean(raw.get("veo_prompt"))
        if not emotion or not intention:
            raise PlanError(f"beat {index} needs an emotion and a visual intention")
        if "মৌলা" in phrase or "মৌলা" in glyph:
            raise PlanError("মৌলা is not permitted; the epithet is মাওলা")
        if not _has_bangla(phrase) or _latin_count(phrase) > _bangla_count(phrase):
            raise PlanError(f"beat {index} must be spoken Bangla")
        if speech_seconds(phrase) > SLOT_SECONDS:
            raise PlanError(f"beat {index} is longer than {SLOT_SECONDS} seconds of speech")
        if not _glyph_ok(glyph):
            raise PlanError(f"beat {index} glyph must be one to three Bangla words")
        if not _has_motion(action):
            raise PlanError(f"beat {index} has no physical motion")
        banned = _banned(action) or _banned(intention)
        if banned:
            raise PlanError(f"beat {index} uses banned imagery ({banned})")
        if index == 1:
            still = _still_opening(action)
            if still:
                raise PlanError(f"shot 1 depends on a still opening ({still})")
        if index == SHOT_COUNT and not _resolves(emotion):
            raise PlanError("the final shot has no emotional resolution")
        if index > 1 and emotion.casefold() == beats[-1].emotion.casefold():
            raise PlanError(f"beats {index - 1} and {index} share the same emotion")
        beats.append(Beat(
            index=index,
            role=ROLES[index - 1],
            phrase=phrase,
            glyph=glyph,
            emotion=emotion,
            visual_intention=intention,
            veo_prompt=lock_veo_prompt(bible, f"{intention} {action}", index),
        ))

    prompts = [beat.veo_prompt for beat in beats]
    if not prompts_share_identity(prompts, bible):
        raise PlanError("the six shots do not share one visual identity")
    script = " ".join(beat.phrase for beat in beats)
    caption = _ensure_tags(_clean(data.get("caption_and_hashtags")) or script)
    return SufiPlan(
        emotional_core=core,
        spiritual_direction=direction,
        visual_bible=bible,
        beats=beats,
        script_text=script,
        veo_prompts=prompts,
        caption_and_hashtags=caption,
        duration_seconds=MASTER_SECONDS,
        slots=slot_durations(MASTER_SECONDS, SHOT_COUNT),
    )


def parse_critic(payload: str | dict) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else _extract_json(payload)
    reasons = data.get("reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, list):
        raise PlanError("critic reasons must be a list")
    return {"pass": bool(data.get("pass")), "reasons": [_clean(item) for item in reasons if _clean(item)]}


_OFFLINE_BIBLE = {
    "world": "moonlit shrine courtyard",
    "time": "deep night",
    "environment": "an old stone courtyard with one brass lamp and wet flagstones",
    "lighting": "low warm lamp against cool moonlight",
    "lighting_direction": "warm light from a low lamp at frame left, cool moon from above",
    "palette": "indigo, lamp gold, and wet stone gray",
    "texture": "damp stone, thin mist, and soft cloth",
    "atmosphere": "quiet mist moving left to right through an empty courtyard",
    "subject": "a distant bowed silhouette in a dark shawl, face unseen",
    "camera_language": "slow 35mm moves with no snap zooms",
    "recurring_elements": ["the same brass lamp", "mist moving left to right"],
    "spiritual_symbolism": "light finding a tired heart, without sacred props",
    "continuity_rules": [
        "The closing mist continues the opening dark so a replay feels like the same night.",
    ],
}

_OFFLINE_BEATS = (
    ("মাওলা, এই রাতের প্রথম আলো তোমার।", "মাওলা", "calling", "The lamp flame is already leaping.",
     "Mist rushes across the wet stone as the flame moves. Slow dolly in."),
    ("ইয়া রব্ব, আমার ক্লান্ত হৃদয় তুমি জানো।", "ক্লান্ত হৃদয়", "ache", "The distance feels close and sore.",
     "The silhouette draws one hand inward while mist drifts. Gentle push-in."),
    ("তোমার দিকেই আমার সব আকুতি।", "আকুতি", "longing", "The courtyard opens toward the light.",
     "The camera travels through mist toward the lamp glow. Slow crane rise."),
    ("আমি দুর্বল, তবু তোমার কাছেই সঁপে দিলাম।", "সমর্পণ", "surrender", "Warmth spreads without getting louder.",
     "Cloth ripples once and then settles beside the lamp. Slow orbit."),
    ("তুমি কাছে থাকলে নীরবতাও শান্তি।", "নীরবতা", "closeness", "The lamp and the moon meet.",
     "Starlight ripples in a shallow pool of rain. Gentle tilt up."),
    ("মাওলা, আজ নিজেকে তোমার হাতে রাখলাম।", "শান্তি", "peace", "The night exhales and stays.",
     "Mist glides on and the flame steadies into a calm last frame. Slow drift."),
)


def offline_plan(thought: str, duration: int) -> SufiPlan:
    """Used when generation is not in production. The live path calls Gemini."""
    core = " ".join((thought or "").split()) or "a hidden loneliness"
    direction = [
        "a hidden loneliness",
        "calling Allah",
        "admitting the ache",
        "letting the longing widen",
        "surrendering the weight",
        "resting in nearness",
    ]
    beats = [
        {
            "phrase": phrase,
            "glyph": glyph,
            "emotion": emotion,
            "visual_intention": intention,
            "veo_prompt": action,
        }
        for phrase, glyph, emotion, intention, action in _OFFLINE_BEATS
    ]
    return parse_plan(
        {
            "emotional_core": core,
            "spiritual_direction": direction,
            "visual_bible": _OFFLINE_BIBLE,
            "beats": beats,
            "caption_and_hashtags": "একটি নিভৃত মোনাজাত।",
        },
        duration if duration == MASTER_SECONDS else MASTER_SECONDS,
    )
