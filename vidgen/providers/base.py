from abc import ABC, abstractmethod
from typing import List
from vidgen.models import GenerationJob


class VideoGenerator(ABC):
    @abstractmethod
    def generate_shot(
        self,
        prompt: str,
        output_uri: str,
        duration: int,
        project_id: str,
        shot_id: str,
        reference_assets: List[dict] | None = None,
    ) -> GenerationJob:
        pass


class StorageProvider(ABC):
    @abstractmethod
    def upload(self, local_path: str, remote_path: str) -> str:
        pass

    @abstractmethod
    def download(self, remote_path: str, local_path: str) -> None:
        pass

    @abstractmethod
    def exists(self, remote_path: str) -> bool:
        pass
