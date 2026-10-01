"""
Orchestrator
============
Main pipeline entry point. Reads run_spec.json and drives every stage:

  Stage 0 – Validate spec & load config
  Stage 1 – SEO Research (trending keywords)
  Stage 2 – Script Generation (LLM)
  Stage 3 – SEO Metadata Generation (title, description, tags, chapters)
  Stage 4 – Video Clip Generation (per-scene diffusion)
  Stage 5 – Narration + Background Music
  Stage 6 – Video Editing (transitions, captions, lower-thirds, audio mix)
  Stage 7 – Thumbnail Generation
  Stage 8 – GitHub Release
  Stage 9 – YouTube Upload (optional)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pika_flow.audio_generator import AudioGenerator
from pika_flow.llm_manager import LLMManager
from pika_flow.logger import PikaLogger
from pika_flow.release_manager import ReleaseManager
from pika_flow.seo_generator import SEOGenerator
from pika_flow.thumbnail_generator import ThumbnailGenerator
from pika_flow.video_editor import VideoEditor
from pika_flow.video_generator import generate_clips

_CFG_DIR  = Path(__file__).parent.parent / "config"
_WORK_DIR = Path(os.environ.get("PIKA_WORK_DIR", "/tmp/pika_work"))


def _run_id() -> str:
    now = datetime.now(timezone.utc)
    return f"run-{now.strftime('%d%m%Y-%H%M')}"


def _load_channel_cfg() -> dict:
    return json.loads((_CFG_DIR / "channel_config.json").read_text())


def _extract_script_text(llm_resp: dict) -> str:
    """Pull plain text out of an LLM response (handles Gemini format)."""
    try:
        return llm_resp["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return str(llm_resp)


def _parse_scenes_from_script(script: str, default_duration: int = 4) -> list[dict]:
    """
    Try to parse a JSON scene list from the LLM script response.
    Falls back to a single-scene list if JSON is not present.
    """
    import re
    match = re.search(r'\[.*\]', script, re.DOTALL)
    if match:
        try:
            scenes = json.loads(match.group(0))
            if isinstance(scenes, list):
                return scenes
        except json.JSONDecodeError:
            pass

    # Fallback: one scene per paragraph
    paragraphs = [p.strip() for p in script.split("\n\n") if p.strip()]
    return [
        {"prompt": p[:200], "description": p[:200], "duration_s": default_duration}
        for p in paragraphs[:10]  # cap at 10 scenes
    ]


# ── Main pipeline ──────────────────────────────────────────────────────────

async def run(spec_path: Path) -> None:
    # ── Setup ──────────────────────────────────────────────────────────────
    spec   = json.loads(spec_path.read_text())
    run_id = spec.get("run_id") or _run_id()
    work   = spec_path.parent
    work.mkdir(parents=True, exist_ok=True)

    log = PikaLogger(run_id=run_id, log_dir=work)
    log.start(f"PikaFlow pipeline started: {run_id}")

    channel_cfg = _load_channel_cfg()
    niche       = channel_cfg.get("niche", "general")
    prompt      = spec.get("prompt", "")
    fmt         = spec.get("format", channel_cfg.get("default_format", "medium"))

    if not prompt:
        log.error("orchestrator", "No prompt found in run_spec.json — aborting")
        sys.exit(1)

    llm = LLMManager(logger=log)

    try:
        # ── Stage 1: SEO Research ──────────────────────────────────────────
        log.step("orchestrator", "▶ Stage 1 — SEO Research")
        seo = SEOGenerator(logger=log, llm=llm, work_dir=work)
        brief = await seo.research(niche=niche, base_prompt=prompt)

        # ── Stage 2: Script Generation ─────────────────────────────────────
        log.step("orchestrator", "▶ Stage 2 — Script Generation")
        keywords_hint = ", ".join(brief.get("keywords", [])[:5])
        script_prompt = (
            f"You are a creative YouTube scriptwriter specialising in {niche} content.\n"
            f"Write a script for a {fmt}-form video about: {prompt}\n"
            f"Incorporate these trending keywords naturally: {keywords_hint}\n\n"
            f"Return a JSON array of scenes. Each scene:\n"
            f'{{"prompt": "Stable Diffusion prompt for the scene visual", '
            f'"description": "Narrator text for this scene", '
            f'"title": "Scene title for chapters", '
            f'"duration_s": <seconds as integer>}}\n\n'
            f"Use {_format_scene_count(fmt)} scenes. No extra text — JSON only."
        )
        script_resp = await llm.call(
            task_type="text",
            payload={"contents": [{"parts": [{"text": script_prompt}]}]},
        )
        script_text = _extract_script_text(script_resp)
        scenes      = _parse_scenes_from_script(script_text)
        spec["scenes"] = scenes

        # Persist updated spec with scenes
        spec_path.write_text(json.dumps(spec, indent=2))
        log.info("orchestrator", f"Script ready — {len(scenes)} scenes", {"format": fmt})

        # ── Stage 3: SEO Metadata ──────────────────────────────────────────
        log.step("orchestrator", "▶ Stage 3 — SEO Metadata Generation")
        narrator_text = " ".join(s.get("description", "") for s in scenes)
        metadata = await seo.generate_metadata(
            script=narrator_text,
            scenes=scenes,
            brief=brief,
        )
        log.info("orchestrator", f"SEO title: {metadata.get('title', 'N/A')}")

        # ── Stage 4: Video Clip Generation ────────────────────────────────
        log.step("orchestrator", "▶ Stage 4 — Video Clip Generation")
        clips = await generate_clips(
            scenes=scenes,
            work_dir=work,
            logger=log,
        )
        log.info("orchestrator", f"{len(clips)} clips generated")

        # ── Stage 5: Narration + Music ────────────────────────────────────
        log.step("orchestrator", "▶ Stage 5 — Audio Generation")
        audio = AudioGenerator(logger=log, work_dir=work)
        narration = audio.generate_narration(narrator_text)

        total_duration = sum(s.get("duration_s", 4) for s in scenes)
        
        bg_music = None
        if spec.get("use_music", True):
            bg_music = audio.fetch_bg_music(
                query=metadata.get("tags", ["background music"])[0] + " background music",
                duration_s=total_duration,
            )

        # ── Stage 6: Video Editing ─────────────────────────────────────────
        log.step("orchestrator", "▶ Stage 6 — Video Editing")
        editor   = VideoEditor(logger=log, work_dir=work)
        stitched = editor.add_transitions(clips)

        captions = metadata.get("chapters", [])
        # Convert chapter markers to caption format
        caption_list = [
            {
                "text":  ch.get("label", ""),
                "start": _time_to_sec(ch.get("time", "0:00")),
                "end":   _time_to_sec(ch.get("time", "0:00")) + 3.0,
            }
            for ch in captions
        ]
        if caption_list:
            stitched = editor.overlay_captions(stitched, caption_list)

        if narration.exists():
            stitched = editor.mix_audio(stitched, narration, bg_music)

        final_video = editor.final_export(stitched, run_id)

        # ── Stage 7: Thumbnail Generation ─────────────────────────────────
        log.step("orchestrator", "▶ Stage 7 — Thumbnail Generation")
        thumb_gen = ThumbnailGenerator(logger=log, work_dir=work)
        thumbnail = await thumb_gen.generate(
            prompt=metadata.get("thumbnail_prompt", prompt),
            title=metadata.get("title", "PikaFlow Video"),
            hf_api_key=os.environ.get("HUGGINGFACE_API_KEY"),
        )

        # ── Stage 8: GitHub Release ────────────────────────────────────────
        log.step("orchestrator", "▶ Stage 8 — GitHub Release")
        assets = [
            a for a in [
                final_video,
                thumbnail,
                work / "seo_metadata.json",
                work / "seo_brief.json",
                spec_path,
                work / "log.jsonl",
                work / "summary.md",
            ]
            if a and a.exists()
        ]

        release_url = ""
        try:
            rm = ReleaseManager(logger=log)
            release_url = rm.create_release(
                run_id=run_id,
                assets=assets,
                title=metadata.get("title", f"PikaFlow {run_id}"),
                body=metadata.get("description", ""),
            )
            log.info("orchestrator", f"Release created → {release_url}")
        except Exception as exc:  # noqa: BLE001
            log.warn("orchestrator", f"Release creation failed (non-fatal): {exc}")

        # ── Stage 9: YouTube Upload (optional) ────────────────────────────
        yt_client_id = os.environ.get("YOUTUBE_CLIENT_ID", "")
        if yt_client_id and final_video.exists():
            if spec.get("upload_to_youtube", False):
                log.step("orchestrator", "▶ Stage 9 — YouTube Upload")
                try:
                    from pika_flow.youtube_uploader import upload_video
                    vid_id = upload_video(final_video, metadata, thumbnail, log)
                    log.info("orchestrator", f"YouTube video → https://youtu.be/{vid_id}")
                except Exception as exc:  # noqa: BLE001
                    log.warn("orchestrator", f"YouTube upload failed (non-fatal): {exc}")
            else:
                log.info("orchestrator", "YouTube upload skipped (disabled in Dev Panel)")
        else:
            log.info("orchestrator", "YouTube upload skipped (YOUTUBE_CLIENT_ID not set)")

        # ── Done ───────────────────────────────────────────────────────────
        log.done(f"Pipeline complete ✅  |  Run: {run_id}  |  Release: {release_url}")

    except Exception as exc:  # noqa: BLE001
        log.error("orchestrator", f"Pipeline failed: {exc}")
        import traceback
        log.error("orchestrator", traceback.format_exc())
        sys.exit(1)

    finally:
        await llm.close()


# ── Utilities ──────────────────────────────────────────────────────────────

def _format_scene_count(fmt: str) -> str:
    return {"short": "4–6", "medium": "8–12", "long": "16–24"}.get(fmt, "8–12")


def _time_to_sec(time_str: str) -> float:
    """Convert 'M:SS' or 'H:MM:SS' to seconds."""
    parts = time_str.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return 0.0


if __name__ == "__main__":
    spec = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("run_spec.json")
    asyncio.run(run(spec))
