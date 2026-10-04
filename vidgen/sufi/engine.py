"""One reflection in, one 30-second munajat out. A valid plan is not yet a film."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from vidgen.config import settings
from vidgen.sufi.ledger import Ledger
from vidgen.sufi.plan import (
    PlanError,
    SufiPlan,
    _extract_json,
    choose_duration,
    critic_system_prompt,
    critic_user_prompt,
    offline_plan,
    parse_critic,
    parse_plan,
    prompt_count_for,
    system_prompt,
    user_prompt,
)
from vidgen.sufi.publish import PublishError, publish_video
from vidgen.sufi.render import (
    PronunciationError,
    QualityError,
    assert_film,
    check_pronunciation,
    cloud_tts,
    concat_audio,
    concat_video,
    extract_jpg,
    fit_phrase,
    fit_slot,
    inspect_shots,
    mux,
    transcribe_bangla,
    volume_levels,
    write_captions,
    write_drone,
    write_plate,
    write_tone_voice,
)
from vidgen.utils.retry import call_with_retry

VISION_CODES = {
    "broken_anatomy",
    "identity_break",
    "text_or_logo",
    "static_opening",
    "flicker",
    "ugly_transition",
}


class SufiResult(BaseModel):
    job_id: str
    thought: str
    duration_seconds: int
    emotional_core: str = ""
    spiritual_direction: list[str] = Field(default_factory=list)
    visual_bible: dict = Field(default_factory=dict)
    beats: list[dict] = Field(default_factory=list)
    script_text: str
    veo_prompts: list[str]
    caption_and_hashtags: str
    slots: list[int]
    video_path: str
    plan_source: str
    voice_source: str
    visual_qa: str = ""
    voice_qa: str = ""
    shots: list[dict] = Field(default_factory=list)
    ledger: dict = Field(default_factory=dict)
    publish: dict = Field(default_factory=dict)
    probe: dict = Field(default_factory=dict)
    gcs_video_uri: str = ""


def _client():
    from google import genai
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def _gemini_text(system: str, contents: str, temperature: float) -> str:
    from google.genai import types

    client = _client()

    def _call() -> str:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
        text = response.text or ""
        if not text.strip():
            raise RuntimeError("Gemini returned an empty response")
        return text

    return call_with_retry(_call, provider="gemini", model=settings.GEMINI_MODEL, operation="sufi_plan")


def _call_llm(llm, thought: str, duration: int, prompt_count: int, correction: str):
    try:
        return llm(thought, duration, prompt_count, correction)
    except TypeError:
        return llm(thought, duration, prompt_count)


def _draft_plan(thought: str, duration: int, ledger: Ledger, llm) -> tuple[SufiPlan, str]:
    prompt_count = prompt_count_for(duration)
    if llm is not None:
        ledger.charge("gemini")
        return parse_plan(_call_llm(llm, thought, duration, prompt_count, ""), duration), "llm"
    if not settings.is_production:
        return offline_plan(thought, duration), "offline_draft"
    ledger.charge("gemini")
    raw = _gemini_text(system_prompt(duration, prompt_count), user_prompt(thought, duration, prompt_count), 0.4)
    try:
        return parse_plan(raw, duration), "gemini"
    except PlanError as first:
        ledger.charge("gemini")
        raw = _gemini_text(
            system_prompt(duration, prompt_count),
            user_prompt(thought, duration, prompt_count) + f"\n\nThe previous JSON was rejected: {first}\nReturn corrected JSON only.",
            0.2,
        )
        return parse_plan(raw, duration), "gemini"


def _review_plan(plan: SufiPlan, ledger: Ledger, critic) -> dict:
    if critic is not None:
        verdict = critic(plan)
        if isinstance(verdict, dict):
            return {"pass": bool(verdict.get("pass")), "reasons": list(verdict.get("reasons") or [])}
        return parse_critic(verdict)
    if not settings.is_production:
        return {"pass": True, "reasons": []}
    ledger.charge("gemini")
    raw = _gemini_text(critic_system_prompt(), critic_user_prompt(plan), 0.2)
    return parse_critic(raw)


def _write_plan(thought: str, duration: int, ledger: Ledger, llm, critic) -> tuple[SufiPlan, str]:
    prompt_count = prompt_count_for(duration)
    plan, source = _draft_plan(thought, duration, ledger, llm)
    verdict = _review_plan(plan, ledger, critic)
    if verdict["pass"]:
        return plan, source
    reasons = "; ".join(verdict["reasons"]) or "the plan is not one film"
    note = f"The coherence review rejected the plan: {reasons}\nReturn one corrected film."
    if llm is not None:
        ledger.charge("gemini")
        plan = parse_plan(_call_llm(llm, thought, duration, prompt_count, note), duration)
    elif settings.is_production:
        ledger.charge("gemini")
        raw = _gemini_text(
            system_prompt(duration, prompt_count),
            user_prompt(thought, duration, prompt_count) + "\n\n" + note,
            0.2,
        )
        plan = parse_plan(raw, duration)
    else:
        raise PlanError(reasons)
    return plan, source + "+regen"


def _render_source(prompt: str, dest: Path, job_id: str, shot_id: str, video_gen) -> str:
    if video_gen is not None:
        from vidgen.providers import get_storage_provider
        out_uri = f"gs://{settings.GCS_BUCKET}/sufi/{job_id}/{shot_id}/"
        job = video_gen.generate_shot(
            prompt=prompt,
            output_uri=out_uri,
            duration=settings.VEO_NATIVE_SECONDS,
            project_id=job_id,
            shot_id=shot_id,
            reference_assets=[],
            aspect_ratio="9:16",
            generate_audio=False,
        )
        if settings.is_production:
            if getattr(job, "status", "") != "completed" or not getattr(job, "artifact_uri", ""):
                raise RuntimeError(getattr(job, "error", "") or "Veo shot failed")
            get_storage_provider().download(job.artifact_uri, str(dest))
            return "veo"
        if not dest.exists():
            write_plate(str(dest), settings.VEO_NATIVE_SECONDS)
        return "injected"
    if not settings.is_production:
        write_plate(str(dest), settings.VEO_NATIVE_SECONDS)
        return "plate"
    from vidgen.providers import get_storage_provider, get_video_generator
    generator = get_video_generator()
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


def _gemini_visual_review(paths: list[str], bible: dict, work: Path) -> list[dict]:
    from google.genai import types

    client = _client()
    parts = [types.Part.from_text(text=(
        "Compare these frames with the visual identity. "
        "Fail a shot only for broken_anatomy, identity_break, text_or_logo, "
        "static_opening, flicker, or ugly_transition. "
        "identity_break means the environment, lighting direction, palette, subject, "
        "camera language, atmosphere, or a recurring element disagrees across shots. "
        "The world name matching is not enough. "
        "Return JSON {\"shots\": [{\"index\": 1, \"codes\": []}]}. "
        "index is 1-based. codes is empty when the shot holds.\n"
        + json.dumps(bible, ensure_ascii=False)
    ))]
    for index, path in enumerate(paths, start=1):
        for label, moment in (("open", 0.2), ("mid", 2.5), ("close", 4.6)):
            dest = work / f"qa_{index}_{label}.jpg"
            extract_jpg(path, moment, str(dest))
            parts.append(types.Part.from_text(text=f"shot {index} {label}"))
            parts.append(types.Part.from_bytes(data=dest.read_bytes(), mime_type="image/jpeg"))

    def _call() -> str:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=parts,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
        text = response.text or ""
        if not text.strip():
            raise RuntimeError("Gemini returned an empty visual review")
        return text

    raw = call_with_retry(_call, provider="gemini", model=settings.GEMINI_MODEL, operation="sufi_visual_qa")
    data = _extract_json(raw)
    if "shots" not in data:
        raise QualityError("visual review did not return shot findings")
    findings = []
    for item in data.get("shots") or []:
        codes = [code for code in (item.get("codes") or []) if code in VISION_CODES]
        if codes:
            findings.append({"index": int(item.get("index", 1)) - 1, "codes": codes})
    return findings


def _fit_shot(source: Path, fitted: Path, slot: int) -> None:
    fit_slot(str(source), str(fitted), slot)


def _qa_shots(paths: list[str], plan: SufiPlan, reviewer, ledger: Ledger, work: Path) -> tuple[list[dict], str]:
    if reviewer is not None:
        findings = inspect_shots(paths, reviewer=reviewer, bible=plan.visual_bible)
        return findings, "reviewed"
    if not settings.is_production:
        return inspect_shots(paths), "local_only"
    ledger.charge("gemini")

    def _review(shot_paths, bible):
        return _gemini_visual_review(shot_paths, bible, work)

    return inspect_shots(paths, reviewer=_review, bible=plan.visual_bible), "gemini"


def _speak(plan: SufiPlan, root: Path, ledger: Ledger, tts_fn, recognizer) -> tuple[Path, str, str]:
    phrase_paths = []
    voice_source = "tone_fallback"
    voice_qa = "skipped"
    for index, beat in enumerate(plan.beats, start=1):
        raw = root / f"phrase_{index}_raw.m4a"
        fitted = root / f"phrase_{index}.m4a"
        if tts_fn is not None:
            try:
                tts_fn(beat.phrase, str(raw))
            except TypeError:
                tts_fn(beat.phrase, str(raw), None)
            voice_source = "injected"
        elif settings.is_production:
            ledger.charge("tts")
            raw = raw.with_suffix(".mp3")
            cloud_tts(beat.phrase, str(raw))
            voice_source = "cloud_tts"
        else:
            write_tone_voice(str(raw), 2)
        fit_phrase(str(raw), str(fitted), 5)
        if recognizer is not None:
            voice_qa = _hear(beat.phrase, fitted, raw, ledger, tts_fn, recognizer, allow_retry=True)
        elif settings.is_production and voice_source == "cloud_tts":
            voice_qa = _hear_cloud(beat.phrase, fitted, root, index, ledger)
        else:
            mean, _peak = volume_levels(str(fitted), start=0, duration=1.2)
            if mean <= -50:
                raise QualityError(f"beat {index} produced silence")
        phrase_paths.append(str(fitted))
    voice = root / "voice.m4a"
    concat_audio(phrase_paths, str(voice))
    return voice, voice_source, voice_qa


def _hear(phrase: str, fitted: Path, raw: Path, ledger: Ledger, tts_fn, recognizer, allow_retry: bool) -> str:
    transcript = recognizer(str(fitted), phrase)
    try:
        check_pronunciation(phrase, transcript)
        return "passed"
    except PronunciationError:
        if not allow_retry:
            raise
        if tts_fn is not None:
            tts_fn(phrase, str(raw))
        elif settings.is_production:
            ledger.charge("tts")
            cloud_tts(phrase, str(raw), break_ms=settings.TTS_MAWLA_BREAK_MS * 3)
        fit_phrase(str(raw), str(fitted), 5)
        return _hear(phrase, fitted, raw, ledger, tts_fn, recognizer, allow_retry=False)


def _hear_cloud(phrase: str, fitted: Path, root: Path, index: int, ledger: Ledger) -> str:
    try:
        check_pronunciation(phrase, transcribe_bangla(str(fitted)))
        return "stt"
    except PronunciationError:
        ledger.charge("tts")
        raw = root / f"phrase_{index}_retry.mp3"
        cloud_tts(phrase, str(raw), break_ms=settings.TTS_MAWLA_BREAK_MS * 3)
        fit_phrase(str(raw), str(fitted), 5)
        check_pronunciation(phrase, transcribe_bangla(str(fitted)))
        return "stt"


def _store(final: Path, shots: list[dict], job_id: str) -> str:
    from vidgen.providers import get_storage_provider
    storage = get_storage_provider()
    uris = []
    for shot in shots:
        remote = f"sufi/{job_id}/shots/{shot['shot_id']}.mp4"
        uri = storage.upload(shot["path"], remote)
        shot["gcs_uri"] = uri
        uris.append(uri)
    master = storage.upload(str(final), f"sufi/{job_id}/final_short.mp4")
    if settings.is_production:
        for uri in uris + [master]:
            if not storage.exists(uri):
                raise QualityError(f"missing object {uri}")
    return master


def generate(
    thought: str,
    *,
    llm=None,
    video_gen=None,
    tts_fn=None,
    critic=None,
    reviewer=None,
    recognizer=None,
    publish: bool = True,
) -> SufiResult:
    text = " ".join((thought or "").split())
    if len(text) < 3:
        raise PlanError("thought is required")
    duration = choose_duration(text)
    job_id = str(uuid4())
    root = settings.VIDGEN_WORK_ROOT / "sufi" / job_id
    root.mkdir(parents=True, exist_ok=True)
    ledger = Ledger()

    plan, plan_source = _write_plan(text, duration, ledger, llm, critic)
    shots = []
    slot_paths = []
    for index, (prompt, slot) in enumerate(zip(plan.veo_prompts, plan.slots), start=1):
        ledger.charge("veo")
        shot_id = f"shot_{index}"
        source = root / f"{shot_id}_source.mp4"
        fitted = root / f"{shot_id}.mp4"
        origin = _render_source(prompt, source, job_id, shot_id, video_gen)
        _fit_shot(source, fitted, slot)
        slot_paths.append(str(fitted))
        shots.append({
            "shot_id": shot_id,
            "veo_seconds": settings.VEO_NATIVE_SECONDS,
            "slot_seconds": slot,
            "source": origin,
            "prompt": prompt,
            "path": str(fitted),
            "gcs_uri": "",
        })

    findings, visual_qa = _qa_shots(slot_paths, plan, reviewer, ledger, root)
    if findings:
        failed = {item["index"] for item in findings}
        for index in sorted(failed):
            ledger.charge("veo")
            shot = shots[index]
            reason = ", ".join(
                code for item in findings if item["index"] == index for code in item["codes"]
            )
            prompt = shot["prompt"] + f" Previous take failed visual QA: {reason}. Keep the same visual identity and fix only that."
            source = root / f"{shot['shot_id']}_retry.mp4"
            origin = _render_source(prompt, source, job_id, shot["shot_id"] + "_retry", video_gen)
            _fit_shot(source, Path(shot["path"]), shot["slot_seconds"])
            shot["source"] = origin
            shot["prompt"] = prompt
        findings, visual_qa = _qa_shots(slot_paths, plan, reviewer, ledger, root)
        if findings:
            summary = "; ".join(
                f"shot {item['index'] + 1}: {', '.join(item['codes'])}" for item in findings
            )
            raise QualityError(f"visual QA failed after one reshoot: {summary}")

    voice_path, voice_source, voice_qa = _speak(plan, root, ledger, tts_fn, recognizer)
    bed = root / "bed.m4a"
    write_drone(str(bed), plan.duration_seconds)
    picture = root / "picture.mp4"
    final = root / "short.mp4"
    captions = root / "captions.ass"
    glyphs = [beat.glyph for beat in plan.beats]
    phrases = [beat.phrase for beat in plan.beats]
    concat_video(slot_paths, str(picture))
    write_captions(glyphs, str(captions))
    mux(str(picture), str(voice_path), str(bed), str(final), plan.duration_seconds, str(captions))
    info = assert_film(
        str(final),
        segments=slot_paths,
        voice_path=str(voice_path),
        bed_path=str(bed),
        captions_path=str(captions),
        glyphs=glyphs,
        phrases=phrases,
    )
    gcs_video_uri = _store(final, shots, job_id)
    for shot in shots:
        shot.pop("path", None)

    publication = {
        "youtube": {"status": "skipped", "reason": "disabled"},
        "webhook": {"status": "skipped", "reason": "disabled"},
    }
    result = SufiResult(
        job_id=job_id,
        thought=text,
        duration_seconds=plan.duration_seconds,
        emotional_core=plan.emotional_core,
        spiritual_direction=plan.spiritual_direction,
        visual_bible=plan.visual_bible.model_dump(),
        beats=[beat.model_dump() for beat in plan.beats],
        script_text=plan.script_text,
        veo_prompts=plan.veo_prompts,
        caption_and_hashtags=plan.caption_and_hashtags,
        slots=plan.slots,
        video_path=str(final),
        plan_source=plan_source,
        voice_source=voice_source,
        visual_qa=visual_qa,
        voice_qa=voice_qa,
        shots=shots,
        ledger=ledger.model_dump(),
        publish=publication,
        probe=info,
        gcs_video_uri=gcs_video_uri,
    )
    (root / "job.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (root / "caption.txt").write_text(plan.caption_and_hashtags, encoding="utf-8")
    if publish:
        try:
            result.publish = publish_video(
                str(final), plan.script_text, plan.caption_and_hashtags, plan.duration_seconds,
            )
        except PublishError as exc:
            result.publish = exc.details
            (root / "job.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
            raise
        (root / "job.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def result_json(result: SufiResult) -> str:
    return json.dumps(result.model_dump(), indent=2, ensure_ascii=False)
