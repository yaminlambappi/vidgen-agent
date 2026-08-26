import os
import sys
import argparse
from typing import Dict, Any, List
import uuid
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, ValidationError
import requests
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest
from fastapi.responses import FileResponse

from vidgen.config import settings
from vidgen.models import FilmProject, FilmStatus
from vidgen.orchestrator import Orchestrator
from pathlib import Path
import hashlib, json

app = FastAPI(
    title="VidGen Autonomous Film Studio",
    version="3.0.0"
)

# In-memory storage for active projects (backed by GCS checkpoints in Orchestrator)
active_projects: Dict[str, FilmProject] = {}
orchestrator = Orchestrator()


class FilmCreateRequest(BaseModel):
    topic: str = Field(..., min_length=5)
    duration_seconds: int = Field(60, ge=4, le=300)
    genre: str = Field(..., min_length=3)
    language: str = Field("English")
    aspect_ratio: str = Field("16:9")


def _submit_cloud_run_job(project_id: str) -> dict:
    """
    Trigger a Cloud Run Job execution by calling the Cloud Run Jobs API.
    Requires env var CLOUD_RUN_JOB_NAME to be set when running in production.
    This function attempts to obtain an access token from the environment
    credentials and call the Jobs:run endpoint. If the environment lacks
    a configured job name, it raises RuntimeError so the caller can report
    the missing configuration as an external blocker.
    """
    job_name = os.getenv("CLOUD_RUN_JOB_NAME")
    if not job_name:
        raise RuntimeError("Missing CLOUD_RUN_JOB_NAME for production job execution")

    project = settings.GOOGLE_CLOUD_PROJECT
    location = settings.GOOGLE_CLOUD_LOCATION
    # Build endpoint
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{location}/jobs/{job_name}:run"

    # Acquire credentials and token
    creds, _ = google_auth_default()
    creds.refresh(GoogleAuthRequest())
    token = creds.token

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # Build a RunJobRequest with container override to pass the VIDGEN_PROJECT_ID
    body = {
        "overrides": {
            "containerOverrides": [
                {
                    "env": [
                        {"name": "VIDGEN_PROJECT_ID", "value": project_id}
                    ]
                }
            ]
        }
    }
    resp = requests.post(url, headers=headers, json=body)
    if not resp.ok:
        raise RuntimeError(f"Cloud Run Jobs API returned {resp.status_code}: {resp.text}")
    return resp.json()

@app.get("/")
def health_check():
    return {
        "status": "healthy",
        "service": "VidGen Autonomous Film Studio",
        "film_mode": settings.FILM_MODE,
        "allow_real_generation": settings.ALLOW_REAL_GENERATION,
        "location": settings.GOOGLE_CLOUD_LOCATION
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


def _load_project(project_id: str) -> FilmProject | None:
    # First check in-memory
    if project_id in active_projects:
        return active_projects[project_id]

    # Then try local disk
    state_file = settings.VIDGEN_WORK_ROOT / project_id / "project_state.json"
    if state_file.exists():
        try:
            txt = state_file.read_text()
            proj = FilmProject.model_validate_json(txt)
            active_projects[project_id] = proj
            return proj
        except Exception:
            pass

    # Finally try GCS checkpoint
    gcs_path = f"gs://{settings.GCS_BUCKET}/projects/{project_id}/state.json"
    try:
        if orchestrator.storage.exists(gcs_path):
            local = settings.VIDGEN_WORK_ROOT / project_id / "project_state.json"
            orchestrator.storage.download(gcs_path, str(local))
            txt = local.read_text()
            proj = FilmProject.model_validate_json(txt)
            active_projects[project_id] = proj
            return proj
    except Exception:
        pass
    return None


@app.post("/api/v1/films")
def create_film_api(payload: FilmCreateRequest, bg: BackgroundTasks):
    # Validate request via Pydantic
    try:
        data = payload.model_dump()
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Compute request fingerprint for idempotency
    fp_src = f"{data['topic']}|{data['duration_seconds']}|{data['genre']}|{data['language']}|{data['aspect_ratio']}"
    fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()

    # Check persistent fingerprint index in GCS
    fp_path = f"gs://{settings.GCS_BUCKET}/fingerprints/{fingerprint}.json"
    try:
        if orchestrator.storage.exists(fp_path):
            # load existing mapping
            local_fp = settings.VIDGEN_WORK_ROOT / f"fingerprint_{fingerprint}.json"
            orchestrator.storage.download(fp_path, str(local_fp))
            j = json.loads(local_fp.read_text())
            existing_id = j.get("project_id")
            if existing_id:
                proj = _load_project(existing_id)
                if proj and proj.status != FilmStatus.FAILED:
                    return {"project_id": existing_id, "existing": True}
    except Exception:
        # On storage lookup failure, proceed to create a new project (will be retriable by user)
        pass

    # Create project with requested fields
    project = FilmProject(
        topic=data["topic"],
        duration_seconds=data["duration_seconds"],
        genre=data["genre"],
        language=data["language"],
        aspect_ratio=data["aspect_ratio"],
        request_fingerprint=fingerprint,
    )

    # Persist in-memory and checkpoint to GCS
    active_projects[project.project_id] = project
    orchestrator.checkpoint(project)

    # Persist fingerprint mapping to GCS for idempotency
    try:
        local_fp = settings.VIDGEN_WORK_ROOT / f"fingerprint_{fingerprint}.json"
        local_fp.parent.mkdir(parents=True, exist_ok=True)
        local_fp.write_text(json.dumps({"project_id": project.project_id, "request": data}))
        orchestrator.storage.upload(str(local_fp), f"fingerprints/{fingerprint}.json")
    except Exception:
        # Non-fatal: continue even if fingerprint upload failed
        pass

    # In production, submit durable job to Cloud Run Jobs (do not attempt to validate run.jobs.run here)
    if settings.is_production:
        try:
            submission = _submit_cloud_run_job(project.project_id)
            return {"project_id": project.project_id, "submitted": True, "submission": submission}
        except Exception as exc:
            # Persist failure state and return error
            project.status = FilmStatus.FAILED
            project.message = f"Job submission failed: {exc}"
            orchestrator.checkpoint(project)
            raise HTTPException(status_code=500, detail=str(exc))

    # Do not run long-running production/simulation work inline from the API.
    # Use the separate simulation/run endpoints or Cloud Run Job invocation to start production.
    return {"project_id": project.project_id, "submitted": False}


@app.get("/api/v1/films/{project_id}")
def get_film_status(project_id: str):
    proj = _load_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project_id": proj.project_id,
        "status": proj.status.value,
        "progress": proj.progress,
        "final_video_uri": proj.final_manifest_uri or None,
        "manifest_uri": proj.final_manifest_uri or None,
        "error": None if proj.status != FilmStatus.FAILED else proj.message,
    }


