"""Veo prompt compiler for controlled short Reel shots."""
from __future__ import annotations

from typing import List, Optional

from vidgen.reels.schemas import CharacterSpec, ProductSpec, ReelJob, ReelShot


def compile_shot_prompt(
    shot: ReelShot,
    job: ReelJob,
    previous: Optional[ReelShot] = None,
) -> dict:
    brief = job.brief
    product = job.product_bible or ProductSpec()
    char = _char(job, shot)
    parts: List[str] = [
        "PHOTOGRAPHIC VERTICAL SOCIAL VIDEO SHOT. 9:16. Photorealistic. "
        "Single continuous take. No text, no captions, no watermark, no logo overlay, no subtitles.",
        "REALISM MANDATE: real skin texture, real hands, believable physics, real weight in objects. "
        "No plastic skin, no beauty-filter face, no exaggerated expression, no impossible anatomy.",
        f"FORMAT: 1080x1920, 9:16, {shot.duration:.0f} seconds, social-native pacing.",
    ]
    if brief:
        parts.append(
            f"MODE: {brief.content_mode}. TONE: {brief.tone}. "
            f"VISUAL: {brief.visual_style}. CAMERA STYLE: {brief.camera_style}."
        )
        if brief.talking_head and shot.talking_head:
            parts.append(
                "SYNCED SPEECH: the person visibly says the dialogue below. "
                "Mouth motion must match the line. Do not invent extra spoken words."
            )
        else:
            parts.append(
                "NO VISIBLE SPEECH: the person does not talk to camera. "
                "If a mouth moves, it is a breath or a small unvoiced reaction only."
            )

    if product.name:
        parts.append(
            f"PRODUCT IDENTITY (DO NOT REDESIGN): {product.name}. "
            f"SHAPE: {product.shape}. PACKAGING: {product.packaging}. "
            f"COLORS: {product.colors}. LABELS: {product.labels}. "
            f"USAGE: {product.correct_usage}. ORIENTATION: {product.orientation}."
        )
        if product.user_image_authoritative:
            parts.append(
                "The supplied product image is AUTHORITATIVE. "
                "Do not invent logos, do not change the bottle, do not hallucinate label text."
            )

    if char:
        parts.append(
            f"CHARACTER IDENTITY (DO NOT ALTER): {char.name}, {char.age}. "
            f"APPEARANCE: {char.appearance}. FACE: {char.face}. SKIN: {char.skin}. "
            f"HAIR: {char.hair}. WARDROBE: {char.wardrobe}. ACCESSORIES: {char.accessories}. "
            f"MANNERISMS: {char.mannerisms}."
        )

    parts.append(f"SHOT PURPOSE: {shot.purpose}.")
    parts.append(f"ACTION: {shot.action}")
    if shot.dialogue and shot.talking_head:
        parts.append(f"SPOKEN LINE (exact): {shot.dialogue}")
    if shot.performance:
        p = shot.performance
        parts.append(
            f"PERFORMANCE: objective={p.objective}; emotion={p.emotional_state}; "
            f"subtext={p.subtext}; body={p.body_language}; gaze={p.gaze}; "
            f"gesture={p.gesture}; face={p.facial_reaction}; intensity={p.delivery_intensity}."
        )
    parts.append(f"ENVIRONMENT: {shot.environment}")
    parts.append(
        f"CAMERA: {shot.camera}; framing={shot.framing}; look={shot.lens_look}; "
        f"motion={shot.camera_motion}."
    )
    parts.append(f"LIGHTING: {shot.lighting}.")
    parts.append(f"SOUND IN FRAME: {shot.sound}.")

    if previous:
        parts.append(
            f"CONTINUITY FROM {previous.shot_id}: same person, same wardrobe, same product, "
            f"same location time-of-day. Previous action was: {previous.action}"
        )
    if shot.continuity_requirements:
        parts.append("MUST PRESERVE: " + "; ".join(shot.continuity_requirements))

    negs = list(shot.negative_constraints) + [
        "malformed hands", "warped product label", "changing face",
        "studio infinity backdrop", "stock-video smile", "random camera orbit",
    ]
    parts.append("DO NOT GENERATE: " + " | ".join(negs))

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

    prompt = "\n".join(parts)
    shot.generation_prompt = prompt
    return {
        "prompt": prompt,
        "reference_assets": refs[:3],  # Veo reference slot is small
        "aspect_ratio": "9:16",
        "duration": int(round(shot.duration)),
        "generate_audio": bool(shot.native_audio and shot.talking_head),
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
