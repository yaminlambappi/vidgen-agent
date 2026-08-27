"""
Autonomous filmmaking agents — Gemini text only, no image generation.
All character/location identity embedded in rich Veo prompts.
Upgraded for festival-grade quality.
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
    ProductionMode, VoiceAssignment, VoiceBible,
)
from vidgen.providers.image import GeminiImageGenerator
from vidgen.providers import get_storage_provider
from vidgen.utils.references import ensure_character_reference, ensure_location_reference

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
        # Upgraded temperature for more creative but grounded generation
        cfg = {"system_instruction": system, "temperature": 0.7, "max_output_tokens": 16384}
        if schema:
            cfg.update(response_mime_type="application/json", response_schema=schema)
        last = None
        for attempt in range(settings.RETRY_ATTEMPTS):
            try:
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
            except Exception as exc:
                if _is_det(str(exc)):
                    raise RuntimeError(f"Deterministic Gemini failure: {exc}")
                last = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Gemini failed: {last}")


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
                     production_mode: "ProductionMode" = None) -> StorySpec:
        if production_mode is None:
            production_mode = ProductionMode.SHORT_FILM
        mode_context = {
            ProductionMode.SHORT_FILM: (
                "This is a SHORT FILM for international festival submission. "
                "Cinematic inspiration: Christopher Nolan (temporal structure, moral weight), "
                "James Cameron (spectacle grounded in human stakes). "
                "Performance quality: Cillian Murphy (internality, restraint), "
                "Christian Bale (physical transformation, commitment), "
                "Tom Hardy (physicality, subtext). "
                "Generate ORIGINAL fictional characters. Do NOT reference or reproduce real people."
            ),
            ProductionMode.PREMIUM_AUTOMOTIVE_AD: (
                "This is a PREMIUM AUTOMOTIVE ADVERTISEMENT. "
                "The hero product is the vehicle. Human characters exist to contextualise the vehicle's identity. "
                "Tone: aspirational, cinematic, futuristic. Pacing: precise, commercial, punchy. "
                "Every shot must showcase vehicle materials, reflections, motion physics, or silhouette. "
                "No generic car-ad tropes. Aim for art-directed brand filmmaking."
            ),
        }[production_mode]
        return self.llm(
            f"Topic: {topic}\n\nResearch (focus on emotional/philosophical):\n{research[:4000]}\n\n"
            f"Production Mode Context:\n{mode_context}\n\n"
            "Create a 3-act story for a discerning international audience. "
            "The story must feature three fictional, deeply interconnected characters whose lives reveal the topic's core tensions. "
            "Avoid cliche. Aim for moral ambiguity and emotional complexity.\n"
            "ACT I: Introduce the world and characters. Present the central conflict subtly, through character actions, not exposition.\n"
            "ACT II: Escalate the conflict. The characters face profound internal and external challenges, forcing them to confront their core beliefs.\n"
            "ACT III: A powerful, non-obvious climax. Resolve the conflict in a way that is both surprising and inevitable, leaving the audience with a lingering question, not a simple answer.",
            "You are a master screenwriter. Return JSON: title (evocative, not literal), logline (concise, poetic), theme (a universal question), genre (e.g., 'Psychological Drama'), three_act_structure (detailed, emotionally resonant).",
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
        Returns a CinematicBible with all five pillars non-empty, tuned to production_mode.

        Preconditions:
            - p.story.title and p.story.theme are non-empty
            - p.production_mode is set

        Postconditions:
            - SHORT_FILM: camera_language forbids drone shots and generic coverage
            - PREMIUM_AUTOMOTIVE_AD: color_palette includes metallic/reflective language;
              camera_language specifies hero product framing rules
            - All five pillars (color_palette, lighting, camera_language, texture,
              editing_rhythm) are non-empty strings
        """
        mode = getattr(p, "production_mode", ProductionMode.SHORT_FILM)

        if mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD:
            mode_prompt = (
                f'Craft a cinematic bible for a PREMIUM AUTOMOTIVE ADVERTISEMENT: "{p.story.title}".\n'
                "The hero subject is the vehicle. Every visual choice must flatter the vehicle.\n\n"
                "Requirements:\n"
                "1. color_palette: Metallic and reflective language — deep blacks, polished chrome highlights, "
                "clearcoat paint depth, cool whites that catch on aerodynamic surfaces.\n"
                "2. lighting: Studio-grade controlled lighting. Three-point setups for hero product shots. "
                "Practical ambient light for environmental sequences. No blown highlights on bodywork.\n"
                "3. camera_language: FORBIDDEN: drone shots, hand-held wobble, Dutch angles. "
                "REQUIRED: Low hero angles flattering vehicle silhouette. "
                "Tracking shots that reveal bodywork as sculpture. "
                "Macro detail inserts. Slow dolly approaches emphasising material quality.\n"
                "4. texture: Ultra-sharp 8K studio sharpness. No grain. Deep focus on vehicle surfaces. "
                "Shallow DOF only for interior cockpit macro details.\n"
                "5. editing_rhythm: Precise commercial pacing. 3–6 second cuts during action sequences. "
                "Hold on product reveals. Typography and tagline pacing if applicable."
            )
        else:
            mode_prompt = (
                f'Craft a unique, auteur cinematic bible for the short film "{p.story.title}".\n'
                "Style: Social realism meets poetic visual storytelling. "
                "The camera is an intimate observer, not a neutral one.\n\n"
                "Requirements:\n"
                "1. color_palette: A specific, restrictive palette with clear psychological reasoning.\n"
                "2. lighting: A strict lighting philosophy motivated by natural or practical sources.\n"
                "3. camera_language: FORBIDDEN: drone shots, generic wide establishing shots, "
                "unmotivated slow-motion, lens flares for style alone, Dutch angles. "
                "REQUIRED: Eye-level handheld or locked-off compositions. "
                "Every camera move must have a specific narrative purpose.\n"
                "4. texture: The tactile quality of the image — grain, focus philosophy, depth of field rules.\n"
                "5. editing_rhythm: Pacing and flow — cut rationale, hold lengths, scene transitions."
            )

        return self.llm(
            mode_prompt,
            "You are a world-class Director of Photography. Return JSON. "
            "No generic terms. Every choice must have a clear, specific artistic purpose. "
            "All five fields must be non-empty.",
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
        n = settings.SHOTS_PER_SCENE
        chars = p.character_bible.characters
        loc = next((l for l in p.world_bible.locations if l.location_id == scene.location_id), None)
        loc_desc = f"{loc.name}: {loc.description} | {loc.lighting}" if loc else scene.location_id
        char_block = "\n".join(
            f"  ID={c.character_id} NAME={c.name}: {c.physical_description} | Wardrobe: {c.wardrobe}" for c in chars)
        cine = p.cinematic_bible

        class Out(BaseModel):
            shots: List[Shot]

        r = self.llm(
            f'Design EXACTLY {n} shots for Scene {scene.index}: "{scene.title}" ({scene.dramatic_purpose})\n'
            f"Location: {loc_desc}\n"
            f"Characters:\n{char_block}\n"
            f"Cinematic Bible: palette={cine.color_palette} | lighting={cine.lighting} | camera={cine.camera_language} | texture={cine.texture}\n\n"
            f"SHOT SEQUENCE PHILOSOPHY ({n} shots):\n"
            "Follow a rigorous visual grammar. Start wide to establish context and slowly move closer to characters to build intimacy and tension. Use composition to reflect power dynamics. Every shot must serve the scene's dramatic purpose.\n"
            "Example flow: Establishing Wide -> Medium on action -> Close-up on face -> Insert detail -> Close-up on reaction -> Medium two-shot -> Hold on a final, meaningful wide.\n\n"
            "Each of the {n} shots MUST include ALL fields with no exceptions:\n"
            "- shot_type: (e.g., 'extreme wide', 'medium close-up', 'insert')\n"
            "- subject: The precise focal point of the shot.\n"
            "- action: The specific physical activity occurring. Present tense.\n"
            "- camera: Position and angle (e.g., 'low angle, looking up').\n"
            "- lens: (e.g., '24mm wide-angle', '85mm portrait')\n"
            "- movement: (e.g., 'static on tripod', 'subtle handheld drift', 'slow dolly push-in')\n"
            "- composition: (e.g., 'rule of thirds, character on left', 'deep focus with subject in foreground')\n"
            "- lighting: Precise source and quality (e.g., 'single key light from a window, creating chiaroscuro').\n"
            "- atmosphere: The sensory mood (e.g., 'hazy from smoke, sound of distant sirens').\n"
            "- character_ids: List of character_id strings. Empty if none.\n"
            "- emotional_direction: A specific, actionable note for the actor (e.g., 'tries to appear confident, but her trembling hands betray her fear').\n"
            "- performance_objective: What the performer is trying to get in this moment.\n"
            "- performance_subtext: What they conceal or cannot say.\n"
            "- physical_behavior: Specific hands, posture, breath, facial behavior and pacing.\n"
            "- eyelines: Exact focus and interaction eyelines.\n"
            "- sound: Key ambient sounds to be captured.\n"
            "- transition: The reason for the cut (e.g., 'cut on action', 'match cut to a similar shape').\n"
            "- duration: An integer number of seconds for the shot's length, between 4 and 12, based on the shot's creative purpose.\n",
            f"You are a visionary director. Return JSON array of exactly {n} shots. Every field is mandatory and must be specific and cinematic.",
            Out)

        shots = r.shots[:n]
        for i, shot in enumerate(shots):
            shot.index = i + 1
            shot.shot_id = f"{scene.scene_id}_SH{i+1:02d}"
            shot.location_id = scene.location_id
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
    Construct a rich, self-contained Veo generation package from shot + Film Bible.

    Postconditions:
        - Returns dict with keys 'prompt' (str) and 'reference_assets' (List[dict])
        - All reference_asset URIs start with 'gs://'
        - PREMIUM_AUTOMOTIVE_AD mode: prompt contains 'AUTOMOTIVE MANDATE'
        - Does not mutate shot, p, or previous_shot
    """
    loc = next((l for l in (p.world_bible.locations if p.world_bible else [])
                if l.location_id == shot.location_id), None)
    visible_chars = [c for c in (p.character_bible.characters if p.character_bible else [])
                     if c.character_id in shot.character_ids]
    cine = p.cinematic_bible
    mode = getattr(p, "production_mode", ProductionMode.SHORT_FILM)

    parts = [
        "CINEMATIC FILM SHOT. 8K. Photorealistic. Emotionally resonant. No text, titles, watermarks, logos.",
        "This is for a serious, award-winning cinematic production."
    ]

    # QC corrective feedback (prepended so the model addresses it first)
    if feedback:
        if "artifact" in feedback.lower() or "distorted" in feedback.lower():
            neg = (feedback
                   .replace("CORRECTIVE FEEDBACK: The previous attempt failed QC. Address these issues: ", "")
                   .replace("Visual artifacts detected: ", "")
                   .replace("Continuity break: ", ""))
            parts.append(
                f"== DO NOT GENERATE ==\nAVOID: {neg}\n"
                "Focus on photorealism and natural anatomy. No distorted hands, faces, or props.\n"
                "== END DO NOT GENERATE ==")
        else:
            parts.append(feedback)

    # Automotive mandate
    if mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD:
        parts.append(
            "== AUTOMOTIVE MANDATE ==\n"
            "The hero subject is the vehicle. Render with studio-quality materials: "
            "paint clearcoat depth, panel reflection accuracy, tyre sidewall detail, "
            "interior ambient glow. Physics must be believable (no floating, no gravity errors). "
            "Camera angles must flatter the vehicle silhouette and surface geometry. "
            "Human subjects are supporting cast — never obscure the vehicle's hero angles. "
            "Every frame must look like it could open a premium automotive brand film.\n"
            "== END AUTOMOTIVE MANDATE ==")

    # Cinematic Identity Mandate
    if cine:
        parts.append(
            "== CINEMATIC IDENTITY MANDATE ==\n"
            f"Strictly adhere to this visual philosophy.\n"
            f"COLOR: {cine.color_palette}.\n"
            f"LIGHTING: {cine.lighting}.\n"
            f"TEXTURE: {cine.texture}.\n"
            f"CAMERA: {cine.camera_language}.\n"
            f"RHYTHM: {cine.editing_rhythm}.\n"
            "== END MANDATE ==")

    # Location context
    if loc:
        parts.append(
            f"SCENE: {loc.name}. {loc.description} "
            f"Time: {loc.time_of_day}. Atmosphere: {loc.atmosphere}.")
        if loc.recurring_props:
            parts.append(f"Key props visible: {', '.join(loc.recurring_props[:4])}.")

    # Subject + Action
    parts.append(f"SUBJECT: {shot.subject}.")
    parts.append(f"ACTION: {shot.action}. The action should be natural and unforced.")

    # Character identity blocks (full physical description for every visible character)
    for c in visible_chars:
        parts.append(
            f"CHARACTER: {c.name}. "
            f"PHYSICALITY: {c.physical_description}. "
            f"WARDROBE: {c.wardrobe}. "
            f"MANNERISMS: {c.mannerisms}. "
            f"PERFORMANCE (restrained, internal): {shot.emotional_direction or c.motivation}.")

    # ── REFERENCE ASSET COLLECTION ──────────────────────────────────────────
    # Priority:
    #   1. Location canonical reference  (scene geography lock)
    #   2. All visible character canonical references  (identity lock)
    #   3. Previous-shot continuity frame  (intra-scene only)
    # Only GCS URIs are accepted — non-GCS URIs are silently skipped.
    reference_assets: List[AssetReference] = []
    seen_uris: set = set()

    def _add_ref(asset: AssetReference) -> None:
        if asset.uri and asset.uri.startswith("gs://") and asset.uri not in seen_uris:
            seen_uris.add(asset.uri)
            reference_assets.append(asset)

    if loc and loc.canonical_visual_assets:
        _add_ref(loc.canonical_visual_assets[0])

    for c in visible_chars:
        if c.canonical_visual_assets:
            _add_ref(c.canonical_visual_assets[0])
        elif c.reference_image_uri and c.reference_image_uri.startswith("gs://"):
            _add_ref(AssetReference(
                asset_type=AssetType.IMAGE, uri=c.reference_image_uri,
                metadata={"role": "character_identity", "mime_type": "image/png"}))

    # Intra-scene previous-shot continuity frame
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
            "CANONICAL REFERENCE ASSETS (maintain all visual identities exactly): "
            + "; ".join(
                f"{a.metadata.get('role', 'identity')}={a.uri}"
                for a in reference_assets))

    # Cross-shot continuity note (text-level)
    if previous_shot:
        if previous_shot.scene_id != shot.scene_id:
            parts.append(
                "NOTE: This is the first shot of a NEW SCENE. "
                "Establish the new location cleanly while preserving character appearance and wardrobe.")
        else:
            parts.append(
                f"PREVIOUS SHOT CONTINUITY: subject={previous_shot.subject}; "
                f"action={previous_shot.action}; location={previous_shot.location_id}. "
                "Continue screen direction, wardrobe, props, emotional state, and lighting exactly.")

    # Camera & Composition
    cam_parts = [shot.shot_type]
    if shot.camera:    cam_parts.append(shot.camera)
    if shot.lens:      cam_parts.append(f"lens {shot.lens}")
    if shot.movement:  cam_parts.append(f"movement must be {shot.movement}")
    parts.append(f"CAMERA: {', '.join(cam_parts)}.")
    if shot.composition:
        parts.append(f"COMPOSITION: {shot.composition}. The framing is deliberate and meaningful.")

    # Lighting
    lighting = shot.lighting or (cine.lighting if cine else "natural available light")
    parts.append(f"LIGHTING: {lighting}. Avoid artificial, overlit aesthetics.")

    # Atmosphere + Sound
    if shot.atmosphere: parts.append(f"ATMOSPHERE: {shot.atmosphere}.")
    if shot.sound:      parts.append(f"PRODUCTION SOUND: {shot.sound}.")

    # Acting direction
    perf = " | ".join(x for x in [
        f"objective={shot.performance_objective}"   if shot.performance_objective else "",
        f"emotion={shot.emotional_direction}"       if shot.emotional_direction else "",
        f"subtext={shot.performance_subtext}"       if shot.performance_subtext else "",
        f"physical behavior={shot.physical_behavior}" if shot.physical_behavior else "",
        f"eyelines={shot.eyelines}"                 if shot.eyelines else "",
    ] if x)
    if perf:
        parts.append(f"PERFORMANCE: {perf}")

    parts.append("Final output: a single, continuous shot of the highest cinematic quality.")
    parts.append(
        "CONTINUITY IS PARAMOUNT: Preserve all character appearances, wardrobe, and location details "
        "exactly as described. No random elements.")

    return {
        "prompt": " ".join(parts),
        "reference_assets": [a.model_dump() for a in reference_assets],
    }
