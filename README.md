# vidgen-agent: Autonomous AI Filmmaking Studio

## Crafting Oscar-Level Cinema with Generative AI

The `vidgen-agent` is an ambitious autonomous AI filmmaking studio designed to take a film idea from concept to a professionally directed, emotionally powerful, and technically crafted final movie. Our benchmark is the filmmaking excellence, ambition, scale, storytelling discipline, and technical craftsmanship associated with visionaries like Christopher Nolan and James Cameron, aiming for competition-level, Oscar-worthy cinematic output.

This agent-driven system goes beyond merely generating impressive individual AI clips; it orchestrates the entire filmmaking process to produce a coherent, finished film with persistent character, world, visual, and voice consistency.

## Introduction

In traditional filmmaking, every decision—from camera angles and lighting to character performance and sound design—contributes to the emotional impact and narrative effectiveness of the final piece. The `vidgen-agent` mimics this holistic approach, making intelligent cinematic decisions and continuously critiquing and regenerating weak outputs to achieve unparalleled quality.

## Key Features

*   **Story & Screenplay Generation**: From a high-level topic, the agent generates detailed story arcs, character motivations, and compelling screenplays with emotional depth.
*   **Character & World Design**: Develops persistent character identities (visuals, personality, wardrobe) and creates rich, atmospheric world designs with recurring locations and props.
*   **Cinematic Planning**: Utilizes a `CinematicBible` to define and enforce a consistent visual identity, including color palettes, lighting philosophies, camera language, texture, and editing rhythms.
*   **Real Veo Video Generation**: Integrates directly with Google's advanced Veo video generation model (`veo-2.0-generate-001`) to create high-quality, motion-rich shots based on precise prompts.
*   **Festival-Grade Quality Control (QC) & Regeneration Loop**:
    *   **Zero-Artifact QC**: Ruthlessly filters out visual jitter, unnatural morphing, audio mismatch, and continuity breaks.
    *   **AI Critique**: Leverages multimodal AI to critique generated shots against cinematic principles, character consistency, and overall quality.
    *   **Self-Correction**: Automatically regenerates shots using specific, AI-driven feedback when quality standards are not met.
*   **Audio Design**: Generates narration using Cloud Text-to-Speech, composes atmospheric musical scores, and creates accurate subtitles.
*   **Editing & Mastering**: Assembles approved shots, applies color grading, mixes audio tracks (narration, music, sound effects), and encodes the final MP4.
*   **Persistent Consistency**: Maintains character identity, world details, and cinematic style across all generated assets to ensure a cohesive film.

## How It Works (High-Level Architecture)

The `vidgen-agent` operates as an orchestrated pipeline of specialized AI agents, each responsible for a specific aspect of filmmaking.

1.  **Orchestrator**: The central nervous system, managing the entire film production workflow from `QUEUED` to `COMPLETED` status. It coordinates agents, handles state management, and implements retry logic.
2.  **ResearchAgent**: Grounds the film in profound and evocative facts relevant to the topic.
3.  **StoryArchitectAgent**: Designs the overarching three-act structure and core narrative.
4.  **CharacterDesignAgent**: Creates detailed character profiles and generates reference images, ensuring visual consistency.
5.  **WorldDesignAgent**: Crafts immersive locations with rich sensory details.
6.  **CinematographerAgent**: Defines the film's unique visual language and aesthetic principles in the `CinematicBible`.
7.  **ScreenwriterAgent**: Develops detailed scenes and narration text.
8.  **StoryboardAgent**: Translates scenes into precise shot-by-shot plans, incorporating cinematic direction.
9.  **Video Generation (`VeoVideoGenerator`)**: Uses the Veo model to turn shot descriptions into actual video clips. This stage is tightly integrated with the QC loop.
10. **QCMAgent (Quality Control Multimodal Agent)**: Critically evaluates generated shots for artifacts, style adherence, character consistency, and continuity. Provides actionable feedback for regeneration.
11. **VoiceAgent**: Synthesizes high-quality narration.
12. **MusicAgent**: Plans and integrates musical scores.
13. **EditorAgent**: Compiles the final edit plan.
14. **SubtitleAgent**: Generates subtitles from narration.
15. **FFmpeg Utility**: Used for video concatenation, audio mixing, and final encoding/validation.

### The Iterative QC Loop

