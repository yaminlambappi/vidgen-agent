"""Veo video generation — confirmed working: veo-3.1-generate-001 on vidgen-504817."""
import time, random, uuid
from pathlib import Path
from typing import Iterable, Optional
from google import genai
from google.genai import types
from vidgen.providers.base import VideoGenerator
from vidgen.models import GenerationJob
from vidgen.config import settings
from vidgen.utils.retry import call_with_retry, RateLimitExhausted


class MockVideoGenerator(VideoGenerator):
    def generate_shot(self, prompt, output_uri, duration=8, project_id="", shot_id="", reference_assets=None):
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

    def _build_config(self, duration_seconds: int, output_uri: str, reference_assets: Iterable[dict] = ()):
        args = dict(
            aspect_ratio="16:9",
            duration_seconds=duration_seconds,
            number_of_videos=1,
            output_gcs_uri=output_uri,
            # Audio is disabled: VidGen supplies its own time-coded narration, dialogue,
            # and score in final_mix(). Veo-baked audio would contaminate the soundtrack
            # with model-generated voices that cannot be time-controlled.
            generate_audio=False,
        )
        references = []
        for asset in reference_assets:
            uri = asset.get("uri", "")
            if not uri.startswith("gs://"):
                raise ValueError(f"Veo reference must be a GCS URI, got {uri!r}")
            role = asset.get("metadata", {}).get("role", "")
            # VideoGenerationReferenceType has exactly two valid values in the SDK:
            #   ASSET  — character/product/vehicle/object/environment identity references
            #   STYLE  — cinematic style references
            # "SUBJECT" is NOT a valid value and will cause a 400 INVALID_ARGUMENT.
            ref_type = "STYLE" if role == "cinematic_style" else "ASSET"
            references.append(types.VideoGenerationReferenceImage(
                image=types.Image(gcs_uri=uri, mime_type=asset.get("metadata", {}).get("mime_type", "image/png")),
                reference_type=ref_type))
        if references:
            args["reference_images"] = references
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
                      project_id: str = "", shot_id: str = "", reference_assets: Iterable[dict] = ()) -> GenerationJob:
        if not output_uri.endswith("/"):
            output_uri += "/"
        # When reference images are supplied (reference_to_video mode),
        # Veo only supports duration=8 seconds. Enforce unconditionally when refs present.
        # Without references, snap to nearest valid duration from settings.
        ref_list = list(reference_assets) if not isinstance(reference_assets, (list, tuple)) else list(reference_assets)
        if ref_list:
            dur = 8
        else:
            valid = set(settings.VEO_VALID_DURATIONS) or {5, 6, 7, 8}
            dur = duration if duration in valid else min(valid, key=lambda d: abs(d - duration))
        config = self._build_config(dur, output_uri, reference_assets)

        def _submit() -> GenerationJob:
            print(f"[VEO] {shot_id} submitting...")
            op = self.client.models.generate_videos(
                model=self.model, prompt=prompt, config=config
            )
            op = self._poll(op)

            if not op.done:
                # Treat timeout as transient so retry policy can re-submit
                raise RuntimeError("Veo operation timed out (transient)")

            err_obj = getattr(op, "error", None)
            if err_obj:
                # err_obj may be a protobuf Status object (with .code) or a plain string.
                # Any non-empty Veo operation error is a definitive generation failure —
                # prefix "400 " so classify_error marks it deterministic (no retry).
                code = getattr(err_obj, "code", None)
                if code is not None and code not in (0,):
                    raise RuntimeError(f"400 Veo operation error: {err_obj}")
                elif code is None and str(err_obj).strip():
                    raise RuntimeError(f"400 Veo operation error: {err_obj}")

            uri = self._extract_uri(op, output_uri, shot_id)
            if not uri or not isinstance(uri, str):
                raise RuntimeError(f"no video URI in response. operation: {op}")
            print(f"[VEO] {shot_id} ✓ {uri}")
            return GenerationJob(project_id=project_id, shot_id=shot_id,
                                 status="completed", artifact_uri=uri)

        try:
            return call_with_retry(
                fn=_submit,
                provider="veo",
                model=self.model,
                operation=f"generate_shot/{shot_id}",
            )
        except RateLimitExhausted as exc:
            return GenerationJob(
                project_id=project_id, shot_id=shot_id,
                status="rate_limit_exhausted",
                error=str(exc),
            )
        except RuntimeError as exc:
            return GenerationJob(
                project_id=project_id, shot_id=shot_id,
                status="failed",
                error=str(exc),
            )
        except Exception as exc:
            return GenerationJob(
                project_id=project_id, shot_id=shot_id,
                status="failed",
                error=str(exc),
            )
