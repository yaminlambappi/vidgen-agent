"""Veo prompt compiler — scene prose a DP would give, not a compliance form."""
from __future__ import annotations

from typing import List, Optional

from vidgen.reels.duration import snap_veo_duration
from vidgen.reels.schemas import CharacterSpec, ProductSpec, ReelJob, ReelShot


def compile_shot_prompt(
    shot: ReelShot,
    job: ReelJob,
    previous: Optional[ReelShot] = None,
) -> dict:
    brief = job.brief
    product = job.product_bible or ProductSpec()
    char = _char(job, shot)
    spoken = (shot.dialogue or "").strip()
    talking = bool(shot.talking_head and spoken)

    who = ""
    comedy = bool(brief and brief.creative_type in {"COMEDY", "SKIT"})
    cast = list(job.character_bible or [])
    if comedy and len(cast) >= 2:
        a, b = cast[0], cast[1]
        who = (
            f"TWO people, same take: "
            f"(1) {a.name}, {a.age}, {a.appearance}, hair {a.hair}, wearing {a.wardrobe}, role {a.role}. "
            f"(2) {b.name}, {b.age}, {b.appearance}, hair {b.hair}, wearing {b.wardrobe}, role {b.role}. "
            f"Do not swap their clothes. Do not change either face."
        )
    elif char:
        who = (
            f"{char.name}, {char.age}, {char.appearance}. "
            f"Hair: {char.hair}. Face: {char.face}. Skin: {char.skin}. "
            f"Wearing {char.wardrobe}. Same person for the whole reel."
        )

    perfume = bool(product.required) and (
        "atomizer" in (product.shape or "").lower() or "perfume" in (product.name or "").lower()
    )
    product_lock = ""
    if product.required and product.name and brief and brief.needs_product:
        product_lock = f"{product.name}: {product.shape}. {product.distinctive_details or product.correct_usage}."
        if perfume:
            product_lock += (
                " This is a perfume atomizer with liquid and a spray nozzle. "
                "Forbidden stand-ins: wine bottle, water bottle, empty flask, kitchen glass."
            )
    if product.user_image_authoritative:
        product_lock += " Match the supplied product photo exactly. Do not invent a logo."

    lang = (brief.language if brief else "english")
    he = bool(char and "man" in (char.appearance or "").lower())
    they = "He" if he else "She"
    speak_note = ""
    if comedy and talking:
        tongue = "conversational Bangladeshi Bangla" if str(lang).startswith("bengali") else "casual English"
        speak_note = (
            f"Two distinct voices speak {tongue} to each other, never into a phone. "
            f'Play this exact exchange, no extra lines: "{spoken}". '
            "Mouths must match. Generate native synced audio for both people."
        )
    elif talking:
        tongue = "conversational Bangladeshi Bangla" if str(lang).startswith("bengali") else "casual English"
        speak_note = (
            f"{they} speaks naturally in {tongue}. "
            f'Exact line, no extra words: "{spoken}". Mouth must match the line. Generate native synced audio.'
        )
    else:
        speak_note = f"{they} does not talk. No visible speech, no extra voice."

    hook_rule = ""
    if comedy:
        hook_rule = (
            "SCROLL-STOP: first frame is a third-person two-shot of both men at a desk. "
            "FORBIDDEN: selfie, first-person phone, one face only, empty room, walking in."
        )
    elif shot.purpose.startswith("hook"):
        hook_obj = "the atomizer in their hands" if perfume else "the product in their hands"
        hook_rule = (
            f"SCROLL-STOP: frame 1 must be their face or {hook_obj}. "
            "Never open on a back, a lamp, a wall, or an empty table."
        )

    continuity = ""
    if previous and comedy and len(cast) >= 2:
        continuity = (
            f"SAME two men as the previous shot, same faces, same clothes, same cheap fluorescent office, same desk. "
            f"{cast[0].name} still in {cast[0].wardrobe}. {cast[1].name} still in {cast[1].wardrobe}."
        )
    elif previous and char:
        continuity = (
            f"Same person as the previous shot: same face, hair, {char.wardrobe}, same room, same time of day."
        )

    scene = " ".join(x for x in [
        f"Vertical 9:16 photorealistic phone video, one continuous {int(round(shot.duration))}s take.",
        hook_rule,
        who,
        f"Place: {shot.environment}",
        f"Action: {shot.action}",
        product_lock,
        speak_note,
        f"Camera: {shot.camera}. {shot.framing}. {shot.lens_look}. Motion: {shot.camera_motion}.",
        f"Light: {shot.lighting}. {'Locked third-person, not a selfie.' if comedy else 'Handheld social, not a luxury catalogue.'}",
        continuity,
        "No on-screen text, captions, logos, watermarks, or subtitles.",
        "Real skin texture, real hands, real weight. No plastic beauty filter.",
        "NEVER selfie. NEVER first-person. NEVER a phone held toward the lens." if comedy else "",
    ] if x)

    negs = list(shot.negative_constraints) + [
        "wine bottle", "empty glass bottle", "back of head as first frame",
        "malformed hands", "changed actor", "on-screen text", "garbled letters",
        "selfie", "first-person", "phone selfie arm",
    ]
    refs = []
    if product.reference_uris:
        for uri in product.reference_uris:
            if uri.startswith("gs://"):
                refs.append({
                    "uri": uri,
                    "metadata": {"role": "product_identity", "mime_type": "image/png"},
                })
    if char and char.reference_uri.startswith("gs://"):
        refs.append({
            "uri": char.reference_uri,
            "metadata": {"role": "character_identity", "mime_type": "image/png"},
        })

    shot.generation_prompt = scene
    return {
        "prompt": scene,
        "reference_assets": refs[:3],
        "aspect_ratio": "9:16",
        "duration": snap_veo_duration(shot.duration),
        "generate_audio": talking,
        "negative": " | ".join(negs),
    }


def _char(job: ReelJob, shot: ReelShot) -> Optional[CharacterSpec]:
    if not job.character_bible:
        return None
    if shot.characters:
        for c in job.character_bible:
            if c.character_id in shot.characters:
                return c
    return job.character_bible[0]
