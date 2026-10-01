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
        self.token = (
            os.environ.get("PAT_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or ""
        )
        self.repo  = os.environ.get("GITHUB_REPOSITORY", "")  # e.g. Naruto-67/PikaFlow

        if not self.repo:
            try:
                import subprocess
                remote = subprocess.check_output(
                    ["git", "config", "--get", "remote.origin.url"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
                import re
                m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", remote)
                if m:
                    self.repo = m.group(1)
            except Exception:
                pass

        if not self.repo:
            self.repo = "Naruto-67/PikaFlow"

        if not self.token:
            raise RuntimeError("PAT_TOKEN / GITHUB_TOKEN / GH_TOKEN env var not set")

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
        Idempotent: updates existing release if it already exists.

        Returns:
            URL of the created release.
        """
        self.log.step("release_manager", f"Creating GitHub Release: {run_id}")

        # ── Create or retrieve existing release ───────────────────────────
        release_id   = None
        release_url  = ""
        upload_url   = ""

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

        if release_resp.status_code in (200, 201):
            release_data = release_resp.json()
            release_id   = release_data["id"]
            release_url  = release_data["html_url"]
            upload_url   = release_data["upload_url"].replace("{?name,label}", "")
            self.log.info("release_manager", f"Release created → {release_url}")
        elif release_resp.status_code == 422:
            self.log.info("release_manager", f"Release {run_id} already exists — fetching existing release")
            get_resp = requests.get(
                f"{_GH_API}/repos/{self.repo}/releases/tags/{run_id}",
                headers=self.headers,
                timeout=20,
            )
            get_resp.raise_for_status()
            release_data = get_resp.json()
            release_id   = release_data["id"]
            release_url  = release_data["html_url"]
            upload_url   = release_data["upload_url"].replace("{?name,label}", "")
        else:
            release_resp.raise_for_status()

        # ── Check existing assets on release to avoid collisions ──────────
        existing_assets: dict[str, int] = {}
        if release_id:
            try:
                a_resp = requests.get(
                    f"{_GH_API}/repos/{self.repo}/releases/{release_id}/assets",
                    headers=self.headers,
                    timeout=20,
                )
                if a_resp.ok:
                    existing_assets = {a["name"]: a["id"] for a in a_resp.json()}
            except Exception:
                pass

        # ── Upload assets ─────────────────────────────────────────────────
        for asset in assets:
            if not asset.exists():
                self.log.warn("release_manager", f"Asset missing, skipping: {asset.name}")
                continue

            if asset.name in existing_assets:
                try:
                    requests.delete(
                        f"{_GH_API}/repos/{self.repo}/releases/assets/{existing_assets[asset.name]}",
                        headers=self.headers,
                        timeout=15,
                    )
                except Exception:
                    pass

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

