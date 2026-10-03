"""Pro Director — one prompt, requested length, assembled Veo takes, renter quote."""
from __future__ import annotations

from vidgen.reels.duration import parse_prompt_duration
from vidgen.reels.factory import ReelFactory
from vidgen.reels.schemas import ReelJob, ReelRequest


def resolve_duration(idea: str, duration_seconds: float | None) -> float:
    if duration_seconds and duration_seconds > 0:
        return float(duration_seconds)
    inferred = parse_prompt_duration(idea)
    if inferred:
        return inferred
    return 20.0


def director_request(
    idea: str,
    duration_seconds: float | None = None,
    language: str = "",
    product: str = "",
    cta: str = "",
    dry_run: bool = True,
) -> ReelRequest:
    seconds = resolve_duration(idea, duration_seconds)
    return ReelRequest(
        idea=idea,
        language=language,
        duration_seconds=seconds,
        product_name=product,
        cta=cta,
        platform="director",
        dry_run=dry_run,
        long_form=True,
    )


def plan_director(
    idea: str,
    duration_seconds: float | None = None,
    language: str = "",
    product: str = "",
    cta: str = "",
) -> ReelJob:
    factory = ReelFactory()
    job = factory.create(director_request(
        idea, duration_seconds, language, product, cta, dry_run=True,
    ))
    return factory.run(job, dry_run=True)


def run_director(
    idea: str,
    duration_seconds: float | None = None,
    language: str = "",
    product: str = "",
    cta: str = "",
    live: bool = False,
    assets: list | None = None,
) -> ReelJob:
    req = director_request(idea, duration_seconds, language, product, cta, dry_run=not live)
    if assets:
        req.assets = list(assets)
    factory = ReelFactory()
    job = factory.create(req)
    return factory.run(job, dry_run=not live)