A cornerstone of our festival-grade output is the integrated Quality Control (QC) loop. After each shot is generated, the `QCMAgent` rigorously evaluates it against a suite of criteria. If a shot fails any check, the `Orchestrator` intelligently re-prompts the video generation model with specific, AI-generated feedback, ensuring that only high-quality, compliant assets proceed to the next stage. This iterative refinement is crucial for achieving the desired level of polish and avoiding generic AI aesthetics.

## Technical Stack

*   **Language**: Python 3.12+
*   **Web Framework**: FastAPI
*   **AI/ML**: Google Gemini API, Vertex AI (Veo `veo-2.0-generate-001` for video, Imagen `imagen-3.0-generate-002` for images, `gemini-2.5-flash` for director/creative agents).
*   **Cloud Platform**: Google Cloud (Cloud Run for deployment, Cloud Storage for asset management, Cloud Build for CI/CD).
*   **Media Processing**: FFmpeg (for video/audio manipulation), MoviePy.
*   **Serialization**: Pydantic (for robust data models).
*   **Environment Management**: `python-dotenv`.
*   **Testing**: Pytest.

## Setup Instructions

### Prerequisites

1.  **Google Cloud Project**: A Google Cloud Project with billing enabled.
2.  **`gcloud` CLI**: Ensure you have the `gcloud` command-line tool installed and configured for your project.
3.  **Docker**: Installed and running on your local machine if you plan to build images locally.
4.  **Python 3.12+** and `pip`.

### Google Cloud Setup

1.  **Enable APIs**:
    ```bash
    gcloud services enable run.googleapis.com \
                         cloudbuild.googleapis.com \
                         artifactregistry.googleapis.com \
                         aiplatform.googleapis.com \
                         generativelanguage.googleapis.com \
                         texttospeech.googleapis.com \
                         storage.googleapis.com
    ```
2.  **Create Google Cloud Storage Bucket**:
    ```bash
    gsutil mb -p YOUR_PROJECT_ID -l us-central1 gs://YOUR_GCS_BUCKET_NAME
    ```
    *   **Replace `YOUR_PROJECT_ID`** with your Google Cloud Project ID.
    *   **Replace `YOUR_GCS_BUCKET_NAME`** with a unique name for your GCS bucket (e.g., `vidgen-media-assets`).
3.  **Create a Service Account**: This service account will be used by your Cloud Run service.
    ```bash
    gcloud iam service-accounts create vidgen-sa \
        --display-name="Vidgen Agent Service Account" --project=YOUR_PROJECT_ID
    ```
4.  **Grant Roles to Service Account**:
    ```bash
    gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
        --member="serviceAccount:vidgen-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
        --role="roles/editor" # Editor role for simplicity, refine as needed for production
    gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
        --member="serviceAccount:vidgen-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
        --role="roles/run.admin" # Required for Cloud Run deployment management
    ```
    *   The `roles/editor` might be too broad for production. Consider refining to `roles/storage.admin`, `roles/aiplatform.user`, `roles/ml.developer`, etc., if you need more granular control. `roles/aiplatform.user` is specifically needed for Vertex AI model access.

### Local Setup (for Development/Testing)

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/your-org/vidgen-agent.git
    cd vidgen-agent
    ```
2.  **Create and activate a Python virtual environment**:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```
3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
4.  **Install FFmpeg**: Ensure FFmpeg is installed on your local machine for video processing utilities.
    ```bash
    sudo apt-get update && sudo apt-get install -y ffmpeg # For Debian/Ubuntu
    # Or use equivalent for your OS
    ```
5.  **Environment Variables**: Create a `.env` file in the project root with your configuration.
    ```
    FILM_MODE=production
    ALLOW_REAL_GENERATION=true
    GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
    GCS_BUCKET=YOUR_GCS_BUCKET_NAME
    # Optional: Override default models if needed
    # VEO_MODEL=veo-2.0-generate-001
    # IMAGE_MODEL=imagen-3.0-generate-002
    ```
    *   **Replace `YOUR_PROJECT_ID` and `YOUR_GCS_BUCKET_NAME`** with your actual values.

## Deployment to Cloud Run

Deploy the `vidgen-agent` as a service on Google Cloud Run. This method ensures proper authentication and resource management within your GCP project.

