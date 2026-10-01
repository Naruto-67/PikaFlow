"""
Diffusion Wrapper
=================
Unified interface for image and video generation.

Multi-tier execution strategy:
  1. Remote Text-to-Video: HuggingFace Inference Router (if available)
  2. Local GPU Video: AnimateDiff (CUDA only — strictly skipped on CPU to prevent runner hangs)
  3. High-Res Remote Image + Cinematic Ken Burns Motion Engine:
     - Tier A: HuggingFace Serverless Inference (FLUX / SD-2-1)
     - Tier B: Pollinations AI (Zero-auth, free tier, instant FLUX/SDXL, 1280x720)
     - Dynamic Camera Motions: Zoom-in, Pan-right, Zoom-out, Pan-left, Tilt-up
  4. Local Fallback: Stylised dark anime motion card (last resort, never crashes)

All clips are encoded to MP4 via FFmpeg.
"""

from __future__ import annotations

import io
import os
import urllib.parse
from pathlib import Path
from typing import Literal

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
            or self._local_image(prompt, width, height)
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
        If direct text-to-video is unavailable (standard on free tier / CPU runners),
        generates a high-res scene visual and applies smooth cinematic Ken Burns motion.
        """
        self.log.step("diffusion", f"Clip: {prompt[:60]}…")

        # 1. Try remote text-to-video
        frames = self._hf_video_frames(prompt, duration_s, fps)

        # 2. Try local GPU video (CUDA ONLY)
        if not frames:
            frames = self._local_video_frames(prompt, duration_s, fps)

        # 3. Cinematic Ken Burns motion engine on high-res scene visual
        if not frames:
            motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "tilt_up"]
            chosen_motion = motions[motion_index % len(motions)]
            self.log.info("diffusion", f"Rendering cinematic {chosen_motion} motion clip ({width}x{height} @ {fps}fps)")

            img = (
                self._hf_image(prompt, width, height)
                or self._pollinations_image(prompt, width, height)
                or self._local_image(prompt, width, height)
            )
            frames = self._image_to_ken_burns_frames(
                img=img,
                duration_s=duration_s,
                fps=fps,
                width=width,
                height=height,
                motion=chosen_motion,
            )

        # Fallback to placeholder if everything failed
        if not frames:
            frames = self._placeholder_frames(duration_s, fps, width, height)

        out = self.work_dir / f"clip_{_slug(prompt)}.mp4"
        self._frames_to_mp4(frames, out, fps, width, height)
        self.log.info("diffusion", f"Clip saved → {out.name}")
        return out

    # ── Remote Image Generation ───────────────────────────────────────────

    def _hf_image(self, prompt: str, width: int, height: int) -> Image.Image | None:
        """HuggingFace Serverless Inference API."""
        if not self.hf_key:
            return None
        headers = {"Authorization": f"Bearer {self.hf_key}"}
        for url in [_HF_IMAGE_URL, _HF_IMAGE_BACKUP_URL]:
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json={"inputs": prompt, "parameters": {"width": width, "height": height}},
                    timeout=30,
                )
                if resp.status_code == 200:
                    img = Image.open(io.BytesIO(resp.content))
                    self.log.info("diffusion", f"HF image generated via {url.split('/')[-1]}")
                    return img
            except Exception as exc:  # noqa: BLE001
                self.log.warn("diffusion", f"HF image ({url.split('/')[-1]}) failed: {exc}")
        return None

    def _pollinations_image(self, prompt: str, width: int, height: int) -> Image.Image | None:
        """Pollinations AI — free tier, zero auth, high-res FLUX/SDXL anime visuals."""
        try:
            seed = abs(hash(prompt)) % 1000000
            encoded = urllib.parse.quote(prompt[:300])
            url = f"{_POLLINATIONS_BASE_URL}/{encoded}?width={width}&height={height}&model=flux&nologo=true&seed={seed}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = requests.get(url, headers=headers, timeout=25)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            self.log.info("diffusion", f"Pollinations Flux visual generated ({width}x{height})")
            return img
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"Pollinations image generation failed: {exc}")
            return None

    # ── Remote Video Generation ───────────────────────────────────────────

    def _hf_video_frames(
        self, prompt: str, duration_s: int, fps: int
    ) -> list[Image.Image] | None:
        """HuggingFace text-to-video (when supported by serverless endpoints)."""
        if not self.hf_key:
            return None
        try:
            resp = requests.post(
                _HF_VIDEO_URL,
                headers={"Authorization": f"Bearer {self.hf_key}"},
                json={"inputs": prompt},
                timeout=60,
            )
            resp.raise_for_status()
            gif = Image.open(io.BytesIO(resp.content))
            frames: list[Image.Image] = []
            for i in range(min(duration_s * fps, getattr(gif, "n_frames", 1))):
                gif.seek(i)
                frames.append(gif.copy().convert("RGB"))
            return frames or None
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"HF video endpoint unavailable ({exc}) — falling back to Ken Burns engine")
            return None

    # ── Local Generation ──────────────────────────────────────────────────

    def _local_image(self, prompt: str, width: int, height: int) -> Image.Image:
        """Local SD pipeline (CUDA ONLY). On CPU, returns a high-contrast cinematic card."""
        try:
            import torch
            if torch.cuda.is_available():
                from diffusers import StableDiffusionPipeline  # type: ignore

                self.log.info("diffusion", "Local SD image (GPU)")
                pipe = StableDiffusionPipeline.from_pretrained(
                    "runwayml/stable-diffusion-v1-5", torch_dtype=torch.float16
                )
                pipe.to("cuda")
                return pipe(prompt, num_inference_steps=15, width=width, height=height).images[0]
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"Local GPU SD failed: {exc}")

        # Fast CPU placeholder (avoids hanging runner on slow CPU diffusion)
        return self._placeholder_image(prompt, width, height)

    def _local_video_frames(
        self, prompt: str, duration_s: int, fps: int
    ) -> list[Image.Image] | None:
        """AnimateDiff (CUDA ONLY). CPU is strictly skipped to prevent 40+ minute CI hangs."""
        try:
            import torch
            if not torch.cuda.is_available():
                self.log.info("diffusion", "No CUDA GPU detected — skipping CPU AnimateDiff (Ken Burns engine will be used)")
                return None

            from diffusers import AnimateDiffPipeline, MotionAdapter  # type: ignore

            self.log.info("diffusion", "Local AnimateDiff (GPU)")
            adapter = MotionAdapter.from_pretrained("guoyww/animatediff-motion-adapter-v1-5-2")
            pipe = AnimateDiffPipeline.from_pretrained(
                "runwayml/stable-diffusion-v1-5",
                motion_adapter=adapter,
                torch_dtype=torch.float16,
            )
            pipe.to("cuda")
            result = pipe(prompt, num_frames=duration_s * fps, num_inference_steps=15)
            return result.frames[0]
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"Local GPU AnimateDiff failed: {exc}")
            return None

    # ── Cinematic Ken Burns Engine ────────────────────────────────────────

    @staticmethod
    def _image_to_ken_burns_frames(
        img: Image.Image,
        duration_s: int,
        fps: int,
        width: int,
        height: int,
        motion: str = "zoom_in",
    ) -> list[Image.Image]:
        """
        Creates smooth camera motion across the source visual.
        Runs in ~0.05s via optimized sub-pixel PIL crops.
        """
        # Ensure base image is high resolution
        base = img.resize((width, height), Image.LANCZOS)
        total_frames = max(duration_s * fps, 1)
        frames: list[Image.Image] = []

        max_zoom = 1.15

        for i in range(total_frames):
            progress = i / max(total_frames - 1, 1)

            if motion == "zoom_in":
                # Smooth push towards center
                zoom = 1.0 + (max_zoom - 1.0) * progress
                cw, ch = int(width / zoom), int(height / zoom)
                x = (width - cw) // 2
                y = (height - ch) // 2
            elif motion == "zoom_out":
                # Smooth pull back from center
                zoom = max_zoom - (max_zoom - 1.0) * progress
                cw, ch = int(width / zoom), int(height / zoom)
                x = (width - cw) // 2
                y = (height - ch) // 2
            elif motion == "pan_right":
                # Zoomed in, pan left to right
                zoom = 1.12
                cw, ch = int(width / zoom), int(height / zoom)
                max_x = width - cw
                x = int(max_x * progress)
                y = (height - ch) // 2
            elif motion == "pan_left":
                # Zoomed in, pan right to left
                zoom = 1.12
                cw, ch = int(width / zoom), int(height / zoom)
                max_x = width - cw
                x = int(max_x * (1.0 - progress))
                y = (height - ch) // 2
            elif motion == "tilt_up":
                # Zoomed in, tilt bottom to top
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
        # Subtle horizontal accent lines
        draw.rectangle([(0, 0), (width, 8)], fill=(138, 43, 226))
        draw.rectangle([(0, height - 8), (width, height)], fill=(75, 0, 130))
        # Title snippet
        draw.text(
            (width // 2, height // 2),
            prompt[:90],
            fill=(220, 220, 240),
            anchor="mm",
        )
        return img

    @staticmethod
    def _placeholder_frames(
        duration_s: int, fps: int, width: int, height: int
    ) -> list[Image.Image]:
        n = max(duration_s * fps, 1)
        colours = [(15, 15, min(255, 30 + i * 2)) for i in range(n)]
        return [Image.new("RGB", (width, height), c) for c in colours]

    # ── Frame → MP4 ───────────────────────────────────────────────────────

    @staticmethod
    def _frames_to_mp4(
        frames: list[Image.Image], out: Path, fps: int, width: int, height: int
    ) -> None:
        import subprocess, tempfile

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


def _slug(text: str, max_len: int = 20) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "_", text.lower())[:max_len]
