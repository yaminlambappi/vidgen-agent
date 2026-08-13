# Master Execution Blueprint: Autonomous Agentic Filmmaking Studio (`vidgen-agent`)

## Core Directive
Transform `vidgen-agent` into a fully autonomous, multi-agent cinematic film studio. 
- **Cost Constraint**: `FILM_MODE=simulation` must be the default. No real Veo or expensive video/audio APIs can be invoked during testing, simulation, or development.
- **Production Guard**: Real video generation requires BOTH `FILM_MODE=production` AND `ALLOW_REAL_GENERATION=true`.

## Phase 1: Data Models & Persistence (`models.py`)
Implement strongly typed Pydantic models for:
- `StorySpec`, `Character`, `CharacterBible`, `Location`, `WorldBible`, `CinematicBible`, `Scene`, `Shot`, `FilmProject`, `EditPlan`, `AudioPlan`, `FinalManifest`.
- Ensure every entity has stable IDs (`project_id`, `character_id`, `location_id`, `scene_id`, `shot_id`).

## Phase 2: Provider-Abstracted Video Generator (`generators/veo.py`)
Implement:
- Abstract base class `VideoGenerator`.
- `MockVideoGenerator`: Returns valid mock metadata, fake artifact paths, and validates schemas without calling external APIs.
- `VeoVideoGenerator`: Integrates securely with the official `google-genai` SDK (`client.models.generate_videos`) with proper async polling and error handling.

## Phase 3: The 18 Autonomous Agents (`agents.py`)
Implement the logical agent classes:
1. Master Director Agent
2. Story Architect Agent
3. Screenwriter Agent
4. Character Design Agent (with persistent Character Bible & Canonical visual references)
5. World / Location Design Agent (World Bible)
6. Prop / Object Continuity Agent
7. Cinematographer Agent (Cinematic Bible)
8. Storyboard / Shot Design Agent
9. Visual Consistency Agent (Resolving character/location/prop references before generation)
10. Video Generation Agent
11. Performance / Voice Agent
12. Sound Design Agent
13. Music / Score Agent
14. Editor Agent (Edit Decision List / EDL compilation)
15. Film Critic Agent (Evaluates story, visuals, editing, audio)
16. Continuity Supervisor Agent (Preflight validation against canonical state)
17. Revision Agent (Targeted shot regeneration)
18. Quality Gate Agent (Gates 1 through 12)

## Phase 4: Structured Prompt Compiler
Build a deterministic prompt builder that avoids string concatenation and compiles sections: WORLD, CHARACTER, CHARACTER STATE, LOCATION, ACTION, EMOTION, CAMERA, LENS, COMPOSITION, LIGHTING, COLOR, ATMOSPHERE, CONTINUITY, REFERENCE ASSETS, NEGATIVE CONSTRAINTS.

## Phase 5: Pipeline Orchestrator & Checkpoints (`orchestrator.py`)
- Implement resumable execution workflows.
- Enable checkpoints and state persistence to Google Cloud Storage (`projects/{project_id}/...`).

## Phase 6: FastAPI Backend & CLI (`main.py`)
- Preserve existing endpoints while adding `/films`, `/films/{id}/simulate`, `/films/{id}/production/authorize`, and `/films/{id}/generate`.
- Implement CLI commands for running simulation mode.

## Phase 7: Testing Suite (`tests/test_simulation.py`)
- Implement comprehensive unit and integration tests using mocks.
- Add an explicit test proving that **Simulation Mode never invokes the real video provider**.
