"""Typed contracts for the Reels / Shorts factory. No free-form stage handoff."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _uid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReelStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    SCRIPT_READY = "script_ready"
    STORYBOARD_READY = "storyboard_ready"
    REFERENCES_READY = "references_ready"
    SHOTS_GENERATING = "shots_generating"
    SHOTS_READY = "shots_ready"
    AUDIO_READY = "audio_ready"
    EDIT_PLAN_READY = "edit_plan_ready"
    ASSEMBLING = "assembling"
    QC = "qc"
    COMPLETE = "complete"
    FAILED = "failed"
    FAILED_COST_GUARD = "failed_cost_guard"


class ReelLanguage(str, Enum):
    ENGLISH = "english"
    BENGALI = "bengali"
    BILINGUAL = "bengali_english"


class ContentMode(str, Enum):
    UGC = "UGC"
    PRODUCT_DEMO = "PRODUCT_DEMO"
    TESTIMONIAL = "TESTIMONIAL"
    LIFESTYLE_COMMERCIAL = "LIFESTYLE_COMMERCIAL"
    DIRECT_RESPONSE_AD = "DIRECT_RESPONSE_AD"
    FOUNDER_STYLE = "FOUNDER_STYLE"
    STORYTELLING = "STORYTELLING"
    CINEMATIC_COMMERCIAL = "CINEMATIC_COMMERCIAL"
    STREET_STYLE = "STREET_STYLE"
    INTERVIEW = "INTERVIEW"
    COMEDY = "COMEDY"
    EMOTIONAL = "EMOTIONAL"
    PROBLEM_SOLUTION = "PROBLEM_SOLUTION"
    BEFORE_AFTER = "BEFORE_AFTER"
    PRODUCT_REVEAL = "PRODUCT_REVEAL"


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    COST_GUARD = "cost_guard"
    DURATION = "duration"
    APPLICATION = "application"


class InputAsset(BaseModel):
    asset_id: str = Field(default_factory=_uid)
    kind: str = "product_image"  # product_image|logo|brand|video|actor|environment|script
    uri: str = ""
    local_path: str = ""
    mime_type: str = ""
    authoritative: bool = False
    notes: str = ""


class ReelRequest(BaseModel):
    idea: str = Field(..., min_length=3)
    language: str = "english"
    duration_seconds: float = 20.0
    audience: str = ""
    style: str = ""
    goal: str = ""
    cta: str = ""
    product_name: str = ""
    brand_name: str = ""
    platform: str = "instagram_reels"
    voice_gender: str = ""
    content_mode: str = ""
    allowed_claims: List[str] = Field(default_factory=list)
    assets: List[InputAsset] = Field(default_factory=list)
    variant_count: int = 1
    dry_run: bool = False

    @field_validator("duration_seconds")
    @classmethod
    def _cap_duration(cls, v: float) -> float:
        from vidgen.reels.constants import MAX_DURATION_SECONDS
        if v <= 0:
            raise ValueError("duration_seconds must be positive")
        if v > MAX_DURATION_SECONDS:
            raise ValueError(f"duration_seconds {v} exceeds MAX_DURATION_SECONDS={MAX_DURATION_SECONDS}")
        return float(v)

    @field_validator("variant_count")
    @classmethod
    def _variants(cls, v: int) -> int:
        if v < 1:
            raise ValueError("variant_count must be >= 1")
        if v > 3:
            raise ValueError("variant_count must be <= 3")
        return v


class CreativeBrief(BaseModel):
    objective: str = ""
    target_audience: str = ""
    product_positioning: str = ""
    emotional_objective: str = ""
    creative_concept: str = ""
    platform: str = "instagram_reels"
    language: str = "english"
    dialect: str = ""
    duration_seconds: float = 20.0
    tone: str = ""
    visual_style: str = ""
    narrative_structure: str = ""
    cta: str = ""
    acting_style: str = ""
    camera_style: str = ""
    sound_direction: str = ""
    content_mode: str = "LIFESTYLE_COMMERCIAL"
    look_into_camera: bool = False
    talking_head: bool = False
    forbidden_claims: List[str] = Field(default_factory=list)
    allowed_claims: List[str] = Field(default_factory=list)
    cultural_notes: str = ""


class HookConcept(BaseModel):
    approach: str = ""
    concept: str = ""
    visual: str = ""
    line: str = ""
    score: float = 0.0
    rationale: str = ""


class HookStrategy(BaseModel):
    concepts: List[HookConcept] = Field(default_factory=list)
    chosen: Optional[HookConcept] = None


class ScriptLine(BaseModel):
    line_id: str = Field(default_factory=_uid)
    speaker: str = "narrator"
    text: str = ""
    start_seconds: float = 0.0
    estimated_seconds: float = 0.0
    emotion: str = "natural"
    on_camera: bool = False


class ReelScript(BaseModel):
    language: str = "english"
    hook_line: str = ""
    body_lines: List[ScriptLine] = Field(default_factory=list)
    cta_line: str = ""
    full_text: str = ""
    estimated_speech_seconds: float = 0.0
    target_duration: float = 20.0


class CharacterSpec(BaseModel):
    character_id: str = Field(default_factory=_uid)
    name: str = ""
    age: str = ""
    appearance: str = ""
    hair: str = ""
    face: str = ""
    skin: str = ""
    wardrobe: str = ""
    accessories: str = ""
    body: str = ""
    personality: str = ""
    mannerisms: str = ""
    emotional_baseline: str = ""
    speaking_style: str = ""
    reference_uri: str = ""


class ProductSpec(BaseModel):
    product_id: str = Field(default_factory=_uid)
    name: str = ""
    shape: str = ""
    proportions: str = ""
    packaging: str = ""
    colors: str = ""
    materials: str = ""
    labels: str = ""
    logo: str = ""
    typography: str = ""
    distinctive_details: str = ""
    correct_usage: str = ""
    orientation: str = ""
    reference_uris: List[str] = Field(default_factory=list)
    user_image_authoritative: bool = False


class BrandBible(BaseModel):
    brand_name: str = ""
    brand_voice: str = ""
    brand_tone: str = ""
    colors: List[str] = Field(default_factory=list)
    typography: str = ""
    logo_uri: str = ""
    product_rules: str = ""
    language: str = ""
    cta_style: str = ""
    visual_style: str = ""
    forbidden_claims: List[str] = Field(default_factory=list)


class PerformanceDirection(BaseModel):
    objective: str = ""
    emotional_state: str = ""
    subtext: str = ""
    body_language: str = ""
    gaze: str = ""
    gesture: str = ""
    facial_reaction: str = ""
    timing: str = ""
    pause: str = ""
    delivery_intensity: str = "restrained"


class ReelShot(BaseModel):
    shot_id: str = ""
    duration: float = 5.0
    start_time: float = 0.0
    end_time: float = 5.0
    purpose: str = ""
    characters: List[str] = Field(default_factory=list)
    character_state: str = ""
    location: str = ""
    environment: str = ""
    wardrobe: str = ""
    product_state: str = ""
    action: str = ""
    dialogue: str = ""
    camera: str = ""
    framing: str = ""
    lens_look: str = ""
    camera_motion: str = ""
    lighting: str = ""
    visual_style: str = ""
    sound: str = ""
    music: str = ""
    transition: str = "hard_cut"
    continuity_requirements: List[str] = Field(default_factory=list)
    generation_prompt: str = ""
    negative_constraints: List[str] = Field(default_factory=list)
    performance: Optional[PerformanceDirection] = None
    talking_head: bool = False
    use_first_last_frame: bool = False
    use_user_footage: bool = False
    user_footage_uri: str = ""
    generated_asset_uri: str = ""
    local_path: str = ""
    prompt_hash: str = ""
    attempts: int = 0
    native_audio: bool = False


class Storyboard(BaseModel):
    shots: List[ReelShot] = Field(default_factory=list)
    total_duration: float = 0.0
    target_duration: float = 20.0


class EditDecision(BaseModel):
    shot_id: str = ""
    cut_type: str = "hard_cut"
    rationale: str = ""
    punch_in: bool = False
    speed: float = 1.0
    j_cut: bool = False
    l_cut: bool = False


class EditPlan(BaseModel):
    sequence: List[str] = Field(default_factory=list)
    decisions: List[EditDecision] = Field(default_factory=list)
    caption_placement: str = "lower_third_safe"
    music_duck_db: float = -12.0
    color_treatment: str = "natural"


class AudioCue(BaseModel):
    cue_id: str = Field(default_factory=_uid)
    kind: str = "dialogue"  # dialogue|ambience|foley|product|music|transition
    text: str = ""
    start_seconds: float = 0.0
    duration_seconds: float = 0.0
    uri: str = ""
    local_path: str = ""
    voice_name: str = ""
    speaking_rate: float = 1.0
    pitch: float = 0.0


class AudioPlan(BaseModel):
    language: str = "english"
    voice_name: str = ""
    dialogue_cues: List[AudioCue] = Field(default_factory=list)
    music_mood: str = ""
    music_uri: str = ""
    music_path: str = ""
    foley: List[AudioCue] = Field(default_factory=list)
    subtitle_path: str = ""
    subtitle_uri: str = ""
    duck_under_dialogue: bool = True


class GenerationRecord(BaseModel):
    request_hash: str
    prompt_hash: str = ""
    model: str = ""
    operation: str = ""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    operation_id: str = Field(default_factory=_uid)
    status: str = "pending"
    attempt: int = 1
    result_uri: str = ""
    created_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    error: str = ""


class GenerationLedger(BaseModel):
    veo_calls: int = 0
    image_calls: int = 0
    tts_calls: int = 0
    gemini_calls: int = 0
    total_calls: int = 0
    regenerations: Dict[str, int] = Field(default_factory=dict)
    records: List[GenerationRecord] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)


class DryRunManifest(BaseModel):
    target_duration: float
    max_duration: float
    estimated_veo_calls: int
    estimated_image_calls: int
    estimated_tts_calls: int
    estimated_gemini_calls: int
    estimated_stages: int
    shot_count: int
    budget_status: str
    content_mode: str = ""
    language: str = ""
    talking_head: bool = False
    expensive_calls_made: int = 0


class QCReport(BaseModel):
    passed: bool = False
    technical: Dict[str, Any] = Field(default_factory=dict)
    creative: Dict[str, Any] = Field(default_factory=dict)
    visual: Dict[str, Any] = Field(default_factory=dict)
    audio: Dict[str, Any] = Field(default_factory=dict)
    failures: List[str] = Field(default_factory=list)


class ReelJob(BaseModel):
    job_id: str = Field(default_factory=_uid)
    request: ReelRequest
    status: ReelStatus = ReelStatus.QUEUED
    resume_stage: ReelStatus = ReelStatus.QUEUED
    message: str = "Queued."
    progress: int = 0
    brief: Optional[CreativeBrief] = None
    hook: Optional[HookStrategy] = None
    script: Optional[ReelScript] = None
    character_bible: List[CharacterSpec] = Field(default_factory=list)
    product_bible: Optional[ProductSpec] = None
    brand_bible: Optional[BrandBible] = None
    storyboard: Optional[Storyboard] = None
    edit_plan: Optional[EditPlan] = None
    audio_plan: Optional[AudioPlan] = None
    ledger: GenerationLedger = Field(default_factory=GenerationLedger)
    qc: Optional[QCReport] = None
    dry_run_manifest: Optional[DryRunManifest] = None
    final_video_uri: str = ""
    final_video_path: str = ""
    failure_class: str = ""
    last_error: str = ""
    last_error_signature: str = ""
    repeat_failure_count: int = 0
    pipeline_attempts: int = 0
    started_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    request_hash: str = ""

    @field_validator("updated_at", mode="before")
    @classmethod
    def _touch(cls, _):
        return _now()
