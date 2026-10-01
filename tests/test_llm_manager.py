"""
Tests for LLMManager
====================
Covers: provider selection, quota fallback, connection-error fallback,
all-providers-exhausted error, and performance recording.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pika_flow.utils.connection_manager import PikaConnectionError, PikaQuotaError


# ── Fixture helpers ────────────────────────────────────────────────────────

def _make_providers_cfg(providers: list[dict]) -> dict:
    return {
        "providers": providers,
        "_meta": {"generated_at": "test", "schema_version": "1.0"},
    }


def _make_quotas_cfg() -> dict:
    return {
        "run_id": "test",
        "providers": {},
        "_meta": {"last_reset": None, "schema_version": "1.0"},
    }


def _make_performance_cfg(provider_ids: list[str]) -> dict:
    return {
        "providers": {pid: {"avg_latency_ms": None, "success_rate": None, "best_for": []} for pid in provider_ids},
        "_meta": {"last_updated": None, "schema_version": "1.0"},
    }


def _dummy_provider(pid: str, ptype: str = "text", priority: int = 1) -> dict:
    return {
        "id": pid,
        "name": pid.title(),
        "model": "test-model",
        "type": ptype,
        "base_url": "https://fake.api.com",
        "endpoint": "/v1/generate",
        "auth_header": "Authorization",
        "secret_key": None,
        "quota": {"unlimited": True},
        "priority": priority,
        "enabled": True,
        "deprecated": False,
    }


@pytest.fixture
def cfg_dir(tmp_path):
    """Create a temporary config directory with minimal JSON files."""
    providers = [
        _dummy_provider("provider_a", priority=1),
        _dummy_provider("provider_b", priority=2),
    ]
    (tmp_path / "llm_providers.json").write_text(
        json.dumps(_make_providers_cfg(providers))
    )
    (tmp_path / "quotas_state.json").write_text(
        json.dumps(_make_quotas_cfg())
    )
    (tmp_path / "llm_performance.json").write_text(
        json.dumps(_make_performance_cfg(["provider_a", "provider_b"]))
    )
    return tmp_path


def _make_llm_manager(cfg_dir: Path):
    """Import LLMManager with patched config path."""
    import pika_flow.llm_manager as mod
    log = MagicMock()

    with patch.object(mod, "_CFG_DIR", cfg_dir):
        mgr = mod.LLMManager(logger=log)
    return mgr, log


# ── Tests ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calls_first_provider_on_success(cfg_dir):
    """Should call the highest-priority provider and return its response."""
    import pika_flow.llm_manager as mod

    mgr, _ = _make_llm_manager(cfg_dir)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"answer": "42"}

    with patch.object(mgr._conn, "request", new=AsyncMock(return_value=mock_resp)):
        result = await mgr.call("text", {"contents": []})

    assert result == {"answer": "42"}


@pytest.mark.asyncio
async def test_falls_back_on_quota_error(cfg_dir):
    """429/quota on provider_a → should transparently fall back to provider_b."""
    import pika_flow.llm_manager as mod

    mgr, _ = _make_llm_manager(cfg_dir)

    call_count = {"n": 0}
    mock_success = MagicMock()
    mock_success.json.return_value = {"answer": "fallback"}

    async def fake_request(base_url, endpoint, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise PikaQuotaError("quota_exhausted")
        return mock_success

    with patch.object(mgr._conn, "request", new=fake_request):
        result = await mgr.call("text", {"contents": []})

    assert result == {"answer": "fallback"}
    assert call_count["n"] == 2  # tried provider_a, then provider_b


@pytest.mark.asyncio
async def test_falls_back_on_connection_error(cfg_dir):
    """Network error on provider_a → should fall back to provider_b."""
    import pika_flow.llm_manager as mod

    mgr, _ = _make_llm_manager(cfg_dir)
    call_count = {"n": 0}
    mock_success = MagicMock()
    mock_success.json.return_value = {"ok": True}

    async def fake_request(base_url, endpoint, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise PikaConnectionError("network error")
        return mock_success

    with patch.object(mgr._conn, "request", new=fake_request):
        result = await mgr.call("text", {})

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_raises_when_all_providers_exhausted(cfg_dir):
    """If every provider fails, RuntimeError must be raised."""
    import pika_flow.llm_manager as mod

    mgr, _ = _make_llm_manager(cfg_dir)

    async def always_fail(*args, **kwargs):
        raise PikaConnectionError("always fails")

    with patch.object(mgr._conn, "request", new=always_fail):
        with pytest.raises(RuntimeError, match="All providers exhausted"):
            await mgr.call("text", {})


@pytest.mark.asyncio
async def test_local_provider_returns_local_dict(cfg_dir):
    """A provider with base_url='local' should return immediately without HTTP call."""
    import pika_flow.llm_manager as mod

    # Add a local provider
    data = json.loads((cfg_dir / "llm_providers.json").read_text())
    data["providers"].insert(0, {
        **_dummy_provider("bark_tts", ptype="tts", priority=0),
        "base_url": "local",
        "endpoint": "local",
        "auth_header": None,
        "secret_key": None,
    })
    (cfg_dir / "llm_providers.json").write_text(json.dumps(data))
    perf = json.loads((cfg_dir / "llm_performance.json").read_text())
    perf["providers"]["bark_tts"] = {"avg_latency_ms": None, "success_rate": None, "best_for": []}
    (cfg_dir / "llm_performance.json").write_text(json.dumps(perf))

    mgr, _ = _make_llm_manager(cfg_dir)

    result = await mgr.call("tts", {})
    assert result.get("local") is True
    assert result.get("provider") == "bark_tts"
