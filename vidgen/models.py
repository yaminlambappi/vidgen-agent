"""Small types still shared with the Veo provider."""
from pydantic import BaseModel


class GenerationJob(BaseModel):
    project_id: str = ""
    shot_id: str = ""
    status: str = ""
    artifact_uri: str = ""
    error: str = ""
