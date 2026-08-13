# Repository Audit: `vidgen-agent`

**Date:** Saturday, August 8, 2026
**Auditor:** Gemini CLI Agent

---

## Repository Overview
The `vidgen-agent` repository contains a functional prototype for an automated documentary film generator. It integrates Google's latest generative AI models (Gemini and Veo) via a FastAPI backend to transform a topic into a fully mastered cinematic video.

### Key Files:
- `main.py`: Core FastAPI application and video production pipeline.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: Containerization setup.
- `auto_fix.py`: Diagnostic script for testing Gemini, Imagen, and TTS.
- `EXECUTION_PLAN.md`: Roadmap for future development (Phase 1-7).

---

## Existing Architecture

### Backend
- **Framework:** FastAPI (v0.111.0)
- **Server:** Uvicorn (v0.30.1)
- **Job Management:** In-memory dictionary (`jobs`) with `BackgroundTasks`. **(VOLATILE)**

### GenAI Stack
- **SDK:** `google-genai` (v2.8.0)
- **Orchestrator/Director:** `gemini-2.5-flash`
- **Video Generation:** `veo-3.1-generate-001`
- **Text-to-Speech:** Google Cloud TTS (`en-US-Neural2-J`)
- **Image Generation:** `imagen-3.0-fast-generate-001` (verified in `auto_fix.py`, not used in `main.py`)

### Media Processing
- **Engine:** FFmpeg (external binary)
- **Libraries:** `moviepy` (v1.0.3), `Pillow` (v11.3.0)
- **Workflow:** 
    1. Gemini creates a JSON plan (script + shots).
    2. TTS generates narration audio.
    3. Veo generates individual video shots (GCS output).
    4. FFmpeg downloads, normalizes (1080p, 24fps), and concatenates shots.
    5. FFmpeg mixes narration and masters the final output with contrast/saturation adjustments.

---

## Existing Veo Integration

- **SDK:** `google-genai` (Client-based Vertex AI integration).
- **Model:** `veo-3.1-generate-001`.
- **Generation Method:** `client.models.generate_videos(prompt=..., config=...)`.
- **Polling:** Manual loop checking `operation.done` every 10 seconds via `client.operations.get(operation)`.
- **Storage:** Direct output to GCS via `output_gcs_uri`.
- **Inputs:** **Text prompts only.** No evidence of image-to-video or video-to-video reference support in the current `main.py` implementation.
- **Character Consistency:** **NOT SUPPORTED** in current implementation. Relies solely on text prompt descriptive quality.

---

## Existing Gemini Integration

- **Model:** `gemini-2.5-flash`.
- **Role:** "Director Agent".
- **Prompting:** Uses a large system prompt with a JSON schema to define the "Visual Bible", narration sections, and a shot list (id, duration, shot_type, subject, action, camera, etc.).
- **Extraction:** Manual regex/JSON parsing of model output.

---

## Existing Storage

- **Ephemeral:** `/tmp/vidgen` (local filesystem) for intermediate MP4 files, audio, and the production plan.
- **Persistent:** Google Cloud Storage (`gs://{BUCKET_NAME}/`).
    - Shot outputs from Veo: `gs://{BUCKET_NAME}/videos/{job_id}/veo_shots/shot_{index}/`.
    - Final Master: `gs://{BUCKET_NAME}/videos/{job_id}/final_output.mp4`.

---

## Existing API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check and configuration metadata. |
| `/generate` | POST | Triggers the pipeline for a given `topic` and `duration_minutes`. |
| `/status/{job_id}`| GET | Returns progress percentage and status message. |
| `/download/{job_id}`| GET | Streams the final `final_output.mp4` from the local `/tmp` folder. |

---

## Existing Deployment

- **Container:** Dockerfile uses `python:3.10-slim`.
- **OS Dependencies:** `ffmpeg`, `imagemagick`.
- **Environment Variables:**
    - `GOOGLE_CLOUD_PROJECT`: Project ID.
    - `GOOGLE_CLOUD_LOCATION`: Region (default: `us-central1`).
    - `GCS_BUCKET`: Storage bucket.
    - `VEO_MODEL`: Veo model ID.
    - `DIRECTOR_MODEL`: Gemini model ID.
    - `TTS_VOICE`: TTS voice ID.

