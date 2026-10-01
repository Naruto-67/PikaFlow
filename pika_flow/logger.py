"""
Logger
======
Lightweight, structured logger for PikaFlow.

Output formats:
  1. JSON‑Lines  (log.jsonl)  – one JSON object per line, full fidelity
  2. Markdown summary (summary.md) – human‑readable run report
  3. stdout (GitHub Actions live) – coloured, spaced, scannable

Design goals:
  - Every important event is visible
  - Errors include full exception tracebacks in JSONL, truncated in stdout
  - Long bodies (LLM responses, HTTP payloads) truncated in stdout, full in JSONL
  - Stage banners create visual sections in CI logs
  - Noisy stdlib loggers (httpx, httpcore, hf_hub) suppressed to WARNING
"""

from __future__ import annotations

import json
import logging
import sys
import textwrap
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Emoji map ──────────────────────────────────────────────────────────────

LEVEL_EMOJI = {
    "INFO":  "✅",
    "WARN":  "⚠️ ",
    "ERROR": "❌",
    "DEBUG": "🔍",
    "START": "🚀",
    "DONE":  "🎉",
    "STEP":  "▶️ ",
    "STAGE": "📌",
}

# Noisy third‑party loggers to suppress below WARNING
_SUPPRESS_LOGGERS = [
    "httpx", "httpcore", "hf_hub_download", "huggingface_hub",
    "urllib3", "asyncio", "diffusers", "transformers",
    "torch", "PIL", "filelock",
]

# Max chars of long strings to show in stdout (full version goes to JSONL)
_STDOUT_MAX_LEN = 300


# ── Helpers ────────────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int = _STDOUT_MAX_LEN) -> str:
    """Truncate long strings for stdout readability."""
    if len(text) <= max_len:
        return text
    half = max_len // 2
    return f"{text[:half]} … [+{len(text) - max_len} chars] … {text[-half:]}"


def _fmt_ctx(ctx: dict, indent: int = 4) -> str:
    """Pretty-format a context dict for stdout — one k: v per line."""
    lines = []
    pad = " " * indent
    for k, v in ctx.items():
        v_str = _truncate(str(v)) if isinstance(v, str) else str(v)
        lines.append(f"{pad}{k}: {v_str}")
    return "\n".join(lines)


def _divider(char: str = "─", width: int = 70) -> str:
    return char * width


# ── Logger class ───────────────────────────────────────────────────────────

