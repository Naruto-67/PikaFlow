"""
Logger
======
Lightweight, OpenMontage‑inspired logger for PikaFlow.

Output formats:
  1. JSON‑Lines  (log.jsonl)  – one JSON object per line
  2. Markdown summary (summary.md) – human‑readable coloured log

Each log line:
  {"ts": "ISO‑8601", "lvl": "INFO|WARN|ERROR", "cmp": "component", "msg": "...", "ctx": {...}}
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEVEL_EMOJI = {
    "INFO":  "✅",
    "WARN":  "⚠️ ",
    "ERROR": "❌",
    "DEBUG": "🔍",
    "START": "🚀",
    "DONE":  "🎉",
    "STEP":  "▶️ ",
}


class PikaLogger:
    """Dual‑output logger: JSON‑Lines + Markdown summary."""

    def __init__(self, run_id: str, log_dir: Path) -> None:
        self.run_id = run_id
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._jsonl_path = log_dir / "log.jsonl"
        self._summary_path = log_dir / "summary.md"
        self._entries: list[dict] = []

        # Also pipe to Python stdlib logging so GitHub Actions shows it live
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
            stream=sys.stdout,
        )
        self._stdlib = logging.getLogger("pika_flow")

    # ── Public methods ──────────────────────────────────────────────────────

    def start(self, msg: str = "Pipeline started") -> None:
        self._log("START", "orchestrator", msg)

    def done(self, msg: str = "Pipeline complete") -> None:
        self._log("DONE", "orchestrator", msg)
        self._write_summary()

    def step(self, component: str, msg: str, ctx: dict[str, Any] | None = None) -> None:
        self._log("STEP", component, msg, ctx)

    def info(self, component: str, msg: str, ctx: dict[str, Any] | None = None) -> None:
        self._log("INFO", component, msg, ctx)

    def warn(self, component: str, msg: str, ctx: dict[str, Any] | None = None) -> None:
        self._log("WARN", component, msg, ctx)

    def error(self, component: str, msg: str, ctx: dict[str, Any] | None = None) -> None:
        self._log("ERROR", component, msg, ctx)

    # ── Internal ────────────────────────────────────────────────────────────

    def _log(self, lvl: str, cmp: str, msg: str, ctx: dict | None = None) -> None:
        entry = {
            "ts":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lvl": lvl,
            "cmp": cmp,
            "msg": msg,
        }
        if ctx:
            entry["ctx"] = ctx

        self._entries.append(entry)

        # Write JSON‑Lines immediately (append mode)
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        # Mirror to stdlib logger
        emoji = LEVEL_EMOJI.get(lvl, "")
        line  = f"{emoji} [{cmp}] {msg}"
        if lvl in ("ERROR",):
            self._stdlib.error(line)
        elif lvl in ("WARN",):
            self._stdlib.warning(line)
        else:
            self._stdlib.info(line)

    def _write_summary(self) -> None:
        lines = [
            f"# 📋 PikaFlow Run Summary — `{self.run_id}`\n",
            f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC\n",
            "---\n",
            "| Time (UTC) | Level | Component | Message |",
            "|------------|-------|-----------|---------|",
        ]
        for e in self._entries:
            emoji = LEVEL_EMOJI.get(e["lvl"], "")
            lines.append(
                f"| `{e['ts']}` | {emoji} {e['lvl']} | `{e['cmp']}` | {e['msg']} |"
            )

        # Error count badge
        errors = sum(1 for e in self._entries if e["lvl"] == "ERROR")
        warns  = sum(1 for e in self._entries if e["lvl"] == "WARN")
        lines += [
            "\n---\n",
            f"**Errors:** {errors} &nbsp; **Warnings:** {warns}",
        ]
        self._summary_path.write_text("\n".join(lines), encoding="utf-8")

