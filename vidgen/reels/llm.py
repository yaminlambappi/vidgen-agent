"""Optional single-call Gemini polish. Never loops. Dry-run never enters here."""
from __future__ import annotations

from vidgen.config import settings
from vidgen.reels.duration import assert_duration, estimate_speech_seconds
from vidgen.reels.language import has_bengali, is_garbled_caption
from vidgen.reels.safety import IdempotencyStore, PermanentGenerationError, execute_expensive
from vidgen.reels.schemas import CreativeBrief, ReelJob, ReelScript, ScriptLine


def maybe_polish_script(job: ReelJob, store: IdempotencyStore, dry: bool) -> None:
    if dry or settings.DRY_RUN or not settings.is_production:
        return
    if not job.brief or not job.script:
        return
    try:
        execute_expensive(
            job,
            kind="gemini",
            operation="script_polish",
            model=settings.GEMINI_MODEL,
            prompt=job.script.full_text,
            inputs={"language": job.brief.language, "mode": job.brief.content_mode},
            store=store,
            fn=lambda: _polish(job),
            dry_run=False,
        )
    except PermanentGenerationError:
        # Keep the already-fitted deterministic script.
        return
    except Exception:
        return


def _polish_acceptable(brief: CreativeBrief, text: str) -> bool:
    if not text or not text.strip():
        return False
    if is_garbled_caption(text, brief.language):
        return False
    if brief.language.startswith("bengali") and not has_bengali(text):
        return False
    return True


def _polish(job: ReelJob) -> str:
    from google import genai
    from google.genai import types

    brief: CreativeBrief = job.brief
    script: ReelScript = job.script
    product = job.product_bible.name if job.product_bible else "the product"
    client = genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )
    lang_rule = (
        "Write ONLY conversational Bangladeshi Bangla (Dhaka register). "
        "Every line must contain Bengali letters. Do not translate into English."
        if brief.language.startswith("bengali") else
        "Write natural spoken English. No corporate slogans."
    )
    prompt = (
        f"Rewrite this short social Reel as something a real person would say to one friend. "
        f"{lang_rule} Dialect: {brief.dialect}. Mode: {brief.content_mode}. "
        f"Audience: {brief.target_audience}. "
        f"Keep the exact product name '{product}'. Do not invent a brand, testimonial, award, or statistic. "
        f"Do not start with generic openings. Why someone stops: {brief.why_watch}. "
        f"Why they stay: {brief.why_stay}. CTA (keep or lightly paraphrase): {brief.cta}. "
        f"3 lines or fewer. Short spoken sentences.\n\n"
        f"CURRENT SCRIPT:\n{script.full_text}\n\n"
        "Return only the spoken lines, one line per sentence."
    )
    r = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=512),
    )
    text = (r.text or "").strip()
    if not _polish_acceptable(brief, text):
        raise PermanentGenerationError("Gemini polish rejected — keeping deterministic script")
    lines = [ln.strip(" -•") for ln in text.splitlines() if ln.strip()]
    if not lines or not _polish_acceptable(brief, " ".join(lines)):
        raise PermanentGenerationError("Gemini returned unusable lines")
    budget = max(4.0, min(brief.duration_seconds * 0.68, brief.duration_seconds - 2.0))
    kept = []
    acc = 0.0
    for ln in lines:
        if is_garbled_caption(ln, brief.language):
            continue
        if brief.language.startswith("bengali") and not has_bengali(ln):
            continue
        est = estimate_speech_seconds(ln, brief.language)
        if acc + est > budget and kept:
            break
        kept.append(ln)
        acc += est + 0.25
    if not kept or not _polish_acceptable(brief, " ".join(kept)):
        raise PermanentGenerationError("Gemini polish empty after filters")
    cursor = 0.15
    body = []
    for ln in kept:
        est = estimate_speech_seconds(ln, brief.language)
        body.append(ScriptLine(
            speaker="talent" if brief.talking_head else "narrator",
            text=ln,
            start_seconds=round(cursor, 3),
            estimated_seconds=est,
            emotion="natural",
            on_camera=bool(brief.talking_head),
        ))
        cursor += est + 0.25
    full = " ".join(kept)
    speech = estimate_speech_seconds(full, brief.language)
    assert_duration(min(speech, brief.duration_seconds), "polished script")
    job.script = ReelScript(
        language=brief.language,
        hook_line=kept[0],
        body_lines=body,
        cta_line=kept[-1],
        full_text=full,
        estimated_speech_seconds=speech,
        target_duration=brief.duration_seconds,
    )
    return "polished"
