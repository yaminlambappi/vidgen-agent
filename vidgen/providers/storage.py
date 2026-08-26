import shutil
import subprocess
from pathlib import Path
from google.cloud import storage as gcs
from vidgen.providers.base import StorageProvider
from vidgen.config import settings


def _write_stub_media(local_path: str, ext: str) -> None:
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        Path(local_path).write_bytes(b"mock-media")
        return

    if ext == ".mp4":
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=1",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=1",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", local_path,
        ]
    elif ext in {".m4a", ".mp3"}:
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=1",
            "-c:a", "aac" if ext == ".m4a" else "libmp3lame",
            "-q:a", "5" if ext == ".mp3" else "8",
            local_path,
        ]
    elif ext == ".srt":
        Path(local_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nmock\n\n")
        return
    else:
        Path(local_path).write_text("mock")
        return

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        Path(local_path).write_bytes(b"mock-media")


class CloudStorageProvider(StorageProvider):
    def __init__(self):
        self._client = gcs.Client(project=settings.GOOGLE_CLOUD_PROJECT)

    def upload(self, local_path: str, remote_path: str) -> str:
        if remote_path.startswith("gs://"):
            path = remote_path[5:]
            bucket_name, blob_name = path.split("/", 1)
        else:
            bucket_name = settings.GCS_BUCKET
            blob_name = remote_path
        ct = {"mp4": "video/mp4", "mp3": "audio/mpeg", "m4a": "audio/mp4",
              "png": "image/png", "jpg": "image/jpeg", "json": "application/json",
              "srt": "text/plain", "txt": "text/plain", "md": "text/plain"}
        ext = local_path.rsplit(".", 1)[-1].lower() if "." in local_path else ""
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path, content_type=ct.get(ext, "application/octet-stream"), timeout=600)
        return f"gs://{bucket_name}/{blob_name}"

    def download(self, remote_path: str, local_path: str) -> None:
        path = remote_path[5:] if remote_path.startswith("gs://") else remote_path
        bucket_name, blob_name = path.split("/", 1)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._client.bucket(bucket_name).blob(blob_name).download_to_filename(local_path, timeout=600)

    def exists(self, remote_path: str) -> bool:
        if not remote_path.startswith("gs://"):
            return False
        path = remote_path[5:]
        bucket_name, blob_name = path.split("/", 1)
        return self._client.bucket(bucket_name).blob(blob_name).exists()


class MockStorageProvider(StorageProvider):
    def upload(self, local_path: str, remote_path: str) -> str:
        uri = remote_path if remote_path.startswith("gs://") else f"gs://mock/{remote_path}"
        print(f"[MOCK] upload {local_path} -> {uri}")
        return uri

    def download(self, remote_path: str, local_path: str) -> None:
        ext = Path(local_path).suffix.lower()
        _write_stub_media(local_path, ext)

    def exists(self, remote_path: str) -> bool:
        return True
