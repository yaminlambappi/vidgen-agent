"""Per-job ceilings for Veo, speech, and Gemini. A trip stops the job."""
from __future__ import annotations

from pydantic import BaseModel, Field

from vidgen.config import settings


class CostGuardTripped(RuntimeError):
    """Raised before a paid call that would pass the ceiling."""


class Ledger(BaseModel):
    veo: int = 0
    tts: int = 0
    gemini: int = 0
    events: list[str] = Field(default_factory=list)

    def charge(self, kind: str) -> None:
        limits = {
            "veo": settings.MAX_VEO_CALLS,
            "tts": settings.MAX_TTS_CALLS,
            "gemini": settings.MAX_GEMINI_CALLS,
        }
        if kind not in limits:
            raise CostGuardTripped(f"unknown cost kind {kind}")
        used = getattr(self, kind)
        cap = limits[kind]
        if used >= cap:
            raise CostGuardTripped(f"{kind} budget exhausted ({cap})")
        setattr(self, kind, used + 1)
        self.events.append(kind)