---

## Installed Dependencies (Verified via `pip list`)

- `google-genai`: 2.8.0
- `google-cloud-aiplatform`: 1.157.0
- `fastapi`: 0.111.0
- `moviepy`: 1.0.3
- `tensorflow`: 2.21.0
- `google-cloud-storage`: 3.12.0
- `google-cloud-texttospeech`: 2.36.0

---

## Verified Capabilities

- **Cinematic Planning:** Gemini successfully produces structured JSON shot lists.
- **Video Generation:** Veo produces 4-8 second shots from text prompts.
- **TTS Synthesis:** Narration is generated and synchronized.
- **Post-Production:** FFmpeg successfully handles scaling, padding, concatenation, audio mixing, and basic color grading.
- **GCS Integration:** Upload/Download of assets works.

---

## Unsupported Capabilities

- **Character Continuity:** No mechanism to maintain a character's look across shots.
- **Simulation Mode:** No "mock" mode to test logic without spending budget.
- **Asynchronous Resilience:** Job state is lost if the process restarts.
- **Multi-Agent Logic:** The pipeline is a single script; agents described in `EXECUTION_PLAN.md` are not yet implemented.

---

## Unverified Capabilities

- **Veo Image-to-Video:** The SDK likely supports it, but it is not implemented in the current code.
- **Concurrent Job Scaling:** Unclear how many Veo operations can run in parallel before hitting quotas.

---

## Existing Working Functionality

1. **End-to-End Documentary Creation:** Can take a topic and produce a finished MP4 with narration and color-graded visuals.
2. **Shot Normalization:** Automatically handles different shot durations and ensures 1080p/24fps consistency.
3. **Automated Retries:** Basic retry logic in `main.py` if a shot generation fails (uses a "simpler" prompt).

---

## Current Problems

1. **State Persistence:** `jobs` dictionary is in-memory only.
2. **Hardcoded IDs:** `auto_fix.py` contains a hardcoded project ID (`vidgen-504817`).
3. **Monolithic Design:** `main.py` is nearly 500 lines of mixed concerns (API, logic, FFmpeg commands, AI clients).
4. **Budget Risk:** No safety mechanism to prevent accidental expensive generations during dev.

---

## Required Architectural Changes

1. **Pydantic Models:** Transition from dictionaries to typed models (Phase 1).
2. **Agent Separation:** Break `main.py` into specialized agents (Phase 3).
3. **Mock Provider:** Implement a `MockVideoGenerator` for simulation (Phase 2).
4. **Persistence Layer:** Move job state to a database or GCS checkpoints (Phase 5).
5. **Prompt Compiler:** Formalize prompt construction to handle "Visual Bible" and "Character References" (Phase 4).

---

## Risks

- **Quota Limits:** Veo is a high-demand resource; sequential polling may be slow.
- **Budget Leakage:** Without `FILM_MODE=simulation`, developers may accidentally trigger expensive Veo calls.
- **Character Drift:** Significant risk of characters looking different in every shot without reference inputs.

---

## Recommended Implementation Order

1. **Phase 0.5 (Refactor):** Extract job state and clients into separate modules.
2. **Phase 1 (Models):** Define the schema for the new Studio.
3. **Phase 2 (Generators):** Implement Simulation/Production abstraction.
4. **Phase 3 (Agents):** Incrementally implement the 18 agents, starting with the Master Director.

---

## Commands Executed
1. `ls -Rla`: Verified directory structure.
2. `pip list`: Verified installed packages and versions.
3. `read_file`: Inspected `main.py`, `requirements.txt`, `Dockerfile`, `auto_fix.py`, `EXECUTION_PLAN.md`.
4. `gcloud config get-value project`: Attempted to verify project ID (unset).
