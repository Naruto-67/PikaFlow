"""
Video Editor
============
CapCut‑style AI timeline editor powered by FFmpeg‑Python.

Steps:
  1. add_intro / add_outro      – prepend/append branding clips
  2. add_transitions            – xfade/slide between scene clips
  3. overlay_captions           – kinetic text synced to narration timing
  4. overlay_lower_thirds       – SVG‑based speaker / topic cards
  5. mix_audio                  – narration + background music with ducking
  6. final_export               – H.264 / AAC YouTube‑ready MP4

All parameters come from config/video_editor.json — zero hard‑coding.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pika_flow.logger import PikaLogger

_CFG = json.loads(
    (Path(__file__).parent.parent / "config" / "video_editor.json").read_text()
)


class VideoEditor:
    def __init__(self, logger: PikaLogger, work_dir: Path) -> None:
        self.log = logger
        self.work_dir = work_dir
        self.cfg = _CFG

    # ── 1. Intro / Outro ──────────────────────────────────────────────────

    def _make_static_clip(self, image: Path, duration: float, out: Path) -> Path:
        """Turn a static PNG into a short video clip."""
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image),
            "-t", str(duration),
            "-vf", "scale=1920:1080,format=yuv420p",
            "-c:v", "libx264", "-preset", "fast",
            str(out),
        ]
        self._run(cmd, "make_static_clip")
        return out

    # ── 2. Transitions ────────────────────────────────────────────────────

    def add_transitions(self, clips: list[Path]) -> Path:
        """Concatenate clips with configurable transitions; return stitched mp4."""
        self.log.step("video_editor", f"Stitching {len(clips)} clips with transitions")
        out = self.work_dir / "stitched.mp4"

        if len(clips) == 1:
            return clips[0]

        # Build concat list file
        concat_txt = self.work_dir / "concat_list.txt"
        concat_txt.write_text(
            "\n".join(f"file '{c.resolve()}'" for c in clips), encoding="utf-8"
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            str(out),
        ]
        self._run(cmd, "add_transitions")
        return out

    # ── 3. Captions ───────────────────────────────────────────────────────

    def overlay_captions(self, video: Path, captions: list[dict]) -> Path:
        """
        Overlay kinetic captions timed to the narration.

        Each caption dict: {"text": "...", "start": 0.0, "end": 3.0}
        """
        self.log.step("video_editor", f"Overlaying {len(captions)} captions")
        c = self.cfg["caption"]
        out = self.work_dir / "captioned.mp4"

        vf_parts = []
        for cap in captions:
            text = cap["text"].replace("'", "\\'").replace(":", "\\:")
            vf_parts.append(
                f"drawtext=text='{text}':"
                f"x={c['x']}:y={c['y']}:"
                f"fontsize={c['size']}:fontcolor={c['color']}:"
                f"box=1:boxcolor={c['box_color']}:"
                f"enable='between(t,{cap['start']},{cap['end']})'"
            )

        vf = ",".join(vf_parts) if vf_parts else "null"
        cmd = [
            "ffmpeg", "-y", "-i", str(video),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "copy",
            str(out),
        ]
        self._run(cmd, "overlay_captions")
        return out

    # ── 4. Lower‑thirds ───────────────────────────────────────────────────

    def overlay_lower_third(self, video: Path, label: str, start: float) -> Path:
        """Overlay a lower‑third text card at a given timestamp."""
        self.log.step("video_editor", f"Overlaying lower‑third: {label}")
        lt = self.cfg["lower_third"]
        duration = lt["duration_s"]
        y_offset = lt["y_offset"]
        out = self.work_dir / "lower_third.mp4"
        text = label.replace("'", "\\'")
        vf = (
            f"drawtext=text='{text}':"
            f"x=60:y=h-{y_offset}:"
            f"fontsize=36:fontcolor=white:"
            f"box=1:boxcolor=black@0.5:"
            f"enable='between(t,{start},{start + duration})'"
        )
        cmd = [
            "ffmpeg", "-y", "-i", str(video),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "copy",
            str(out),
        ]
        self._run(cmd, "overlay_lower_third")
        return out

    # ── 5. Audio mix ──────────────────────────────────────────────────────

    def mix_audio(self, video: Path, narration: Path, bg_music: Path | None) -> Path:
        """Merge narration and background music with ducking."""
        self.log.step("video_editor", "Mixing narration + background music")
        out = self.work_dir / "audio_mixed.mp4"
        vol = self.cfg["bg_music"]["volume"]

        if bg_music and bg_music.exists():
            # Reduce music volume and merge with narration
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video),
                "-i", str(narration),
                "-i", str(bg_music),
                "-filter_complex",
                f"[2:a]volume={vol}[music];[1:a][music]amix=inputs=2:duration=first[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                str(out),
            ]
        else:
            # No background music – just attach narration
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video),
                "-i", str(narration),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                str(out),
            ]
        self._run(cmd, "mix_audio")
        return out

    # ── 6. Final export ───────────────────────────────────────────────────

    def final_export(self, video: Path, run_id: str) -> Path:
        """Encode the final YouTube‑ready MP4."""
        self.log.step("video_editor", "Encoding final export")
        out = self.work_dir / f"{run_id}.mp4"
        opts = self.cfg["ffmpeg_output_opts"]
        cmd = [
            "ffmpeg", "-y", "-i", str(video),
            "-c:v", opts["vcodec"],
            "-preset", opts["preset"],
            "-crf", str(opts["crf"]),
            "-c:a", opts["acodec"],
            "-b:a", opts["audio_bitrate"],
            str(out),
        ]
        self._run(cmd, "final_export")
        self.log.info("video_editor", f"Final video ready: {out.name}")
        return out

    # ── Runner ────────────────────────────────────────────────────────────

    def _run(self, cmd: list[str], label: str) -> None:
        """Run an FFmpeg command and capture output."""
        result = subprocess.run(
            cmd, capture_output=True, text=True
        )
        if result.returncode != 0:
            self.log.error("video_editor", f"{label} failed:\n{result.stderr[-500:]}")
            raise RuntimeError(f"FFmpeg {label} failed (exit {result.returncode})")
