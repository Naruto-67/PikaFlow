"""
Tests for SEOGenerator utilities
=================================
Covers: JSON parsing, chapter building, niche→subreddit mapping,
text splitting, and fallback metadata on bad LLM output.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pika_flow.seo_generator import (
    SEOGenerator,
    _build_chapters,
    _niche_to_subreddit,
    _yt_suggestions,
)
from pika_flow.orchestrator import _parse_scenes_from_script, _time_to_sec


# ── _build_chapters ────────────────────────────────────────────────────────

def test_build_chapters_timing():
    scenes = [
        {"title": "Intro", "duration_s": 10},
        {"title": "Main Part", "duration_s": 60},
        {"title": "Outro", "duration_s": 15},
    ]
    chapters = _build_chapters(scenes)
    assert chapters[0]["time"] == "0:00"
    assert chapters[0]["label"] == "Intro"
    assert chapters[1]["time"] == "0:10"
    assert chapters[2]["time"] == "1:10"


def test_build_chapters_fallback_title():
    """Scenes without a 'title' field should get a default label."""
    scenes = [{"duration_s": 5}, {"duration_s": 5}]
    chapters = _build_chapters(scenes)
    assert "Scene 1" in chapters[0]["label"]
    assert "Scene 2" in chapters[1]["label"]


# ── _niche_to_subreddit ────────────────────────────────────────────────────

def test_niche_to_subreddit_known():
    assert _niche_to_subreddit("anime art tutorials") == "anime"
    assert _niche_to_subreddit("AI generated images") == "artificial"
    assert _niche_to_subreddit("gaming highlights") == "gaming"


def test_niche_to_subreddit_unknown_returns_videos():
    assert _niche_to_subreddit("obscure topic xyz") == "videos"


# ── SEOGenerator._parse_json_safe ─────────────────────────────────────────

def test_parse_json_safe_valid():
    text = 'Some preamble {"title": "Test", "tags": ["a"]} trailing text'
    result = SEOGenerator._parse_json_safe(text)
    assert result["title"] == "Test"
    assert result["tags"] == ["a"]


def test_parse_json_safe_invalid_returns_defaults():
    result = SEOGenerator._parse_json_safe("not json at all !!!")
    assert result["title"] == "Untitled Video"
    assert isinstance(result["tags"], list)


# ── _parse_scenes_from_script (orchestrator utility) ──────────────────────

def test_parse_scenes_valid_json():
    script = json.dumps([
        {"prompt": "a sunset", "description": "A beautiful sunset", "duration_s": 4},
        {"prompt": "a city", "description": "A busy city", "duration_s": 5},
    ])
    scenes = _parse_scenes_from_script(script)
    assert len(scenes) == 2
    assert scenes[0]["prompt"] == "a sunset"


def test_parse_scenes_fallback_paragraphs():
    """Non-JSON script should fall back to paragraph splitting."""
    script = "First scene text.\n\nSecond scene text.\n\nThird scene."
    scenes = _parse_scenes_from_script(script)
    assert len(scenes) == 3
    assert "First scene" in scenes[0]["prompt"]


def test_parse_scenes_caps_at_10():
    """Fallback should never produce more than 10 scenes."""
    paragraphs = "\n\n".join([f"Scene {i}" for i in range(20)])
    scenes = _parse_scenes_from_script(paragraphs)
    assert len(scenes) <= 10


# ── _time_to_sec (orchestrator utility) ───────────────────────────────────

def test_time_to_sec_minutes():
    assert _time_to_sec("1:30") == 90.0


def test_time_to_sec_hours():
    assert _time_to_sec("1:01:00") == 3660.0


def test_time_to_sec_zero():
    assert _time_to_sec("0:00") == 0.0


def test_time_to_sec_invalid_returns_zero():
    assert _time_to_sec("bad") == 0.0


# ── SEOGenerator.generate_metadata (mocked LLM) ───────────────────────────

@pytest.mark.asyncio
async def test_generate_metadata_writes_file(tmp_path):
    """generate_metadata must save seo_metadata.json to work_dir."""
    log = MagicMock()
    llm = MagicMock()
    llm.call = AsyncMock(return_value={
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "title": "Test Title",
                        "description": "A description.",
                        "tags": ["ai", "art"],
                        "hashtags": ["#AI"],
                        "thumbnail_prompt": "anime sunset",
                        "chapters": [],
                    })
                }]
            }
        }]
    })

    gen = SEOGenerator(logger=log, llm=llm, work_dir=tmp_path)
    metadata = await gen.generate_metadata(
        script="test script",
        scenes=[{"title": "Intro", "duration_s": 5}],
        brief={"niche": "anime", "keywords": ["anime art"], "base_prompt": "test"},
    )

    assert metadata["title"] == "Test Title"
    assert (tmp_path / "seo_metadata.json").exists()
    saved = json.loads((tmp_path / "seo_metadata.json").read_text())
    assert saved["title"] == "Test Title"
