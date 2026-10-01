"""
YouTube Uploader
================
Uploads the final video and thumbnail to YouTube using the
YouTube Data API v3 via OAuth 2.0.

Auth flow (run once locally, then store tokens in GitHub Secrets):
  1. python -m pika_flow.youtube_uploader --auth
     → Opens browser, you log in and approve
     → Saves refresh token — copy it to YOUTUBE_REFRESH_TOKEN secret

Pipeline usage (no browser needed):
  python -m pika_flow.youtube_uploader --video path/to/video.mp4 \
    --metadata path/to/seo_metadata.json \
    --thumbnail path/to/thumbnail.jpg
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pika_flow.logger import PikaLogger

# YouTube API scopes required
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_UPLOAD_URI = "https://www.googleapis.com/upload/youtube/v3/videos"
_THUMB_URI  = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"


def _get_credentials() -> dict:
    """Build OAuth2 credentials from GitHub Secrets (env vars)."""
    client_id     = os.environ["YOUTUBE_CLIENT_ID"]
    client_secret = os.environ["YOUTUBE_CLIENT_SECRET"]
    refresh_token = os.environ["YOUTUBE_REFRESH_TOKEN"]
    return {
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "token_uri":     _TOKEN_URI,
    }


def _refresh_access_token(creds: dict) -> str:
    """Exchange refresh token for a short-lived access token."""
    import requests

    resp = requests.post(_TOKEN_URI, data={
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type":    "refresh_token",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def upload_video(
    video_path: Path,
    metadata: dict,
    thumbnail_path: Path | None,
    logger: PikaLogger,
) -> str:
    """
    Upload the video to YouTube and optionally set the thumbnail.

    Returns:
        YouTube video ID on success.
    """
    import requests

    logger.step("youtube_uploader", "Authenticating with YouTube API")
    creds        = _get_credentials()
    access_token = _refresh_access_token(creds)
    headers      = {"Authorization": f"Bearer {access_token}"}

    # ── Build video resource ───────────────────────────────────────────────
    chapters_text = "\n".join(
        f"{c['time']} {c['label']}" for c in metadata.get("chapters", [])
    )
    description = metadata.get("description", "") + (
        f"\n\n--- Chapters ---\n{chapters_text}" if chapters_text else ""
    )

    body = {
        "snippet": {
            "title":       metadata.get("title", "PikaFlow Video")[:100],
            "description": description[:5000],
            "tags":        metadata.get("tags", [])[:500],
            "categoryId":  "22",   # 22 = People & Blogs (safe default)
        },
        "status": {
            "privacyStatus":           "private",  # always upload private first!
            "selfDeclaredMadeForKids": False,
        },
    }

    # ── Upload video (resumable upload) ───────────────────────────────────
    logger.step("youtube_uploader", f"Uploading video: {video_path.name}")
    init_resp = requests.post(
        _UPLOAD_URI,
        headers={**headers, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4"},
        params={"uploadType": "resumable", "part": "snippet,status"},
        json=body,
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    # Stream the file in chunks
    video_bytes = video_path.read_bytes()
    upload_resp = requests.put(
        upload_url,
        headers={**headers, "Content-Type": "video/mp4"},
        data=video_bytes,
        timeout=600,
    )
    upload_resp.raise_for_status()
    video_id = upload_resp.json()["id"]
    logger.info("youtube_uploader", f"Video uploaded → https://youtu.be/{video_id}", {"video_id": video_id})

    # ── Set thumbnail ──────────────────────────────────────────────────────
    if thumbnail_path and thumbnail_path.exists():
        logger.step("youtube_uploader", "Setting thumbnail")
        thumb_resp = requests.post(
            _THUMB_URI,
            headers={**headers, "Content-Type": "image/jpeg"},
            params={"videoId": video_id},
            data=thumbnail_path.read_bytes(),
            timeout=60,
        )
        if thumb_resp.ok:
            logger.info("youtube_uploader", "Thumbnail set successfully")
        else:
            logger.warn("youtube_uploader", f"Thumbnail upload failed: {thumb_resp.status_code}")

    return video_id


# ── One-time local auth helper ─────────────────────────────────────────────

def run_auth_flow() -> None:
    """
    Run once locally to obtain a refresh token.
    Usage: python -m pika_flow.youtube_uploader --auth
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    except ImportError:
        print("Install: pip install google-auth-oauthlib")
        sys.exit(1)

    secrets_file = input("Path to client_secrets.json downloaded from Google Cloud Console: ").strip()
    flow = InstalledAppFlow.from_client_secrets_file(secrets_file, _SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n=== Copy these to GitHub Secrets ===")
    print(f"YOUTUBE_CLIENT_ID     = {creds.client_id}")
    print(f"YOUTUBE_CLIENT_SECRET = {creds.client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN = {creds.refresh_token}")


if __name__ == "__main__":
    if "--auth" in sys.argv:
        run_auth_flow()
    else:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--video",     required=True)
        parser.add_argument("--metadata",  required=True)
        parser.add_argument("--thumbnail", required=False)
        args = parser.parse_args()

        # Minimal logger for CLI usage
        from pika_flow.logger import PikaLogger
        log = PikaLogger("cli", Path("."))
        meta = json.loads(Path(args.metadata).read_text())
        thumb = Path(args.thumbnail) if args.thumbnail else None
        vid_id = upload_video(Path(args.video), meta, thumb, log)
        print(f"✅ Uploaded: https://youtu.be/{vid_id}")
