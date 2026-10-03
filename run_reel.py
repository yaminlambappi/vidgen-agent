#!/usr/bin/env python3
"""Reels factory entrypoint. Default is dry-run. Live generation requires production flags."""
from __future__ import annotations

import argparse
import json
import sys

from vidgen.config import settings
from vidgen.reels.factory import ReelFactory
from vidgen.reels.schemas import InputAsset, ReelRequest


def main() -> int:
    parser = argparse.ArgumentParser(description="VidGen Reels Super Factory")
    parser.add_argument("--idea", required=True)
    parser.add_argument("--language", default="english")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--audience", default="")
    parser.add_argument("--style", default="")
    parser.add_argument("--cta", default="")
    parser.add_argument("--product", default="")
    parser.add_argument("--product-image", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true", help="Allow expensive generation (still requires production flags)")
    args = parser.parse_args()

    dry = not args.live
    req = ReelRequest(
        idea=args.idea,
        language=args.language,
        duration_seconds=args.duration,
        audience=args.audience,
        style=args.style,
        cta=args.cta,
        product_name=args.product,
        assets=[InputAsset(kind="product_image", uri=u, authoritative=True) for u in args.product_image],
        dry_run=dry,
    )
    factory = ReelFactory()
    job = factory.create(req)
    job = factory.run(job, dry_run=dry)
    payload = {
        "job_id": job.job_id,
        "status": job.status.value,
        "message": job.message,
        "dry_run_manifest": job.dry_run_manifest.model_dump() if job.dry_run_manifest else None,
        "generation_count": job.ledger.total_calls,
        "final_video_path": job.final_video_path,
        "final_video_uri": job.final_video_uri,
        "qc": job.qc.model_dump() if job.qc else None,
        "error": job.last_error or None,
        "film_mode": settings.FILM_MODE,
        "is_production": settings.is_production,
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0 if job.status.value == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
