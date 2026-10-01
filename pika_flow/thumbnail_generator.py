"""
Thumbnail Generator
===================
Generates a high-CTR YouTube thumbnail using a Stable Diffusion
prompt produced by the SEO Generator.

Pipeline:
  1. Receive thumbnail_prompt from seo_metadata.json
  2. Call the diffusion wrapper (local SD or HuggingFace remote)
  3. Post-process: add text overlay (title) via Pillow
  4. Save thumbnail.jpg (1280×720, YouTube recommended size)
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from pika_flow.logger import PikaLogger

_YT_THUMB_SIZE = (1280, 720)


class ThumbnailGenerator:
    def __init__(self, logger: PikaLogger, work_dir: Path) -> None:
        self.log      = logger
        self.work_dir = work_dir

    async def generate(
        self,
        prompt: str,
        title: str,
        hf_api_key: str | None = None,
    ) -> Path:
        """
        Generate a thumbnail for the given prompt and overlay the title.

        Args:
            prompt:     Stable Diffusion prompt from SEO metadata.
            title:      Video title to overlay on the thumbnail.
            hf_api_key: HuggingFace API key for remote inference.

        Returns:
            Path to the saved thumbnail.jpg
        """
        self.log.step("thumbnail_generator", "Generating thumbnail")

        img = await self._generate_image(prompt, hf_api_key)
        img = img.resize(_YT_THUMB_SIZE, Image.LANCZOS)
        img = self._overlay_title(img, title)

        out = self.work_dir / "thumbnail.jpg"
        img.save(out, "JPEG", quality=95)
        self.log.info("thumbnail_generator", f"Thumbnail saved → {out.name}")
        return out

    # ── Image generation ──────────────────────────────────────────────────

    async def _generate_image(
        self, prompt: str, hf_api_key: str | None
    ) -> Image.Image:
        """Try HuggingFace remote first, fallback to local generation."""
        if hf_api_key:
            try:
                return self._hf_inference(prompt, hf_api_key)
            except Exception as exc:  # noqa: BLE001
                self.log.warn("thumbnail_generator", f"HF inference failed: {exc}, using local fallback")

        return self._local_fallback(prompt)

    def _hf_inference(self, prompt: str, api_key: str) -> Image.Image:
        """Call HuggingFace Inference API for image generation."""
        api_url = "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-2-1"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "inputs": prompt,
            "parameters": {
                "width": 1280,
                "height": 720,
                "num_inference_steps": 20,
            },
        }
        resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content))

    def _local_fallback(self, prompt: str) -> Image.Image:
        """
        Local CPU-only generation via diffusers.
        Slower but works without internet or API keys.
        """
        try:
            from diffusers import StableDiffusionPipeline  # type: ignore
            import torch

            self.log.info("thumbnail_generator", "Running local SD pipeline (CPU)")
            pipe = StableDiffusionPipeline.from_pretrained(
                "runwayml/stable-diffusion-v1-5",
                torch_dtype=torch.float32,
            )
            pipe.to("cpu")
            result = pipe(
                prompt,
                width=640, height=360,  # lower res for CPU speed
                num_inference_steps=15,
            )
            return result.images[0]
        except Exception as exc:  # noqa: BLE001
            self.log.warn("thumbnail_generator", f"Local SD failed: {exc} — using placeholder")
            return self._placeholder(prompt)

    @staticmethod
    def _placeholder(prompt: str) -> Image.Image:
        """Last-resort: solid-colour placeholder with prompt text."""
        img = Image.new("RGB", _YT_THUMB_SIZE, color=(30, 30, 50))
        draw = ImageDraw.Draw(img)
        draw.text(
            (640, 360), prompt[:80], fill=(200, 200, 200), anchor="mm"
        )
        return img

    # ── Title overlay ─────────────────────────────────────────────────────

    @staticmethod
    def _overlay_title(img: Image.Image, title: str) -> Image.Image:
        """
        Overlay a semi-transparent bottom bar with the video title.
        Uses a bundled font if available, otherwise PIL default.
        """
        draw = ImageDraw.Draw(img, "RGBA")
        w, h = img.size
        bar_h = 120

        # Semi-transparent black bar at the bottom
        draw.rectangle([(0, h - bar_h), (w, h)], fill=(0, 0, 0, 180))

        # Title text
        try:
            font = ImageFont.truetype("assets/fonts/OpenSans-Bold.ttf", size=52)
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()

        # Truncate title if too long
        short_title = title if len(title) <= 55 else title[:52] + "..."
        draw.text(
            (w // 2, h - bar_h // 2),
            short_title,
            fill=(255, 255, 255, 255),
            font=font,
            anchor="mm",
        )
        return img.convert("RGB")

