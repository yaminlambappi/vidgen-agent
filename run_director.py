#!/usr/bin/env python3
"""Pro Director. Default is a priced dry-run. --live spends Veo only after the quote."""
from __future__ import annotations

import argparse
import json
import sys

from vidgen.config import settings
from vidgen.reels.director import run_director
from vidgen.reels.edit import FFmpegMissing, require_ffmpeg


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VidGen Pro Director — any prompt length, assembled from Veo 4/6/8 takes",
    )
    parser.add_argument("--idea", required=True)
    parser.add_argument("--duration", type=float, default=None, help="Seconds. If omitted, read from the idea.")
    parser.add_argument("--language", default="")
    parser.add_argument("--product", default="")
    parser.add_argument("--cta", default="")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if args.live:
        try:
            require_ffmpeg()
        except FFmpegMissing as exc:
            print(str(exc), file=sys.stderr)
            return 2

    job = run_director(
        idea=args.idea,
        duration_seconds=args.duration,
        language=args.language,
        product=args.product,
        cta=args.cta,
        live=args.live,
    )
    payload = {
        "job_id": job.job_id,
        "status": job.status.value,
        "message": job.message,
        "dry_run_manifest": job.dry_run_manifest.model_dump() if job.dry_run_manifest else None,
        "final_video_path": job.final_video_path,
        "final_video_uri": job.final_video_uri,
        "error": job.last_error or None,
        "resume": f"python3 run_reel.py --resume {job.job_id} --live",
        "film_mode": settings.FILM_MODE,
        "director": True,
    }
    print(json.dumps(payload, indent=2, default=str))
    offer = (job.dry_run_manifest.offer if job.dry_run_manifest else None) or {}
    if offer:
        print(
            f"\n[DIRECTOR] planned={offer.get('planned_seconds')}s "
            f"shots={offer.get('veo_shots')} "
            f"rent=${offer.get('rent_usd')} "
            f"compute~${offer.get('estimated_compute_usd')} "
            f"agency~${offer.get('agency_comparable_usd')}",
            file=sys.stderr,
        )
    return 0 if job.status.value == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