@app.get("/api/v1/films/{project_id}/result")
def get_film_result(project_id: str):
    proj = _load_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    if proj.status != FilmStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Project not completed yet")
    return {"project_id": proj.project_id, "final_video_uri": proj.final_manifest_uri}

@app.get("/films")
def list_films():
    return list(active_projects.values())

@app.post("/films")
def create_film(payload: dict):
    topic = payload.get("topic")
    if not topic:
        raise HTTPException(status_code=400, detail="Topic field required.")
    
    project = FilmProject(topic=topic)
    active_projects[project.project_id] = project
    orchestrator.checkpoint(project)
    
    return project

@app.post("/films/{project_id}/simulate")
def simulate_film(project_id: str, bg_tasks: BackgroundTasks):
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found.")
    
    project = active_projects[project_id]
    
    # Force simulation mode for this task if needed, but we rely on settings
    bg_tasks.add_task(orchestrator.run, project)
    
    return {"status": "simulation_started", "project_id": project_id}

@app.post("/films/{project_id}/run")
def run_film(project_id: str):
    """Synchronous worker endpoint for Cloud Run Jobs/invocations."""
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found.")
    orchestrator.run(active_projects[project_id])
    return active_projects[project_id]

@app.post("/films/{project_id}/generate")
def generate_film(project_id: str, bg_tasks: BackgroundTasks):
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found.")
    
    project = active_projects[project_id]
    
    if not settings.is_production:
        raise HTTPException(status_code=403, detail="Production mode not enabled. Use /simulate or enable FILM_MODE=production.")
    
    bg_tasks.add_task(orchestrator.run, project)
    return {"status": "production_generation_started", "project_id": project_id}

@app.get("/status/{project_id}")
def project_status(project_id: str):
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = active_projects[project_id]
    return project

@app.get("/download/{project_id}")
def download_video(project_id: str):
    if project_id not in active_projects:
        raise HTTPException(status_code=404, detail="Project not found.")

    project = active_projects[project_id]
    if project.status != FilmStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Video is not completed yet.")

    final_path = settings.VIDGEN_WORK_ROOT / project.project_id / "final_film.mp4"
    if not final_path.exists():
        # Try to download from GCS if it's there
        if project.final_manifest_uri:
             orchestrator.storage.download(project.final_manifest_uri, str(final_path))
        else:
             raise HTTPException(status_code=404, detail="Local final file no longer exists.")

    return FileResponse(path=str(final_path), media_type="video/mp4", filename=f"film_{project_id}.mp4")

# CLI COMMANDS
def run_cli():
    parser = argparse.ArgumentParser(description="VidGen CLI")
    parser.add_argument("command", choices=["simulate", "status"])
    parser.add_argument("--topic", help="Topic for simulation")
    parser.add_argument("--id", help="Project ID for status")
    
    args = parser.parse_args()
    
    if args.command == "simulate":
        if not args.topic:
            print("Error: --topic is required for simulation")
            return
        
        project = FilmProject(topic=args.topic)
        print(f"Starting simulation for project: {project.project_id}")
        orchestrator.run(project)
        print(f"Simulation finished. Status: {project.status.value}")
        if project.status == FilmStatus.COMPLETED:
            print(f"Final video: {project.final_manifest_uri}")
            
    elif args.command == "status":
        if not args.id:
            print("Error: --id is required for status")
            return
        # In a real CLI, we'd load the state from GCS or local disk
        state_file = settings.VIDGEN_WORK_ROOT / args.id / "project_state.json"
        if state_file.exists():
            with open(state_file, "r") as f:
                print(f.read())
        else:
            print("Project state not found locally.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["simulate", "status"]:
        run_cli()
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
