"""Buyer quote for the rentable factory. This is the product, not a Veo wrapper."""
from __future__ import annotations

from typing import Any, Dict

from vidgen.config import settings
from vidgen.reels.schemas import ReelJob


def build_commercial_offer(job: ReelJob) -> Dict[str, Any]:
    shots = job.storyboard.shots if job.storyboard else []
    planned = round(sum(s.duration for s in shots), 3)
    requested = float(job.request.duration_seconds) if job.request else 0.0
    veo_seconds = sum(s.duration for s in shots if s.generation_strategy == "veo" and not s.use_user_footage)
    compute = round(float(veo_seconds) * float(settings.VEO_USD_PER_SECOND), 2)
    rent = float(settings.FACTORY_LIST_PRICE_USD)
    variants = []
    if job.hook:
        seen = set()
        for c in job.hook.concepts or []:
            line = (c.line or "").strip()
            if not line or line in seen:
                continue
            seen.add(line)
            variants.append(line)
            if len(variants) >= 3:
                break
    return {
        "product": "VidGen Shorts Factory",
        "what_you_rent": (
            "A production desk: one idea becomes a publishable 9:16 reel "
            "with script, locked identity, QC, and a cost ledger."
        ),
        "who_pays": ["creative agencies", "DTC brands", "creator studios"],
        "do_not_pitch": "A Veo wrapper to Google. They already own the model.",
        "acquireable_asset": (
            "The factory, language playbook, cost/quality data, and brand-locked production OS."
        ),
        "rent_usd": rent,
        "monthly_usd": float(settings.FACTORY_MONTHLY_USD),
        "monthly_includes_reels": int(settings.FACTORY_MONTHLY_REELS),
        "requested_seconds": requested,
        "planned_seconds": planned,
        "duration_honored": abs(planned - requested) <= 8.0,
        "veo_shots": sum(1 for s in shots if s.generation_strategy == "veo"),
        "estimated_compute_usd": compute,
        "agency_comparable_usd": float(settings.AGENCY_COMPARABLE_USD),
        "gross_margin_usd": round(rent - compute, 2),
        "time_minutes_factory": int(8 + 4 * max(1, len(shots))),
        "time_hours_agency": 6,
        "hook_variants": variants,
        "language": job.brief.language if job.brief else "",
        "creative_type": job.brief.creative_type if job.brief else "",
        "why_rent": [
            "Duration is a contract: 15 asked must plan near 15, not silently 8.",
            "Compute stays a fraction of a freelancer invoice.",
            "Watchability gate can refuse slop before a client sees it.",
            "Bengali and English are first-class, not an afterthought.",
        ],
    }
