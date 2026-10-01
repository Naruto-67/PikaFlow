"""
SEO Generator
=============
Two-phase SEO module for PikaFlow:

Phase 1 – Research (before script generation)
  • Fetches trending keywords via Google Trends (pytrends)
  • Scrapes YouTube autocomplete suggestions
  • Optionally queries Reddit hot posts (praw)
  • Returns a keyword brief fed into the LLM script prompt

Phase 2 – Metadata generation (after video is ready)
  • Uses the LLM to generate: title, description, tags, hashtags,
    chapter markers, and a thumbnail diffusion prompt
  • All outputs are written to seo_metadata.json in the work dir
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from pika_flow.logger import PikaLogger
from pika_flow.llm_manager import LLMManager


# ── Helpers ────────────────────────────────────────────────────────────────

def _yt_suggestions(query: str) -> list[str]:
    """Fetch YouTube autocomplete suggestions — no API key required."""
    try:
        url = "https://suggestqueries.google.com/complete/search"
        resp = requests.get(
            url,
            params={"client": "youtube", "ds": "yt", "q": query},
            timeout=10,
        )
        # Response is JSONP: window.google.ac.h([...])
        raw = resp.text
        match = re.search(r'\[.*\]', raw)
        if not match:
            return []
        data = json.loads(match.group(0))
        # data[1] is list of [suggestion, score] pairs
        return [item[0] for item in data[1]] if len(data) > 1 else []
    except Exception:  # noqa: BLE001
        return []


def _google_trends(keywords: list[str], timeframe: str = "now 7-d") -> dict[str, int]:
    """
    Fetch Google Trends interest scores.
    Returns {keyword: score} — requires pytrends.
    Falls back to empty dict if pytrends is not installed.
    """
    try:
        from pytrends.request import TrendReq  # type: ignore
        pt = TrendReq(hl="en-US", tz=330)
        pt.build_payload(keywords[:5], timeframe=timeframe)
        df = pt.interest_over_time()
        if df.empty:
            return {}
        # Average interest per keyword over the period
        return {col: int(df[col].mean()) for col in df.columns if col != "isPartial"}
    except Exception:  # noqa: BLE001
        return {}


def _reddit_hot(subreddit: str, limit: int = 10) -> list[str]:
    """
    Fetch hot post titles from a subreddit — no API key needed for read-only.
    """
    try:
        headers = {"User-Agent": "PikaFlow/1.0"}
        resp = requests.get(
            f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}",
            headers=headers, timeout=10,
        )
        data = resp.json()
        return [post["data"]["title"] for post in data["data"]["children"]]
    except Exception:  # noqa: BLE001
        return []


# ── Main class ─────────────────────────────────────────────────────────────

class SEOGenerator:
    def __init__(self, logger: PikaLogger, llm: LLMManager, work_dir: Path) -> None:
        self.log      = logger
        self.llm      = llm
        self.work_dir = work_dir

    # ── Phase 1: Research ─────────────────────────────────────────────────

    async def research(self, niche: str, base_prompt: str) -> dict[str, Any]:
        """
        Research trending keywords for the niche.
        Returns a brief dict to be injected into the script prompt.
        """
        self.log.step("seo_generator", f"Researching keywords for niche: {niche}")

        suggestions = _yt_suggestions(base_prompt)
        time.sleep(1)  # be polite to the autocomplete endpoint
        trends      = _google_trends([niche] + suggestions[:4])
        reddit      = _reddit_hot(_niche_to_subreddit(niche))

        self.log.info("seo_generator", "Research done", {
            "yt_suggestions": len(suggestions),
            "trends_fetched": len(trends),
            "reddit_posts":   len(reddit),
        })

        brief = {
            "niche":       niche,
            "base_prompt": base_prompt,
            "keywords":    suggestions[:10],
            "trend_scores": trends,
            "reddit_titles": reddit[:5],
        }

        # Save brief for reproducibility
        (self.work_dir / "seo_brief.json").write_text(
            json.dumps(brief, indent=2, ensure_ascii=False)
        )
        return brief

    # ── Phase 2: Metadata generation ──────────────────────────────────────

    async def generate_metadata(
        self,
        script: str,
        scenes: list[dict],
        brief: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Generate SEO-optimised title, description, tags, hashtags,
        chapter markers, and a thumbnail diffusion prompt.
        """
        self.log.step("seo_generator", "Generating SEO metadata")

        keywords_str = ", ".join(brief.get("keywords", []))
        prompt = f"""
You are an expert YouTube SEO specialist.
Given the video script and keyword research below, generate optimised metadata.

=== SCRIPT (summary) ===
{script[:2000]}

=== TRENDING KEYWORDS ===
{keywords_str}

=== NICHE ===
{brief.get("niche", "")}

Return ONLY valid JSON in this exact structure:
{{
  "title": "...",                  // ≤70 chars, keyword-rich, click-bait optimised
  "description": "...",            // 2-3 paragraphs, keywords woven in naturally, ends with CTA
  "tags": ["tag1", "tag2", ...],   // 15-20 relevant tags
  "hashtags": ["#tag1", ...],      // 3-5 trending hashtags
  "thumbnail_prompt": "...",       // Stable Diffusion prompt for high-CTR thumbnail
  "chapters": [                    // timestamp list based on scenes
    {{"time": "0:00", "label": "Intro"}},
    ...
  ]
}}
"""
        resp = await self.llm.call(
            task_type="text",
            payload={"contents": [{"parts": [{"text": prompt}]}]},
        )

        # Parse the LLM response
        raw_text = self._extract_text(resp)
        metadata = self._parse_json_safe(raw_text)

        # Inject auto-generated chapter markers from scene list if LLM missed them
        if not metadata.get("chapters") and scenes:
            metadata["chapters"] = _build_chapters(scenes)

        # Save metadata
        out_path = self.work_dir / "seo_metadata.json"
        out_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
        self.log.info("seo_generator", f"SEO metadata saved → {out_path.name}")
        return metadata

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(resp: dict) -> str:
        """Extract plain text from LLM response (handles Gemini format)."""
        try:
            return resp["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return str(resp)

    @staticmethod
    def _parse_json_safe(text: str) -> dict:
        """Extract and parse JSON block from LLM text output."""
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {
            "title": "Untitled Video",
            "description": "",
            "tags": [],
            "hashtags": [],
            "thumbnail_prompt": "",
            "chapters": [],
        }


# ── Utilities ──────────────────────────────────────────────────────────────

def _niche_to_subreddit(niche: str) -> str:
    """Map common niches to relevant subreddits."""
    mapping = {
        "anime":        "anime",
        "ai":           "artificial",
        "tech":         "technology",
        "gaming":       "gaming",
        "facts":        "todayilearned",
        "art":          "Art",
        "finance":      "personalfinance",
        "motivation":   "GetMotivated",
    }
    niche_lower = niche.lower()
    for key, sub in mapping.items():
        if key in niche_lower:
            return sub
    return "videos"  # generic fallback


def _build_chapters(scenes: list[dict]) -> list[dict]:
    """Build chapter markers from scene list (each scene assumed ~60 s)."""
    chapters = []
    t = 0
    for i, scene in enumerate(scenes):
        minutes, seconds = divmod(t, 60)
        chapters.append({
            "time": f"{minutes}:{seconds:02d}",
            "label": scene.get("title", f"Scene {i + 1}"),
        })
        t += scene.get("duration_s", 60)
    return chapters
