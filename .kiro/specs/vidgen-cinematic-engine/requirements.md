# Requirements Document

## Introduction

This document defines the requirements for the VidGen Cinematic Engine upgrade. The system extends the existing VidGen autonomous film pipeline with two first-class production modes (`SHORT_FILM` and `PREMIUM_AUTOMOTIVE_AD`), a formal `VoiceBible` for per-character TTS voice assignment, mode-aware story and cinematic bible generation, and automotive-specific Veo prompt injection. All changes are additive and preserve backward compatibility with the existing simulation and production pipelines.

## Glossary

- **ProductionMode**: An enum (`SHORT_FILM` | `PREMIUM_AUTOMOTIVE_AD`) that governs story tone, cinematic bible defaults, and Veo prompt grammar.
- **VoiceAssignment**: A Pydantic model holding a character's TTS voice name and performance parameters (speaking_rate, pitch, volume_gain_db, performance_style).
- **VoiceBible**: A Pydantic model mapping `character_id` → `VoiceAssignment`, plus narrator voice defaults.
- **VoiceDesignAgent**: A new `BaseAgent` subclass that derives a `VoiceBible` from the `CharacterBible` using the LLM.
- **FilmProject**: The central Pydantic state object persisted to GCS at every pipeline stage boundary.
- **FilmCreateRequest**: The FastAPI request body for `POST /api/v1/films`.
- **Orchestrator**: The pipeline controller that sequences all agents and providers.
- **build_veo_generation_package**: The function in `agents.py` that constructs a self-contained Veo prompt and reference-asset list from a `Shot` and all production bibles.
- **StoryArchitectAgent**: The agent that generates the `StorySpec` from a topic and research.
- **CinematographerAgent**: The agent that generates the `CinematicBible` from the `FilmProject`.
- **System**: The VidGen application comprising `main.py`, `vidgen/orchestrator.py`, `vidgen/agents.py`, `vidgen/models.py`, and supporting modules.
- **MockVideoGenerator**: The video generator used in simulation mode; never calls the Veo API.
- **VeoVideoGenerator**: The video generator used in production mode; calls `veo-3.1-generate-001`.

---

## Requirements

### Requirement 1: ProductionMode Data Model

**User Story:** As a developer, I want a `ProductionMode` enum and related voice models in `models.py`, so that the system can represent and persist the two production modes and per-character voice assignments.

#### Acceptance Criteria

1. THE System SHALL define a `ProductionMode` enum with members `SHORT_FILM = "short_film"` and `PREMIUM_AUTOMOTIVE_AD = "premium_automotive_ad"` in `vidgen/models.py`.
2. THE System SHALL define a `VoiceAssignment` Pydantic model in `vidgen/models.py` with fields: `character_id: str`, `voice_name: str`, `speaking_rate: float`, `pitch: float`, `volume_gain_db: float`, `performance_style: str`.
3. THE System SHALL define a `VoiceBible` Pydantic model in `vidgen/models.py` with fields: `assignments: Dict[str, VoiceAssignment]`, `narrator_voice: str`, `narrator_speaking_rate: float`, `narrator_pitch: float`.
4. THE `FilmProject` model SHALL include a `production_mode: ProductionMode` field defaulting to `ProductionMode.SHORT_FILM`.
5. THE `FilmProject` model SHALL include a `voice_bible: Optional[VoiceBible]` field defaulting to `None`.
6. WHEN a `ProductionMode` value is serialised to JSON and deserialised, THE System SHALL produce an equivalent `ProductionMode` value (round-trip identity).
7. WHEN a `FilmProject` is serialised to JSON and deserialised, THE System SHALL preserve all new fields including `production_mode` and `voice_bible`.

### Requirement 2: FilmCreateRequest Production Mode Field

**User Story:** As an API client, I want to specify `production_mode` when creating a film, so that the pipeline applies the correct mode-specific behaviour from the start.

#### Acceptance Criteria

1. THE `FilmCreateRequest` model in `main.py` SHALL include a `production_mode: ProductionMode` field defaulting to `ProductionMode.SHORT_FILM`.
2. WHEN a `POST /api/v1/films` request omits `production_mode`, THE System SHALL use `ProductionMode.SHORT_FILM` as the default.
3. WHEN a `POST /api/v1/films` request includes `production_mode: "premium_automotive_ad"`, THE System SHALL create a `FilmProject` with `production_mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD`.
4. WHEN a `POST /api/v1/films` request includes an invalid `production_mode` value, THE System SHALL return HTTP 422.

