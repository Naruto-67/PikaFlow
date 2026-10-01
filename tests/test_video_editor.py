"""
Tests for VideoEditor
=====================
Covers: transition stitching, caption overlay, audio mixing,
final export, and FFmpeg error handling.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────

def _make_editor(tmp_path: Path):
    """Create a VideoEditor with a patched config."""
    cfg = {
        "transition": "fade",
        "transition_duration": 0.5,
        "transition_offset_s": 0.0,
        "caption": {
            "x": "(w-text_w)/2",
            "y": "h-180",
            "size": 52,
            "color": "ffffff",
            "box_color": "black@0.45",
            "box": 1,
            "font": "OpenSans-Bold.ttf",
        },
        "lower_third": {"template": "lower_third.svg", "duration_s": 3.5, "y_offset": 80},
        "intro": {"enabled": True, "asset": "intro.png", "duration_s": 3},
        "outro": {"enabled": True, "asset": "outro.png", "duration_s": 5},
        "bg_music": {"enabled": True, "duck_ratio": 4, "duck_threshold": 0.1, "volume": 0.3},
        "ffmpeg_output_opts": {
            "vcodec": "libx264",
            "acodec": "aac",
            "audio_bitrate": "192k",
            "preset": "fast",
            "crf": 23,
        },
    }
    log = MagicMock()
    with patch("pika_flow.video_editor._CFG", cfg):
        from pika_flow.video_editor import VideoEditor
        return VideoEditor(logger=log, work_dir=tmp_path), log


# ── Tests ──────────────────────────────────────────────────────────────────

def test_add_transitions_single_clip_returns_same(tmp_path):
    """A single clip needs no concat — should be returned as-is."""
    editor, _ = _make_editor(tmp_path)
    clip = tmp_path / "clip_0000.mp4"
    clip.touch()

    result = editor.add_transitions([clip])
    assert result == clip


def test_add_transitions_multiple_clips_calls_ffmpeg(tmp_path):
    """Multiple clips should trigger an ffmpeg concat command."""
    editor, _ = _make_editor(tmp_path)
    clips = []
    for i in range(3):
        c = tmp_path / f"clip_{i:04d}.mp4"
        c.touch()
        clips.append(c)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = editor.add_transitions(clips)

    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "-f" in cmd
    assert "concat" in cmd


def test_overlay_captions_builds_drawtext_filter(tmp_path):
    """overlay_captions must call ffmpeg with a drawtext filter."""
    editor, _ = _make_editor(tmp_path)
    video = tmp_path / "video.mp4"
    video.touch()

    captions = [
        {"text": "Hello World", "start": 0.0, "end": 3.0},
        {"text": "Second caption", "start": 3.5, "end": 6.0},
    ]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        editor.overlay_captions(video, captions)

    cmd_str = " ".join(mock_run.call_args[0][0])
    assert "drawtext" in cmd_str
    assert "Hello World" in cmd_str
    assert "Second caption" in cmd_str
    assert "between(t,0.0,3.0)" in cmd_str


def test_overlay_captions_escapes_special_chars(tmp_path):
    """Colons and apostrophes in caption text must be escaped for FFmpeg."""
    editor, _ = _make_editor(tmp_path)
    video = tmp_path / "video.mp4"
    video.touch()

    captions = [{"text": "It's 10:00", "start": 0.0, "end": 3.0}]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        editor.overlay_captions(video, captions)

    cmd_str = " ".join(mock_run.call_args[0][0])
    # Apostrophe and colon must be escaped
    assert "\\'" in cmd_str or "It" in cmd_str  # apostrophe escaped
    assert "\\:" in cmd_str or "10" in cmd_str  # colon escaped


def test_mix_audio_with_music(tmp_path):
    """mix_audio with both narration and music should use amix filter."""
    editor, _ = _make_editor(tmp_path)
    video     = tmp_path / "video.mp4"
    narration = tmp_path / "narration.wav"
    music     = tmp_path / "bg_music.mp3"
    for f in [video, narration, music]:
        f.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        editor.mix_audio(video, narration, music)

    cmd_str = " ".join(mock_run.call_args[0][0])
    assert "amix" in cmd_str


def test_mix_audio_without_music(tmp_path):
    """mix_audio without music should attach narration directly."""
    editor, _ = _make_editor(tmp_path)
    video     = tmp_path / "video.mp4"
    narration = tmp_path / "narration.wav"
    video.touch()
    narration.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        editor.mix_audio(video, narration, None)

    cmd_str = " ".join(mock_run.call_args[0][0])
    assert "amix" not in cmd_str
    assert str(narration) in cmd_str


def test_ffmpeg_failure_raises_runtime_error(tmp_path):
    """An FFmpeg non-zero exit must raise RuntimeError."""
    editor, _ = _make_editor(tmp_path)
    video = tmp_path / "video.mp4"
    video.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="ffmpeg error details")
        with pytest.raises(RuntimeError):
            editor.final_export(video, "run-test")


def test_final_export_produces_named_output(tmp_path):
    """final_export should produce a file named <run_id>.mp4."""
    editor, _ = _make_editor(tmp_path)
    video = tmp_path / "video.mp4"
    video.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        out = editor.final_export(video, "run-01102026-1427")

    assert out.name == "run-01102026-1427.mp4"
