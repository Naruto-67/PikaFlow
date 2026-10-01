"""
Release Manager
===============
Creates a GitHub Release tagged run-ddmmyyyy-hhmm and uploads all
pipeline assets:
  - final video (MP4)
  - thumbnail (JPG)
  - SEO metadata (seo_metadata.json)
  - run spec (run_spec.json)
  - log (log.jsonl)
  - summary (summary.md)

Uses the GITHUB_TOKEN secret provided automatically by GitHub Actions.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from pika_flow.logger import PikaLogger

_GH_API = "https://api.github.com"


class ReleaseManager:
    def __init__(self, logger: PikaLogger) -> None:
        self.log   = logger
        self.token = os.environ.get("GITHUB_TOKEN", "")
        self.repo  = os.environ.get("GITHUB_REPOSITORY", "")  # e.g. Naruto-67/PikaFlow

        if not self.token:
            raise RuntimeError("GITHUB_TOKEN env var not set")
        if not self.repo:
            raise RuntimeError("GITHUB_REPOSITORY env var not set")

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def create_release(
        self,
        run_id:  str,
        assets:  list[Path],
        title:   str | None = None,
        body:    str | None = None,
    ) -> str:
        """
        Create a GitHub Release tagged with run_id and upload all assets.

        Returns:
            URL of the created release.
        """
        self.log.step("release_manager", f"Creating GitHub Release: {run_id}")

        # ── Create the release ────────────────────────────────────────────
        release_resp = requests.post(
            f"{_GH_API}/repos/{self.repo}/releases",
            headers=self.headers,
            json={
                "tag_name":   run_id,
                "name":       title or f"🌸 PikaFlow {run_id}",
                "body":       body  or f"Automated video pipeline run `{run_id}`.",
                "draft":      False,
                "prerelease": False,
            },
            timeout=30,
        )
        release_resp.raise_for_status()
        release_data = release_resp.json()
        release_id   = release_data["id"]
        release_url  = release_data["html_url"]
        upload_url   = release_data["upload_url"].replace("{?name,label}", "")

        self.log.info("release_manager", f"Release created → {release_url}")

        # ── Upload assets ─────────────────────────────────────────────────
        for asset in assets:
            if not asset.exists():
                self.log.warn("release_manager", f"Asset missing, skipping: {asset.name}")
                continue
            self._upload_asset(upload_url, asset)

        return release_url

    def _upload_asset(self, upload_url: str, asset: Path) -> None:
        """Upload a single file to the release."""
        self.log.step("release_manager", f"Uploading asset: {asset.name}")
        content_type = _mime(asset)

        resp = requests.post(
            upload_url,
            headers={
                **self.headers,
                "Content-Type":   content_type,
                "Content-Length": str(asset.stat().st_size),
            },
            params={"name": asset.name},
            data=asset.read_bytes(),
            timeout=300,
        )
        if resp.ok:
            self.log.info("release_manager", f"  ✅ {asset.name} uploaded")
        else:
            self.log.warn("release_manager", f"  ⚠️ {asset.name} upload failed: {resp.status_code}")


def _mime(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".json": "application/json",
        ".jsonl": "application/jsonl",
        ".md": "text/markdown",
    }.get(ext, "application/octet-stream")

