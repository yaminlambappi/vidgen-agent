"""YouTube Data API upload and an optional social webhook. Unconfigured targets are skipped."""
from __future__ import annotations

import json
import mimetypes
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from vidgen.config import settings

CHANNEL = "School of Sufi"


class PublishError(RuntimeError):
    def __init__(self, video_path: str, details: dict):
        self.video_path = video_path
        self.details = details
        super().__init__(f"publish failed for {video_path}: {details}")


def youtube_configured() -> bool:
    return bool(settings.YOUTUBE_CLIENT_ID and settings.YOUTUBE_CLIENT_SECRET and settings.YOUTUBE_REFRESH_TOKEN)


def _privacy() -> str:
    value = (settings.YOUTUBE_PRIVACY or "unlisted").strip().lower()
    if value not in {"public", "unlisted", "private"}:
        return "unlisted"
    return value


def _title(script: str) -> str:
    line = script.strip().split(".")[0].strip() or CHANNEL
    title = line if len(line) <= 90 else line[:87].rstrip() + "..."
    return title or CHANNEL


def _tags() -> list[str]:
    return ["Sufism", "SpiritualReminders", "Tasawwuf", "SchoolOfSufi"]


def _request(url: str, data: bytes, headers: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _access_token() -> str:
    form = urllib.parse.urlencode({
        "client_id": settings.YOUTUBE_CLIENT_ID,
        "client_secret": settings.YOUTUBE_CLIENT_SECRET,
        "refresh_token": settings.YOUTUBE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    token = _request(
        "https://oauth2.googleapis.com/token",
        form,
        {"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    access = token.get("access_token") or ""
    if not access:
        raise RuntimeError("YouTube token response had no access_token")
    return access


def upload_youtube(video_path: str, script: str, caption: str) -> dict:
    video = Path(video_path).read_bytes()
    boundary = f"sufi{uuid.uuid4().hex}"
    metadata = {
        "snippet": {
            "title": _title(script),
            "description": caption[:4900],
            "categoryId": "22",
            "tags": _tags(),
        },
        "status": {
            "privacyStatus": _privacy(),
            "selfDeclaredMadeForKids": False,
        },
    }
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + json.dumps(metadata).encode()
        + f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode()
        + video
        + f"\r\n--{boundary}--\r\n".encode()
    )
    uploaded = _request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status",
        body,
        {
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        timeout=300,
    )
    video_id = uploaded.get("id") or ""
    if not video_id:
        raise RuntimeError("YouTube upload returned no video id")
    return {
        "status": "uploaded",
        "video_id": video_id,
        "url": f"https://www.youtube.com/shorts/{video_id}",
        "privacy": _privacy(),
    }


def post_webhook(video_path: str, caption: str, duration: int) -> dict:
    url = settings.SOCIAL_WEBHOOK_URL
    boundary = f"sufi{uuid.uuid4().hex}"
    file_bytes = Path(video_path).read_bytes()
    filename = Path(video_path).name
    mime = mimetypes.guess_type(filename)[0] or "video/mp4"
    chunks = []
    for name, value in (
        ("channel", CHANNEL),
        ("caption", caption),
        ("duration_seconds", str(duration)),
    ):
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    _request(
        url,
        b"".join(chunks),
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=120,
    )
    return {"status": "posted", "url": url}


def publish_video(video_path: str, script: str, caption: str, duration: int) -> dict:
    details: dict = {}
    errors: dict = {}
    if youtube_configured():
        try:
            details["youtube"] = upload_youtube(video_path, script, caption)
        except Exception as exc:
            errors["youtube"] = str(exc)[:400]
            details["youtube"] = {"status": "failed", "error": errors["youtube"]}
    else:
        details["youtube"] = {"status": "skipped", "reason": "not_configured"}
    if settings.SOCIAL_WEBHOOK_URL:
        try:
            details["webhook"] = post_webhook(video_path, caption, duration)
        except Exception as exc:
            errors["webhook"] = str(exc)[:400]
            details["webhook"] = {"status": "failed", "error": errors["webhook"]}
    else:
        details["webhook"] = {"status": "skipped", "reason": "not_configured"}
    if errors:
        raise PublishError(video_path, details)
    return details
