from vidgen.config import settings
from .base import VideoGenerator, StorageProvider
from .video import VeoVideoGenerator, MockVideoGenerator
from .storage import CloudStorageProvider, MockStorageProvider


def get_video_generator() -> VideoGenerator:
    if settings.is_production:
        print(f"[FACTORY] VeoVideoGenerator ({settings.VEO_MODEL})")
        return VeoVideoGenerator()
    return MockVideoGenerator()


def get_storage_provider() -> StorageProvider:
    if settings.is_production:
        return CloudStorageProvider()
    return MockStorageProvider()
