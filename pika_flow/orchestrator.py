"""
Orchestrator
============
Main pipeline entry point. Reads run_spec.json and drives every stage:
  1. Script generation (via LLM Manager)
  2. Per‑scene clip generation (video_generator)
  3. Narration + background music (audio_generator)
  4. Timeline editing (video_editor)
  5. Release creation (release_manager)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pika_flow.llm_manager import LLMManager
from pika_flow.logger import PikaLogger
from pika_flow.video_editor import VideoEditor

_WORK_DIR = Path(os.environ.get("PIKA_WORK_DIR", "/tmp/pika_work"))
_CFG_DIR  = Path(__file__).parent.parent / "config"


def _run_id() -> str:
    now = datetime.now(timezone.utc)
    return f"run-{now.strftime('%d%m%Y-%H%M')}"


async def run(spec_path: Path) -> None:
    run_id = _run_id()
    work   = _WORK_DIR / run_id
    work.mkdir(parents=True, exist_ok=True)

    log = PikaLogger(run_id=run_id, log_dir=work)
    log.start(f"PikaFlow run started: {run_id}")

    try:
        spec = json.loads(spec_path.read_text())
        log.info("orchestrator", f"Loaded spec: {spec_path.name}", {"scenes": len(spec.get('scenes', []))})

        llm = LLMManager(logger=log)

        # ── Stage 1: Script generation ─────────────────────────────────
        log.step("orchestrator", "Stage 1 — Script generation")
        script_resp = await llm.call(
            task_type="text",
            payload={
                "contents": [{"parts": [{"text": spec.get("prompt", "")}]}]
            },
        )
        log.info("orchestrator", "Script generated", {"provider": script_resp.get("model", "unknown")})

        # ── Stage 2: Clip generation ────────────────────────────────────
        log.step("orchestrator", "Stage 2 — Clip generation (placeholder)")
        clips: list[Path] = []  # populated by video_generator in a real run

        # ── Stage 3: Audio ──────────────────────────────────────────────
        log.step("orchestrator", "Stage 3 — Audio generation (placeholder)")
        narration_path: Path | None = None
        bg_music_path:  Path | None = None

        # ── Stage 4: Editing ────────────────────────────────────────────
        if clips:
            log.step("orchestrator", "Stage 4 — Video editing")
            editor  = VideoEditor(logger=log, work_dir=work)
            stitched = editor.add_transitions(clips)
            captions = spec.get("captions", [])
            if captions:
                stitched = editor.overlay_captions(stitched, captions)
            if narration_path:
                stitched = editor.mix_audio(stitched, narration_path, bg_music_path)
            final = editor.final_export(stitched, run_id)
            log.info("orchestrator", f"Final video: {final}")
        else:
            log.warn("orchestrator", "No clips generated — skipping edit stage")

        # ── Stage 5: Release ────────────────────────────────────────────
        log.step("orchestrator", "Stage 5 — GitHub Release (placeholder)")

        log.done(f"Run {run_id} finished successfully")
        await llm.close()

    except Exception as exc:  # noqa: BLE001
        log.error("orchestrator", f"Pipeline failed: {exc}")
        await asyncio.sleep(0)  # allow logs to flush
        sys.exit(1)


if __name__ == "__main__":
    spec = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("run_spec.json")
    asyncio.run(run(spec))
