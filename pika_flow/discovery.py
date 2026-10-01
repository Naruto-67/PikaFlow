"""
Discovery
=========
Nightly health-check and free-tier provider discovery.

For each provider in config/llm_providers.json:
  1. Send a minimal test request
  2. Record: latency, success/failure, HTTP status
  3. Mark deprecated models (check provider's model list endpoint if available)
  4. Update config/llm_providers.json and config/llm_performance.json

Run by the discovery.yml GitHub Action every night at 00:00 UTC.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

_CFG_DIR = Path(__file__).parent.parent / "config"

_TEST_PAYLOAD = {
    "gemini": {
        "contents": [{"parts": [{"text": "Reply with: OK"}]}]
    },
    "groq": {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": "Reply with: OK"}],
        "max_tokens": 5,
    },
    "openrouter_mistral": {
        "model": "mistralai/mistral-7b-instruct:free",
        "messages": [{"role": "user", "content": "Reply with: OK"}],
        "max_tokens": 5,
    },
    "huggingface_flan": {
        "inputs": "Reply with: OK"
    },
}


def _load_providers() -> list[dict]:
    return json.loads((_CFG_DIR / "llm_providers.json").read_text())["providers"]


def _load_performance() -> dict:
    return json.loads((_CFG_DIR / "llm_performance.json").read_text())


def _save_providers(providers: list[dict]) -> None:
    data = json.loads((_CFG_DIR / "llm_providers.json").read_text())
    data["providers"] = providers
    data["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    (_CFG_DIR / "llm_providers.json").write_text(json.dumps(data, indent=2))


def _save_performance(perf: dict) -> None:
    perf["_meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    (_CFG_DIR / "llm_performance.json").write_text(json.dumps(perf, indent=2))


def _get_api_key(secret_key: str | None) -> str:
    if not secret_key:
        return ""
    return os.environ.get(secret_key, "")


def _refresh_model_ids(providers: list[dict]) -> None:
    """Query each provider's /models endpoint to find the latest active/free model."""
    for p in providers:
        if p.get("base_url") == "local":
            continue
            
        api_key = _get_api_key(p.get("secret_key"))
        if not api_key and p["id"] != "openrouter_mistral":
            continue
            
        if p["id"] == "gemini":
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    models = [
                        m["name"] for m in resp.json().get("models", [])
                        if "flash" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", [])
                        and "exp" not in m["name"] and "vision" not in m["name"]
                    ]
                    if models:
                        models.sort(reverse=True) # Usually gemini-1.5-flash > gemini-1.0-flash
                        best_model = models[0].replace("models/", "")
                        p["model"] = best_model
                        p["endpoint"] = f"v1beta/models/{best_model}:generateContent"
                        print(f"  🔄 Discovered Gemini model: {best_model}")
            except Exception as e:
                print(f"  ⚠️ Failed to discover Gemini models: {e}")
                
        elif p["id"] == "groq":
            try:
                url = "https://api.groq.com/openai/v1/models"
                resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
                if resp.status_code == 200:
                    models = [m["id"] for m in resp.json().get("data", []) if m.get("active", True)]
                    llama = [m for m in models if "llama-3" in m.lower() or "llama3" in m.lower()]
                    best_model = llama[0] if llama else models[0]
                    p["model"] = best_model
                    print(f"  🔄 Discovered Groq model: {best_model}")
            except Exception as e:
                print(f"  ⚠️ Failed to discover Groq models: {e}")
                
        elif p["id"] == "openrouter_mistral":
            try:
                url = "https://openrouter.ai/api/v1/models"
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    free_models = [
                        m["id"] for m in resp.json().get("data", [])
                        if m.get("pricing", {}).get("prompt") == "0" and m.get("pricing", {}).get("completion") == "0"
                    ]
                    if free_models:
                        preferred = [m for m in free_models if "mistral" in m.lower() or "llama" in m.lower()]
                        best_model = preferred[0] if preferred else free_models[0]
                        p["model"] = best_model
                        print(f"  🔄 Discovered OpenRouter model: {best_model}")
            except Exception as e:
                print(f"  ⚠️ Failed to discover OpenRouter models: {e}")


