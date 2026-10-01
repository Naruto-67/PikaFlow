"""
Dynamic LLM Manager
===================
Selects the best available free‑tier provider for each task type,
tracks quotas, and falls back automatically on failure.

Provider selection priority (per task type) is read from
config/llm_providers.json and config/llm_performance.json.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pika_flow.logger import PikaLogger
from pika_flow.utils.connection_manager import (
    ConnectionManager,
    PikaConnectionError,
    PikaQuotaError,
)

_CFG_DIR = Path(__file__).parent.parent / "config"


class LLMManager:
    def __init__(self, logger: PikaLogger) -> None:
        self.logger = logger
        self._conn = ConnectionManager(timeout=15)
        self._providers: list[dict] = self._load_providers()
        self._quotas: dict = self._load_quotas()
        self._perf: dict = self._load_performance()

    # ── Loaders ────────────────────────────────────────────────────────────

    def _load_providers(self) -> list[dict]:
        data = json.loads((_CFG_DIR / "llm_providers.json").read_text())
        return [p for p in data["providers"] if p.get("enabled") and not p.get("deprecated")]

    def _load_quotas(self) -> dict:
        path = _CFG_DIR / "quotas_state.json"
        if not path.exists():
            return {
                "run_id": "",
                "providers": {},
                "_meta": {"last_reset": datetime.now(timezone.utc).isoformat()}
            }
        return json.loads(path.read_text())

    def _load_performance(self) -> dict:
        path = _CFG_DIR / "llm_performance.json"
        if not path.exists():
            return {"providers": {}, "_meta": {"last_updated": datetime.now(timezone.utc).isoformat()}}
        return json.loads(path.read_text())

    # ── Provider selection ─────────────────────────────────────────────────

    def _best_providers(self, task_type: str) -> list[dict]:
        """Return providers suitable for task_type, sorted by priority."""
        perf = self._perf.get("providers", {})
        candidates = [
            p for p in self._providers
            if p["type"] == task_type
            and not self._is_disabled(p["id"])
        ]
        # Sort: performance‑based if we have data, otherwise by config priority
        def score(p: dict) -> int:
            p_perf = perf.get(p["id"], {})
            rate = p_perf.get("success_rate") or 1.0
            latency = p_perf.get("avg_latency_ms") or 9999
            return int((1 - rate) * 1000 + latency // 10 + p["priority"] * 100)
        return sorted(candidates, key=score)

    def _is_disabled(self, provider_id: str) -> bool:
        state = self._quotas.get("providers", {}).get(provider_id, {})
        until = state.get("disabled_until")
        if until is None:
            return False
        return datetime.fromisoformat(until) > datetime.now(timezone.utc)

    def _disable_provider(self, provider_id: str) -> None:
        """Disable provider for the rest of this run (until end of day)."""
        from datetime import timedelta
        until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        self._quotas.setdefault("providers", {}).setdefault(provider_id, {})
        self._quotas["providers"][provider_id]["disabled_until"] = until
        self._save_quotas()
        self.logger.warn("llm_manager", f"Provider disabled for run: {provider_id}")

    def _save_quotas(self) -> None:
        (_CFG_DIR / "quotas_state.json").write_text(
            json.dumps(self._quotas, indent=2)
        )

    # ── Core call ─────────────────────────────────────────────────────────

    async def call(
        self,
        task_type: str,
        payload: dict[str, Any],
        tried: list[str] | None = None,
    ) -> dict:
        """
        Call the best available provider for task_type.
        Falls back recursively on failure.

        Args:
            task_type: "text" | "image" | "tts"
            payload:   provider‑agnostic request body
            tried:     list of provider ids already attempted (internal)
        """
        tried = tried or []
        candidates = [p for p in self._best_providers(task_type) if p["id"] not in tried]

        if not candidates:
            raise RuntimeError(
                f"All providers exhausted for task_type='{task_type}'. "
                f"Tried: {tried}"
            )

        provider = candidates[0]
        self.logger.step("llm_manager", f"Calling {provider['name']} for {task_type}")

        try:
            result = await self._do_request(provider, payload)
            self._record_success(provider["id"])
            return result

        except PikaQuotaError:
            self._disable_provider(provider["id"])
            return await self.call(task_type, payload, tried + [provider["id"]])

        except PikaConnectionError as exc:
            self.logger.warn("llm_manager", f"Provider {provider['id']} failed: {exc}")
            return await self.call(task_type, payload, tried + [provider["id"]])

    async def _do_request(self, provider: dict, payload: dict) -> dict:
        """Build provider‑specific request and call ConnectionManager."""
        secret_key = provider.get("secret_key")
        api_key = os.environ.get(secret_key, "") if secret_key else ""

        headers = {}
        auth_header = provider.get("auth_header")
        if auth_header and api_key:
            if auth_header.lower() == "authorization":
                headers["Authorization"] = f"Bearer {api_key}"
            else:
                headers[auth_header] = api_key

        if provider["base_url"] == "local":
            # Local model — handled by the caller (e.g. bark, local diffusion)
            return {"local": True, "provider": provider["id"]}

        resp = await self._conn.request(
            base_url=provider["base_url"],
            endpoint=provider["endpoint"],
            method="POST",
            json=payload,
            headers=headers,
        )
        return resp.json()

    def _record_success(self, provider_id: str) -> None:
        """Bump success metrics — simplified; full benchmarking tracked over runs."""
        p = self._perf.setdefault("providers", {}).setdefault(provider_id, {})
        p["success_rate"] = min(1.0, (p.get("success_rate") or 0.9) + 0.01)
        self._perf["_meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
        (_CFG_DIR / "llm_performance.json").write_text(
            json.dumps(self._perf, indent=2)
        )

    async def close(self) -> None:
        await self._conn.close()