class PikaLogger:
    """Dual‑output logger: JSON‑Lines + Markdown summary + stdout."""

    def __init__(self, run_id: str, log_dir: Path) -> None:
        self.run_id = run_id
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._jsonl_path = log_dir / "log.jsonl"
        self._summary_path = log_dir / "summary.md"
        self._entries: list[dict] = []
        self._error_count = 0
        self._warn_count = 0

        # ── stdlib logging setup ──
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(message)s",   # We handle formatting ourselves
            stream=sys.stdout,
            force=True,
        )
        self._stdlib = logging.getLogger("pika_flow")
        self._stdlib.setLevel(logging.DEBUG)

        # Suppress noisy third-party loggers
        for name in _SUPPRESS_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, msg: str = "Pipeline started") -> None:
        self._print_banner(f"🚀  {self.run_id}  —  {msg}")
        self._log("START", "orchestrator", msg)

    def done(self, msg: str = "Pipeline complete") -> None:
        self._print_banner(f"🎉  {msg}", char="═")
        self._log("DONE", "orchestrator", msg)
        self._write_summary()

    def stage(self, number: int, name: str) -> None:
        """Print a prominent stage separator for CI log readability."""
        line = f"  Stage {number}: {name}  "
        bar = _divider("━", len(line) + 4)
        self._stdout_raw(f"\n{bar}")
        self._stdout_raw(f"  📌 {line}")
        self._stdout_raw(f"{bar}\n")
        self._log("STAGE", "orchestrator", f"Stage {number}: {name}")

    def step(self, component: str, msg: str, ctx: dict[str, Any] | None = None) -> None:
        self._log("STEP", component, msg, ctx)

    def info(self, component: str, msg: str, ctx: dict[str, Any] | None = None) -> None:
        self._log("INFO", component, msg, ctx)

    def warn(self, component: str, msg: str, ctx: dict[str, Any] | None = None) -> None:
        self._warn_count += 1
        self._log("WARN", component, msg, ctx)

    def error(
        self,
        component: str,
        msg: str,
        ctx: dict[str, Any] | None = None,
        exc: BaseException | None = None,
    ) -> None:
        """Log an error. Pass exc= to capture full traceback in JSONL."""
        self._error_count += 1
        tb_str = None
        if exc is not None:
            tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._log("ERROR", component, msg, ctx, traceback=tb_str)

    # ── Internal ────────────────────────────────────────────────────────────

    def _log(
        self,
        lvl: str,
        cmp: str,
        msg: str,
        ctx: dict | None = None,
        traceback: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "ts":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lvl": lvl,
            "cmp": cmp,
            "msg": msg,
        }
        if ctx:
            entry["ctx"] = ctx
        if traceback:
            entry["traceback"] = traceback

        self._entries.append(entry)

        # ── Write JSONL (full, untruncated) ──
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # ── Write stdout (formatted, truncated for readability) ──
        self._emit_stdout(lvl, cmp, msg, ctx, traceback)

    def _emit_stdout(
        self,
        lvl: str,
        cmp: str,
        msg: str,
        ctx: dict | None,
        tb: str | None,
    ) -> None:
        emoji = LEVEL_EMOJI.get(lvl, "  ")
        ts    = datetime.now(timezone.utc).strftime("%H:%M:%S")

        # Primary line
        line = f"{emoji} {ts} [{cmp}] {msg}"

        # Indent any wrapped continuation
        if len(line) > 120:
            line = textwrap.fill(line, width=120, subsequent_indent="             ")

        self._stdout_raw(line)

        # Context dict — indented below the main line
        if ctx:
            self._stdout_raw(_fmt_ctx(ctx))

        # Traceback — truncated to last 20 lines in stdout, full in JSONL
        if tb:
            tb_lines = tb.strip().splitlines()
            shown = tb_lines[-20:] if len(tb_lines) > 20 else tb_lines
            if len(tb_lines) > 20:
                self._stdout_raw(f"    [traceback truncated — {len(tb_lines) - 20} lines omitted, see log.jsonl]")
            for tl in shown:
                self._stdout_raw(f"    {tl}")

        # Extra blank line after WARN/ERROR/DONE/STAGE for breathing room
        if lvl in ("WARN", "ERROR", "DONE", "STAGE", "START"):
            self._stdout_raw("")

    def _stdout_raw(self, text: str) -> None:
        """Write directly to stdout, bypassing stdlib log formatting."""
        print(text, flush=True)

    def _print_banner(self, title: str, char: str = "─") -> None:
        width = max(len(title) + 8, 70)
        bar   = _divider(char, width)
        self._stdout_raw(f"\n{bar}")
        self._stdout_raw(f"  {title}")
        self._stdout_raw(f"{bar}\n")

    def _write_summary(self) -> None:
        lines = [
            f"# 📋 PikaFlow Run Summary — `{self.run_id}`\n",
            f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC  ",
            f"**Errors:** {self._error_count} &nbsp; **Warnings:** {self._warn_count}\n",
            "---\n",
            "| Time (UTC) | Level | Component | Message |",
            "|:-----------|:-----:|:----------|:--------|",
        ]
        for e in self._entries:
            emoji   = LEVEL_EMOJI.get(e["lvl"], "")
            msg_md  = e["msg"].replace("|", "\\|")
            row     = f"| `{e['ts']}` | {emoji} {e['lvl']} | `{e['cmp']}` | {msg_md} |"
            lines.append(row)
            # If there's context, add an indented sub-row
            if e.get("ctx"):
                ctx_str = " &nbsp;·&nbsp; ".join(f"`{k}={v}`" for k, v in e["ctx"].items())
                lines.append(f"| | | | ↳ {ctx_str} |")
            if e.get("traceback"):
                tb_short = e["traceback"].strip().splitlines()[-3:]
                lines.append(f"| | | | ↳ `{'  '.join(tb_short)}`|")

        errors = self._error_count
        warns  = self._warn_count
        status = "✅ Clean" if errors == 0 else f"❌ {errors} error(s)"
        lines += [
            "\n---\n",
            f"**Status:** {status} &nbsp;|&nbsp; **Warnings:** {warns}",
        ]
        self._summary_path.write_text("\n".join(lines), encoding="utf-8")
