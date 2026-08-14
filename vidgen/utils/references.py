"""Canonical reference image helpers — GCS idempotency and local cache."""
from __future__ import annotations

import time
from pathlib import Path

from vidgen.config import settings
from vidgen.models import AssetReference, AssetType, Character, Location


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


def ensure_location_reference(
    loc: Location, project_id: str, storage, image_generator, prompt: str,
) -> None:
    """Reuse GCS canonical location still or generate once."""
    gcs_uri = gcs_ref_uri(project_id, "location", loc.location_id)
    local_path = references_dir(project_id) / f"location_{loc.location_id}.png"

    if storage.exists(gcs_uri):
        print(f"  [REF] Reusing location {loc.name} → {gcs_uri}")
        if not local_path.exists():
            storage.download(gcs_uri, str(local_path))
        _attach_asset(loc, gcs_uri, "location_identity", "location_id", loc.location_id)
        return

    time.sleep(settings.IMAGE_REQUEST_DELAY_SECONDS)
    image_bytes = image_generator.generate(prompt)
    if not image_bytes:
        raise RuntimeError(f"No canonical image returned for location {loc.name}")
    local_path.write_bytes(image_bytes)
    uri = storage.upload(str(local_path), gcs_uri)
    _attach_asset(loc, uri, "location_identity", "location_id", loc.location_id)
    print(f"  [REF] Generated location {loc.name} → {uri}")


def ensure_character_reference(
    char: Character, project_id: str, storage, image_generator, prompt: str,
) -> None:
    """Reuse GCS canonical character headshot or generate once."""
    gcs_uri = gcs_ref_uri(project_id, "character", char.character_id)
    local_path = references_dir(project_id) / f"character_{char.character_id}.png"

    if storage.exists(gcs_uri):
        print(f"  [REF] Reusing character {char.name} → {gcs_uri}")
        if not local_path.exists():
            storage.download(gcs_uri, str(local_path))
        char.reference_image_path = str(local_path)
        char.reference_image_uri = gcs_uri
        _attach_asset(char, gcs_uri, "character_identity", "character_id", char.character_id)
        return

    time.sleep(settings.IMAGE_REQUEST_DELAY_SECONDS)
    image_bytes = image_generator.generate(prompt)
    if not image_bytes:
        raise RuntimeError(f"No canonical image returned for character {char.name}")
    local_path.write_bytes(image_bytes)
    char.reference_image_path = str(local_path)
    char.reference_image_uri = storage.upload(str(local_path), gcs_uri)
    _attach_asset(char, char.reference_image_uri, "character_identity", "character_id", char.character_id)
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
