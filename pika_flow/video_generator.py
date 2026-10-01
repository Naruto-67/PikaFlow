"""
Video Generator
===============
Generates per-scene video clips from the LLM-produced scene spec.

For each scene:
  1. Build an enriched diffusion prompt (style keywords from channel_config)
  2. Call DiffusionWrapper.generate_video_clip()
  3. Save clip_<N>.mp4 to the work directory

Supports chunked generation: pass --start and --end to process
only a range of scenes (used for long-form videos with multiple jobs).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pika_flow.diffusion_wrapper import DiffusionWrapper
from pika_flow.logger import PikaLogger

_CFG_DIR = Path(__file__).parent.parent / "config"


def _load_channel_cfg() -> dict:
    return json.loads((_CFG_DIR / "channel_config.json").read_text())


def _enrich_prompt(base: str, style_keywords: list[str]) -> str:
    """Append style keywords to the raw scene prompt."""
    style = ", ".join(style_keywords)
    return f"{base}, {style}" if style else base


async def generate_clips(
    spec: dict | None = None,
    work_dir: Path | None = None,
    logger: PikaLogger | None = None,
    scenes: list[dict] | None = None,
    start: int = 0,
    end: int | None = None,
) -> list[Path]:
    """
    Generate video clips for scenes[start:end].

    Args:
        spec:     The full run_spec.json dict (optional if scenes provided)
        work_dir: Directory to save clips
        logger:   PikaLogger instance
        scenes:   Optional list of scene dicts (falls back to spec['scenes'])
        start:    First scene index (inclusive)
        end:      Last scene index (exclusive); None = all

    Returns:
        List of paths to generated MP4 clips, in order.
    """
    spec   = spec or {}
    work_dir = work_dir or Path(".")
    cfg    = _load_channel_cfg()
    
    # Overrides from spec, fallback to channel_config
    style_str = spec.get("style_keywords", "")
    style  = [s.strip() for s in style_str.split(",") if s.strip()] if style_str else cfg.get("style_keywords", [])
    
    fmt    = cfg.get("video_format", {}).get(spec.get("format", cfg.get("default_format", "medium")), {})
    
    res_str = spec.get("resolution") or fmt.get("resolution", "1280x720")
    try:
        width  = int(res_str.split("x")[0])
        height = int(res_str.split("x")[1])
    except Exception:
        width, height = 1280, 720
    
    fps    = fmt.get("fps", 24)
    scenes = scenes if scenes is not None else spec.get("scenes", [])

    diffusion = DiffusionWrapper(logger=logger, work_dir=work_dir)
    subset    = scenes[start:end]

    logger.step("video_generator", f"Generating {len(subset)} clips (scenes {start}–{(end or len(scenes)) - 1})")

    clip_paths: list[Path] = []
    for i, scene in enumerate(subset):
        scene_idx   = start + i
        base_prompt = scene.get("prompt", scene.get("description", "anime scene"))
        prompt      = _enrich_prompt(base_prompt, style)
        duration    = scene.get("duration_s", 4)

        logger.step("video_generator", f"Scene {scene_idx + 1}/{len(scenes)}: {base_prompt[:50]}…")

        clip = diffusion.generate_video_clip(
            prompt=prompt,
            duration_s=duration,
            fps=fps,
            width=width,
            height=height,
            motion_index=scene_idx,
        )

        # Rename to ordered filename for reliable concat
        ordered = work_dir / f"clip_{scene_idx:04d}.mp4"
        clip.rename(ordered)
        clip_paths.append(ordered)
        logger.info("video_generator", f"Clip {scene_idx + 1} ready → {ordered.name}")

    return clip_paths


# ── CLI entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PikaFlow clip generator")
    parser.add_argument("spec",          help="Path to run_spec.json")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end",   type=int, default=None)
    args = parser.parse_args()

    spec_path = Path(args.spec)
    spec      = json.loads(spec_path.read_text())
    work_dir  = spec_path.parent

    log = PikaLogger(run_id=spec["run_id"], log_dir=work_dir)

    clips = asyncio.run(
        generate_clips(
            spec     = spec,
            work_dir = work_dir,
            logger   = log,
            start    = args.start,
            end      = args.end,
        )
    )
    log.info("video_generator", f"Done — {len(clips)} clips generated")

