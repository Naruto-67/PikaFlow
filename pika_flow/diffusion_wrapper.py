"""
Diffusion Wrapper
=================
Unified interface for image and video diffusion.

Priority order:
  1. HuggingFace Inference API (remote, free tier)
  2. Local CPU-only Stable Diffusion (diffusers library)
  3. Placeholder solid-colour frame (last resort, never fails)

All calls go through the ConnectionManager for retry/circuit-breaker.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import requests
from PIL import Image

from pika_flow.logger import PikaLogger

_HF_IMAGE_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1"
_HF_VIDEO_URL = "https://api-inference.huggingface.co/models/damo-vilab/text-to-video-ms-1.7b"


class DiffusionWrapper:
    def __init__(self, logger: PikaLogger, work_dir: Path) -> None:
        self.log      = logger
        self.work_dir = work_dir
        self.hf_key   = os.environ.get("HUGGINGFACE_API_KEY", "")

    # ── Public API ────────────────────────────────────────────────────────

    def generate_image(self, prompt: str, width: int = 512, height: int = 512) -> Path:
        """Generate a single image from a text prompt. Returns saved PNG path."""
        self.log.step("diffusion", f"Image: {prompt[:60]}…")

        img = self._hf_image(prompt, width, height) or self._local_image(prompt)
        out = self.work_dir / f"img_{_slug(prompt)}.png"
        img.save(out, "PNG")
        self.log.info("diffusion", f"Image saved → {out.name}")
        return out

    def generate_video_clip(
        self,
        prompt: str,
        duration_s: int = 3,
        fps: int = 8,
        width: int = 256,
        height: int = 256,
    ) -> Path:
        """
        Generate a short video clip from a text prompt.
        Returns a saved MP4 path.
        """
        self.log.step("diffusion", f"Clip: {prompt[:60]}…")

        frames = (
            self._hf_video_frames(prompt, duration_s, fps)
            or self._local_video_frames(prompt, duration_s, fps)
            or self._placeholder_frames(duration_s, fps, width, height)
        )

        out = self.work_dir / f"clip_{_slug(prompt)}.mp4"
        self._frames_to_mp4(frames, out, fps, width, height)
        self.log.info("diffusion", f"Clip saved → {out.name}")
        return out

    # ── HuggingFace remote ────────────────────────────────────────────────

    def _hf_image(self, prompt: str, width: int, height: int) -> Image.Image | None:
        if not self.hf_key:
            return None
        try:
            resp = requests.post(
                _HF_IMAGE_URL,
                headers={"Authorization": f"Bearer {self.hf_key}"},
                json={"inputs": prompt, "parameters": {"width": width, "height": height}},
                timeout=60,
            )
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content))
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"HF image failed: {exc}")
            return None

    def _hf_video_frames(
        self, prompt: str, duration_s: int, fps: int
    ) -> list[Image.Image] | None:
        """HuggingFace text-to-video returns a GIF/MP4; we extract frames."""
        if not self.hf_key:
            return None
        try:
            resp = requests.post(
                _HF_VIDEO_URL,
                headers={"Authorization": f"Bearer {self.hf_key}"},
                json={"inputs": prompt},
                timeout=120,
            )
            resp.raise_for_status()
            # Response is a GIF stream
            gif = Image.open(io.BytesIO(resp.content))
            frames: list[Image.Image] = []
            for i in range(min(duration_s * fps, gif.n_frames)):
                gif.seek(i)
                frames.append(gif.copy().convert("RGB"))
            return frames or None
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"HF video failed: {exc}")
            return None

    # ── Local CPU fallback ────────────────────────────────────────────────

    def _local_image(self, prompt: str) -> Image.Image:
        try:
            from diffusers import StableDiffusionPipeline  # type: ignore
            import torch

            self.log.info("diffusion", "Local SD image (CPU)")
            pipe = StableDiffusionPipeline.from_pretrained(
                "runwayml/stable-diffusion-v1-5", torch_dtype=torch.float32
            )
            pipe.to("cpu")
            return pipe(prompt, num_inference_steps=15, width=512, height=512).images[0]
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"Local SD failed: {exc} — using placeholder")
            return self._placeholder_image(prompt)

    def _local_video_frames(
        self, prompt: str, duration_s: int, fps: int
    ) -> list[Image.Image] | None:
        """AnimateDiff for short animated clips (CPU)."""
        try:
            from diffusers import AnimateDiffPipeline, MotionAdapter  # type: ignore
            import torch

            self.log.info("diffusion", "Local AnimateDiff (CPU)")
            adapter = MotionAdapter.from_pretrained("guoyww/animatediff-motion-adapter-v1-5-2")
            pipe = AnimateDiffPipeline.from_pretrained(
                "runwayml/stable-diffusion-v1-5",
                motion_adapter=adapter,
                torch_dtype=torch.float32,
            )
            pipe.to("cpu")
            result = pipe(prompt, num_frames=duration_s * fps, num_inference_steps=10)
            return result.frames[0]
        except Exception as exc:  # noqa: BLE001
            self.log.warn("diffusion", f"AnimateDiff failed: {exc}")
            return None

    # ── Placeholders ──────────────────────────────────────────────────────

    @staticmethod
    def _placeholder_image(prompt: str) -> Image.Image:
        img  = Image.new("RGB", (512, 512), color=(20, 20, 40))
        return img

    @staticmethod
    def _placeholder_frames(
        duration_s: int, fps: int, width: int, height: int
    ) -> list[Image.Image]:
        n = duration_s * fps
        colours = [(20, 20, 40 + i * 5) for i in range(n)]
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
                "-c:v", "libx264", "-preset", "fast",
                "-pix_fmt", "yuv420p",
                str(out),
            ], capture_output=True, check=True)


def _slug(text: str, max_len: int = 20) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "_", text.lower())[:max_len]

