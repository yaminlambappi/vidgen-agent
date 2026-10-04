"""Instinct to video: one reflection in, one 30s or 60s School of Sufi short out."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from vidgen.config import settings
from vidgen.sufi.ledger import Ledger
from vidgen.sufi.plan import PlanError, SufiPlan, choose_duration, offline_plan, parse_plan, system_prompt, user_prompt
from vidgen.sufi.publish import PublishError, publish_video
from vidgen.sufi.render import (
    assert_reel,
    cloud_tts,
    concat_video,
    fit_slot,
    mux,
    write_drone,
    write_plate,
    write_tone_voice,
)
from vidgen.utils.retry import call_with_retry


class SufiResult(BaseModel):
    job_id: str
    thought: str
    duration_seconds: int
    script_text: str
    veo_prompts: list[str]
    caption_and_hashtags: str
    slots: list[int]
    video_path: str
    plan_source: str
    voice_source: str
    shots: list[dict] = Field(default_factory=list)
    ledger: dict = Field(default_factory=dict)
    publish: dict = Field(default_factory=dict)
    probe: dict = Field(default_factory=dict)


def _gemini_text(thought: str, duration: int, prompt_count: int, correction: str = "") -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )
    contents = user_prompt(thought, duration, prompt_count)
    if correction:
        contents += f"\n\nThe previous JSON was rejected: {correction}\nReturn corrected JSON only."

    def _call() -> str:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt(duration, prompt_count),
                temperature=0.7,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
        text = response.text or ""
        if not text.strip():
            raise RuntimeError("Gemini returned an empty plan")
        return text

    return call_with_retry(_call, provider="gemini", model=settings.GEMINI_MODEL, operation="sufi_plan")


def _write_plan(thought: str, duration: int, ledger: Ledger, llm) -> tuple[SufiPlan, str]:
    prompt_count = 2
    if llm is not None:
        ledger.charge("gemini")
        return parse_plan(llm(thought, duration, prompt_count), duration), "llm"
    if not settings.is_production:
        return offline_plan(thought, duration), "offline_draft"
    ledger.charge("gemini")
    raw = _gemini_text(thought, duration, prompt_count)
    try:
        return parse_plan(raw, duration), "gemini"
    except PlanError as first:
        ledger.charge("gemini")
        raw = _gemini_text(thought, duration, prompt_count, correction=str(first))
        return parse_plan(raw, duration), "gemini"


def _render_source(prompt: str, dest: Path, job_id: str, shot_id: str, video_gen) -> str:
    if not settings.is_production:
        write_plate(str(dest), settings.VEO_NATIVE_SECONDS)
        return "plate"
    from vidgen.providers import get_storage_provider, get_video_generator
    generator = video_gen or get_video_generator()
    out_uri = f"gs://{settings.GCS_BUCKET}/sufi/{job_id}/{shot_id}/"
    job = generator.generate_shot(
        prompt=prompt,
        output_uri=out_uri,
        duration=settings.VEO_NATIVE_SECONDS,
        project_id=job_id,
        shot_id=shot_id,
        reference_assets=[],
        aspect_ratio="9:16",
        generate_audio=False,
    )
    if getattr(job, "status", "") != "completed" or not getattr(job, "artifact_uri", ""):
        raise RuntimeError(getattr(job, "error", "") or "Veo shot failed")
    get_storage_provider().download(job.artifact_uri, str(dest))
    return "veo"


def generate(thought: str, *, llm=None, video_gen=None, tts_fn=None, publish: bool = True) -> SufiResult:
    text = " ".join((thought or "").split())
    if len(text) < 3:
        raise PlanError("thought is required")
    duration = choose_duration(text)
    job_id = str(uuid4())
    root = settings.VIDGEN_WORK_ROOT / "sufi" / job_id
    root.mkdir(parents=True, exist_ok=True)
    ledger = Ledger()

    plan, plan_source = _write_plan(text, duration, ledger, llm)
    shots = []
    slot_paths = []
    for index, (prompt, slot) in enumerate(zip(plan.veo_prompts, plan.slots), start=1):
        ledger.charge("veo")
        shot_id = f"shot_{index}"
        source = root / f"{shot_id}_source.mp4"
        fitted = root / f"{shot_id}.mp4"
        origin = _render_source(prompt, source, job_id, shot_id, video_gen)
        fit_slot(str(source), str(fitted), slot)
        slot_paths.append(str(fitted))
        shots.append({
            "shot_id": shot_id,
            "veo_seconds": settings.VEO_NATIVE_SECONDS,
            "slot_seconds": slot,
            "source": origin,
            "prompt": prompt,
        })

    ledger.charge("tts")
    voice_path = root / "voice.m4a"
    if tts_fn is not None:
        tts_fn(plan.script_text, str(voice_path))
        voice_source = "injected"
    elif settings.is_production:
        cloud_tts(plan.script_text, str(voice_path.with_suffix(".mp3")))
        voice_path = voice_path.with_suffix(".mp3")
        voice_source = "cloud_tts"
    else:
        write_tone_voice(str(voice_path), plan.duration_seconds)
        voice_source = "tone_fallback"

    bed = root / "bed.m4a"
    write_drone(str(bed), plan.duration_seconds)
    picture = root / "picture.mp4"
    final = root / "short.mp4"
    concat_video(slot_paths, str(picture))
    mux(str(picture), str(voice_path), str(bed), str(final), plan.duration_seconds)
    info = assert_reel(str(final), plan.duration_seconds)

    publication = {
        "youtube": {"status": "skipped", "reason": "disabled"},
        "webhook": {"status": "skipped", "reason": "disabled"},
    }
    result = SufiResult(
        job_id=job_id,
        thought=text,
        duration_seconds=plan.duration_seconds,
        script_text=plan.script_text,
        veo_prompts=plan.veo_prompts,
        caption_and_hashtags=plan.caption_and_hashtags,
        slots=plan.slots,
        video_path=str(final),
        plan_source=plan_source,
        voice_source=voice_source,
        shots=shots,
        ledger=ledger.model_dump(),
        publish=publication,
        probe=info,
    )
    (root / "job.json").write_text(result.model_dump_json(indent=2))
    (root / "caption.txt").write_text(plan.caption_and_hashtags)
    if publish:
        try:
            result.publish = publish_video(
                str(final), plan.script_text, plan.caption_and_hashtags, plan.duration_seconds,
            )
        except PublishError as exc:
            result.publish = exc.details
            (root / "job.json").write_text(result.model_dump_json(indent=2))
            raise
        (root / "job.json").write_text(result.model_dump_json(indent=2))
    return result


def result_json(result: SufiResult) -> str:
    return json.dumps(result.model_dump(), indent=2)
