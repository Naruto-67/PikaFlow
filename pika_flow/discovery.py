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
    "mixtral-8x7b-32768", "gemma2-9b-it", "llama3-70b-8192", "llama3-8b-8192",
    "llama-3.1-70b-versatile", "llama-3.1-8b-instant",
    "mistralai/mistral-7b-instruct:free"
}

def _load_banned_models() -> set[str]:
    path = _CFG_DIR / "banned_models.json"
    banned = set(DEPRECATED_KNOWN)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            banned.update(data.get("banned_models", []))
        except Exception:
            pass
    return banned


def _add_banned_model(model_name: str) -> None:
    if not model_name:
        return
    path = _CFG_DIR / "banned_models.json"
    current_banned = list(DEPRECATED_KNOWN)
    if path.exists():
        try:
            current_banned = json.loads(path.read_text()).get("banned_models", current_banned)
        except Exception:
            pass
    if model_name not in current_banned:
        current_banned.append(model_name)
        data = {
            "banned_models": sorted(list(set(current_banned))),
            "_meta": {
                "description": "Permanently banned or decommissioned models. Auto-updated when discovery or LLM manager detects 404/decommissioned responses.",
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
        }
        path.write_text(json.dumps(data, indent=2))


BANNED_MODALITY_PATTERNS = [
    r"image", r"picture", r"tts", r"audio", r"live", r"embed",
    r"robotics", r"video", r"veo", r"whisper", r"transcribe",
    r"guard", r"safeguard", r"deepseek-r1-distill-qwen-1\.5b",
    r"omni", r"imagen",
    r"orpheus", r"canopylabs", r"speech", r"voice", r"sound", r"realtime",
    r"inkling", r"lyria", r"banana", r"deep-research", r"antigravity",
    r"computer-use", r"customtools", r"content-safety",
]

def is_modality_allowed(model_name: str) -> bool:
    lowered = model_name.lower()
    for pattern in BANNED_MODALITY_PATTERNS:
        if re.search(pattern, lowered):
            return False
    return True

def is_model_allowed(model_name: str, banned: set[str]) -> bool:
    if not is_modality_allowed(model_name):
        return False
    lowered = model_name.lower()
    if lowered in banned or model_name in banned:
        return False
    for b in banned:
        if b.lower() == lowered:
            return False
    return True

def _refresh_model_ids(providers: list[dict], banned_models: set[str]) -> list[dict]:
    """Query each provider's /models endpoint to discover ALL active/free models without hardcoded limits."""
    expanded_providers = []
    
    for p in providers:
        if "local" in p.get("base_url", ""):
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
                    valid_models = [m for m in models if is_model_allowed(m, banned_models)]
                    for m in valid_models:
                        new_p = p.copy()
                        new_p["id"] = f"gemini_{m.replace('-', '_').replace('.', '_')}"
                        new_p["name"] = f"Google ({m})"
                        new_p["model"] = m
                        new_p["endpoint"] = f"v1beta/models/{m}:generateContent"
                        expanded_providers.append(new_p)
                        print(f"  🔄 Discovered Gemini model: {m}")
                    if valid_models:
                        added = True
            except Exception as e:
                print(f"  ⚠️ Failed to discover Gemini models: {e}")
                
        elif "groq" in p["id"]:
            try:
                url = "https://api.groq.com/openai/v1/models"
                resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
                if resp.status_code == 200:
                    models = [m["id"] for m in resp.json().get("data", []) if m.get("active", True)]
                    valid_models = [m for m in models if is_model_allowed(m, banned_models)]
                    for m in valid_models:
                        new_p = p.copy()
                        new_p["id"] = f"groq_{m.replace('-', '_').replace('.', '_')}"
                        new_p["name"] = f"Groq ({m})"
                        new_p["model"] = m
                        expanded_providers.append(new_p)
                        print(f"  🔄 Discovered Groq model: {m}")
                    if valid_models:
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
                    valid_models = [m for m in free_models if is_model_allowed(m, banned_models)]
                    for m in valid_models:
                        new_p = p.copy()
                        new_p["id"] = f"or_{m.split('/')[-1].replace('-', '_').replace('.', '_').replace(':', '_')}"
                        new_p["name"] = f"OpenRouter ({m.split('/')[-1]})"
                        new_p["model"] = m
                        expanded_providers.append(new_p)
                        print(f"  🔄 Discovered OpenRouter model: {m}")
                    if valid_models:
                        added = True
            except Exception as e:
                print(f"  ⚠️ Failed to discover OpenRouter models: {e}")
        
        elif "huggingface" in p["id"]:
            if is_model_allowed(p.get("model", ""), banned_models):
                expanded_providers.append(p)
                added = True
            
        if not added and p.get("type") == "text":
            if is_model_allowed(p.get("model", ""), banned_models):
                expanded_providers.append(p)
            
    return expanded_providers


def _health_check(provider: dict) -> dict:
    """
    Send a minimal request to the provider and return health metrics.
    Returns: {"ok": bool, "latency_ms": int, "status_code": int, "error": str|None}
    """
    if "local" in provider.get("base_url", ""):
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
            timeout=15,
        )
        latency = int((time.time() - start) * 1000)

        if resp.status_code == 200:
            return {"ok": True,  "latency_ms": latency, "status_code": 200, "error": None}
        elif resp.status_code == 404:
            return {"ok": False, "latency_ms": latency, "status_code": 404, "error": "Model not found / deprecated"}
        elif resp.status_code == 429:
            return {"ok": False, "latency_ms": 50000, "status_code": 429, "error": "Quota limit (rate-limited)"}
        else:
            return {"ok": False, "latency_ms": latency, "status_code": resp.status_code, "error": resp.text[:200]}

    except requests.Timeout:
        return {"ok": False, "latency_ms": 15000, "status_code": 0, "error": "Timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": 0, "status_code": 0, "error": str(exc)}


