"""
Tests for ConnectionManager
===========================
Covers: success, 429 quota error, 5xx retry, all-retries-exhausted,
timeout, and connection cleanup.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pika_flow.utils.connection_manager import (
    ConnectionManager,
    PikaConnectionError,
    PikaQuotaError,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ── Tests ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_request():
    """200 response is returned immediately with no retries."""
    mgr = ConnectionManager(timeout=5)
    mock_resp = _mock_response(200, {"result": "ok"})

    with patch.object(httpx.AsyncClient, "request", new=AsyncMock(return_value=mock_resp)):
        resp = await mgr.request("https://api.example.com", "/test")
        assert resp.status_code == 200

    await mgr.close()


@pytest.mark.asyncio
async def test_429_raises_quota_error_immediately():
    """HTTP 429 must raise PikaQuotaError without any retry."""
    mgr = ConnectionManager(timeout=5)
    mock_resp = _mock_response(429)

    call_count = 0

    async def fake_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return mock_resp

    with patch.object(httpx.AsyncClient, "request", new=fake_request):
        with pytest.raises(PikaQuotaError):
            await mgr.request("https://api.example.com", "/test", retries=2)

    # Must NOT retry on 429 — called exactly once
    assert call_count == 1
    await mgr.close()


@pytest.mark.asyncio
async def test_5xx_retries_then_succeeds():
    """5xx on first attempt, 200 on second — should succeed after 1 retry."""
    mgr = ConnectionManager(timeout=5)
    responses = [_mock_response(503), _mock_response(200, {"ok": True})]
    call_index = 0

    async def fake_request(*args, **kwargs):
        nonlocal call_index
        resp = responses[call_index]
        call_index += 1
        return resp

    with patch("asyncio.sleep", new=AsyncMock()):  # skip wait time in tests
        with patch.object(httpx.AsyncClient, "request", new=fake_request):
            resp = await mgr.request("https://api.example.com", "/test", retries=2)
            assert resp.status_code == 200

    assert call_index == 2  # tried twice
    await mgr.close()


@pytest.mark.asyncio
async def test_all_retries_exhausted_raises_connection_error():
    """If all retries fail with 5xx, PikaConnectionError must be raised."""
    mgr = ConnectionManager(timeout=5)
    mock_resp = _mock_response(503)

    async def fake_request(*args, **kwargs):
        return mock_resp

    with patch("asyncio.sleep", new=AsyncMock()):
        with patch.object(httpx.AsyncClient, "request", new=fake_request):
            with pytest.raises(PikaConnectionError):
                await mgr.request("https://api.example.com", "/test", retries=2)

    await mgr.close()


@pytest.mark.asyncio
async def test_timeout_raises_connection_error():
    """Network timeout must be caught and raise PikaConnectionError."""
    mgr = ConnectionManager(timeout=5)

    async def fake_request(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    with patch("asyncio.sleep", new=AsyncMock()):
        with patch.object(httpx.AsyncClient, "request", new=fake_request):
            with pytest.raises(PikaConnectionError):
                await mgr.request("https://api.example.com", "/test", retries=1)

    await mgr.close()


@pytest.mark.asyncio
async def test_connection_pooling_reuses_client():
    """The same base_url should reuse the same httpx.AsyncClient instance."""
    mgr = ConnectionManager(timeout=5)
    mock_resp = _mock_response(200)

    with patch.object(httpx.AsyncClient, "request", new=AsyncMock(return_value=mock_resp)):
        await mgr.request("https://api.example.com", "/a")
        await mgr.request("https://api.example.com", "/b")

    # Only one client should have been created for the same base_url
    assert len(mgr._clients) == 1
    await mgr.close()


@pytest.mark.asyncio
async def test_close_clears_all_clients():
    """close() must remove all clients from the pool."""
    mgr = ConnectionManager(timeout=5)
    mock_resp = _mock_response(200)

    with patch.object(httpx.AsyncClient, "request", new=AsyncMock(return_value=mock_resp)):
        await mgr.request("https://api.example.com", "/a")
        await mgr.request("https://api2.example.com", "/b")

    assert len(mgr._clients) == 2
    await mgr.close()
    assert len(mgr._clients) == 0

