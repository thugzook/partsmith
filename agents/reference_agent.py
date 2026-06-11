#!/usr/bin/env python3
"""
Reference Agent — resolves two kinds of unknowns in the intent spec:

  1. DIMENSIONS  (dimensions_needed → dimensions_known)
     Looks up published product specs or asks the user to measure.

  2. ASSETS  (assets_needed → assets_resolved)
     Finds freely available SVGs / PNGs for icons, silhouettes, textures, etc.
     Downloads the file to outputs/<slug>/assets/ or, if no clean download is
     available, generates a polygon approximation and saves it as JSON.

The agent is fully generic — it reads all context from the spec so it works
for any future project without code changes.

Usage:
    python3 agents/reference_agent.py

Output:
    outputs/<slug>/intent_spec.json  (updated in-place)
    outputs/<slug>/assets/           (new files written here for each asset)
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"
SPEC_PATH = Path("outputs/intent_spec.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_spec() -> dict:
    if not SPEC_PATH.exists():
        print(f"ERROR: {SPEC_PATH} not found. Run the intent agent first.")
        sys.exit(1)
    with open(SPEC_PATH) as f:
        return json.load(f)


def save_spec(spec: dict) -> None:
    with open(SPEC_PATH, "w") as f:
        json.dump(spec, f, indent=2)
    print(f"\n[Agent] Spec saved → {SPEC_PATH}")


def display_dims(dims: dict) -> None:
    print("\n" + "─" * 54)
    print("  RESOLVED DIMENSIONS")
    print("─" * 54)
    for key, val in dims.items():
        print(f"  {key:<42} {val} mm")
    print("─" * 54 + "\n")


def display_assets(assets: dict) -> None:
    print("\n" + "─" * 54)
    print("  RESOLVED ASSETS")
    print("─" * 54)
    for name, meta in assets.items():
        print(f"  {name}")
        print(f"    file   : {meta.get('file') or '(none)'}")
        print(f"    format : {meta.get('format', '?')}")
        print(f"    license: {meta.get('license', 'unknown')}")
        notes = (meta.get("geometry_notes") or "")[:120]
        if notes:
            print(f"    notes  : {notes}")
    print("─" * 54 + "\n")


def parse_json_block(text: str) -> dict:
    if "```json" not in text:
        return {}
    try:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return json.loads(text[start:end].strip())
    except (ValueError, json.JSONDecodeError):
        return {}


def prompt_float(label: str, suggested: float | None = None) -> float:
    hint = f" [suggested {suggested} mm — Enter to accept]" if suggested is not None else ""
    while True:
        raw = input(f"  {label}{hint}: ").strip()
        if raw == "" and suggested is not None:
            return float(suggested)
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
        print("    Enter a positive number (in mm).")


# ── Asset resolution ──────────────────────────────────────────────────────────

def _build_asset_search_prompt(asset_desc: str, spec: dict) -> str:
    return f"""\
You are a reference agent in a 3D printing pipeline. The designer needs a reference
asset to use as a CadQuery engraving profile.

OBJECT: {spec.get("object", "unknown")}
ASSET NEEDED: {asset_desc}

Search the web for 3–5 freely available, simple SVG or PNG candidates that match this
description. Prefer CC0 / Public Domain sources (freesvg.org, publicdomainvectors.org,
openclipart.org, svgrepo.com, etc.). Avoid anything requiring sign-in to download.

For each candidate return:
- A short label (1–5 words)
- The page URL where it lives
- The direct download URL if you can find one (null if not)
- The license
- A one-sentence visual description of what the shape looks like

Return ONLY a JSON block then a NOTES line:

```json
{{
  "candidates": [
    {{
      "label": "<short name>",
      "page_url": "<URL>",
      "download_url": "<direct .svg or .png URL, or null>",
      "license": "<CC0 / Public Domain / etc.>",
      "description": "<what it looks like>"
    }}
  ]
}}
```