def _health_check(provider: dict) -> dict:
    """
    Send a minimal request to the provider and return health metrics.
    Returns: {"ok": bool, "latency_ms": int, "status_code": int, "error": str|None}
    """
    if provider.get("base_url") == "local":
        # Local models (Bark, etc.) are always considered healthy
        return {"ok": True, "latency_ms": 0, "status_code": 200, "error": None}

    api_key     = _get_api_key(provider.get("secret_key"))
    auth_header = provider.get("auth_header", "")
    headers     = {}

    if auth_header and api_key:
        if auth_header.lower() == "authorization":
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers[auth_header] = api_key

    payload = _TEST_PAYLOAD.get(provider["id"], {"inputs": "OK"}).copy()
    if "model" in payload:
        payload["model"] = provider["model"]

    url = provider["base_url"] + provider["endpoint"]
    if "generativelanguage" in provider["base_url"] and api_key:
        sep = "&" if "?" in url else "?"
        url += f"{sep}key={api_key}"
        
    if "openrouter" in provider["base_url"]:
        headers["HTTP-Referer"] = "https://github.com/Naruto-67/PikaFlow"
        headers["X-Title"] = "PikaFlow"

    try:
        start = time.time()
        resp  = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20,
        )
        latency = int((time.time() - start) * 1000)

        if resp.status_code == 200:
            return {"ok": True,  "latency_ms": latency, "status_code": 200, "error": None}
        elif resp.status_code == 404:
            # 404 usually means the model is deprecated / moved
            return {"ok": False, "latency_ms": latency, "status_code": 404, "error": "Model not found — possibly deprecated"}
        elif resp.status_code == 429:
            # 429 = quota, but model is alive
            return {"ok": True,  "latency_ms": latency, "status_code": 429, "error": "Quota limit (model is live)"}
        else:
            return {"ok": False, "latency_ms": latency, "status_code": resp.status_code, "error": resp.text[:200]}

    except requests.Timeout:
        return {"ok": False, "latency_ms": 20000, "status_code": 0, "error": "Timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": 0, "status_code": 0, "error": str(exc)}


def run() -> None:
    print("🔍 PikaFlow Nightly Discovery")
    print(f"   Time: {datetime.now(timezone.utc).isoformat()}")
    print()

    providers   = _load_providers()
    performance = _load_performance()

    _refresh_model_ids(providers)
    
    for p in providers:
        if p.get("base_url") == "local":
            print(f"  ⚪ {p['name']:30s} — local model, skipping HTTP check")
            continue

        result = _health_check(p)
        status = "✅" if result["ok"] else "❌"
        print(f"  {status} {p['name']:30s} | {result['status_code']} | {result['latency_ms']} ms | {result['error'] or 'OK'}")

        # Update performance record
        perf = performance.setdefault("providers", {}).setdefault(p["id"], {})
        perf["avg_latency_ms"] = result["latency_ms"]

        # Smoothed success rate (EMA, alpha=0.3)
        prev_rate = perf.get("success_rate") or (1.0 if result["ok"] else 0.0)
        perf["success_rate"] = round(0.7 * prev_rate + 0.3 * (1.0 if result["ok"] else 0.0), 3)

        # Mark deprecated on 404
        if result["status_code"] == 404:
            p["deprecated"] = True
            print(f"    ⚠️  Marked as deprecated: {p['name']}")

        # Disable provider if consistently failing (success_rate < 0.2)
        if perf["success_rate"] < 0.2:
            p["enabled"] = False
            print(f"    ⛔ Disabled (low success rate): {p['name']}")

    _save_providers(providers)
    _save_performance(performance)

    print("\n✅ Discovery complete — configs updated.")


if __name__ == "__main__":
    run()

