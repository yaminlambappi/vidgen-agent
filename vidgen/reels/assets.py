"""Autonomous asset orchestration: user → generate → licensed source → compose."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import List, Optional

from vidgen.config import settings
from vidgen.reels.schemas import AssetNeed, AssetPlan, ReelJob


def plan_assets(job: ReelJob) -> AssetPlan:
    brief = job.brief
    ctype = (brief.creative_type if brief else "") or "OTHER"
    needs: List[AssetNeed] = []
    for char in job.character_bible:
        if char.reference_uri:
            needs.append(AssetNeed(
                kind="character", name=char.name, strategy="user",
                uri=char.reference_uri, status="ready",
                reason="user-supplied actor reference",
            ))
        else:
            needs.append(AssetNeed(
                kind="character", name=char.name, strategy="generate_in_prompt",
                reason="Veo generates the actor; identity is locked in the character bible",
                query=f"{char.name} {char.appearance} {char.wardrobe}",
            ))
    if job.product_bible and job.product_bible.required:
        if job.product_bible.reference_uris:
            needs.append(AssetNeed(
                kind="product", name=job.product_bible.name, strategy="user",
                uri=job.product_bible.reference_uris[0], status="ready",
                reason="user-supplied product image",
            ))
        elif job.product_bible.fictional:
            needs.append(AssetNeed(
                kind="product", name=job.product_bible.name, strategy="generate_in_prompt",
                reason="fictional product locked in the product bible; no user image required",
                query=job.product_bible.shape,
            ))
        else:
            needs.append(AssetNeed(
                kind="product", name=job.product_bible.name, strategy="source",
                reason="named real product — prefer a licensed/official still, else prompt-lock",
                query=job.product_bible.name,
            ))
    env = ""
    if job.storyboard and job.storyboard.shots:
        env = job.storyboard.shots[0].location
    needs.append(AssetNeed(
        kind="environment", name=env or "scene", strategy="generate_in_prompt",
        reason="environment is described in-shot; no stock required",
        query=env,
    ))
    if ctype in {"EDUCATIONAL", "EXPLAINER", "FACT"}:
        topic = (job.request.idea or "")[:80]
        needs.append(AssetNeed(
            kind="graphic", name="topic visual", strategy="compose",
            reason="a graphic beat is cheaper and clearer than a second Veo shot",
            query=topic,
        ))
        needs.append(AssetNeed(
            kind="stock", name="reference still", strategy="source",
            reason="public-domain / CC still if one exists; else skip",
            query=_topic_query(job.request.idea),
        ))
    plan = AssetPlan(needs=needs)
    plan.prompt_locked = sum(1 for n in needs if n.strategy == "generate_in_prompt")
    plan.composed = sum(1 for n in needs if n.strategy == "compose")
    plan.sourced = sum(1 for n in needs if n.strategy == "source")
    plan.generated = sum(1 for n in needs if n.strategy == "generate_image")
    job.asset_plan = plan
    return plan


def resolve_assets(job: ReelJob, dry: bool) -> AssetPlan:
    """Live path may source licensed stills. Dry-run never leaves the machine."""
    plan = job.asset_plan or plan_assets(job)
    if dry:
        return plan
    for need in plan.needs:
        if need.strategy != "source" or need.uri:
            continue
        got = _source_still(need.query)
        if got:
            need.uri, need.source, need.provider, need.license, need.status = got + ("ready",)
            plan.sourced += 1
        else:
            need.strategy = "generate_in_prompt"
            need.reason = (need.reason or "") + " — source miss, falling back to generation-in-prompt"
            plan.prompt_locked += 1
    job.asset_plan = plan
    return plan


def apply_compose_strategy(job: ReelJob) -> None:
    """Mark educational later shots as FFmpeg compose so they do not burn Veo."""
    if not job.storyboard or not job.brief:
        return
    if job.brief.creative_type not in {"EDUCATIONAL", "EXPLAINER", "FACT"}:
        return
    shots = job.storyboard.shots
    if len(shots) >= 2:
        shots[-1].generation_strategy = "compose"


def _topic_query(idea: str) -> str:
    raw = (idea or "").lower()
    for stop in ("make", "create", "reel", "funny", "bengali", "english", "second", "seconds", "explain"):
        raw = raw.replace(stop, " ")
    return " ".join(raw.split())[:80] or "abstract science"


def _source_still(query: str) -> Optional[tuple]:
    """Wikimedia Commons CC/PD still. Never scrapes commercial sites."""
    if not query.strip():
        return None
    if settings.PEXELS_API_KEY:
        got = _pexels(query)
        if got:
            return got
    return _wikimedia(query)


def _wikimedia(query: str) -> Optional[tuple]:
    params = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": 5,
        "gsrnamespace": 6,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1080,
    })
    url = "https://commons.wikimedia.org/w/api.php?" + params
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vidgen-agent/reels (educational factory)"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        meta = info.get("extmetadata") or {}
        license_name = ((meta.get("LicenseShortName") or {}).get("value") or "").lower()
        if not any(k in license_name for k in ("cc", "public domain", "pd")):
            continue
        file_url = info.get("thumburl") or info.get("url") or ""
        if file_url.startswith("https://"):
            return (file_url, file_url, "wikimedia_commons", license_name)
    return None


def _pexels(query: str) -> Optional[tuple]:
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode({"query": query, "per_page": 1})
    try:
        req = urllib.request.Request(url, headers={"Authorization": settings.PEXELS_API_KEY})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    photos = data.get("photos") or []
    if not photos:
        return None
    src = (photos[0].get("src") or {}).get("portrait") or (photos[0].get("src") or {}).get("large")
    if not src:
        return None
    return (src, src, "pexels", "pexels-license")