### Requirement 3: VoiceDesignAgent

**User Story:** As a film director, I want unique, character-appropriate TTS voices assigned to each character, so that dialogue sounds distinct and matched to each character's personality.

#### Acceptance Criteria

1. THE System SHALL implement a `VoiceDesignAgent` class in `vidgen/agents.py` that extends `BaseAgent`.
2. THE `VoiceDesignAgent` SHALL expose a `design_voices(p: FilmProject) -> VoiceBible` method.
3. WHEN `design_voices` is called, THE `VoiceDesignAgent` SHALL use the LLM to derive a `performance_style`, `speaking_rate`, and `pitch` for each character in `p.character_bible`.
4. WHEN `design_voices` is called, THE `VoiceDesignAgent` SHALL assign a `voice_name` from the confirmed Neural2 voice pool (`en-US-Neural2-A/D/E/F/I/J`, `en-GB-Neural2-B/C/D`) without duplication across characters.
5. THE `VoiceDesignAgent` SHALL clamp `speaking_rate` to the range `[0.75, 1.05]` for every assignment.
6. THE `VoiceDesignAgent` SHALL clamp `pitch` to the range `[-4.0, 2.0]` for every assignment.
7. FOR ALL `VoiceBible` instances returned by `VoiceDesignAgent.design_voices`, THE System SHALL ensure all `voice_name` values in `assignments` are distinct.

### Requirement 4: Mode-Aware StoryArchitectAgent

**User Story:** As a producer, I want the story design to reflect the chosen production mode, so that `SHORT_FILM` projects produce character-driven drama and `PREMIUM_AUTOMOTIVE_AD` projects produce aspirational brand narratives.

#### Acceptance Criteria

1. THE `StoryArchitectAgent.design_story` method SHALL accept a `production_mode: ProductionMode` parameter (defaulting to `ProductionMode.SHORT_FILM`).
2. WHEN `production_mode == ProductionMode.SHORT_FILM`, THE `StoryArchitectAgent` SHALL include festival-film cinematic inspiration (Nolan, Cameron, Murphy, Bale, Hardy) in the LLM context.
3. WHEN `production_mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD`, THE `StoryArchitectAgent` SHALL include automotive brand narrative context (vehicle as hero product, aspirational tone, precise commercial pacing) in the LLM context.
4. THE `StoryArchitectAgent.design_story` method SHALL return a `StorySpec` with all fields non-empty for any valid topic and production mode.

### Requirement 5: Mode-Aware CinematographerAgent

**User Story:** As a director of photography, I want the cinematic bible to reflect the production mode, so that `SHORT_FILM` cinematics enforce intimate character-driven camera work and `PREMIUM_AUTOMOTIVE_AD` cinematics enforce vehicle-flattering compositions.

#### Acceptance Criteria

1. THE `CinematographerAgent.design_cinematics` method SHALL use `p.production_mode` when constructing the LLM prompt.
2. WHEN `p.production_mode == ProductionMode.SHORT_FILM`, THE `CinematographerAgent` SHALL produce a `CinematicBible` whose `camera_language` forbids drone shots and generic coverage.
3. WHEN `p.production_mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD`, THE `CinematographerAgent` SHALL produce a `CinematicBible` whose `color_palette` includes metallic or reflective language and whose `camera_language` specifies vehicle hero-framing rules.
4. THE `CinematographerAgent.design_cinematics` method SHALL return a `CinematicBible` with all five pillars (`color_palette`, `lighting`, `camera_language`, `texture`, `editing_rhythm`) non-empty.

### Requirement 6: Automotive Mandate in Veo Prompt

**User Story:** As a brand filmmaker, I want Veo prompts for automotive projects to contain a mandatory vehicle-identity block, so that every generated shot foregrounds the vehicle as the hero subject.

#### Acceptance Criteria

