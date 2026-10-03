"""Optional single-call Gemini polish. Never loops. Dry-run never enters here."""
from __future__ import annotations

from vidgen.config import settings
from vidgen.reels.duration import assert_duration, estimate_speech_seconds
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


def _polish(job: ReelJob) -> str:
    from google import genai
    from google.genai import types

    brief: CreativeBrief = job.brief
    script: ReelScript = job.script
    client = genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )
    prompt = (
        f"Rewrite this short-form ad script. Keep the same number of lines or fewer. "
        f"Language/dialect: {brief.dialect}. Mode: {brief.content_mode}. "
        f"Audience: {brief.target_audience}. CTA: {brief.cta}. "
        f"Do not invent facts, testimonials, awards, or statistics. "
        f"Do not start with generic openings like 'In today's fast-paced world'. "
        f"Natural spoken {brief.language}. Short sentences.\n\n"
        f"CURRENT SCRIPT:\n{script.full_text}\n\n"
        "Return only the spoken lines, one line per sentence."
    )
    r = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.6, max_output_tokens=512),
    )
    text = (r.text or "").strip()
    if not text:
        raise PermanentGenerationError("empty Gemini script polish")
    lines = [ln.strip(" -•") for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise PermanentGenerationError("Gemini returned no lines")
    budget = max(4.0, min(brief.duration_seconds * 0.68, brief.duration_seconds - 2.0))
    kept = []
    acc = 0.0
    for ln in lines:
        est = estimate_speech_seconds(ln, brief.language)
        if acc + est > budget and kept:
            break
        kept.append(ln)
        acc += est + 0.25
    if not kept:
        return "kept-deterministic"
    cursor = 0.15
    body = []
    for i, ln in enumerate(kept):
        est = estimate_speech_seconds(ln, brief.language)
        body.append(ScriptLine(
            speaker="narrator",
            text=ln,
            start_seconds=round(cursor, 3),
            estimated_seconds=est,
            emotion="natural",
            on_camera=False,
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
