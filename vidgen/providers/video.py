"""Veo video generation — confirmed working: veo-3.1-generate-001 on vidgen-504817."""
import time, random, uuid
from pathlib import Path
from typing import Optional
from google import genai
from google.genai import types
from vidgen.providers.base import VideoGenerator
from vidgen.models import GenerationJob
from vidgen.config import settings

# Error classification
_DETERMINISTIC = ("404", "not_found", "not found", "403", "permission", "invalid_argument",
                   "400", "unsupported", "does not have access", "model was not found",
                   "unauthenticated", "401")
_TRANSIENT = ("429", "500", "502", "503", "timeout", "timed out", "unavailable",
              "deadline exceeded", "resource exhausted")


def _classify(err: str) -> str:
    low = err.lower()
    if any(k in low for k in _DETERMINISTIC):
        return "deterministic"
    if any(k in low for k in _TRANSIENT):
        return "transient"
    return "transient"  # default to transient so unknown errors get retried once


class MockVideoGenerator(VideoGenerator):
    def generate_shot(self, prompt, output_uri, duration=8, project_id="", shot_id="", reference_image=None):
        import time as t; t.sleep(0.1)
        return GenerationJob(project_id=project_id, shot_id=shot_id, status="completed",
                             artifact_uri=f"{output_uri.rstrip('/')}/mock_{uuid.uuid4().hex[:8]}.mp4")


class VeoVideoGenerator(VideoGenerator):
    def __init__(self):
        self.client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        self.model = settings.VEO_MODEL  # veo-3.1-generate-001

    def _build_config(self, duration_seconds: int, output_uri: str):
        args = dict(
            aspect_ratio="16:9",
            duration_seconds=duration_seconds,
            number_of_videos=1,
            output_gcs_uri=output_uri,
            generate_audio=True,
        )
        supported = set(types.GenerateVideosConfig.model_fields)
        return types.GenerateVideosConfig(**{k: v for k, v in args.items() if k in supported})

    def _poll(self, operation):
        deadline = time.monotonic() + settings.VEO_TIMEOUT_SECONDS
        delay = 5.0
        while time.monotonic() < deadline:
            try:
                operation = self.client.operations.get(operation=operation)
            except Exception:
                pass
            if operation.done:
                return operation
            time.sleep(min(delay, 30) + random.uniform(0, 2))
            delay = min(delay * 1.5, 30)
        return operation

    def _extract_uri(self, operation, output_uri: str, shot_id: str) -> Optional[str]:
        from vidgen.providers.storage import CloudStorageProvider
        resp = getattr(operation, "response", None) or getattr(operation, "result", None)
        if not resp:
            return None
        videos = getattr(resp, "generated_videos", None) or getattr(resp, "videos", None)
        if not videos:
            return None
        video = getattr(videos[0], "video", videos[0])
        uri = getattr(video, "uri", None) or getattr(video, "gcs_uri", None)
        if uri:
            return uri
        vbytes = getattr(video, "video_bytes", None)
        if vbytes:
            local = Path(settings.VIDGEN_WORK_ROOT) / f"veo_{uuid.uuid4().hex[:8]}.mp4"
            local.write_bytes(vbytes)
            st = CloudStorageProvider()
            blob = f"projects/{shot_id}/{uuid.uuid4().hex[:8]}.mp4"
            result = st.upload(str(local), blob)
            local.unlink(missing_ok=True)
            return result
        return None

    def generate_shot(self, prompt: str, output_uri: str, duration: int = 8,
                      project_id: str = "", shot_id: str = "", reference_image: Optional[str] = None) -> GenerationJob:
        if not output_uri.endswith("/"):
            output_uri += "/"
        dur = duration if duration in (4, 6, 8) else 8
        config = self._build_config(dur, output_uri)

        last_err = "unknown"
        for attempt in range(settings.RETRY_ATTEMPTS):
            try:
                print(f"[VEO] {shot_id} attempt {attempt+1}/{settings.RETRY_ATTEMPTS}")
                
                contents = [prompt]
                if reference_image:
                    contents.append(reference_image)

                op = self.client.models.generate_videos(
                    model=self.model, contents=contents, config=config
                )
                op = self._poll(op)

                if not op.done:
                    last_err = "Veo timed out"
                    print(f"[VEO] {shot_id} timed out, retrying...")
                    continue

                err_obj = getattr(op, "error", None)
                if err_obj and getattr(err_obj, "code", 0) not in (0, None):
                    last_err = str(err_obj)
                    cls = _classify(last_err)
                    if cls == "deterministic":
                        return GenerationJob(project_id=project_id, shot_id=shot_id,
                                             status="failed", error=f"DETERMINISTIC: {last_err}")
                    print(f"[VEO] {shot_id} error {cls}: {last_err[:120]}")
                    time.sleep(2 ** attempt)
                    continue

                uri = self._extract_uri(op, output_uri, shot_id)
                if uri:
                    print(f"[VEO] {shot_id} ✓ {uri}")
                    return GenerationJob(project_id=project_id, shot_id=shot_id,
                                         status="completed", artifact_uri=uri)

                last_err = "no video URI in response"
                time.sleep(2 ** attempt)

            except Exception as exc:
                last_err = str(exc)
                cls = _classify(last_err)
                if cls == "deterministic":
                    return GenerationJob(project_id=project_id, shot_id=shot_id,
                                         status="failed", error=f"DETERMINISTIC: {last_err}")
                print(f"[VEO] {shot_id} exception ({cls}): {last_err[:120]}")
                if attempt < settings.RETRY_ATTEMPTS - 1:
                    time.sleep(2 ** (attempt + 1))

        return GenerationJob(project_id=project_id, shot_id=shot_id,
                             status="failed", error=last_err)
