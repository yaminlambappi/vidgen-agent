"""School of Sufi — POST /generate turns one reflection into a 30-second munajat."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from vidgen.config import settings
from vidgen.sufi.engine import generate
from vidgen.sufi.ledger import CostGuardTripped
from vidgen.sufi.plan import PlanError
from vidgen.sufi.publish import PublishError
from vidgen.sufi.render import QualityError

app = FastAPI(title="School of Sufi", version="4.0.0")


class GenerateRequest(BaseModel):
    thought: str = Field(..., min_length=3, max_length=8000)


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "School of Sufi",
        "film_mode": settings.FILM_MODE,
        "allow_real_generation": settings.ALLOW_REAL_GENERATION,
        "veo_model": settings.VEO_MODEL,
        "gemini_model": settings.GEMINI_MODEL,
    }


@app.post("/generate")
def generate_short(payload: GenerateRequest):
    try:
        result = generate(payload.thought)
    except (PlanError, QualityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CostGuardTripped as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except PublishError as exc:
        raise HTTPException(status_code=502, detail={"video_path": exc.video_path, "publish": exc.details}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc
    return result.model_dump()
