import os
import uuid
from pathlib import Path
from PIL import Image
from vidgen.providers.video import VeoVideoGenerator
from vidgen.models import AssetReference, AssetType, Character
from vidgen.config import settings
from vidgen.providers import get_storage_provider

def main():
    if not settings.is_production:
        raise RuntimeError("Production mode not enabled. Set FILM_MODE=production and ALLOW_REAL_GENERATION=true.")

    veo = VeoVideoGenerator()

# Ensure a real reference image exists in GCS for production validation.
    storage = get_storage_provider()
    ref_uri = f"gs://{settings.GCS_BUCKET}/tests/character_ref.jpg"
    if not storage.exists(ref_uri):
        local_path = Path('/tmp/character_ref.jpg')
        Image.new('RGB', (1024, 576), color=(18, 52, 86)).save(local_path)
        storage.upload(str(local_path), f"tests/character_ref.jpg")

    ref_image = AssetReference(
    asset_type=AssetType.IMAGE,
    uri=ref_uri,
    metadata={"mime_type": "image/jpeg"}
    )

    character = Character(
    name="TestCharacter",
    physical_description="A professional presenter",
    canonical_visual_assets=[ref_image]
    )

# Configuration
    project_id = f"test-prod-{uuid.uuid4().hex[:8]}"
    shot_id = "test-shot-001"
    output_uri = f"gs://{settings.GCS_BUCKET}/videos/{project_id}/shots/{shot_id}/"

    print(f"Generating shot for project {project_id}")

# Action
    job = veo.generate_shot(
    prompt="A professional presenter standing in a bright studio, cinematic, 4k",
    output_uri=output_uri,
    duration=8,
    project_id=project_id,
    shot_id=shot_id,
    visual_references=character.canonical_visual_assets
    )

    print(f"Job Status: {job.status}")
    if job.artifact_uri: print(f"Output URI: {job.artifact_uri}")
    if job.error: print(f"Error: {job.error}")

if __name__ == "__main__": main()
