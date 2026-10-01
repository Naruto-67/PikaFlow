"""
Audio Generator
===============
Generates narration and background music for the video.

Narration:
  - Primary:  Bark TTS (local, offline, high quality)
  - Fallback: gTTS (Google Text-to-Speech, requires internet)

Background music:
  - Fetches a royalty-free track from Freesound.org API (free API key)
    OR falls back to a silent audio file if not available.

Output files:
  - narration.wav   – full narration audio
  - bg_music.mp3    – background music track (trimmed to video length)
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from pika_flow.logger import PikaLogger

try:
    import inflect
    _p = inflect.engine()
except ImportError:
    _p = None

def _normalize_tts_text(text: str) -> str:
    """Convert numbers/symbols to spoken words for better TTS."""
    if not text:
        return ""
    
    # Symbols
    text = text.replace("$", " dollars ")
    text = text.replace("%", " percent ")
    text = text.replace("&", " and ")

    if _p:
        # Convert standalone numbers to words (e.g., 112 -> one hundred and twelve)
        def replace_num(match):
            num_str = match.group(0)
            try:
                # remove commas to parse cleanly
                val = num_str.replace(',', '')
                words = _p.number_to_words(val)
                return f" {words} "
            except Exception:
                return num_str
        
        # Matches numbers like 10, 1000, 1,000, etc.
        text = re.sub(r'\b\d+(?:,\d{3})*\b', replace_num, text)

    return " ".join(text.split())

class AudioGenerator:
    def __init__(self, logger: PikaLogger, work_dir: Path) -> None:
        self.log      = logger
        self.work_dir = work_dir

    # ── Narration ─────────────────────────────────────────────────────────

    def generate_narration(self, script: str) -> Path:
        """
        Generate narration audio from the script text.
        Returns path to narration.wav
        """
        self.log.step("audio_generator", "Generating narration")
        out = self.work_dir / "narration.wav"

        script = _normalize_tts_text(script)
        if not script or not script.strip():
            self.log.warn("audio_generator", "Narration script is empty — creating silent audio")
            self._silent_audio(out, duration_s=10)
            self.log.info("audio_generator", f"Narration ready → {out.name}")
            return out

        success = self._bark_tts(script, out)
        if not success:
            self.log.warn("audio_generator", "Bark failed — falling back to gTTS")
            self._gtts_fallback(script, out)

        self.log.info("audio_generator", f"Narration ready → {out.name}")
        return out

    def _bark_tts(self, text: str, out: Path) -> bool:
        """Generate audio with Bark TTS (local, offline)."""
        try:
            import torch
            try:
                import numpy as np
                safe_globals = []
                for mod in [getattr(np, "_core", None), getattr(np, "core", None)]:
                    if mod and hasattr(mod, "multiarray") and hasattr(mod.multiarray, "scalar"):
                        safe_globals.append(mod.multiarray.scalar)
                if safe_globals and hasattr(torch.serialization, "add_safe_globals"):
                    torch.serialization.add_safe_globals(safe_globals)
            except Exception:
                pass

            orig_torch_load = torch.load
            def safe_torch_load(*args, **kwargs):
                if "weights_only" not in kwargs:
                    kwargs["weights_only"] = False
                return orig_torch_load(*args, **kwargs)

            torch.load = safe_torch_load
            try:
                from bark import SAMPLE_RATE, generate_audio, preload_models  # type: ignore
                import scipy.io.wavfile as wav
                import numpy as np

                self.log.info("audio_generator", "Loading Bark models (first run may take a few minutes)…")
                preload_models()

                # Split long scripts into chunks (Bark handles ~200 words at once)
                chunks  = _split_text(text, max_words=200)
                samples = []
                for i, chunk in enumerate(chunks):
                    self.log.info("audio_generator", f"Bark chunk {i + 1}/{len(chunks)}")
                    audio = generate_audio(chunk)
                    samples.append(audio)

                combined = np.concatenate(samples)
                wav.write(str(out), SAMPLE_RATE, combined)
                return True
            finally:
                torch.load = orig_torch_load

        except Exception as exc:  # noqa: BLE001
            self.log.warn("audio_generator", f"Bark error: {exc}")
            return False

    def _gtts_fallback(self, text: str, out: Path) -> None:
        """Generate audio with gTTS (requires internet)."""
        try:
            from gtts import gTTS  # type: ignore

            mp3_path = out.with_suffix(".mp3")
            tts = gTTS(text=text, lang="en", slow=False)
            tts.save(str(mp3_path))

            # Convert mp3 → wav
            subprocess.run([
                "ffmpeg", "-y", "-i", str(mp3_path), str(out)
            ], capture_output=True, check=True)
            mp3_path.unlink(missing_ok=True)

        except Exception as exc:  # noqa: BLE001
            self.log.error("audio_generator", f"gTTS also failed: {exc} — generating silent audio")
            self._silent_audio(out, duration_s=10)

    # ── Background music ──────────────────────────────────────────────────

    def fetch_bg_music(self, query: str = "upbeat background music", duration_s: int = 120) -> Path | None:
        """
        Fetch royalty-free background music from Freesound.org.
        Requires FREESOUND_API_KEY secret (free to register).
        Falls back to None (no background music) if unavailable.
        """
        api_key = os.environ.get("FREESOUND_API_KEY", "")
        if not api_key:
            self.log.warn("audio_generator", "FREESOUND_API_KEY not set — skipping background music")
            return None

        try:
            import requests

            self.log.step("audio_generator", f"Fetching background music: {query}")
            search_resp = requests.get(
                "https://freesound.org/apiv2/search/text/",
                params={
                    "query":    query,
                    "token":    api_key,
                    "fields":   "id,name,previews,duration",
                    "filter":   "duration:[30 TO 300] license:\"Creative Commons 0\"",
                    "sort":     "rating_desc",
                    "page_size": 5,
                },
                timeout=15,
            )
            search_resp.raise_for_status()
            results = search_resp.json().get("results", [])

            if not results:
                self.log.warn("audio_generator", "No music results found")
                return None

            # Pick the first result and download its preview
            track    = results[0]
            preview_url = track["previews"]["preview-hq-mp3"]
            mp3_resp = requests.get(preview_url, timeout=30)
            mp3_resp.raise_for_status()

            mp3_path = self.work_dir / "bg_music.mp3"
            mp3_path.write_bytes(mp3_resp.content)

            # Trim / loop to match video duration
            out = self.work_dir / "bg_music_trimmed.mp3"
            subprocess.run([
                "ffmpeg", "-y",
                "-stream_loop", "-1",  # loop the track if shorter than duration
                "-i", str(mp3_path),
                "-t", str(duration_s),
                "-c", "copy",
                str(out),
            ], capture_output=True, check=True)

            self.log.info("audio_generator", f"Background music ready → {out.name}")
            return out

        except Exception as exc:  # noqa: BLE001
            self.log.warn("audio_generator", f"Music fetch failed: {exc}")
            return None

    # ── Utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _silent_audio(out: Path, duration_s: int = 10) -> None:
        """Generate a silent WAV file as last resort."""
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono",
            "-t", str(duration_s),
            str(out),
        ], capture_output=True, check=True)


def _split_text(text: str, max_words: int = 200) -> list[str]:
    """Split text into chunks of at most max_words words."""
    words  = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i : i + max_words]))
    return chunks

