#!/usr/bin/env python3
"""
Logger Agent — summarizes a completed pipeline run and appends to session_log.json.

Reads:
  outputs/intent_spec.json  — the resolved spec
  outputs/model.py          — generated CadQuery script (for key decisions)
  outputs/last_review.txt   — review text saved by the designer agent

Writes:
  outputs/session_log.json  — append-only log (one entry per completed run)

Model: claude-sonnet-4-6 (cheap summarization; no deep reasoning needed)

Usage:
    python3 agents/logger_agent.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"
SPEC_PATH = Path("outputs/intent_spec.json")
MODEL_SCRIPT_PATH = Path("outputs/model.py")
REVIEW_PATH = Path("outputs/last_review.txt")
LOG_PATH = Path("outputs/session_log.json")
LESSONS_PATH = Path("outputs/design_lessons.json")


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_spec() -> dict:
    if not SPEC_PATH.exists():
        print(f"ERROR: {SPEC_PATH} not found.")
        sys.exit(1)
    with open(SPEC_PATH) as f:
        return json.load(f)


def load_optional(path: Path, max_chars: int = 4000) -> str:
    if path.exists():
        return path.read_text()[:max_chars]
    return ""


def write_lessons(entry: dict) -> None:
    lessons = entry.get("lessons_learned", [])
    if not lessons:
        return
    existing = json.loads(LESSONS_PATH.read_text()) if LESSONS_PATH.exists() else []
    for lesson in lessons:
        existing.append({
            "object": entry["object"],
            "version": entry.get("version", "unknown"),
            "lesson": lesson,
        })
    LESSONS_PATH.write_text(json.dumps(existing, indent=2))


def load_log() -> list:
    if LOG_PATH.exists():
        try:
            data = json.loads(LOG_PATH.read_text())
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    return []


def find_latest_stl() -> str | None:
    stls = list(Path("outputs").glob("*.stl"))
    if not stls:
        return None
    return str(max(stls, key=lambda p: p.stat().st_mtime))


def parse_json_block(text: str) -> dict:
    text = text.strip()
    if "```json" in text:
        try:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return json.loads(text[start:end].strip())
        except (ValueError, json.JSONDecodeError):
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# ── Summarization ──────────────────────────────────────────────────────────────

def _summarize_prompt(spec: dict, script: str, review: str) -> str:
    return f"""You are a logger agent in a 3D printing pipeline.
Extract key design decisions and print settings from the spec, script, and review.

<spec>
{json.dumps(spec, indent=2)}
</spec>

<script>
{script}
</script>

<review>
{review or "(no review text available — infer print settings from spec material/printer)"}
</review>

Return ONLY a valid JSON object (no markdown, no explanation):
{{
  "key_decisions": [
    "<non-obvious design choice and why — 3 to 5 bullets>"
  ],
  "lessons_learned": [
    "<what was tried → what failed → what worked and why — 1–3 bullets>"
  ],
  "print_settings": {{
    "material": "...",
    "layer_height": "...",
    "walls": "...",
    "infill": "...",
    "supports": "yes/no + reason",
    "orientation": "..."
  }},
  "verdict": "APPROVED" | "NEEDS_REVISION" | "UNKNOWN"
}}

key_decisions should explain WHY, not just what. Extract print_settings from the
review if present; otherwise use sensible defaults for the material in the spec.
lessons_learned should capture design approaches that were tried and revised during
this session: what was attempted, why it failed or was rejected, and what the
working solution was. Leave the list empty [] if no revisions occurred.
"""


def summarize(client: anthropic.Anthropic, spec: dict, script: str, review: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": _summarize_prompt(spec, script, review)}],
    )
    text = response.content[0].text
    result = parse_json_block(text)
    if not result:
        print("[Logger] WARNING: Could not parse summary JSON — using empty defaults.")
        return {"key_decisions": [], "print_settings": {}, "verdict": "UNKNOWN"}
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def run() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY before running.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("\n" + "=" * 54)
    print("  3D PRINT LOGGER AGENT")
    print("=" * 54 + "\n")

    spec = load_spec()
    script = load_optional(MODEL_SCRIPT_PATH)
    review = load_optional(REVIEW_PATH)
    stl = find_latest_stl()

    print(f"[Logger] Object: {spec.get('object')}")
    if not script:
        print("[Logger] WARNING: outputs/model.py not found — decisions will be limited.")
    if not review:
        print("[Logger] NOTE: outputs/last_review.txt not found — print settings inferred.")

    summary = summarize(client, spec, script, review)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "object": spec.get("object"),
        "material": spec.get("material"),
        "printer": spec.get("printer"),
        "dimensions_known": spec.get("dimensions_known", {}),
        "stl": stl,
        "verdict": summary.get("verdict", "UNKNOWN"),
        "print_settings": summary.get("print_settings", {}),
        "key_decisions": summary.get("key_decisions", []),
        "lessons_learned": summary.get("lessons_learned", []),
    }

    log = load_log()
    log.append(entry)
    LOG_PATH.parent.mkdir(exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2))
    write_lessons(entry)

    print(f"[Logger] Entry #{len(log)} appended → {LOG_PATH}")
    print(f"[Logger] Verdict  : {entry['verdict']}")
    print(f"[Logger] Key decisions:")
    for d in entry["key_decisions"]:
        print(f"  • {d}")
    print()


if __name__ == "__main__":
    run()
