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


import re

DEPRECATED_KNOWN = {
    "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash",
    "gemini-1.5-flash-8b", "gemini-1.5-pro", "gemini-2.0-pro",
    "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite",
    "mixtral-8x7b-32768", "gemma2-9b-it", "llama3-70b-8192", "llama3-8b-8192",
    "mistralai/mistral-7b-instruct:free"
}

BANNED_MODALITY_PATTERNS = [
    r"image", r"picture", r"tts", r"audio", r"live", r"embed",
    r"robotics", r"video", r"veo", r"whisper", r"transcribe",
    r"guard", r"safeguard", r"deepseek-r1-distill-qwen-1\.5b",
    r"omni", r"imagen",
    r"orpheus", r"canopylabs", r"speech", r"voice", r"sound", r"realtime",
    r"inkling",
]

def is_modality_allowed(model_name: str) -> bool:
    lowered = model_name.lower()
    for pattern in BANNED_MODALITY_PATTERNS:
        if re.search(pattern, lowered):
            return False
    return True

def _refresh_model_ids(providers: list[dict]) -> list[dict]:
    """Query each provider's /models endpoint to find the latest active/free models, and expand them."""
    expanded_providers = []
    
    for p in providers:
        if p.get("base_url") == "local":
            expanded_providers.append(p)
            continue
            
        api_key = _get_api_key(p.get("secret_key"))
        if not api_key and "openrouter" not in p["id"]:
            expanded_providers.append(p)
            continue
            
        added = False
        
        if "gemini" in p["id"]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    models = [
                        m["name"].replace("models/", "") for m in resp.json().get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", [])
                    ]
                    valid_models = [m for m in models if m not in DEPRECATED_KNOWN and is_modality_allowed(m)]
                    # Remove pro models since free tier rate limits are too strict for video pipelines
                    valid_models = [m for m in valid_models if "pro" not in m.lower()]
                    
                    if valid_models:
                        # Prefer flash over older models, sort descending so 1.5 > 1.0
                        valid_models.sort(key=lambda x: (1 if "flash" in x else 2, x), reverse=True)
                        for idx, best_model in enumerate(valid_models[:3]): # Top 3 gemini models
                            new_p = p.copy()
                            new_p["id"] = f"gemini_{best_model.replace('-', '_').replace('.', '_')}"
                            new_p["name"] = f"Google ({best_model})"
                            new_p["model"] = best_model
                            new_p["endpoint"] = f"v1beta/models/{best_model}:generateContent"
                            new_p["priority"] = (idx * 10) + 1  # 1, 11, 21
                            expanded_providers.append(new_p)
                            print(f"  🔄 Discovered Gemini model: {best_model}")
                        added = True
            except Exception as e:
                print(f"  ⚠️ Failed to discover Gemini models: {e}")
                
        elif "groq" in p["id"]:
            try:
                url = "https://api.groq.com/openai/v1/models"
                resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
                if resp.status_code == 200:
                    models = [m["id"] for m in resp.json().get("data", []) if m.get("active", True)]
                    valid_models = [m for m in models if m not in DEPRECATED_KNOWN and is_modality_allowed(m)]
                    if valid_models:
                        # Prefer 8b over 70b since 70b has strict free rate limits on Groq
                        valid_models.sort(key=lambda x: (1 if "8b" in x.lower() else 2 if "3.3" in x.lower() else 3, x))
                        for idx, best_model in enumerate(valid_models[:3]):
                            new_p = p.copy()
                            new_p["id"] = f"groq_{best_model.replace('-', '_').replace('.', '_')}"
                            new_p["name"] = f"Groq ({best_model})"
                            new_p["model"] = best_model
                            new_p["priority"] = (idx * 10) + 2  # 2, 12, 22
                            expanded_providers.append(new_p)
                            print(f"  🔄 Discovered Groq model: {best_model}")
                        added = True
            except Exception as e:
                print(f"  ⚠️ Failed to discover Groq models: {e}")
                
        elif "openrouter" in p["id"]:
            try:
                url = "https://openrouter.ai/api/v1/models"
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    free_models = [
                        m["id"] for m in resp.json().get("data", [])
                        if m.get("pricing", {}).get("prompt") == "0" and m.get("pricing", {}).get("completion") == "0"
                    ]
                    valid_models = [m for m in free_models if m not in DEPRECATED_KNOWN and is_modality_allowed(m)]
                    if valid_models:
                        # Prefer 8b models, then llama, then mistral
                        valid_models.sort(key=lambda x: (1 if "8b" in x.lower() else 2 if "llama" in x.lower() else 3, x))
                        for idx, best_model in enumerate(valid_models[:3]):
                            new_p = p.copy()
                            new_p["id"] = f"or_{best_model.split('/')[-1].replace('-', '_').replace('.', '_').replace(':', '_')}"
                            new_p["name"] = f"OpenRouter ({best_model.split('/')[-1]})"
                            new_p["model"] = best_model
                            new_p["priority"] = (idx * 10) + 3  # 3, 13, 23
                            expanded_providers.append(new_p)
                            print(f"  🔄 Discovered OpenRouter model: {best_model}")
                        added = True
            except Exception as e:
                print(f"  ⚠️ Failed to discover OpenRouter models: {e}")
        
        elif "huggingface" in p["id"]:
            # Keeping the default HF model as is
            p["priority"] = 40
            expanded_providers.append(p)
            added = True
            
        if not added and p.get("type") == "text":
            expanded_providers.append(p) # fallback if discovery failed
            
    return expanded_providers


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

    # Build generic test payload based on API type
    if "generativelanguage" in provider["base_url"]:
        payload = {"contents": [{"parts": [{"text": "Reply with: OK"}]}]}
    elif "api.groq.com" in provider["base_url"] or "openrouter" in provider["base_url"]:
        payload = {
            "model": provider["model"],
            "messages": [{"role": "user", "content": "Reply with: OK"}],
            "max_tokens": 5,
        }
    else:
        payload = {"inputs": "Reply with: OK"}

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

    providers = _refresh_model_ids(providers)
    
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