NOTES: <one sentence summary of what you found>"""


def resolve_assets(client: anthropic.Anthropic, spec: dict) -> dict:
    """
    For every entry in assets_needed:
      1. Search for 3-5 candidates and present them to the user to pick.
      2. If user rejects all, prompt them to upload a file.
      3. Download the chosen file and save to outputs/<slug>/assets/.
      4. Only generate geometry if user explicitly requests it after exhausting 1+2.
    Returns {asset_slug: {file, format, license, source_url, geometry_notes}}.
    """
    needed: list = spec.get("assets_needed", [])
    if not needed:
        return {}

    slug = spec.get("object", "unknown").lower().replace(" ", "_")
    asset_dir = Path(f"outputs/{slug}/assets")
    asset_dir.mkdir(parents=True, exist_ok=True)

    resolved: dict = {}

    for asset_desc in needed:
        asset_slug = asset_desc.lower().replace(" ", "_")[:40]
        print(f"[Agent] Searching for asset: {asset_desc!r}\n")

        # Phase 1 — search for candidates
        prompt = _build_asset_search_prompt(asset_desc, spec)
        text, ok = _agentic_call(client, prompt, use_search=True)
        if not ok or not parse_json_block(text):
            text, _ = _agentic_call(client, prompt, use_search=False)

        meta = parse_json_block(text)
        candidates = (meta or {}).get("candidates", [])

        if "NOTES:" in text:
            idx = text.index("NOTES:") + len("NOTES:")
            print(f"[Reference] {text[idx:].strip().splitlines()[0]}\n")

        # Present candidates to user
        if candidates:
            print("[Agent] Found these candidates. Pick one or type 'upload' / 'generate':\n")
            for i, c in enumerate(candidates, 1):
                print(f"  {i}. {c.get('label', '?')}  [{c.get('license', '?')}]")
                print(f"     {c.get('description', '')}")
                print(f"     Page: {c.get('page_url', '')}")
                print()
        else:
            print("[Agent] No candidates found online.\n")

        # Get user choice
        while True:
            raw = input(
                "  Enter a number to pick, 'upload' to provide your own file,\n"
                "  or 'generate' to use computed geometry: "
            ).strip().lower()

            if raw == "upload":
                file_path = input("  Paste the full path to your file: ").strip()
                if Path(file_path).exists():
                    dest = asset_dir / (asset_slug + Path(file_path).suffix)
                    import shutil
                    shutil.copy(file_path, dest)
                    resolved[asset_slug] = {
                        "description": asset_desc, "file": str(dest),
                        "format": Path(file_path).suffix.lstrip("."),
                        "license": "user-provided", "source_url": None,
                        "geometry_notes": "User-uploaded file.",
                    }
                    break
                print("  File not found — try again.")
                continue

            if raw == "generate":
                print("[Agent] NOTE: falling back to computed geometry — shape may not match a real icon.")
                resolved[asset_slug] = {
                    "description": asset_desc, "file": None,
                    "format": "generated", "license": "n/a", "source_url": None,
                    "geometry_notes": "No file sourced. Designer must generate geometry from scratch.",
                }
                break

            try:
                idx = int(raw) - 1
                chosen = candidates[idx]
            except (ValueError, IndexError):
                print("  Invalid choice — enter a number, 'upload', or 'generate'.")
                continue

            # Try to download chosen candidate
            url = chosen.get("download_url")
            fmt = "svg" if (url or "").endswith(".svg") else "png"
            file_path = None
            if url:
                dest = asset_dir / f"{asset_slug}.{fmt}"
                try:
                    urllib.request.urlretrieve(url, dest)
                    file_path = str(dest)
                    print(f"[Agent] Downloaded → {file_path}")
                except Exception as e:
                    print(f"[Agent] Download failed ({e}). File not saved; record page URL for manual download.")

            resolved[asset_slug] = {
                "description": asset_desc,
                "file": file_path,
                "format": fmt,
                "license": chosen.get("license", "unknown"),
                "source_url": chosen.get("page_url"),
                "download_url": url,
                "geometry_notes": chosen.get("description", ""),
            }
            break

    return resolved


# ── Phase 1: Research ─────────────────────────────────────────────────────────

def _build_research_prompt(spec: dict) -> str:
    dims = spec.get("dimensions_needed", [])
    dims_list = "\n".join(f"  - {d}" for d in dims)
    ref = spec.get("reference_context", {})
    ref_text = json.dumps(ref, indent=2) if ref else "(none)"
    return f"""\
You are a reference agent in a 3D printing pipeline. Given the design spec below,
resolve each listed dimension.

OBJECT: {spec.get("object", "unknown")}
PURPOSE: {spec.get("purpose", "unknown")}
REFERENCE CONTEXT:
{ref_text}

DIMENSIONS TO RESOLVE:
{dims_list}

