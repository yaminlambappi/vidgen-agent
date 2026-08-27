"""
Canonical reference image helpers — idempotent GCS-first, rate-limit resilient.

Guarantees
----------
1. GCS existence is checked BEFORE any image generation call.
   If the asset already exists, it is reused — no API call is made.

2. In-process deduplication: a module-level set prevents two concurrent or
   sequential callers in the same process from generating the same reference
   twice (e.g., if the same character appears in multiple scenes).

3. If image generation exhausts its retry budget, RateLimitExhausted is raised
   and propagates to the orchestrator. The entity's canonical_visual_assets are
   NOT set, preventing silent use of a missing reference.

4. Never fabricates a URI or local path. If generation fails, the caller gets
   a hard exception, not a broken reference object.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from vidgen.config import settings
from vidgen.models import AssetReference, AssetType, Character, Location
from vidgen.utils.retry import RateLimitExhausted   # re-exported for callers

# ── In-process deduplication lock ────────────────────────────────────────────
# Maps "kind_entity_id" → GCS URI for references already generated this process.
_ref_lock = threading.Lock()
_generated_refs: dict[str, str] = {}  # key → gcs_uri


def references_dir(project_id: str) -> Path:
    root = settings.VIDGEN_WORK_ROOT / project_id / "references"
    root.mkdir(parents=True, exist_ok=True)
    return root


def gcs_ref_uri(project_id: str, kind: str, entity_id: str) -> str:
    return f"gs://{settings.GCS_BUCKET}/projects/{project_id}/references/{kind}_{entity_id}.png"


def _attach_asset(entity, uri: str, role: str, entity_key: str, entity_id: str) -> None:
    entity.canonical_visual_assets = [AssetReference(
        asset_type=AssetType.IMAGE, uri=uri,
        metadata={"role": role, entity_key: entity_id, "mime_type": "image/png"})]


def _ref_key(project_id: str, kind: str, entity_id: str) -> str:
    return f"{project_id}/{kind}/{entity_id}"


def ensure_location_reference(
    loc: Location, project_id: str, storage, image_generator, prompt: str,
) -> None:
    """
    Ensure the canonical location reference image exists in GCS.

    Priority:
      1. GCS already has the asset → reuse, no generation.
      2. In-process cache (same run already generated it) → reuse.
      3. Generate with image_generator → upload → cache.

    Raises RateLimitExhausted if image generation exhausts its retry budget.
    Never attaches a canonical_visual_assets entry unless the asset is confirmed
    to exist in GCS.
    """
    key = _ref_key(project_id, "location", loc.location_id)
    gcs_uri = gcs_ref_uri(project_id, "location", loc.location_id)
    local_path = references_dir(project_id) / f"location_{loc.location_id}.png"

    # 1. GCS existence check (idempotent across runs)
    if storage.exists(gcs_uri):
        print(f"  [REF] Reusing location {loc.name} → {gcs_uri}")
        if not local_path.exists():
            storage.download(gcs_uri, str(local_path))
        _attach_asset(loc, gcs_uri, "location_identity", "location_id", loc.location_id)
        with _ref_lock:
            _generated_refs[key] = gcs_uri
        return

    # 2. In-process deduplication
    with _ref_lock:
        if key in _generated_refs:
            cached_uri = _generated_refs[key]
            print(f"  [REF] In-process cache hit for location {loc.name} → {cached_uri}")
            if not local_path.exists():
                storage.download(cached_uri, str(local_path))
            _attach_asset(loc, cached_uri, "location_identity", "location_id", loc.location_id)
            return

    # 3. Generate — raises RateLimitExhausted on exhaustion (never silenced)
    time.sleep(settings.IMAGE_REQUEST_DELAY_SECONDS)
    image_bytes = image_generator.generate(prompt)  # raises on failure
    if not image_bytes:
        raise RuntimeError(f"No canonical image returned for location {loc.name}")

    local_path.write_bytes(image_bytes)
    uri = storage.upload(str(local_path), gcs_uri)
    _attach_asset(loc, uri, "location_identity", "location_id", loc.location_id)

    with _ref_lock:
        _generated_refs[key] = uri
    print(f"  [REF] Generated location {loc.name} → {uri}")


def ensure_character_reference(
    char: Character, project_id: str, storage, image_generator, prompt: str,
) -> None:
    """
    Ensure the canonical character headshot exists in GCS.

    Same priority/guarantee as ensure_location_reference.
    Raises RateLimitExhausted if image generation exhausts its retry budget.
    """
    key = _ref_key(project_id, "character", char.character_id)
    gcs_uri = gcs_ref_uri(project_id, "character", char.character_id)
    local_path = references_dir(project_id) / f"character_{char.character_id}.png"

    # 1. GCS existence check
    if storage.exists(gcs_uri):
        print(f"  [REF] Reusing character {char.name} → {gcs_uri}")
        if not local_path.exists():
            storage.download(gcs_uri, str(local_path))
        char.reference_image_path = str(local_path)
        char.reference_image_uri = gcs_uri
        _attach_asset(char, gcs_uri, "character_identity", "character_id", char.character_id)
        with _ref_lock:
            _generated_refs[key] = gcs_uri
        return

    # 2. In-process deduplication
    with _ref_lock:
        if key in _generated_refs:
            cached_uri = _generated_refs[key]
            print(f"  [REF] In-process cache hit for character {char.name} → {cached_uri}")
            if not local_path.exists():
                storage.download(cached_uri, str(local_path))
            char.reference_image_path = str(local_path)
            char.reference_image_uri = cached_uri
            _attach_asset(char, cached_uri, "character_identity", "character_id", char.character_id)
            return

    # 3. Generate — raises RateLimitExhausted on exhaustion
    time.sleep(settings.IMAGE_REQUEST_DELAY_SECONDS)
    image_bytes = image_generator.generate(prompt)  # raises on failure
    if not image_bytes:
        raise RuntimeError(f"No canonical image returned for character {char.name}")

    local_path.write_bytes(image_bytes)
    char.reference_image_path = str(local_path)
    char.reference_image_uri = storage.upload(str(local_path), gcs_uri)
    _attach_asset(char, char.reference_image_uri, "character_identity", "character_id", char.character_id)

    with _ref_lock:
        _generated_refs[key] = char.reference_image_uri
    print(f"  [REF] Generated character {char.name} → {char.reference_image_uri}")


def resolve_reference_path(char: Character, storage) -> str:
    """Return a local path for QC; download from GCS when needed."""
    if char.reference_image_path and Path(char.reference_image_path).exists():
        return char.reference_image_path
    uri = char.reference_image_uri
    if not uri and char.canonical_visual_assets:
        uri = char.canonical_visual_assets[0].uri
    if not uri:
        return ""
    local = references_dir("_qc") / f"character_{char.character_id}.png"
    if not local.exists():
        storage.download(uri, str(local))
    char.reference_image_path = str(local)
    return str(local)