def run() -> None:
    print("🔍 PikaFlow Automated Free Model Discovery & Benchmark")
    print(f"   Time: {datetime.now(timezone.utc).isoformat()}\n")

    base_providers = _load_providers()
    performance    = _load_performance()
    banned_models  = _load_banned_models()

    # Discover ALL free text models across provider catalogs (excluding permanently banned)
    providers = _refresh_model_ids(base_providers, banned_models)
    
    # Test and benchmark EVERY discovered model
    tested_results = []
    for p in providers:
        if "local" in p.get("base_url", ""):
            print(f"  ⚪ {p['name']:35s} — local model, skipping HTTP check")
            tested_results.append((p, {"ok": True, "latency_ms": 0, "status_code": 200, "error": None}))
            continue

        result = _health_check(p)
        status = "✅" if result["ok"] else "❌"
        print(f"  {status} {p['name']:35s} | {result['status_code']:3d} | {result['latency_ms']:5d} ms | {result['error'] or 'OK'}")
        tested_results.append((p, result))

        # Update performance metrics
        perf = performance.setdefault("providers", {}).setdefault(p["id"], {})
        perf["avg_latency_ms"] = result["latency_ms"]

        prev_rate = perf.get("success_rate") or (1.0 if result["ok"] else 0.0)
        perf["success_rate"] = round(0.7 * prev_rate + 0.3 * (1.0 if result["ok"] else 0.0), 3)

        # Automatically permanently ban on 404 or decommissioned error
        err_msg = str(result.get("error") or "").lower()
        if result["status_code"] in (400, 404) and ("decommissioned" in err_msg or "not found" in err_msg or result["status_code"] == 404):
            p["deprecated"] = True
            p["enabled"] = False
            _add_banned_model(p.get("model", ""))
            banned_models.add(p.get("model", ""))
            print(f"    🚫 Permanently banned & decommissioned: {p['name']} ({p.get('model')})")

        if perf["success_rate"] < 0.2:
            p["enabled"] = False
            print(f"    ⛔ Disabled (low success rate): {p['name']}")

    # ── EMPIRICAL BENCHMARK RANKING & INTERLEAVED SELECTION ──
    # Sort all text models empirically, interleaving top models from each provider family
    def perf_rank(item):
        prov, res = item
        if not prov.get("enabled", True) or prov.get("deprecated", False) or prov.get("model") in banned_models:
            return 999999
        p_perf = performance.get("providers", {}).get(prov["id"], {})
        s_rate = p_perf.get("success_rate", 1.0 if res["ok"] else 0.0)
        lat = res["latency_ms"] if res["latency_ms"] > 0 else 9999
        return int((1.0 - s_rate) * 1000 + lat)

    def get_provider_family(p: dict) -> str:
        pid = p.get("id", "").lower()
        burl = p.get("base_url", "").lower()
        if "gemini" in pid or "generativelanguage" in burl:
            return "google"
        elif "groq" in pid or "groq.com" in burl:
            return "groq"
        elif "openrouter" in pid or "openrouter" in burl or pid.startswith("or_"):
            return "openrouter"
        elif "huggingface" in pid or "huggingface" in burl:
            return "huggingface"
        return "other"

    text_items = [(p, r) for p, r in tested_results if p.get("type") == "text"]
    other_items = [(p, r) for p, r in tested_results if p.get("type") != "text"]

    # Group text items by provider family
    families: dict[str, list] = {}
    for item in text_items:
        fam = get_provider_family(item[0])
        families.setdefault(fam, []).append(item)

    # Sort each family internally by empirical benchmark score (lowest score = fastest & healthiest)
    for fam in families:
        families[fam].sort(key=perf_rank)

    # Interleave across provider families (Round 1: Best model of each provider, Round 2: 2nd best, etc.)
    interleaved_text_items = []
    family_order = sorted(
        families.keys(),
        key=lambda f: perf_rank(families[f][0]) if families[f] else 999999
    )

    max_len = max(len(items) for items in families.values()) if families else 0
    for idx in range(max_len):
        for fam in family_order:
            if idx < len(families[fam]):
                interleaved_text_items.append(families[fam][idx])

    # Assign priority 1..N dynamically based on interleaved empirical ranking
    final_providers = []
    for rank, (prov, _) in enumerate(interleaved_text_items, start=1):
        prov["priority"] = rank
        final_providers.append(prov)

    for prov, _ in other_items:
        final_providers.append(prov)

    # Permanently exclude all deprecated or banned providers so they NEVER exist in llm_providers.json
    final_providers = [
        prov for prov in final_providers
        if not prov.get("deprecated", False) and prov.get("model") not in banned_models
    ]

    _save_providers(final_providers)
    _save_performance(performance)

    print(f"\n✅ Discovery & Benchmark complete — ranked {len(final_providers)} active models empirically across {len(families)} provider families (banned {len(banned_models)} models).")


if __name__ == "__main__":
    run()

