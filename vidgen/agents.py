"""
Autonomous filmmaking agents — Gemini text only, no image generation.
All character/location identity embedded in rich Veo prompts.
Upgraded for festival-grade quality.
"""
from __future__ import annotations
import time
from typing import Any, List, Optional, Type
from google import genai
from google.genai import types
from google.cloud import texttospeech
from pathlib import Path
from pydantic import BaseModel
from vidgen.config import settings
from vidgen.models import (
    FilmProject, StorySpec, CharacterBible, WorldBible, CinematicBible,
    Scene, Shot, MusicPlan, EditPlan, Character, Location, AssetReference, AssetType,
)
from vidgen.providers.image import GeminiImageGenerator
from vidgen.providers import get_storage_provider

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


class StoryArchitectAgent(BaseAgent):
    def design_story(self, topic: str, research: str) -> StorySpec:
        return self.llm(
            f"Topic: {topic}\n\nResearch (focus on emotional/philosophical):\n{research[:4000]}\n\n"
            "Create a 3-act documentary-drama for a discerning international film festival audience. "
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
        references_dir = Path("references")
        references_dir.mkdir(exist_ok=True)
        
        for char in r.characters:
            prompt = (
                f"CINEMATIC HEADSHOT. Photorealistic, 8k, detailed. A character for a film. "
                f"Name: {char.name}. PHYSICALITY: {char.physical_description}. WARDROBE: {char.wardrobe}. "
                "The image must be a striking, emotionally resonant portrait with dramatic lighting, suitable as a reference for a high-end film production."
            )
            image_bytes = image_generator.generate(prompt)
            if image_bytes:
                image_path = references_dir / f"character_{char.character_id}.png"
                image_path.write_bytes(image_bytes)
                char.reference_image_path = str(image_path)

                # Upload to GCS and save URI
                gcs_path = f"projects/{p.project_id}/references/character_{char.character_id}.png"
                char.reference_image_uri = storage.upload(str(image_path), gcs_path)
                char.canonical_visual_assets = [AssetReference(
                    asset_type=AssetType.IMAGE, uri=char.reference_image_uri,
                    metadata={"role": "character_identity", "character_id": char.character_id,
                              "mime_type": "image/png"})]

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
        references_dir = Path("references")
        references_dir.mkdir(exist_ok=True)
        for loc in locations:
            prompt = ("CANONICAL LOCATION REFERENCE, photorealistic cinematic production-design still. "
                      f"LOCATION: {loc.name}. {loc.description}. TIME: {loc.time_of_day}. "
                      f"LIGHTING: {loc.lighting}. RECURRING PROPS: {', '.join(loc.recurring_props)}. "
                      "No people, no text, no logos. Preserve this exact geography and props in every shot.")
            image_bytes = image_generator.generate(prompt)
            if not image_bytes:
                raise RuntimeError(f"No canonical image returned for location {loc.name}")
            image_path = references_dir / f"location_{loc.location_id}.png"
            image_path.write_bytes(image_bytes)
            uri = storage.upload(str(image_path), f"projects/{p.project_id}/references/location_{loc.location_id}.png")
            loc.canonical_visual_assets = [AssetReference(
                asset_type=AssetType.IMAGE, uri=uri,
                metadata={"role": "location_identity", "location_id": loc.location_id,
                          "mime_type": "image/png"})]
        return WorldBible(locations=locations)


class CinematographerAgent(BaseAgent):
    def design_cinematics(self, p: FilmProject) -> CinematicBible:
        return self.llm(
            f'Craft a unique, auteur cinematic bible for the film "{p.story.title}".\n'
            "Style: Social realism meets poetic visual storytelling. The camera is an intimate observer, not a neutral one. "
            "Filmmaker References: Andrei Tarkovsky (for spiritual texture), Lynne Ramsay (for sensory detail), Chloé Zhao (for authentic humanism).\n\n"
            "Define the film's visual identity with absolute precision across these five pillars:\n"
            "1. color_palette: A specific, restrictive palette with clear psychological reasoning (e.g., 'Desaturated ochres and slate greys dominate, with a single, recurring shock of crimson to signify memory').\n"
            "2. lighting: A strict lighting philosophy (e.g., 'Exclusively motivated by natural or practical sources. High contrast, with deep shadows that conceal as much as they reveal').\n"
            "3. camera_language: The rules of camera position and movement (e.g., 'Handheld but steady, staying at eye-level. Zooms are forbidden. A slow push-in is used only twice, to mark crucial shifts in a character's internal state').\n"
            "4. texture: The tactile quality of the image (e.g., 'A visible 16mm-style grain. Lens flares are embraced but must be organic. Focus is shallow, isolating characters from their environment').\n"
            "5. editing_rhythm: The pacing and flow (e.g., 'Contemplative, long takes. Jump cuts are used sparingly to convey psychological distress. Match cuts link thematic ideas across scenes.').",
            "You are a world-class Director of Photography. Return JSON. No generic terms. Every choice must have a clear artistic purpose.",
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
            "- duration: integer 8\n",
            f"You are a visionary director. Return JSON array of exactly {n} shots. Every field is mandatory and must be specific and cinematic.",
            Out)

        shots = r.shots[:n]
        for i, shot in enumerate(shots):
            shot.scene_id = scene.scene_id
            shot.index = i + 1
            shot.location_id = scene.location_id
            shot.duration = 8
        return shots


class VoiceAgent:
    def __init__(self):
        self.client = texttospeech.TextToSpeechClient()
        self.voice_map = {}

    def _get_voice_for_character(self, character_id: str, characters: List[Character]) -> str:
        if character_id not in self.voice_map:
            # Simple round-robin voice assignment for now
            voice_options = [
                "en-US-Neural2-A", "en-US-Neural2-D", "en-US-Neural2-E",
                "en-GB-Neural2-B", "en-GB-Neural2-C"
            ]
            char_index = next((i for i, c in enumerate(characters) if c.character_id == character_id), -1)
            if char_index != -1:
                self.voice_map[character_id] = voice_options[char_index % len(voice_options)]
            else:
                self.voice_map[character_id] = "en-US-Neural2-J" # Default fallback
        return self.voice_map[character_id]

    def synthesize_narration(self, text: str, output_path: str) -> None:
        # Upgraded for more gravitas and slower pace suitable for documentary
        resp = self.client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code="en-US", name=settings.TTS_VOICE, ssml_gender=texttospeech.SsmlVoiceGender.MALE),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                sample_rate_hertz=48000, speaking_rate=0.85, pitch=-2.0))
        Path(output_path).write_bytes(resp.audio_content)

    # Compatibility seam for callers/tests that provide a single production TTS mock.
    def synthesize(self, text: str, output_path: str) -> None:
        self.synthesize_narration(text, output_path)

    def synthesize_dialogue(self, p: FilmProject, root: Path) -> List[tuple[str, dict]]:
        dialogue_paths = []
        if not p.character_bible:
            return []
            
        cursor = 0.0
        for scene in sorted(p.scenes, key=lambda x: x.index):
            scene_duration = sum(shot.duration for shot in scene.shots)
            lines = [line for line in scene.dialogue if line.line.strip()]
            for i, line in enumerate(scene.dialogue):
                if not line.line.strip():
                    continue
                voice_name = self._get_voice_for_character(line.character_id, p.character_bible.characters)
                output_path = root / f"dialogue_{scene.index}_{i}_{line.character_id}.mp3"
                
                resp = self.client.synthesize_speech(
                    input=texttospeech.SynthesisInput(text=line.line),
                    voice=texttospeech.VoiceSelectionParams(language_code="en-US", name=voice_name),
                    audio_config=texttospeech.AudioConfig(
                        audio_encoding=texttospeech.AudioEncoding.MP3,
                        sample_rate_hertz=48000
                    )
                )
                output_path.write_bytes(resp.audio_content)
                slot = scene_duration / max(1, len(lines))
                dialogue_paths.append((str(output_path), {
                    "scene_id": scene.scene_id, "character_id": line.character_id,
                    "text": line.line, "start": cursor + 0.75 + (i * slot),
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
                # Add a slight delay for readability
                start_time = t + 0.5
                end_time = t + dur - 0.5
                lines += [str(idx), f"{_srt(start_time)} --> {_srt(end_time)}",
                          sc.narration_text.strip(), ""]
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
    Upgraded for festival-grade cinematic identity and polish.
    """
    loc = next((l for l in (p.world_bible.locations if p.world_bible else [])
                if l.location_id == shot.location_id), None)
    visible_chars = [c for c in (p.character_bible.characters if p.character_bible else [])
                     if c.character_id in shot.character_ids]
    cine = p.cinematic_bible

    parts = [
        "CINEMATIC FILM SHOT. 8K. Photorealistic. Emotionally resonant. No text, titles, watermarks, logos.",
        "This is for a serious, award-winning documentary-drama film."
    ]

    # Inject QC feedback first if it exists
    if feedback:
        parts.append(feedback)

    # Cinematic Identity Mandate
    if cine:
        parts.append(
            f"== CINEMATIC IDENTITY MANDATE ==\n"
            f"Strictly adhere to this visual philosophy.\n"
            f"COLOR: {cine.color_palette}.\n"
            f"LIGHTING: {cine.lighting}.\n"
            f"TEXTURE: {cine.texture}.\n"
            f"CAMERA: {cine.camera_language}.\n"
            f"RHYTHM: {cine.editing_rhythm}.\n"
            f"== END MANDATE =="
        )

    # Location
    if loc:
        parts.append(f"SCENE: {loc.name}. {loc.description} "
                     f"Time: {loc.time_of_day}. The atmosphere is {loc.atmosphere}.")
        if loc.recurring_props:
            parts.append(f"Key props visible: {', '.join(loc.recurring_props[:4])}.")

    # Subject + Action
    parts.append(f"SUBJECT: {shot.subject}.")
    parts.append(f"ACTION: {shot.action}. The action should be natural and unforced.")

    # Characters with full identity for continuity
    reference_assets = []
    if loc:
        reference_assets.extend(loc.canonical_visual_assets)
    for c in visible_chars:
        parts.append(
            f"CHARACTER: {c.name}. PHYSICALITY: {c.physical_description}. "
            f"WARDROBE: {c.wardrobe}. MANNERISMS: {c.mannerisms}. "
            f"PERFORMANCE (subtle and internal): {shot.emotional_direction or c.motivation}.")
        reference_assets.extend(c.canonical_visual_assets)
        if c.reference_image_uri and not c.canonical_visual_assets:
            reference_assets.append(AssetReference(asset_type=AssetType.IMAGE, uri=c.reference_image_uri,
                                                    metadata={"role": "character_identity", "mime_type": "image/png"}))

    if reference_assets:
        parts.append("CANONICAL REFERENCE ASSETS (provided to the model, never ignore): " + "; ".join(
            f"{a.metadata.get('role', 'identity')}={a.uri}" for a in reference_assets))
    if previous_shot:
        parts.append(f"PREVIOUS SHOT CONTINUITY: {previous_shot.subject}; {previous_shot.action}; "
                     f"location={previous_shot.location_id}; transition={previous_shot.transition}. "
                     "Continue screen direction, wardrobe, props, emotional state, and lighting progression.")

    # Camera & Composition
    cam_parts = [shot.shot_type]
    if shot.camera: cam_parts.append(shot.camera)
    if shot.lens: cam_parts.append(f"lens {shot.lens}")
    if shot.movement: cam_parts.append(f"movement must be {shot.movement}")
    parts.append(f"CAMERA: {', '.join(cam_parts)}.")
    if shot.composition: parts.append(f"COMPOSITION: {shot.composition}. The framing is deliberate and meaningful.")

    # Lighting
    lighting = shot.lighting or (cine.lighting if cine else "natural available light")
    parts.append(f"LIGHTING: Must be {lighting}. Avoid artificial, overlit aesthetics.")

    # Atmosphere + Sound
    if shot.atmosphere: parts.append(f"ATMOSPHERE: {shot.atmosphere}.")
    if shot.sound: parts.append(f"PRODUCTION SOUND: The key ambient sound is {shot.sound}.")
    parts.append("PERFORMANCE: " + " | ".join(x for x in [
        f"objective={shot.performance_objective}" if shot.performance_objective else "",
        f"emotion={shot.emotional_direction}" if shot.emotional_direction else "",
        f"subtext={shot.performance_subtext}" if shot.performance_subtext else "",
        f"physical behavior={shot.physical_behavior}" if shot.physical_behavior else "",
        f"eyelines={shot.eyelines}" if shot.eyelines else ""] if x))

    # Final Command
    parts.append("Final output must be a single, continuous shot of the highest possible cinematic quality.")
    parts.append("CONTINUITY IS PARAMOUNT: Preserve all character appearances, wardrobe, and location details exactly as described. No random elements.")

    return {
        "prompt": " ".join(parts),
        "reference_assets": [a.model_dump() for a in reference_assets],
    }
