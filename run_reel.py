#!/usr/bin/env python3
"""Reels factory entrypoint. Default is dry-run. Live generation requires production flags."""
from __future__ import annotations

import argparse
import json
import sys

from vidgen.config import settings
from vidgen.reels.edit import FFmpegMissing, require_ffmpeg
from vidgen.reels.constants import BLOCKED_JOB_IDS
from vidgen.reels.factory import ReelFactory
from vidgen.reels.safety import load_checkpoint
from vidgen.reels.schemas import InputAsset, ReelRequest


def _payload(job) -> dict:
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "message": job.message,
        "dry_run_manifest": job.dry_run_manifest.model_dump() if job.dry_run_manifest else None,
        "production_manifest": job.production_manifest.model_dump() if getattr(job, "production_manifest", None) else None,
        "generation_count": job.ledger.total_calls,
        "veo_calls": job.ledger.veo_calls,
        "final_video_path": job.final_video_path,
        "final_video_uri": job.final_video_uri,
        "qc": job.qc.model_dump() if job.qc else None,
        "error": job.last_error or None,
        "resume": f"python3 run_reel.py --resume {job.job_id} --live",
        "film_mode": settings.FILM_MODE,
        "is_production": settings.is_production,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="VidGen Reels Super Factory")
    parser.add_argument("--idea", default="")
    parser.add_argument("--language", default="", help="Leave empty to infer from --idea (Bengali beats a leftover english default)")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--audience", default="")
    parser.add_argument("--style", default="")
    parser.add_argument("--cta", default="")
    parser.add_argument("--product", default="")
    parser.add_argument("--product-image", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true", help="Allow expensive generation (still requires production flags)")
    parser.add_argument("--resume", dest="resume_id", default="", help="Resume an existing job_id from checkpoint. Does not regenerate Veo shots.")
    args = parser.parse_args()

    dry = not args.live
    factory = ReelFactory()

    if args.live:
        try:
            require_ffmpeg()
        except FFmpegMissing as exc:
            print(str(exc), file=sys.stderr)
            if args.resume_id:
                print(f"After installing ffmpeg, resume with:\n  python3 run_reel.py --resume {args.resume_id} --live", file=sys.stderr)
            return 2

    if args.resume_id:
        if args.resume_id in BLOCKED_JOB_IDS or str(args.resume_id).startswith("9237d967"):
            print(f"Job {args.resume_id} is blocked. Start a new job. Do not spend more Veo credits on it.", file=sys.stderr)
            return 2
        job = load_checkpoint(args.resume_id, factory.storage)
        if not job:
            print(f"Checkpoint not found for job {args.resume_id}", file=sys.stderr)
            return 2
        print(f"[RESUME] {job.job_id} status={job.status.value} veo_calls={job.ledger.veo_calls}")
    else:
        if not args.idea or len(args.idea.strip()) < 3:
            print("--idea is required unless --resume is set", file=sys.stderr)
            return 2
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
        job = factory.create(req)

    try:
        job = factory.run(job, dry_run=dry)
    except FFmpegMissing as exc:
        print(str(exc), file=sys.stderr)
        print(f"Resume after install:\n  python3 run_reel.py --resume {job.job_id} --live", file=sys.stderr)
        print(json.dumps(_payload(job), indent=2, default=str))
        return 2
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        print(json.dumps(_payload(job), indent=2, default=str))
        return 1

    payload = _payload(job)
    print(json.dumps(payload, indent=2, default=str))
    offer = (job.dry_run_manifest.offer if job.dry_run_manifest else None) or {}
    if offer:
        print(
            f"\n[OFFER] {offer.get('product')}  rent=${offer.get('rent_usd')}  "
            f"planned={offer.get('planned_seconds')}s  "
            f"compute~${offer.get('estimated_compute_usd')}  "
            f"agency~${offer.get('agency_comparable_usd')}  "
            f"honored={offer.get('duration_honored')}",
            file=sys.stderr,
        )
    return 0 if job.status.value == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
