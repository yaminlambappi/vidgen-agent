"""Production-grade film data models."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field, field_validator

def _uid(): return str(uuid4())
def _now(): return datetime.now(timezone.utc)

class FilmStatus(str, Enum):
    QUEUED="queued"; PLANNING="planning"; STORYBOARDING="storyboarding"
    GENERATING="generating"; EDITING="editing"; MASTERING="mastering"
    UPLOADING="uploading"; COMPLETED="completed"; FAILED="failed"

class AssetType(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    TEXT = "text"

class AssetReference(BaseModel):
    asset_type: AssetType
    uri: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Character(BaseModel):
    character_id: str = Field(default_factory=_uid)
    name: str = ""
    age: str = ""
    physical_description: str = ""
    wardrobe: str = ""
    personality: str = ""
    motivation: str = ""
    fear: str = ""
    mannerisms: str = ""
    arc: str = ""
    reference_image_path: str = ""
    reference_image_uri: Optional[str] = ""
    canonical_visual_assets: List[AssetReference] = Field(default_factory=list)

class CharacterBible(BaseModel):
    characters: List[Character] = Field(default_factory=list)

class Location(BaseModel):
    location_id: str = Field(default_factory=_uid)
    name: str = ""
    description: str = ""
    time_of_day: str = ""
    lighting: str = ""
    atmosphere: str = ""
    recurring_props: List[str] = Field(default_factory=list)
    canonical_visual_assets: List[AssetReference] = Field(default_factory=list)

class WorldBible(BaseModel):
    locations: List[Location] = Field(default_factory=list)

class CinematicBible(BaseModel):
    color_palette: str = ""
    lighting: str = ""
    camera_language: str = ""
    texture: str = ""
    editing_rhythm: str = ""

class Shot(BaseModel):
    shot_id: str = Field(default_factory=_uid)
    scene_id: str = ""
    index: int = 0
    duration: int = 8
    shot_type: str = "medium"
    subject: str = ""
    action: str = ""
    location_id: str = ""
    camera: str = ""
    lens: str = ""
    movement: str = ""
    composition: str = ""
    lighting: str = ""
    atmosphere: str = ""
    character_ids: List[str] = Field(default_factory=list)
    emotional_direction: str = ""
    performance_objective: str = ""
    performance_subtext: str = ""
    physical_behavior: str = ""
    eyelines: str = ""
    sound: str = ""
    transition: str = ""
    veo_prompt: str = ""
    generated_frame_uris: List[str] = Field(default_factory=list)
    generated_asset_uri: str = ""
    attempts: int = 0
    qc: Dict[str, Any] = Field(default_factory=dict)
    prompt_hash: str = ""

class DialogueLine(BaseModel):
    character_id: str = ""
    line: str = ""

class Scene(BaseModel):
    scene_id: str = Field(default_factory=_uid)
    index: int = 0
    title: str = ""
    description: str = ""
    location_id: str = ""
    narration_text: str = ""
    dialogue: List[DialogueLine] = Field(default_factory=list)
    dramatic_purpose: str = ""
    shots: List[Shot] = Field(default_factory=list)

class StorySpec(BaseModel):
    title: str = ""
    logline: str = ""
    theme: str = ""
    genre: str = ""
    three_act_structure: str = ""

class EditPlan(BaseModel):
    sequence: List[str] = Field(default_factory=list)

class AudioPlan(BaseModel):
    narration_uri: str = ""
    music_uri: str = ""
    subtitle_uri: str = ""
    dialogue_uris: List[str] = Field(default_factory=list)
    dialogue_timeline: List[Dict[str, Any]] = Field(default_factory=list)

class MusicPlan(BaseModel):
    mood: str = ""
    tempo: str = "72 bpm"
    instrumentation: str = ""
    structure: str = ""

class GenerationJob(BaseModel):
    project_id: str = ""
    shot_id: str = ""
    status: str = ""
    artifact_uri: str = ""
    error: str = ""

class FinalManifest(BaseModel):
    project_id: str
    title: str
    video_uri: str
    narration_uri: str = ""
    music_uri: str = ""
    subtitle_uri: str = ""
    duration_seconds: float = 0
    shots: int = 0
    scenes: int = 0

class FilmProject(BaseModel):
    project_id: str = Field(default_factory=_uid)
    topic: str = ""
    status: FilmStatus = FilmStatus.QUEUED
    progress: int = 0
    message: str = "Queued."
    story: Optional[StorySpec] = None
    character_bible: Optional[CharacterBible] = None
    world_bible: Optional[WorldBible] = None
    cinematic_bible: Optional[CinematicBible] = None
    scenes: List[Scene] = Field(default_factory=list)
    edit_plan: Optional[EditPlan] = None
    audio_plan: Optional[AudioPlan] = None
    music_plan: Optional[MusicPlan] = None
    final_manifest_uri: str = ""
    qc_report: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("updated_at", mode="before")
    @classmethod
    def _ts(cls, _): return _now()