```bash
gcloud run deploy vidgen-agent \
    --source . \
    --region=us-central1 \
    --project=YOUR_PROJECT_ID \
    --service-account=vidgen-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --update-env-vars=ALLOW_REAL_GENERATION=true,FILM_MODE=production,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,GCS_BUCKET=YOUR_GCS_BUCKET_NAME \
    --allow-unauthenticated # Or configure tighter access controls
```
*   **Replace `YOUR_PROJECT_ID` and `YOUR_GCS_BUCKET_NAME`** with your actual values.
*   Note: It's recommended to remove `--allow-unauthenticated` and configure proper IAM access for production deployments.

## Usage: Generating the ODYSSEUS Trailer

Once deployed, you can trigger the film generation by sending a `POST` request to the Cloud Run service URL.

1.  **Get your Cloud Run Service URL**:
    ```bash
    gcloud run services describe vidgen-agent --region=us-central1 --project=YOUR_PROJECT_ID --format='value(status.url)'
    ```
    This will output the URL (e.g., `https://vidgen-agent-xyz-uc.a.run.app`).

2.  **Trigger Film Generation**: Use `curl` to send a request to the `/films` endpoint. The service will then begin processing the film.

    ```bash
    SERVICE_URL=$(gcloud run services describe vidgen-agent --region=us-central1 --project=YOUR_PROJECT_ID --format='value(status.url)')
    
    curl -X POST "${SERVICE_URL}/films" \
         -H "Content-Type: application/json" \
         -d '{
               "topic": "ODYSSEUS trailer: mythic, emotionally powerful, visually coherent, premium theatrical quality. Odysseus has spent years trying to return home after war. The sea, gods, monsters, and his own memories have transformed the journey into a psychological battle. The greatest battle is no longer against the sea—it is against what he has become. Use a compact three-act trailer structure: * 0–8s — Mystery: vast ancient sea, exhausted Odysseus on a damaged ship, haunting atmosphere, restrained dialogue. * 8–20s — Escalation: rapid but coherent flashes of danger—storm, enormous silhouette beneath the water, warriors/ruins, Odysseus fighting, Penelope/home as an emotional memory. * 20–30s — Payoff: extreme danger and emotional revelation, decisive final image, then title ODYSSEUS and a powerful final sound/music hit."
             }'
    ```
    This will return a `project_id`.

3.  **Run the Film Production Pipeline**:
    ```bash
    PROJECT_ID="your-returned-project-id" # Replace with the project_id from the previous step
    curl -X POST "${SERVICE_URL}/films/${PROJECT_ID}/run"
    ```
    The pipeline will execute asynchronously. You can monitor its progress by checking the Cloud Run service logs in Google Cloud Logging.

## Project Structure

*   `main.py`: FastAPI application entry point, defines API endpoints.
*   `run_production.py`: Script to run the full film generation pipeline (used primarily for local execution or as a reference).
*   `vidgen/`: Core application logic.
    *   `agents.py`: Defines the various AI agents (StoryArchitect, Cinematographer, QCMAgent, etc.).
    *   `orchestrator.py`: Manages the overall film production workflow and agent coordination.
    *   `models.py`: Pydantic data models for film elements (FilmProject, Shot, Character, etc.).
    *   `config.py`: Application settings and environment variable loading.
    *   `providers/`: Integrations with external services (Veo, Imagen, GCS, TTS).
    *   `utils/`: Utility functions (FFmpeg wrappers, prompt compilers).
*   `tests/`: Unit and integration tests.
*   `Dockerfile`: Defines the Docker image for Cloud Run deployment.
*   `requirements.txt`: Python dependencies.
*   `.gitignore`: Specifies files to be ignored by Git.

## Roadmap & Future Enhancements

*   **Advanced Cinematic Control**: Deeper integration of director-level controls (e.g., specific camera blocking, complex shot transitions).
*   **Emotional Reasoning**: Agents with enhanced capabilities to understand and manipulate emotional arcs for greater audience impact.
*   **Learning Loop**: Incorporate user feedback and output analysis to continuously improve agent performance and film quality.
*   **Generative Sound Design & Music**: Move beyond simple scores to AI-composed soundtracks and granular sound design.
*   **Scalability & Parallelism**: Optimize the pipeline for parallel processing of shots and scenes for faster generation of longer films.

## Contributing

Contributions are welcome! Please feel free to open issues or submit pull requests.

## License

This project is licensed under the Apache License 2.0.
