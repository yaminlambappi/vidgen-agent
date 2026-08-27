"""
Autonomous filmmaking agents — universal cinematic production engine.
Understands any subject: character, product, vehicle, location, object, concept.
Shot objectives drive prompt compilation — never generic paragraph prompts.
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional, Type
from google import genai
from google.genai import types
from google.cloud import texttospeech
from pathlib import Path
from pydantic import BaseModel
from vidgen.config import settings
from vidgen.models import (
    FilmProject, StorySpec, CharacterBible, WorldBible, CinematicBible,
    Scene, Shot, MusicPlan, EditPlan, Character, Location, AssetReference, AssetType,
    ProductionMode, VoiceAssignment, VoiceBible, ContentIntent, ShotObjective,
)
from vidgen.providers.image import GeminiImageGenerator
from vidgen.providers import get_storage_provider
from vidgen.utils.references import ensure_character_reference, ensure_location_reference
from vidgen.utils.retry import call_with_retry, RateLimitExhausted

_DET = ("400","404","403","invalid_argument","unsupported","not found",
        "does not have access","unauthenticated","permission denied")

def _is_det(e): return any(k in e.lower() for k in _DET)


class BaseAgent:
    def __init__(self, model=None):
        self.model = model or settings.DIRECTOR_MODEL
        self._client = None

    @property
    def client(self):
        if not self._client:
            self._client = genai.Client(vertexai=True,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.GOOGLE_CLOUD_LOCATION)
        return self._client

    def llm(self, prompt: str, system: str = "", schema: Type[BaseModel] | None = None) -> Any:
        """
        Call the Gemini LLM with the shared retry policy.
        Deterministic errors (400/403/404) are never retried.
        Transient errors (429/500/503) use exponential backoff.
        """
        cfg = {"system_instruction": system, "temperature": 0.7, "max_output_tokens": 16384}
        if schema:
            cfg.update(response_mime_type="application/json", response_schema=schema)

        def _call():
            r = self.client.models.generate_content(
                model=self.model, contents=prompt,
                config=types.GenerateContentConfig(**cfg))
            if schema:
                if getattr(r, "parsed", None) is not None:
                    return schema.model_validate(r.parsed)
                if r.text:
                    return schema.model_validate_json(r.text)
                raise RuntimeError("Gemini: no structured output")
            return r.text or ""

        try:
            return call_with_retry(
                fn=_call,
                provider="gemini-llm",
                model=self.model,
                operation="generate_content",
            )
        except RateLimitExhausted:
            raise  # Let callers (orchestrator) handle rate limit exhaustion
        except RuntimeError:
            raise


class ResearchAgent(BaseAgent):
    def ground(self, topic: str) -> str:
        try:
            cfg = types.GenerateContentConfig(
                system_instruction="You are a senior documentary researcher for a Werner Herzog film. Return profound, obscure, and evocative facts, dates, entities, and direct source URLs. Prioritize emotional truth and philosophical depth over dry data.",
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2, max_output_tokens=8000)
            r = self.client.models.generate_content(
                model=self.model,
                contents=(f"Deep research into the poetic and philosophical dimensions of the topic: {topic}"),
                config=cfg)
            return r.text or "Research unavailable."
        except Exception:
            return "Research unavailable — proceeding with general knowledge."


class ContentIntentAgent(BaseAgent):
    """
    First agent to run. Derives a ContentIntent from the raw topic.

    This is the universal content understanding layer.
    The primary subject may be a person, character, vehicle, product, building,
    location, animal, object, environment, event, or abstract concept visualised.
    It is NEVER assumed to be any particular type.

    Postconditions:
        - Returns ContentIntent with all key fields non-empty
        - primary_subject_type is one of the canonical values
        - prohibited_outcomes is non-empty (always generate at least one)
    """

    def understand(self, topic: str, production_mode: ProductionMode) -> ContentIntent:
        mode_hint = {
            ProductionMode.SHORT_FILM: (
                "This is a SHORT FILM. Characters, emotions, and narrative are central. "
                "The primary subject is almost certainly a person or character."
            ),
            ProductionMode.PREMIUM_AUTOMOTIVE_AD: (
                "This is a PREMIUM COMMERCIAL ADVERTISEMENT. "
                "The primary subject may be a vehicle, product, or brand concept. "
                "Do NOT assume vehicle — derive from the topic. "
                "If the topic describes a vehicle, make it primary. "
                "If the topic describes a person or emotion, they may be primary."
            ),
        }[production_mode]

        return self.llm(
            f"TOPIC: {topic}\n\n"
            f"PRODUCTION TYPE HINT: {mode_hint}\n\n"
            "Analyse the topic and determine the COMPLETE content intent for this production.\n\n"
            "For primary_subject_type, choose EXACTLY ONE of:\n"
            "  person | character | vehicle | product | location | animal | object | environment | concept\n\n"
            "For narrative_purpose, describe what story/message the content serves.\n"
            "For emotional_objective, describe how the audience should feel after watching.\n"
            "For visual_objective, describe the dominant visual impression.\n"
            "For realism_requirement, choose: photorealistic | stylised | abstract\n"
            "For shot_level_objectives, list the key types of shots needed (e.g. 'establish setting', "
            "'show product detail', 'capture character emotion', 'reveal information').\n"
            "For prohibited_outcomes, list what would make shots FAIL (e.g. "
            "'subject not visible', 'wrong character', 'generic background').\n"
            "For continuity_requirements, list what must stay consistent (e.g. 'character wardrobe', "
            "'vehicle colour', 'time of day').",
            "You are a creative director. Analyse the topic and return JSON with these exact fields: "
            "primary_subject, primary_subject_type, secondary_subjects (list), characters (list), "
            "locations (list), narrative_purpose, emotional_objective, visual_objective, "
            "genre, tone, target_audience, brand_product_requirements, realism_requirement, "
            "continuity_requirements (list), shot_level_objectives (list), prohibited_outcomes (list).",
            ContentIntent
        )


class VoiceDesignAgent(BaseAgent):
    """
    Assigns a unique, character-appropriate TTS voice and performance parameters
    to each character in the CharacterBible.
    """

    # Available Neural2 voices confirmed in Cloud TTS
    _VOICE_POOL = [
        "en-US-Neural2-A", "en-US-Neural2-D", "en-US-Neural2-E",
        "en-US-Neural2-F", "en-US-Neural2-I", "en-US-Neural2-J",
        "en-GB-Neural2-B", "en-GB-Neural2-C", "en-GB-Neural2-D",
    ]

    def design_voices(self, p: FilmProject) -> VoiceBible:
        """
        Uses LLM to match character personality to voice parameters,
        then maps to a concrete voice from _VOICE_POOL without duplication.

        Postconditions:
            - Returns VoiceBible with one VoiceAssignment per character
            - All voice_name values in assignments are distinct
            - speaking_rate is clamped to [0.75, 1.05]
            - pitch is clamped to [-4.0, 2.0]
        """
        class VoiceSpec(BaseModel):
            character_id: str
            performance_style: str
            speaking_rate: float
            pitch: float

        class Out(BaseModel):
            specs: List[VoiceSpec]

        chars = p.character_bible.characters
        r = self.llm(
            f"Characters: {[f'{c.character_id}={c.name}: {c.personality}' for c in chars]}\n"
            "Assign a unique vocal performance style to each character. "
            "speaking_rate in [0.75, 1.05]; pitch in [-4.0, 2.0].",
            "You are a voice director. Return JSON with a 'specs' array.",
            Out
        )
        assignments: Dict[str, VoiceAssignment] = {}
        used_voices: set = set()
        for i, spec in enumerate(r.specs):
            pool = [v for v in self._VOICE_POOL if v not in used_voices]
            voice = pool[0] if pool else self._VOICE_POOL[i % len(self._VOICE_POOL)]
            used_voices.add(voice)
            assignments[spec.character_id] = VoiceAssignment(
                character_id=spec.character_id,
                voice_name=voice,
                speaking_rate=max(0.75, min(1.05, spec.speaking_rate)),
                pitch=max(-4.0, min(2.0, spec.pitch)),
                performance_style=spec.performance_style,
            )
        return VoiceBible(assignments=assignments)


class StoryArchitectAgent(BaseAgent):
    def design_story(self, topic: str, research: str,
                     production_mode: "ProductionMode" = None,
                     content_intent: "ContentIntent | None" = None) -> StorySpec:
        """
        Universal story design — works for any content type.
        Uses ContentIntent as the primary driver; production_mode provides supplementary context.
        """
        if production_mode is None:
            production_mode = ProductionMode.SHORT_FILM

        # Build intent-driven context rather than hard-coded mode strings
        if content_intent:
            intent_context = (
                f"PRIMARY SUBJECT: {content_intent.primary_subject} "
                f"(type: {content_intent.primary_subject_type})\n"
                f"NARRATIVE PURPOSE: {content_intent.narrative_purpose}\n"
                f"EMOTIONAL OBJECTIVE: {content_intent.emotional_objective}\n"
                f"GENRE: {content_intent.genre or 'derived from topic'}\n"
                f"TONE: {content_intent.tone}\n"
                f"AUDIENCE: {content_intent.target_audience}\n"
            )
            if content_intent.brand_product_requirements:
                intent_context += f"BRAND/PRODUCT REQUIREMENTS: {content_intent.brand_product_requirements}\n"
        else:
            # Fallback for backward compatibility — should not be reached in normal operation
            intent_context = {
                ProductionMode.SHORT_FILM: (
                    "This is a SHORT FILM for international festival submission. "
                    "Cinematic inspiration: Christopher Nolan (temporal structure, moral weight), "
                    "James Cameron (spectacle grounded in human stakes). "
                    "Performance quality target: internality, restraint, physicality, subtext. "
                    "Generate ORIGINAL fictional characters. Do NOT reference or reproduce real people."
                ),
                ProductionMode.PREMIUM_AUTOMOTIVE_AD: (
                    "This is a PREMIUM AUTOMOTIVE ADVERTISEMENT. "
                    "Tone: aspirational, cinematic, precise. "
                    "No generic automotive tropes. Aim for art-directed brand filmmaking."
                ),
            }[production_mode]

        return self.llm(
            f"Topic: {topic}\n\nResearch:\n{research[:4000]}\n\n"
            f"Content Intent:\n{intent_context}\n\n"
            "Create a 3-act story structure appropriate for this content.\n"
            "ACT I: Establish the world, subject, and central tension — show, don't tell.\n"
            "ACT II: Escalate. Force the subject/characters to confront their core challenge.\n"
            "ACT III: Resolve in a way that is both surprising and inevitable.\n"
            "The story must serve the primary subject and emotional objective above.",
            "You are a master director. Return JSON: title (evocative), logline (≤ 2 sentences), "
            "theme (a universal question), genre, three_act_structure (detailed).",
            StorySpec)


class CharacterDesignAgent(BaseAgent):
    def design_characters(self, p: FilmProject) -> CharacterBible:
        class Out(BaseModel):
            characters: List[Character]
        r = self.llm(
            f'Film: "{p.story.title}"\n'
            f'Theme: "{p.story.theme}" (A question to be explored, not a statement)\n'
            f'Three Act Structure: {p.story.three_act_structure}\n\n'
                        "Create EXACTLY 3 unforgettable, festival-worthy characters to inhabit this story. They must feel like real people, not plot devices.\n\n"
            			"For each character, provide a detailed description with profound depth:\n"
            "- physical_description: Highly specific, memorable details for visual identity (e.g., 'a scar above the left eyebrow from a childhood accident', not 'average height').\n"
            "- wardrobe: A signature outfit that reflects their personality and circumstances. Be exact.\n"
            "- personality: Contradictory and complex. What is their mask? What lies beneath?\n"
            "- motivation: A deep, primal need (e.g., 'to feel seen', not 'to get a promotion').\n"
            "- fear: A specific, existential dread that drives their behavior.\n"
            "- mannerisms: A unique, repeatable gesture or habit.\n"
            "- arc: How do they transform (or fail to transform) in response to the story's central question? The arc must be subtle and earned.",
            "You are a casting director and character artist. Return JSON. Descriptions must be powerful and cinematic.",
            Out)
        
        image_generator = GeminiImageGenerator()
        storage = get_storage_provider()

        for char in r.characters:
            prompt = (
                f"CINEMATIC HEADSHOT. Photorealistic, 8k, detailed. A character for a film. "
                f"Name: {char.name}. PHYSICALITY: {char.physical_description}. WARDROBE: {char.wardrobe}. "
                "The image must be a striking, emotionally resonant portrait with dramatic lighting, suitable as a reference for a high-end film production."
            )
            ensure_character_reference(char, p.project_id, storage, image_generator, prompt)

        return CharacterBible(characters=r.characters[:3])


class WorldDesignAgent(BaseAgent):
    def design_world(self, p: FilmProject) -> WorldBible:
        class Out(BaseModel):
            locations: List[Location]
        r = self.llm(
            f'Film: "{p.story.title}"\n'
            f'Theme: "{p.story.theme}"\n'
            f'Three Act Structure: {p.story.three_act_structure}\n\n'
                        "Design EXACTLY 3 recurring locations that are characters in themselves, not just backdrops. They must breathe with history and subtext.\n\n"
            			"For each location:\n"
            "- description: Evocative, multi-sensory prose (4 sentences). What does it smell like? What is the texture of the walls? What history is embedded here?\n"
            "- time_of_day: The specific time that holds the most emotional weight (e.g., 'the pre-dawn blue hour').\n"
            "- lighting: The quality and source of light (e.g., 'harsh, flickering fluorescent tubes casting long shadows').\n"
            "- atmosphere: The emotional tone of the space (e.g., 'oppressive silence broken by the distant hum of machinery').\n"
            "- recurring_props: A list of 3-5 specific, significant props that characters interact with.",
            "You are a production designer. Return JSON. Descriptions must be cinematic and charged with meaning.",
            Out)
        locations = r.locations[:3]
        image_generator = GeminiImageGenerator()
        storage = get_storage_provider()
        for loc in locations:
            prompt = ("CANONICAL LOCATION REFERENCE, photorealistic cinematic production-design still. "
                      f"LOCATION: {loc.name}. {loc.description}. TIME: {loc.time_of_day}. "
                      f"LIGHTING: {loc.lighting}. RECURRING PROPS: {', '.join(loc.recurring_props)}. "
                      "No people, no text, no logos. Preserve this exact geography and props in every shot.")
            ensure_location_reference(loc, p.project_id, storage, image_generator, prompt)
        return WorldBible(locations=locations)


class CinematographerAgent(BaseAgent):
    def design_cinematics(self, p: FilmProject) -> CinematicBible:
        """
        Universal cinematic bible — derived from ContentIntent and StorySpec.
        Works for any content type without hard-coded genre rules.

        Postconditions:
            - All five pillars non-empty
            - camera_language contains explicit prohibitions and requirements
            - color_palette contains psychological/functional reasoning
        """
        intent = p.content_intent
        mode = getattr(p, "production_mode", ProductionMode.SHORT_FILM)

        # Build subject-specific guidance from intent rather than mode switch
        subject_guidance = ""
        if intent:
            pst = intent.primary_subject_type
            if pst == "vehicle":
                subject_guidance = (
                    "The hero subject is a vehicle. Visual choices must flatter it: "
                    "hero angles, material quality, surface reflections, kinetic energy."
                )
            elif pst in ("product", "object"):
                subject_guidance = (
                    f"The hero subject is a {pst}. Visual choices must showcase its "
                    "materials, scale, texture, and function."
                )
            elif pst in ("person", "character"):
                subject_guidance = (
                    "Human performance and internal emotional life are central. "
                    "Camera works with the actor, not around them."
                )
            elif pst == "location":
                subject_guidance = (
                    "The location itself is the subject. Architecture, atmosphere, "
                    "and environmental detail are paramount."
                )
            elif pst == "environment":
                subject_guidance = (
                    "Environment and atmosphere ARE the subject. Natural phenomena, "
                    "light, texture, and scale carry the narrative."
                )
            else:
                subject_guidance = (
                    f"The primary subject is {intent.primary_subject}. "
                    "Frame and compose to make it unmistakably the focal point."
                )
            subject_guidance += (
                f"\nVisual objective: {intent.visual_objective}"
                f"\nEmotional objective: {intent.emotional_objective}"
                f"\nProhibited outcomes: {', '.join(intent.prohibited_outcomes[:3])}"
                if intent.prohibited_outcomes else ""
            )

        mode_guidance = {
            ProductionMode.SHORT_FILM: (
                "FORBIDDEN camera choices: drone shots, generic establishing wides with no character, "
                "unmotivated slow motion, lens flares for decoration, Dutch angles. "
                "REQUIRED: Every camera decision must have a specific narrative justification."
            ),
            ProductionMode.PREMIUM_AUTOMOTIVE_AD: (
                "FORBIDDEN camera choices: shaky hand-held, Dutch angles, "
                "anything that obscures the primary subject's hero angles. "
                "REQUIRED: Low angles that flatter silhouettes, tracking shots revealing surfaces, "
                "macro detail inserts showing materials."
            ),
        }[mode]

        story_context = ""
        if p.story:
            story_context = (
                f'Film/Content: "{p.story.title}"\n'
                f"Theme: {p.story.theme}\n"
                f"Genre: {p.story.genre}\n"
            )

        return self.llm(
            f"{story_context}"
            f"Subject guidance: {subject_guidance}\n\n"
            "Define a unique, precise cinematic identity:\n"
            "1. color_palette: Specific palette with clear psychological/functional reasoning.\n"
            "2. lighting: A strict philosophy with source, quality, and contrast rules.\n"
            f"3. camera_language: Rules of camera. INCLUDE: {mode_guidance}\n"
            "4. texture: Grain, sharpness, DOF philosophy — serves the subject.\n"
            "5. editing_rhythm: Cut rationale, hold lengths, pacing appropriate to content.",
            "You are a world-class Director of Photography. Return JSON. "
            "No generic terms. Every choice must have clear purpose. All five fields non-empty.",
            CinematicBible)


class ScreenwriterAgent(BaseAgent):
    def write_scenes(self, p: FilmProject, research: str) -> List[Scene]:
        chars = p.character_bible.characters
        locs = p.world_bible.locations
        class Out(BaseModel):
            scenes: List[Scene]
        r = self.llm(
            f'Film: "{p.story.title}" | Theme: {p.story.theme}\n\n'
            f"Research:\n{research[:2000]}\n\n"
            f"Characters: {[f'{c.character_id}={c.name}: {c.personality}, Arc: {c.arc}' for c in chars]}\n"
            f"Locations: {[f'{l.location_id}={l.name}: {l.description}' for l in locs]}\n\n"
            "Write EXACTLY 3 pivotal scenes that are the pillars of the film's three-act structure. Show, don't tell.\n"
            "Each scene MUST:\n"
            "- Have a title that hints at its core tension.\n"
            "- Have a 2-3 sentence description of the scene's external action and internal subtext.\n"
            "- Use a specific location_id.\n"
            "- Contain 4-5 sentences of powerful, poetic voiceover prose for the narration_text. This is not exposition; it is thematic reflection.\n"
            "- Contain 2-4 lines of sharp, character-driven dialogue. Each line must have a character_id and the text of the line.\n"
            "- Have a clear dramatic_purpose (e.g., 'To reveal the protagonist's hidden vulnerability').\n"
            "- Include an empty list for shots.",
            "You are a master screenwriter. Return JSON. Use EXACT location_id and character_id values. Dialogue and narration must be profound.",
            Out)
        scenes = r.scenes[:3]
        for i, s in enumerate(scenes):
            s.index = i + 1
            s.scene_id = f"S{i+1}"
        return scenes


class StoryboardAgent(BaseAgent):
    def design_shots(self, scene: Scene, p: FilmProject) -> List[Shot]:
        """
        Designs shots for a scene, deriving a ShotObjective for each shot before
        building the shot specification. The objective drives composition decisions
        rather than generic cinematic defaults.
        """
        n = settings.SHOTS_PER_SCENE
        chars = p.character_bible.characters if p.character_bible else []
        locs = p.world_bible.locations if p.world_bible else []
        loc = next((l for l in locs if l.location_id == scene.location_id), None)
        loc_desc = f"{loc.name}: {loc.description} | {loc.lighting}" if loc else scene.location_id
        char_block = "\n".join(
            f"  ID={c.character_id} NAME={c.name}: {c.physical_description} | Wardrobe: {c.wardrobe}"
            for c in chars) or "  No named characters"
        cine = p.cinematic_bible

        # Build intent-aware context
        intent = p.content_intent
        intent_block = ""
        if intent:
            intent_block = (
                f"\nPRIMARY SUBJECT: {intent.primary_subject} (type: {intent.primary_subject_type})"
                f"\nSHOT LEVEL OBJECTIVES: {', '.join(intent.shot_level_objectives)}"
                f"\nPROHIBITED OUTCOMES: {', '.join(intent.prohibited_outcomes[:3])}"
                f"\nCONTINUITY: {', '.join(intent.continuity_requirements[:3])}"
            )

        class Out(BaseModel):
            shots: List[Shot]

        r = self.llm(
            f'Design EXACTLY {n} shots for Scene {scene.index}: "{scene.title}"\n'
            f"Dramatic purpose: {scene.dramatic_purpose}\n"
            f"Location: {loc_desc}\n"
            f"Characters:\n{char_block}\n"
            f"Cinematic Bible: palette={cine.color_palette} | lighting={cine.lighting} | "
            f"camera={cine.camera_language} | texture={cine.texture}\n"
            f"{intent_block}\n\n"
            "SHOT DESIGN RULES:\n"
            "- Every shot must have an explicit visual OBJECTIVE (what the audience must see/feel)\n"
            "- Subject visibility is determined by the shot objective, not a fixed rule\n"
            "- Sequence shots to build narrative momentum\n"
            "- Performance direction must be specific and actionable\n\n"
            f"Each of the {n} shots MUST include ALL fields:\n"
            "- shot_type, subject, action, camera, lens, movement, composition\n"
            "- lighting, atmosphere, character_ids (list), emotional_direction\n"
            "- performance_objective, performance_subtext, physical_behavior, eyelines\n"
            "- sound, transition\n"
            "- duration (integer 4–12 seconds based on shot purpose)\n"
            "- shot_objective: an object with fields: what_must_audience_see, primary_subject, "
            "  subject_action, where, story_beat, continuity_requirements (list), "
            "  must_not_lose (list), camera_rationale, lighting_rationale, failure_conditions (list)\n",
            f"You are a visionary director. Return JSON with {n} shots. Every field mandatory and specific.",
            Out)

        shots = r.shots[:n]
        for i, shot in enumerate(shots):
            shot.index = i + 1
            shot.shot_id = f"{scene.scene_id}_SH{i+1:02d}"
            shot.location_id = scene.location_id
            # Ensure shot_objective has shot_id set
            if shot.shot_objective:
                shot.shot_objective.shot_id = shot.shot_id
        return shots


class VoiceAgent:
    def __init__(self):
        self.client = texttospeech.TextToSpeechClient()
        # Round-robin fallback pool (used when no VoiceBible is present)
        self._fallback_pool = [
            "en-US-Neural2-A", "en-US-Neural2-D", "en-US-Neural2-E",
            "en-GB-Neural2-B", "en-GB-Neural2-C",
        ]
        self._fallback_map: Dict[str, str] = {}

    def _get_voice_for_character(self, character_id: str, characters: List[Character],
                                  voice_bible=None) -> tuple[str, float, float]:
        """
        Returns (voice_name, speaking_rate, pitch) for a character.

        Priority:
          1. VoiceBible assignment (canonical per-character voice)
          2. Round-robin fallback from _fallback_pool (backward compat)
        """
        # 1. VoiceBible assignment
        if voice_bible and hasattr(voice_bible, "assignments"):
            assignment = voice_bible.assignments.get(character_id)
            if assignment and assignment.voice_name:
                return assignment.voice_name, assignment.speaking_rate, assignment.pitch

        # 2. Round-robin fallback — stable mapping per character_id
        if character_id not in self._fallback_map:
            char_index = next(
                (i for i, c in enumerate(characters) if c.character_id == character_id), -1)
            idx = char_index if char_index >= 0 else len(self._fallback_map)
            self._fallback_map[character_id] = self._fallback_pool[idx % len(self._fallback_pool)]
        return self._fallback_map[character_id], 1.0, 0.0

    def synthesize_narration(self, text: str, output_path: str) -> None:
        resp = self.client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code="en-US", name=settings.TTS_VOICE,
                ssml_gender=texttospeech.SsmlVoiceGender.MALE),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                sample_rate_hertz=48000, speaking_rate=0.85, pitch=-2.0))
        Path(output_path).write_bytes(resp.audio_content)

    # Backward-compat alias used by orchestrator and tests
    def synthesize(self, text: str, output_path: str) -> None:
        self.synthesize_narration(text, output_path)

    def synthesize_dialogue(self, p: FilmProject, root: Path) -> List[tuple[str, dict]]:
        """
        Synthesises every dialogue line in the project.

        Uses per-character VoiceBible assignment when available; falls back to
        round-robin Neural2 voice pool for backward compatibility.

        Postconditions:
            - Returns list of (local_path, timeline_dict) in chronological order
            - timeline start values are non-negative and monotonically non-decreasing
        """
        dialogue_paths: List[tuple[str, dict]] = []
        if not p.character_bible:
            return []

        voice_bible = getattr(p, "voice_bible", None)
        cursor = 0.0

        for scene in sorted(p.scenes, key=lambda x: x.index):
            scene_duration = sum(shot.duration for shot in scene.shots)
            lines = [ln for ln in scene.dialogue if ln.line.strip()]
            slot = scene_duration / max(1, len(lines))

            for i, line in enumerate(lines):
                if not line.line.strip():
                    continue
                voice_name, speaking_rate, pitch = self._get_voice_for_character(
                    line.character_id, p.character_bible.characters, voice_bible)

                output_path = root / f"dialogue_{scene.index}_{i}_{line.character_id}.mp3"
                resp = self.client.synthesize_speech(
                    input=texttospeech.SynthesisInput(text=line.line),
                    voice=texttospeech.VoiceSelectionParams(
                        language_code="en-US", name=voice_name),
                    audio_config=texttospeech.AudioConfig(
                        audio_encoding=texttospeech.AudioEncoding.MP3,
                        sample_rate_hertz=48000,
                        speaking_rate=speaking_rate,
                        pitch=pitch,
                    )
                )
                output_path.write_bytes(resp.audio_content)
                start = max(0.0, cursor + 0.75 + (i * slot))
                dialogue_paths.append((str(output_path), {
                    "scene_id": scene.scene_id,
                    "character_id": line.character_id,
                    "text": line.line,
                    "start": start,
                }))
            cursor += scene_duration

        return dialogue_paths


class MusicAgent(BaseAgent):
    def compose_plan(self, p: FilmProject) -> MusicPlan:
        total = sum(sh.duration for sc in p.scenes for sh in sc.shots)
        return self.llm(
            f"Compose a festival-worthy score plan for a {total}s film titled '{p.story.title}'.\n"
            f"Theme: {p.story.theme}\n"
            "The score must be subtle, atmospheric, and emotionally resonant, never overpowering the narrative. "
            "Think less 'soundtrack', more 'sonic texture'. References: Hildur Guðnadóttir (Joker), Mica Levi (Under the Skin).\n\n"
            "Provide a detailed plan covering:\n"
            "- mood: The core feeling (e.g., 'A sense of creeping dread with moments of fragile hope').\n"
            "- tempo: A specific BPM range (e.g., '60-65 bpm').\n"
            "- instrumentation: A small, unique ensemble (e.g., 'A cello quartet, prepared piano, and distant granular synth pads').\n"
            "- structure: How the score evolves across the three acts.",
            "You are a film composer. Return JSON.",
            MusicPlan)


class EditorAgent:
    def compile(self, p: FilmProject) -> EditPlan:
        # For now, simple sequence. Future: could re-order for pacing.
        return EditPlan(sequence=[
            sh.shot_id
            for sc in sorted(p.scenes, key=lambda x: x.index)
            for sh in sorted(sc.shots, key=lambda x: x.index)])


class SubtitleAgent:
    def generate(self, p: FilmProject) -> str:
        lines, idx, t = [], 1, 0.0
        for sc in sorted(p.scenes, key=lambda x: x.index):
            dur = sum(sh.duration for sh in sc.shots)
            if sc.narration_text:
                words = sc.narration_text.strip().split()
                if words:
                    # Distribute words across the duration with 0.5s buffer at start and end
                    effective_dur = max(0.1, dur - 1.0)
                    group_size = 3
                    groups = [words[i:i+group_size] for i in range(0, len(words), group_size)]
                    group_dur = effective_dur / len(groups)
                    
                    for i, group in enumerate(groups):
                        start_time = t + 0.5 + (i * group_dur)
                        end_time = start_time + group_dur
                        lines += [str(idx), f"{_srt(start_time)} --> {_srt(end_time)}",
                                  " ".join(group), ""]
                        idx += 1
            t += dur
        return "\n".join(lines)


def _srt(s: float) -> str:
    ms = int((s % 1) * 1000)
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def build_veo_generation_package(shot: Shot, p: FilmProject, feedback: str = "", previous_shot: Shot | None = None) -> dict:
    """
    Hierarchical structured Veo prompt compiler.

    Prompt section priority order (A → J):
      A. PROJECT IMMUTABLES    — realism, production rules, format
      B. STORY CONTEXT         — why this shot exists in the narrative
      C. SUBJECT IDENTITY      — the exact person/product/object/character
      D. SHOT OBJECTIVE        — the single most important visual outcome
      E. ACTION                — what the subject is doing
      F. ENVIRONMENT           — only what is necessary to support the shot
      G. CAMERA                — framing, lens, movement, composition
      H. LIGHTING              — appropriate to scene and continuity
      I. CONTINUITY            — what must match previous/next shots
      J. NEGATIVE CONSTRAINTS  — specific failure modes that would invalidate the shot

    Intent → subject → action → continuity → composition → environment → style

    Postconditions:
        - Returns dict with 'prompt' (str) and 'reference_assets' (List[dict])
        - All reference_asset URIs start with 'gs://'
        - Does not mutate shot, p, or previous_shot
        - PREMIUM_AUTOMOTIVE_AD mode: prompt contains 'AUTOMOTIVE MANDATE'
        - Prompt sections are ordered by priority, not aesthetic preference
    """
    loc = next((l for l in (p.world_bible.locations if p.world_bible else [])
                if l.location_id == shot.location_id), None)
    visible_chars = [c for c in (p.character_bible.characters if p.character_bible else [])
                     if c.character_id in shot.character_ids]
    cine = p.cinematic_bible
    mode = getattr(p, "production_mode", ProductionMode.SHORT_FILM)
    intent = getattr(p, "content_intent", None)
    obj = shot.shot_objective  # may be None on legacy/test shots

    parts: List[str] = []

    # ── A. PROJECT IMMUTABLES ────────────────────────────────────────────────
    realism = "photorealistic" if not intent else intent.realism_requirement or "photorealistic"
    parts.append(
        f"PHOTOGRAPHIC FILM SHOT. {realism.upper()}. 8K resolution. "
        "No text overlays, watermarks, titles, or logos. "
        "Single continuous shot — no montage, no cuts within the shot."
    )

    # ── QC corrective feedback (prepended — model must address first) ────────
    if feedback:
        if "artifact" in feedback.lower() or "distorted" in feedback.lower():
            neg = (feedback
                   .replace("CORRECTIVE FEEDBACK: The previous attempt failed QC. Address these issues: ", "")
                   .replace("Visual artifacts detected: ", "")
                   .replace("Continuity break: ", ""))
            parts.append(
                f"== CORRECTION REQUIRED ==\n"
                f"PREVIOUS TAKE FAILED. Specifically avoid: {neg}\n"
                "Prioritise photorealism, natural anatomy, correct subject identity.\n"
                "== END CORRECTION ==")
        else:
            parts.append(f"== CORRECTION REQUIRED ==\n{feedback}\n== END CORRECTION ==")

    # ── Mode-specific mandates (injected only for relevant modes) ────────────
    if mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD:
        # Derive subject type from intent; only inject vehicle mandate if subject IS a vehicle
        pst = intent.primary_subject_type if intent else "vehicle"
        if pst == "vehicle":
            parts.append(
                "== AUTOMOTIVE MANDATE ==\n"
                f"The hero subject is the vehicle: {intent.primary_subject if intent else 'the vehicle'}. "
                "Render with studio-quality materials: paint clearcoat depth, panel reflection accuracy, "
                "tyre sidewall detail, interior ambient glow. "
                "Physics must be believable (no floating, no gravity errors). "
                "Camera angles must flatter the vehicle silhouette and surface geometry. "
                "Human subjects are supporting cast — never obscure the vehicle's hero angles.\n"
                "== END AUTOMOTIVE MANDATE ==")
        else:
            # Automotive mode but non-vehicle primary subject — use general commercial mandate
            parts.append(
                "== COMMERCIAL MANDATE ==\n"
                f"This is a premium commercial production. Primary subject: {intent.primary_subject if intent else 'the subject'}. "
                "Every frame must be agency-quality. Clean, intentional composition.\n"
                "== END COMMERCIAL MANDATE ==")

    # ── B. STORY CONTEXT ─────────────────────────────────────────────────────
    if obj and obj.story_beat:
        parts.append(f"STORY BEAT: {obj.story_beat}")
    elif shot.dramatic_purpose if hasattr(shot, "dramatic_purpose") else False:
        parts.append(f"STORY BEAT: {shot.dramatic_purpose}")  # type: ignore

    # ── C. SUBJECT IDENTITY ──────────────────────────────────────────────────
    # Primary subject (may be a character, product, location, or concept)
    if intent and intent.primary_subject:
        parts.append(f"PRIMARY SUBJECT: {intent.primary_subject}.")

    # Character identity blocks (full physical description for visual lock)
    for c in visible_chars:
        parts.append(
            f"CHARACTER IDENTITY (DO NOT ALTER): {c.name}. "
            f"PHYSICALITY: {c.physical_description}. "
            f"WARDROBE: {c.wardrobe}. "
            f"MANNERISMS: {c.mannerisms}.")

    # ── D. SHOT OBJECTIVE ────────────────────────────────────────────────────
    if obj and obj.what_must_audience_see:
        parts.append(f"SHOT OBJECTIVE (most important): {obj.what_must_audience_see}")
    else:
        # Fallback: derive objective from subject + action
        parts.append(f"SHOT OBJECTIVE: Show {shot.subject} {shot.action}.")

    # ── E. ACTION ────────────────────────────────────────────────────────────
    parts.append(f"ACTION: {shot.action}. Natural, unforced, motivated.")

    # Performance direction (for human subjects)
    perf = " | ".join(x for x in [
        f"objective={shot.performance_objective}"      if shot.performance_objective else "",
        f"emotion={shot.emotional_direction}"          if shot.emotional_direction else "",
        f"subtext={shot.performance_subtext}"          if shot.performance_subtext else "",
        f"physical behavior={shot.physical_behavior}"  if shot.physical_behavior else "",
        f"eyelines={shot.eyelines}"                    if shot.eyelines else "",
    ] if x)
    if perf:
        parts.append(f"PERFORMANCE: {perf}")

    # ── F. ENVIRONMENT ───────────────────────────────────────────────────────
    if loc:
        parts.append(
            f"ENVIRONMENT: {loc.name}. {loc.description} "
            f"Time: {loc.time_of_day}. Atmosphere: {loc.atmosphere}.")
        if loc.recurring_props:
            parts.append(f"Key props: {', '.join(loc.recurring_props[:4])}.")
    if shot.atmosphere:
        parts.append(f"ATMOSPHERE: {shot.atmosphere}.")
    if shot.sound:
        parts.append(f"SOUND: {shot.sound}.")

    # ── G. CAMERA ────────────────────────────────────────────────────────────
    cam_parts = [shot.shot_type]
    if shot.camera:   cam_parts.append(shot.camera)
    if shot.lens:     cam_parts.append(f"lens {shot.lens}")
    if shot.movement: cam_parts.append(f"movement {shot.movement}")
    if obj and obj.camera_rationale:
        cam_parts.append(f"rationale: {obj.camera_rationale}")
    parts.append(f"CAMERA: {', '.join(cam_parts)}.")
    if shot.composition:
        parts.append(f"COMPOSITION: {shot.composition}.")

    # ── H. LIGHTING ──────────────────────────────────────────────────────────
    lighting = shot.lighting or (cine.lighting if cine else "natural available light")
    if obj and obj.lighting_rationale:
        parts.append(f"LIGHTING: {lighting}. Rationale: {obj.lighting_rationale}.")
    else:
        parts.append(f"LIGHTING: {lighting}.")

    # ── Cinematic Identity Mandate (style, after subject/action/camera) ───────
    if cine:
        parts.append(
            "== CINEMATIC IDENTITY ==\n"
            f"COLOR: {cine.color_palette}. "
            f"TEXTURE: {cine.texture}. "
            f"CAMERA RULES: {cine.camera_language}.\n"
            "== END CINEMATIC IDENTITY ==")

    # ── I. CONTINUITY ────────────────────────────────────────────────────────
    # Reference assets — collected by priority
    reference_assets: List[AssetReference] = []
    seen_uris: set = set()

    def _add_ref(asset: AssetReference) -> None:
        if asset.uri and asset.uri.startswith("gs://") and asset.uri not in seen_uris:
            seen_uris.add(asset.uri)
            reference_assets.append(asset)

    # 1. Location canonical reference
    if loc and loc.canonical_visual_assets:
        _add_ref(loc.canonical_visual_assets[0])

    # 2. All visible character references
    for c in visible_chars:
        if c.canonical_visual_assets:
            _add_ref(c.canonical_visual_assets[0])
        elif c.reference_image_uri and c.reference_image_uri.startswith("gs://"):
            _add_ref(AssetReference(
                asset_type=AssetType.IMAGE, uri=c.reference_image_uri,
                metadata={"role": "character_identity", "mime_type": "image/png"}))

    # 3. Intra-scene previous-shot continuity frame
    if (previous_shot
            and previous_shot.generated_frame_uris
            and previous_shot.scene_id == shot.scene_id):
        frame_uri = previous_shot.generated_frame_uris[0]
        if frame_uri.startswith("gs://"):
            _add_ref(AssetReference(
                asset_type=AssetType.IMAGE, uri=frame_uri,
                metadata={"role": "previous_shot_continuity_frame", "mime_type": "image/png"}))

    if reference_assets:
        parts.append(
            "CANONICAL REFERENCES (maintain all visual identities exactly): "
            + "; ".join(
                f"{a.metadata.get('role', 'identity')}={a.uri}"
                for a in reference_assets))

    # Text-level continuity note
    if previous_shot:
        if previous_shot.scene_id != shot.scene_id:
            parts.append(
                "SCENE TRANSITION: New scene begins. Establish new location cleanly. "
                "Preserve character appearance and wardrobe exactly.")
        else:
            cont_reqs = []
            if obj and obj.continuity_requirements:
                cont_reqs = obj.continuity_requirements
            parts.append(
                f"INTRA-SCENE CONTINUITY: "
                f"prev_subject={previous_shot.subject}; prev_action={previous_shot.action}; "
                f"location={previous_shot.location_id}. "
                "Continue: screen direction, wardrobe, props, emotional state, lighting."
                + (f" Must preserve: {', '.join(cont_reqs)}." if cont_reqs else ""))

    # ── J. NEGATIVE CONSTRAINTS ──────────────────────────────────────────────
    neg_constraints: List[str] = []

    if obj and obj.failure_conditions:
        neg_constraints.extend(obj.failure_conditions[:4])
    if intent and intent.prohibited_outcomes:
        neg_constraints.extend(intent.prohibited_outcomes[:3])
    if not neg_constraints:
        neg_constraints = [
            f"{shot.subject} not clearly visible",
            "random background substituted for scripted location",
            "character identity changed from previous shots",
        ]

    parts.append(
        "== DO NOT GENERATE ==\n"
        + " | ".join(neg_constraints)
        + "\n== END DO NOT GENERATE ==")

    parts.append("Output: single continuous shot, highest possible photographic quality.")

    return {
        "prompt": "\n".join(parts),
        "reference_assets": [a.model_dump() for a in reference_assets],
    }