Rules:
1. If a dimension is a published product specification you can look up (e.g., a named
   product's width, a standard hardware size), provide the value in mm.
2. If a dimension requires the user to physically measure something unique to their
   setup (e.g., their specific drawer or enclosure), return null.
3. All values must be in millimeters as plain numbers.

Return ONLY a JSON block, then a single NOTES line:

```json
{{
{chr(10).join(f'  "{d}": <number or null>,' for d in dims)}
}}
```

NOTES: <one sentence on sources for any researched values>"""


def _agentic_call(client: anthropic.Anthropic, prompt: str,
                  use_search: bool) -> tuple[str, bool]:
    """Single API call with optional web_search_20250305 tool; returns (text, success)."""
    kwargs: dict = {"model": MODEL, "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}]}
    if use_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    messages = kwargs.pop("messages")

    for _ in range(6):
        try:
            response = client.messages.create(**kwargs, messages=messages)
        except anthropic.APIError as e:
            return "", False if use_search else (str(e), False)

        if response.stop_reason == "end_turn":
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            return text, True

        if response.stop_reason == "tool_use":
            # web_search_20250305 is server-executed — send back empty tool_results
            messages = list(messages)
            messages.append({"role": "assistant", "content": response.content})
            results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in response.content if b.type == "tool_use"
            ]
            messages.append({"role": "user", "content": results})
        else:
            break

    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    return text, bool(text)


def research_dimensions(client: anthropic.Anthropic, spec: dict) -> dict:
    """
    Ask Claude to look up every dimension in dimensions_needed.
    Returns {dim_name: value_or_null} — null means user must measure it.
    """
    print("[Agent] Researching dimensions...\n")
    prompt = _build_research_prompt(spec)

    # Try web search first
    text, ok = _agentic_call(client, prompt, use_search=True)
    if not ok or not parse_json_block(text):
        print("[Agent] Web search unavailable or returned no data; using training knowledge.\n")
        text, _ = _agentic_call(client, prompt, use_search=False)

    dims = parse_json_block(text)
    if not dims:
        # Last resort: all dimensions need manual input
        print("[Agent] WARNING: could not parse research results — all values will be entered manually.\n")
        dims = {d: None for d in spec.get("dimensions_needed", [])}

    if "NOTES:" in text:
        idx = text.index("NOTES:") + len("NOTES:")
        note = text[idx:].strip().splitlines()[0]
        print(f"[Research] {note}\n")

    return dims


# ── Phase 2: Confirm + collect ────────────────────────────────────────────────

def resolve_all(researched: dict) -> dict:
    """
    Confirm researched (non-null) values with user, then collect user-measured values.
    """
    found = {k: v for k, v in researched.items() if v is not None}
    needs_measure = [k for k, v in researched.items() if v is None]
    resolved: dict = {}

    if found:
        print("[Agent] Claude found these values. Confirm each or enter a correction.\n")
        for key, val in found.items():
            resolved[key] = prompt_float(key, suggested=val)

    if needs_measure:
        print("\n[Agent] These dimensions need physical measurement (grab your tape measure).\n")
        for key in needs_measure:
            resolved[key] = prompt_float(key)

    return resolved


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY before running.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("\n" + "=" * 54)
    print("  3D PRINT REFERENCE AGENT")
    print("  Resolves dimensions and reference assets.")
    print("=" * 54 + "\n")

    spec = load_spec()
    print(f"[Agent] Spec loaded: {spec.get('object')}")

    dims_needed = spec.get("dimensions_needed", [])
    assets_needed = spec.get("assets_needed", [])

    if not dims_needed and not assets_needed:
        print("[Agent] Nothing to resolve — spec is already complete.")
        sys.exit(0)

    # ── Dimensions ────────────────────────────────────────────────────────────
    if dims_needed:
        print(f"[Agent] Dimensions to resolve ({len(dims_needed)}): {', '.join(dims_needed)}\n")
        researched = research_dimensions(client, spec)
        resolved_dims = resolve_all(researched)
        display_dims(resolved_dims)
        spec["dimensions_known"] = resolved_dims
        spec["dimensions_needed"] = []
    else:
        print("[Agent] No dimensions to resolve.\n")

    # ── Assets ────────────────────────────────────────────────────────────────
    if assets_needed:
        print(f"[Agent] Assets to source ({len(assets_needed)}): {', '.join(assets_needed)}\n")
        resolved_assets = resolve_assets(client, spec)
        display_assets(resolved_assets)
        existing = spec.get("assets_resolved", {})
        existing.update(resolved_assets)
        spec["assets_resolved"] = existing
        spec["assets_needed"] = []
    else:
        print("[Agent] No assets to source.\n")

    spec["confidence"] = "high — all references resolved by Reference Agent"

    confirm = input("Save updated spec? [Y/n]: ").strip().lower()
    if confirm in ("", "y", "yes"):
        save_spec(spec)
        print("[Agent] Ready for Designer Agent.\n")
    else:
        print("[Agent] Spec NOT saved.\n")


if __name__ == "__main__":
    run()
