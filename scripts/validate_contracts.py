#!/usr/bin/env python3
"""
validate_contracts.py — CI gate for cross-repo contracts.

Verifies that the event engine stays in sync with the mykidspots app contracts:
  contracts/sources.json   — valid pipeline source values
  contracts/categories.json — valid event category values

Rules enforced:
  1. Every value in PLATFORM_SOURCE_MAP must exist in contracts/sources.json
  2. Every platform used in sources/*.yml must exist in PLATFORM_SOURCE_MAP
  3. Every category in VALID_CATEGORIES (category_mapper.py) must exist in contracts/categories.json
  4. No source YAML may use a platform not in PLATFORM_SOURCE_MAP

Run locally:
  uv run python scripts/validate_contracts.py

Exits 0 on success, 1 on failure.
"""

import ast
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
SOURCES_DIR = REPO_ROOT / "sources"
PIPELINE_PY = REPO_ROOT / "src" / "event_engine" / "normalize" / "pipeline.py"
CATEGORY_MAPPER_PY = REPO_ROOT / "src" / "event_engine" / "normalize" / "category_mapper.py"

errors: list[str] = []
warnings: list[str] = []


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def extract_platform_source_map() -> dict[str, str]:
    """Parse PLATFORM_SOURCE_MAP from pipeline.py using ast.literal_eval (safe)."""
    text = PIPELINE_PY.read_text()
    # Find the dict literal using regex to locate the assignment
    match = re.search(r"PLATFORM_SOURCE_MAP\s*(?::\s*dict\[str,\s*str\])?\s*=\s*(\{[^}]+\})", text, re.DOTALL)
    if not match:
        errors.append(f"Could not find PLATFORM_SOURCE_MAP in {PIPELINE_PY}")
        return {}
    dict_str = match.group(1)
    try:
        result = ast.literal_eval(dict_str)
        if not isinstance(result, dict):
            errors.append("PLATFORM_SOURCE_MAP is not a dict")
            return {}
        return result
    except (ValueError, SyntaxError) as e:
        errors.append(f"Failed to parse PLATFORM_SOURCE_MAP: {e}")
        return {}


def extract_valid_categories() -> list[str]:
    """Parse VALID_CATEGORIES from category_mapper.py using ast.literal_eval (safe)."""
    if not CATEGORY_MAPPER_PY.exists():
        warnings.append(f"category_mapper.py not found at {CATEGORY_MAPPER_PY} — skipping category check")
        return []
    text = CATEGORY_MAPPER_PY.read_text()
    match = re.search(r"VALID_CATEGORIES\s*=\s*(\[[^\]]+\])", text, re.DOTALL)
    if not match:
        warnings.append("VALID_CATEGORIES not found in category_mapper.py — skipping category check")
        return []
    try:
        result = ast.literal_eval(match.group(1))
        if not isinstance(result, list):
            warnings.append("VALID_CATEGORIES is not a list — skipping category check")
            return []
        return result
    except (ValueError, SyntaxError):
        warnings.append("Could not parse VALID_CATEGORIES — skipping category check")
        return []


def get_yaml_platforms() -> dict[str, list[str]]:
    """Return {platform: [source_file, ...]} for all sources/*.yml entries."""
    platform_files: dict[str, list[str]] = {}
    for yml_file in SOURCES_DIR.glob("*.yml"):
        if yml_file.name.startswith("_"):
            continue
        data = load_yaml(yml_file)
        for source in data.get("sources", []):
            platform = source.get("platform", "")
            if platform:
                platform_files.setdefault(platform, []).append(yml_file.name)
    return platform_files


def main() -> int:
    print("=== MyKidSpots Contract Validator ===\n")

    # Load contracts
    sources_contract = load_json(CONTRACTS_DIR / "sources.json")
    categories_contract = load_json(CONTRACTS_DIR / "categories.json")

    valid_source_values = {s["value"] for s in sources_contract["sources"]}
    valid_category_values = {c["value"] for c in categories_contract["categories"]}

    print(f"contracts/sources.json:    {len(valid_source_values)} source values")
    print(f"contracts/categories.json: {len(valid_category_values)} category values\n")

    # Rule 1: PLATFORM_SOURCE_MAP values must be in contracts
    platform_source_map = extract_platform_source_map()
    print(f"PLATFORM_SOURCE_MAP: {len(platform_source_map)} entries")
    for platform, source_value in platform_source_map.items():
        if source_value not in valid_source_values:
            errors.append(
                f"PLATFORM_SOURCE_MAP['{platform}'] = '{source_value}' "
                f"is NOT in contracts/sources.json. "
                f"Valid values: {sorted(valid_source_values)}"
            )
        else:
            print(f"  ✓ {platform!r:20} → {source_value!r}")

    # Rule 2: All platforms in source YAMLs must be in PLATFORM_SOURCE_MAP
    yaml_platforms = get_yaml_platforms()
    print(f"\nsources/*.yml platforms: {sorted(yaml_platforms.keys())}")
    for platform, files in yaml_platforms.items():
        if platform not in platform_source_map:
            errors.append(
                f"Platform '{platform}' used in {files} "
                f"is NOT in PLATFORM_SOURCE_MAP. "
                f"Add it to normalize/pipeline.py and contracts/sources.json."
            )
        else:
            print(f"  ✓ {platform!r:20} (used in {len(files)} file(s))")

    # Rule 3: VALID_CATEGORIES in engine must match contracts
    engine_categories = extract_valid_categories()
    if engine_categories:
        print(f"\nVALID_CATEGORIES: {len(engine_categories)} entries")
        for cat in engine_categories:
            if cat not in valid_category_values:
                errors.append(
                    f"VALID_CATEGORIES entry '{cat}' is NOT in contracts/categories.json. "
                    f"Valid values: {sorted(valid_category_values)}"
                )
            else:
                print(f"  ✓ {cat!r}")
        for cat in valid_category_values:
            if cat not in engine_categories:
                warnings.append(
                    f"Category '{cat}' in contracts/categories.json is NOT in engine VALID_CATEGORIES. "
                    f"Consider adding it to category_mapper.py."
                )

    # Results
    print()
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  ⚠  {w}")
        print()

    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  ✗  {e}")
        print(f"\n❌ Contract validation FAILED ({len(errors)} error(s))")
        return 1

    print("✅ All contracts valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
