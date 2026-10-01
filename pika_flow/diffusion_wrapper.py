"""
Diffusion Wrapper
=================
Unified interface for cloud-based video and visual generation.

Architecture (Zero Local Model Loading):
  1. Priority 1 — Cloudflare Worker / Remote Video API:
     Configured via CLOUDFLARE_VIDEO_API_URL or VIDEO_GEN_API_URL.
     Ready for custom Cloudflare Worker deployment.
  2. Priority 2 — Cloud Free GPU Video (Hugging Face ZeroGPU Spaces):
     Calls remote A100 GPU spaces (via gradio_client) for genuine generative AI video.
  3. Priority 3 — Hugging Face Serverless Router video endpoint.
  4. Fallback (Safe, Fast, 100% Reliable) — High-Res Visual + Ken Burns Motion Engine:
     Generates crisp 1280x720 visuals (HuggingFace FLUX / Pollinations AI FLUX)
     and renders smooth 24 fps cinematic camera movements in <0.1s.
  5. Last Resort:
     Stylised dark anime aesthetic card with scene text.

NO LOCAL RUN: Zero local diffusers or CPU model downloads.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from pika_flow.logger import PikaLogger

_HF_IMAGE_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
_HF_IMAGE_BACKUP_URL = "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-2-1"
_HF_VIDEO_URL = "https://router.huggingface.co/hf-inference/models/damo-vilab/text-to-video-ms-1.7b"
_POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"


class DiffusionWrapper:
    def __init__(self, logger: PikaLogger, work_dir: Path) -> None:
        self.log      = logger
        self.work_dir = work_dir
        self.hf_key   = os.environ.get("HUGGINGFACE_API_KEY") or os.environ.get("HF_TOKEN") or ""

    # ── Public API ────────────────────────────────────────────────────────

    def generate_image(self, prompt: str, width: int = 1280, height: int = 720) -> Path:
        """Generate a single high-res image from a text prompt. Returns saved PNG path."""
        self.log.step("diffusion", f"Image: {prompt[:60]}…")

        img = (
            self._hf_image(prompt, width, height)
            or self._pollinations_image(prompt, width, height)
            or self._placeholder_image(prompt, width, height)
        )
        out = self.work_dir / f"img_{_slug(prompt)}.png"
        img.save(out, "PNG")
        self.log.info("diffusion", f"Image saved → {out.name}")
        return out

    def generate_video_clip(
        self,
        prompt: str,
        duration_s: int = 4,
        fps: int = 24,
        width: int = 1280,
        height: int = 720,
        motion_index: int = 0,
    ) -> Path:
        """
        Generate a video clip from a text prompt.

        Order:
          1. Cloudflare Worker / Remote Video API (if configured)
          2. Hugging Face Cloud Free GPU Space (ZeroGPU via gradio_client)
          3. Hugging Face Serverless Router video endpoint
          4. Fallback: High-Res Visual (HF / Pollinations FLUX) + Ken Burns Motion Engine
        """
        self.log.step("diffusion", f"Clip: {prompt[:60]}…")

        # ── 1. Cloudflare Worker / Remote Video API ────────────────────────
        cf_clip = self._cloudflare_worker_video(prompt, duration_s, width, height)
        if cf_clip and cf_clip.exists():
            return cf_clip

        # ── 2. Cloud Free GPU Space (Hugging Face ZeroGPU) ──────────────────
        space_clip = self._hf_space_video(prompt, duration_s, fps, width, height)
        if space_clip and space_clip.exists():
            return space_clip

        # ── 3. Hugging Face Serverless Router ──────────────────────────────
        frames = self._hf_video_frames(prompt, duration_s, fps)
        if frames:
            out = self.work_dir / f"clip_{_slug(prompt)}.mp4"
            self._frames_to_mp4(frames, out, fps, width, height)
            self.log.info("diffusion", f"Clip saved → {out.name}")
            return out

        # ── 4. Fallback: Image + Ken Burns Motion Engine ───────────────────
        motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "tilt_up"]
        chosen_motion = motions[motion_index % len(motions)]
        self.log.info("diffusion", f"Rendering Ken Burns {chosen_motion} motion visual ({width}x{height} @ {fps}fps)")

        img = (
            self._hf_image(prompt, width, height)
            or self._pollinations_image(prompt, width, height)
            or self._placeholder_image(prompt, width, height)
        )
        frames = self._image_to_ken_burns_frames(
            img=img,
            duration_s=duration_s,
            fps=fps,
            width=width,
            height=height,
            motion=chosen_motion,
        )

        out = self.work_dir / f"clip_{_slug(prompt)}.mp4"
        self._frames_to_mp4(frames, out, fps, width, height)
        self.log.info("diffusion", f"Clip saved → {out.name}")
        return out

    # ── Priority 1: Cloudflare Worker / Remote Video API ──────────────────

    def _cloudflare_worker_video(
        self, prompt: str, duration_s: int, width: int, height: int
    ) -> Path | None:
        """
        Pluggable hook for custom Cloudflare Worker or remote video API.
        Reads CLOUDFLARE_VIDEO_API_URL or VIDEO_GEN_API_URL env vars.
        """
        api_url = os.environ.get("CLOUDFLARE_VIDEO_API_URL") or os.environ.get("VIDEO_GEN_API_URL")
        if not api_url:
            return None

        self.log.step("diffusion", f"Calling Cloudflare Worker video API: {api_url[:50]}…")
        try:
            headers = {"Content-Type": "application/json"}
            api_key = os.environ.get("CLOUDFLARE_API_KEY") or os.environ.get("VIDEO_API_KEY")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            payload = {
                "prompt": prompt,
                "duration_s": duration_s,
                "width": width,
                "height": height,
            }
            resp = requests.post(api_url, headers=headers, json=payload, timeout=90)
            resp.raise_for_status()

            out_path = self.work_dir / f"clip_{_slug(prompt)}.mp4"
            content_type = resp.headers.get("content-type", "")

            # If worker returned binary MP4
            if "video" in content_type or "octet-stream" in content_type:
                out_path.write_bytes(resp.content)
                self.log.info("diffusion", f"✅ Video generated via Cloudflare Worker: {out_path.name}")
                return out_path

            # If worker returned JSON with video URL
            data = resp.json()
            video_url = data.get("video_url") or data.get("url")
            if video_url:
                v_resp = requests.get(video_url, timeout=60)
                v_resp.raise_for_status()
                out_path.write_bytes(v_resp.content)
                self.log.info("diffusion", f"✅ Video downloaded from Cloudflare Worker URL: {out_path.name}")
                return out_path

        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"Cloudflare Worker video API failed: {exc} — trying next tier")
            return None

        return None

    # ── Priority 2: Hugging Face Cloud Free GPU Space (ZeroGPU) ───────────

    def _hf_space_video(
        self, prompt: str, duration_s: int, fps: int, width: int, height: int
    ) -> Path | None:
        """
        Call remote Hugging Face ZeroGPU Space via gradio_client.
        Runs on free cloud A100 GPUs without local compute.
        """
        try:
            from gradio_client import Client
        except ImportError:
            self.log.warn("diffusion", "gradio_client not installed — skipping Cloud GPU Space")
            return None

        space_id = os.environ.get("HF_VIDEO_SPACE") or "ByteDance/AnimateDiff-Lightning"
        self.log.step("diffusion", f"Attempting Cloud GPU Space: {space_id}…")

        try:
            client = None
            auth_kwargs = [{"token": self.hf_key}, {"hf_token": self.hf_key}, {}] if self.hf_key else [{}]
            for kw in auth_kwargs:
                try:
                    client = Client(space_id, **kw)
                    break
                except TypeError:
                    continue

            if client is None:
                client = Client(space_id)

            job = None
            for api in ["/predict", "/generate", None]:
                try:
                    job = client.submit(prompt, api_name=api) if api else client.submit(prompt)
                    break
                except Exception:
                    continue

            if job is None:
                raise RuntimeError(f"Could not submit job to Space {space_id}")

            # Up to 45 seconds timeout for remote cloud generation
            result_path = job.result(timeout=45)

            if result_path and Path(result_path).exists():
                out = self.work_dir / f"clip_{_slug(prompt)}.mp4"
                self._normalize_video(Path(result_path), out, width, height, fps)
                self.log.info("diffusion", f"✅ Cloud GPU Space video generated: {out.name}")
                return out
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"Cloud GPU Space ({space_id}) unavailable: {exc} — falling back to Ken Burns engine")
            return None

        return None

    # ── Priority 3: Hugging Face Serverless Router ────────────────────────

    def _hf_video_frames(
        self, prompt: str, duration_s: int, fps: int
    ) -> list[Image.Image] | None:
        """HuggingFace text-to-video serverless router (if available)."""
        if not self.hf_key:
            return None
        try:
            resp = requests.post(
                _HF_VIDEO_URL,
                headers={"Authorization": f"Bearer {self.hf_key}"},
                json={"inputs": prompt},
                timeout=45,
            )
            resp.raise_for_status()
            gif = Image.open(io.BytesIO(resp.content))
            frames: list[Image.Image] = []
            for i in range(min(duration_s * fps, getattr(gif, "n_frames", 1))):
                gif.seek(i)
                frames.append(gif.copy().convert("RGB"))
            return frames or None
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"HF serverless video endpoint unavailable: {exc}")
            return None

    # ── Priority 4 (Fallback): Remote Visual Generation ───────────────────

    def _hf_image(self, prompt: str, width: int, height: int) -> Image.Image | None:
        """HuggingFace Serverless Inference (FLUX / SD-2-1)."""
        if not self.hf_key:
            return None
        headers = {"Authorization": f"Bearer {self.hf_key}"}
        for url in [_HF_IMAGE_URL, _HF_IMAGE_BACKUP_URL]:
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json={"inputs": prompt, "parameters": {"width": width, "height": height}},
                    timeout=25,
                )
                if resp.status_code == 200:
                    img = Image.open(io.BytesIO(resp.content))
                    self.log.info("diffusion", f"HF visual generated via {url.split('/')[-1]}")
                    return img
            except Exception as exc:  # noqa: BLE001
                self.log.warn("diffusion", f"HF visual ({url.split('/')[-1]}) failed: {exc}")
        return None

    def _pollinations_image(self, prompt: str, width: int, height: int) -> Image.Image | None:
        """Pollinations AI — free tier, zero auth, high-res FLUX/Turbo anime visuals."""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        seed = abs(hash(prompt)) % 1000000
        encoded = urllib.parse.quote(prompt[:300])

        # Try flux first with 45s timeout, then fast turbo fallback (1-3s generation)
        candidates = [("flux", 45), ("turbo", 25)]
        for model_name, timeout_s in candidates:
            try:
                url = f"{_POLLINATIONS_BASE_URL}/{encoded}?width={width}&height={height}&model={model_name}&nologo=true&seed={seed}"
                resp = requests.get(url, headers=headers, timeout=timeout_s)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    self.log.info("diffusion", f"Pollinations {model_name.upper()} visual generated ({width}x{height})")
                    return img
            except Exception as exc:  # noqa: BLE001
                self.log.warn("diffusion", f"Pollinations (model={model_name}) failed: {exc}")

        return None

    # ── Cinematic Ken Burns Motion Engine ─────────────────────────────────

    @staticmethod
    def _image_to_ken_burns_frames(
        img: Image.Image,
        duration_s: int,
        fps: int,
        width: int,
        height: int,
        motion: str = "zoom_in",
    ) -> list[Image.Image]:
        """Creates smooth cinematic camera movements across the visual."""
        base = img.resize((width, height), Image.LANCZOS)
        total_frames = max(duration_s * fps, 1)
        frames: list[Image.Image] = []
        max_zoom = 1.15

        for i in range(total_frames):
            progress = i / max(total_frames - 1, 1)

            if motion == "zoom_in":
                zoom = 1.0 + (max_zoom - 1.0) * progress
                cw, ch = int(width / zoom), int(height / zoom)
                x = (width - cw) // 2
                y = (height - ch) // 2
            elif motion == "zoom_out":
                zoom = max_zoom - (max_zoom - 1.0) * progress
                cw, ch = int(width / zoom), int(height / zoom)
                x = (width - cw) // 2
                y = (height - ch) // 2
            elif motion == "pan_right":
                zoom = 1.12
                cw, ch = int(width / zoom), int(height / zoom)
                max_x = width - cw
                x = int(max_x * progress)
                y = (height - ch) // 2
            elif motion == "pan_left":
                zoom = 1.12
                cw, ch = int(width / zoom), int(height / zoom)
                max_x = width - cw
                x = int(max_x * (1.0 - progress))
                y = (height - ch) // 2
            elif motion == "tilt_up":
                zoom = 1.12
                cw, ch = int(width / zoom), int(height / zoom)
                max_y = height - ch
                x = (width - cw) // 2
                y = int(max_y * (1.0 - progress))
            else:
                cw, ch = width, height
                x, y = 0, 0

            cropped = base.crop((x, y, x + cw, y + ch)).resize((width, height), Image.BILINEAR)
            frames.append(cropped)

        return frames

    # ── Placeholders ──────────────────────────────────────────────────────

    @staticmethod
    def _placeholder_image(prompt: str, width: int = 1280, height: int = 720) -> Image.Image:
        """Aesthetic dark cinematic card with scene text."""
        img = Image.new("RGB", (width, height), color=(15, 15, 28))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (width, 8)], fill=(138, 43, 226))
        draw.rectangle([(0, height - 8), (width, height)], fill=(75, 0, 130))
        draw.text((width // 2, height // 2), prompt[:90], fill=(220, 220, 240), anchor="mm")
        return img

    # ── FFmpeg Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _frames_to_mp4(
        frames: list[Image.Image], out: Path, fps: int, width: int, height: int
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, frame in enumerate(frames):
                frame.resize((width, height)).save(f"{tmpdir}/f{i:04d}.png")
            subprocess.run([
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-i", f"{tmpdir}/f%04d.png",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                str(out),
            ], capture_output=True, check=True)

    @staticmethod
    def _normalize_video(
        src: Path, out: Path, width: int, height: int, fps: int
    ) -> None:
        """Ensure downloaded video matches target resolution and framerate."""
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            str(out),
        ], capture_output=True, check=True)


def _slug(text: str, max_len: int = 20) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "_", text.lower())[:max_len]