1. WHEN `p.production_mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD`, THE `build_veo_generation_package` function SHALL include a block containing the string `"AUTOMOTIVE MANDATE"` in the generated prompt.
2. WHEN `p.production_mode == ProductionMode.SHORT_FILM`, THE `build_veo_generation_package` function SHALL NOT include an `"AUTOMOTIVE MANDATE"` block in the generated prompt.
3. FOR ALL `FilmProject` instances with `production_mode == ProductionMode.PREMIUM_AUTOMOTIVE_AD`, EVERY prompt returned by `build_veo_generation_package` SHALL contain `"AUTOMOTIVE MANDATE"`.
4. THE `build_veo_generation_package` function SHALL NOT mutate the input `shot`, `p`, or `previous_shot` parameters.
5. FOR ALL reference assets in the package returned by `build_veo_generation_package`, THE System SHALL ensure every URI starts with `"gs://"`.

### Requirement 7: VoiceAgent Consumes VoiceBible

**User Story:** As a sound designer, I want dialogue synthesis to use the per-character voice assignments from the `VoiceBible`, so that each character has a consistent, distinct voice throughout the film.

#### Acceptance Criteria

1. WHEN `p.voice_bible` is set and contains an assignment for a character, THE `VoiceAgent.synthesize_dialogue` method SHALL use that character's `voice_name`, `speaking_rate`, and `pitch` from the `VoiceBible`.
2. WHEN `p.voice_bible` is absent or does not contain an assignment for a character, THE `VoiceAgent.synthesize_dialogue` method SHALL fall back to the existing round-robin Neural2 voice assignment.
3. THE `VoiceAgent.synthesize_dialogue` method SHALL return a list of `(local_path, timeline_dict)` tuples in chronological order.
4. THE timeline `start` values in the returned list SHALL be non-negative and monotonically non-decreasing across all dialogue lines.

### Requirement 8: Orchestrator Integrates VoiceDesignAgent

**User Story:** As a pipeline engineer, I want the Orchestrator to call `VoiceDesignAgent.design_voices` during the planning phase, so that a `VoiceBible` is available before any dialogue synthesis occurs.

#### Acceptance Criteria

1. THE `Orchestrator` SHALL instantiate a `VoiceDesignAgent` as `self.voice_design` in `__init__`.
2. WHEN the pipeline is in `FilmStatus.QUEUED` and `p.character_bible` is populated, THE `Orchestrator` SHALL call `self.voice_design.design_voices(p)` and assign the result to `p.voice_bible`.
3. WHEN `p.voice_bible` is already set and non-empty, THE `Orchestrator` SHALL skip the `design_voices` call (idempotency).
4. THE `Orchestrator` SHALL checkpoint the project state after setting `p.voice_bible`.

### Requirement 9: Pipeline Correctness and No Regressions

**User Story:** As a QA engineer, I want the full test suite to pass with no regressions after all changes, so that the upgrade does not break any existing functionality.

#### Acceptance Criteria

1. THE System SHALL pass all pre-existing tests in `tests/test_api.py`, `tests/test_config.py`, `tests/test_models.py`, `tests/test_providers.py`, `tests/test_regression_storyspec.py`, `tests/test_simulation.py`, and `tests/test_veo_fixes.py`.
2. THE System SHALL pass all new tests covering `ProductionMode`, `VoiceBible`, `VoiceDesignAgent`, mode-aware agents, and automotive prompt injection.
3. WHEN `settings.is_production == False`, THE System SHALL use `MockVideoGenerator` and never call `VeoVideoGenerator.generate_shot`.
4. WHEN `Orchestrator.run(p)` is called on a project with `p.status == FilmStatus.COMPLETED`, THE System SHALL return without modifying any GCS assets or re-generating any shots.

### Requirement 10: Production Readiness

**User Story:** As a DevOps engineer, I want the repository to be free of TODOs, placeholder implementations, and static analysis errors, so that the codebase is deployable without manual cleanup.

#### Acceptance Criteria

1. THE System SHALL contain no `TODO`, `FIXME`, `PLACEHOLDER`, or `pass  # not implemented` markers in any production code file.
2. THE System's `Dockerfile` SHALL successfully build a runnable image with all dependencies installed.
3. WHEN `flake8` or `pyflakes` is run on all production code files, THE System SHALL report no blocking errors (undefined names, syntax errors, import errors).
4. THE `VoiceDesignAgent`, `ProductionMode`, `VoiceBible`, and `VoiceAssignment` SHALL be importable from their respective modules without error.
