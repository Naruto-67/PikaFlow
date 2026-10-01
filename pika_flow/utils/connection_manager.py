"""
ConnectionManager
=================
Centralised async HTTP client with:
  - Per‑provider connection pooling (httpx.AsyncClient)
  - Configurable timeout (default 15 s)
  - Quick‑retry: max 2 retries with 1 s → 2 s back‑off
  - Circuit‑breaker: raises PikaQuotaError on HTTP 429
  - All errors logged before re‑raising
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger("pika_flow.connection_manager")


class PikaConnectionError(RuntimeError):
    """Raised when all retries are exhausted for a non‑quota error."""


class PikaQuotaError(RuntimeError):
    """Raised immediately on HTTP 429 (quota exhausted) — no retry."""


class ConnectionManager:
    """Shared async HTTP client pool, one client per base_url."""

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _get_client(self, base_url: str) -> httpx.AsyncClient:
        if base_url not in self._clients:
            self._clients[base_url] = httpx.AsyncClient(
                base_url=base_url,
                timeout=self.timeout,
            )
        return self._clients[base_url]

    async def request(
        self,
        base_url: str,
        endpoint: str,
        method: str = "GET",
        json: Any = None,
        headers: dict[str, str] | None = None,
        retries: int = 2,
    ) -> httpx.Response:
        """
        Make an HTTP request with quick‑retry on transient errors.

        Raises:
            PikaQuotaError  – on HTTP 429 (no retry).
            PikaConnectionError – after all retries are exhausted.
        """
        client = self._get_client(base_url)
        backoff = [1, 2]  # seconds between retries

        for attempt in range(retries + 1):
            try:
                resp = await client.request(
                    method, endpoint, json=json, headers=headers
                )
                if resp.status_code == 429:
                    logger.warning(
                        "[ConnectionManager] 429 quota exhausted | %s%s",
                        base_url, endpoint,
                    )
                    raise PikaQuotaError(f"Quota exhausted: {base_url}{endpoint}")

                resp.raise_for_status()
                return resp

            except PikaQuotaError:
                raise  # never retry quota errors

            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt == retries:
                    logger.error(
                        "[ConnectionManager] All %d retries failed | %s%s | %s",
                        retries, base_url, endpoint, exc,
                    )
                    raise PikaConnectionError(str(exc)) from exc

                wait = backoff[attempt]
                logger.warning(
                    "[ConnectionManager] Attempt %d/%d failed, retrying in %ds | %s",
                    attempt + 1, retries + 1, wait, exc,
                )
                await asyncio.sleep(wait)

        raise PikaConnectionError("Unreachable — retry loop exited unexpectedly")

    async def close(self) -> None:
        """Release all open connections. Call at end of each job."""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        logger.debug("[ConnectionManager] All connections closed.")
