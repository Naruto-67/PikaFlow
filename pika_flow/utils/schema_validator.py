"""
Schema Validator
================
Validates PikaFlow configuration files (e.g., llm_providers.json) against JSON schemas
to prevent pipeline crashes due to malformed configs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Simple structural validation (no third-party dependencies required)
def validate_llm_providers(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate llm_providers.json structure."""
    errors = []
    if "providers" not in data or not isinstance(data["providers"], list):
        errors.append("Root must contain a 'providers' list.")
        return False, errors
    
    for i, p in enumerate(data["providers"]):
        if not isinstance(p, dict):
            errors.append(f"Provider at index {i} is not an object.")
            continue
            
        for field in ["id", "name", "model", "type", "base_url", "endpoint"]:
            if field not in p:
                errors.append(f"Provider '{p.get('name', i)}' missing required field: {field}")
                
        if p.get("type") not in ["text", "image", "tts"]:
            errors.append(f"Provider '{p.get('name', i)}' has invalid type: {p.get('type')}")
            
    return len(errors) == 0, errors

def run_validation(cfg_dir: Path) -> bool:
    """Run all schema validations."""
    print("🔍 Validating configurations...")
    providers_file = cfg_dir / "llm_providers.json"
    
    if not providers_file.exists():
        print(f"❌ Missing config file: {providers_file}")
        return False
        
    try:
        data = json.loads(providers_file.read_text())
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in {providers_file.name}: {e}")
        return False
        
    ok, errors = validate_llm_providers(data)
    if not ok:
        print(f"❌ Validation failed for {providers_file.name}:")
        for err in errors:
            print(f"   - {err}")
        return False
        
    print(f"✅ {providers_file.name} is valid.")
    return True

if __name__ == "__main__":
    import sys
    cfg = Path(__file__).parent.parent.parent / "config"
    if not run_validation(cfg):
        sys.exit(1)
