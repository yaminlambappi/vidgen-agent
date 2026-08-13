import os
import sys
import argparse
from typing import Dict, Any, List
import uuid
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from vidgen.config import settings
from vidgen.models import FilmProject, FilmStatus
from vidgen.orchestrator import Orchestrator

app = FastAPI(
    title="VidGen Autonomous Film Studio",
    version="3.0.0"
)

# In-memory storage for active projects (backed by GCS checkpoints in Orchestrator)
active_projects: Dict[str, FilmProject] = {}
orchestrator = Orchestrator()

@app.get("/")
def health_check():
    return {
        "status": "healthy",
        "service": "VidGen Autonomous Film Studio",
        "film_mode": settings.FILM_MODE,
        "allow_real_generation": settings.ALLOW_REAL_GENERATION,
        "location": settings.GOOGLE_CLOUD_LOCATION
    }

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
    orchestrator.save_checkpoint(project)
    
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
