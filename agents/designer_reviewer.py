#!/usr/bin/env python3
"""
Designer/Reviewer Agent — generates, runs, and validates a CadQuery model.

Flow:
  1. Load the resolved spec (exits if dimensions_needed is still populated)
  2. Call Opus with the spec + cad_skill.md to generate a CadQuery script
  3. Run the script via run_cadquery_model.py; feed errors back to Opus for fixes
     (up to MAX_ITER attempts)
  4. On success, Opus self-reviews the design against spec constraints + design-review.md
  5. If the review flags critical issues, one more fix+run cycle
  6. Deliver: STL path, preview path (if rendered), print settings, parameters table

Model: claude-opus-4-8 (user's explicit choice for this step — strongest geometric reasoning)

Usage:
    python3 agents/designer_reviewer.py

Output:
    outputs/model.py           — the generated CadQuery script
    outputs/<name>.stl         — the 3D model ready for slicing
    outputs/<name>_preview.png — multi-view preview (if pyrender is installed)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic

MODEL = "claude-opus-4-8"
BRIEF_MODEL = "claude-sonnet-4-6"
MAX_ITER = 6
SPEC_PATH = Path("outputs/intent_spec.json")
MODEL_SCRIPT_PATH = Path("outputs/model.py")
RUNNER = "run_cadquery_model.py"
REVIEW_PATH = Path("outputs/last_review.txt")


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_spec() -> dict:
    if not SPEC_PATH.exists():
        print(f"ERROR: {SPEC_PATH} not found. Run the intent and reference agents first.")
        sys.exit(1)
    with open(SPEC_PATH) as f:
        return json.load(f)


def load_text(rel_path: str) -> str:
    path = Path(rel_path)
    if not path.exists():
        print(f"WARNING: Could not load {rel_path} — proceeding without it.")
        return ""
    return path.read_text()


def extract_python(text: str) -> str | None:
    """Return the first ```python ... ``` block from model output, or None."""
    marker = "```python"
    if marker not in text:
        return None
    try:
        start = text.index(marker) + len(marker)
        end = text.index("```", start)
        return text[start:end].strip()
    except ValueError:
        return None


def save_script(code: str) -> None:
    MODEL_SCRIPT_PATH.parent.mkdir(exist_ok=True)
    MODEL_SCRIPT_PATH.write_text(code)
    print(f"[Agent] Script saved → {MODEL_SCRIPT_PATH}")


def run_script(preview: bool = False, strict: bool = True) -> dict:
    """Execute MODEL_SCRIPT_PATH via run_cadquery_model.py. Returns the JSON result dict."""
    if not Path(RUNNER).exists():
        return {"success": False, "stderr": f"{RUNNER} not found in project root.", "stdout": ""}

    cmd = [sys.executable, RUNNER, str(MODEL_SCRIPT_PATH), "--strict"]
    if preview:
        cmd.append("--preview")

    print(f"[Agent] Running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return {"success": False, "stderr": "Script timed out after 240s.", "stdout": ""}

    # run_cadquery_model.py emits a single JSON object to stdout
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "stderr": proc.stderr or f"runner exit {proc.returncode}: no JSON output",
            "stdout": proc.stdout,
        }


# ── Phase 0: Design concept brief ─────────────────────────────────────────────

def _brief_request(spec: dict) -> str:
    return f"""Produce a Design Concept Brief for the following 3D printing spec.
Do NOT write any CadQuery code. The brief is for human review before coding begins.

<spec>
{json.dumps(spec, indent=2)}
</spec>

Your brief must contain exactly these four sections:

## Component Inventory
A table with columns: Component | Dimensions (W×D×H) | Notes
List every distinct solid body (base, panels, sockets, slots, etc.).

## Side-View Sketch
An ASCII cross-section or side view showing how the components relate spatially.
Use simple box/line characters. Show tilt angles and relative positions if relevant.

## Stated Assumptions
Bullet list of anything not explicit in the spec that the design will assume
(angles, proportions, feature placement, cable routing, etc.).

## Open Questions
Anything still ambiguous. Write "None" if everything is clear.

Keep the brief concise — the goal is a quick visual confirmation of the design concept.
"""


def brief_phase(client: anthropic.Anthropic, spec: dict) -> str:
    """Produce a design concept brief using Sonnet before Opus begins coding."""
    print("\n[Agent] Generating design concept brief (Sonnet)...")
    response = client.messages.create(
        model=BRIEF_MODEL,
        max_tokens=1024,
        system="You are a CadQuery design consultant. Produce a design brief only — no code.",
        messages=[{"role": "user", "content": _brief_request(spec)}],
    )
    brief = response.content[0].text
    print("\n=== DESIGN BRIEF (auto-confirmed in pipeline) ===")
    print(brief)
    print("==================================================\n")
    return brief


# ── Phase 1: Design generation + fix loop ─────────────────────────────────────

def _system_prompt(cad_skill: str) -> str:
    return f"""You are a CadQuery expert in an automated 3D printing pipeline.
Your job is to write complete, runnable CadQuery scripts that produce watertight STL files.

Follow this skill guide exactly:
<cad_skill>
{cad_skill}
</cad_skill>

Output rules:
1. When generating or fixing a script, output EXACTLY ONE complete ```python ... ``` block.
   Never output partial code or diffs — always the full script.
2. After the code block, add a short NOTES section (3-6 lines) explaining key design decisions.
3. All geometry dimensions must come from the spec's dimensions_known — never invent or round values.
4. STL export: use a plain filename (e.g., "blade_organizer.stl"). The script runs with its
   own directory as CWD so the file lands in outputs/ automatically.
5. Print a bounding-box summary at the end of the script for dimensional verification.
"""


def _initial_message(spec: dict, brief: str = "") -> str:
    brief_section = (
        f"\n\nThe following design concept brief was produced and confirmed before coding:\n"
        f"<brief>\n{brief}\n</brief>\n\n"
        "Build the script to match this confirmed concept exactly."
        if brief else ""
    )
    return f"""Generate a CadQuery script for this 3D printing spec:

<spec>
{json.dumps(spec, indent=2)}
</spec>
{brief_section}
Requirements:
- Object: {spec.get('object')}
- Material: {spec.get('material', 'PETG')} on {spec.get('printer', 'unknown printer')}
- Honor every item in the constraints list.
- Use ONLY values from dimensions_known — zero invented dimensions.
- Follow the Script Template from the skill guide (PARAMETERS / MODEL / EXPORT sections).
- Export to a plain filename (no path prefix) so it lands in the outputs/ directory.
- Print bounding box (X/Y/Z mm) and volume at the end.

Output a single ```python block with the complete script.
"""


def _fix_message(run_result: dict, attempt: int) -> str:
    stderr = (run_result.get("stderr") or "")[:3000]
    stdout = (run_result.get("stdout") or "")[:500]
    return f"""Attempt {attempt} failed. Fix the script.

stderr:
{stderr}

stdout:
{stdout}

Return the COMPLETE corrected script in a single ```python block. Do not truncate.
"""


def design_loop(
    client: anthropic.Anthropic, spec: dict, cad_skill: str, brief: str = ""
) -> tuple[str, dict]:
    """
    Multi-turn conversation: generate → save → run → fix → repeat.
    Returns (final_script_content, run_result).
    """
    system = _system_prompt(cad_skill)
    messages: list[dict] = [{"role": "user", "content": _initial_message(spec, brief)}]

    last_script = ""
    last_result: dict = {}

    for attempt in range(1, MAX_ITER + 1):
        print(f"\n[Agent] Design attempt {attempt}/{MAX_ITER}...")

        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=system,
            messages=messages,
        )
        text = response.content[0].text
        messages.append({"role": "assistant", "content": text})

        script = extract_python(text)
        if not script:
            print("[Agent] No Python block found — asking model to retry.")
            messages.append({"role": "user", "content":
                "I need a complete CadQuery script inside a ```python block. "
                "Please output the full script."})
            continue

        last_script = script
        save_script(script)

        result = run_script(preview=False, strict=True)
        last_result = result

        if result.get("success"):
            print(f"[Agent] Script succeeded on attempt {attempt}.")
            # Optionally render preview (requires pyrender; skip gracefully if unavailable)
            preview_result = run_script(preview=True, strict=True)
            if preview_result.get("success"):
                return last_script, preview_result
            print("[Agent] Preview render skipped (pyrender likely not installed).")
            return last_script, result

        error_snippet = (result.get("stderr") or "")[:400]
        print(f"[Agent] Script failed:\n{error_snippet}")
        messages.append({"role": "user", "content": _fix_message(result, attempt)})

    print(f"\n[Agent] Could not produce a valid script after {MAX_ITER} attempts.")
    return last_script, last_result


# ── Phase 2: Design review ─────────────────────────────────────────────────────

def _review_prompt(spec: dict, script: str, run_result: dict, design_review: str) -> str:
    stl = run_result.get("stl") or "(not produced)"
    watertight = run_result.get("watertight")
    stdout = (run_result.get("stdout") or "")[:1000]
    return f"""Review this CadQuery script and its run result against the design spec.

<design_review_guide>
{design_review}
</design_review_guide>

<spec>
{json.dumps(spec, indent=2)}
</spec>

<script>
{script}
</script>

<run_result>
STL: {stl}
Watertight: {watertight}
stdout:
{stdout}
</run_result>

Check EVERY constraint from the spec's constraints list.
Also check printability: overhangs, minimum wall thickness (>= 1.2mm), flat print surface,
and whether supports are required.

Format your response exactly as follows (use these exact section headers):

CONSTRAINTS:
- [exact constraint text from spec]: PASS / FAIL / UNCERTAIN — <reason if not PASS>

PRINTABILITY:
- <finding>

PRINT_SETTINGS:
Material: <material>
Layer height: <e.g., 0.2mm>
Walls: <count>
Infill: <% and pattern>
Supports: <yes/no and why>
Orientation: <which face on bed>

VERDICT: APPROVED / NEEDS_REVISION
REVISION_NOTES: <if NEEDS_REVISION: specific changes required in the script; else "None">
"""


def review_design(
    client: anthropic.Anthropic,
    spec: dict,
    script: str,
    run_result: dict,
    design_review: str,
) -> str:
    print("\n[Agent] Running design review against spec constraints...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content":
            _review_prompt(spec, script, run_result, design_review)}],
    )
    return response.content[0].text


def _parse_verdict(review_text: str) -> tuple[str, str]:
    """Return (verdict, revision_notes) parsed from review output."""
    verdict = "APPROVED"
    notes = ""
    for line in review_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("VERDICT:"):
            verdict = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("REVISION_NOTES:"):
            notes = stripped.split(":", 1)[1].strip()
    return verdict, notes


# ── Phase 3: Post-review fix (single attempt) ──────────────────────────────────

def revision_fix(
    client: anthropic.Anthropic,
    spec: dict,
    script: str,
    revision_notes: str,
    cad_skill: str,
) -> tuple[str, dict]:
    """One targeted fix pass after a NEEDS_REVISION review verdict."""
    print(f"\n[Agent] Applying review revision: {revision_notes[:150]}")
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=_system_prompt(cad_skill),
        messages=[
            {"role": "user", "content": _initial_message(spec)},
            {"role": "assistant", "content": f"```python\n{script}\n```\nNOTES: initial version"},
            {"role": "user", "content":
                f"The design reviewer found these issues that must be fixed:\n\n"
                f"{revision_notes}\n\n"
                "Return the COMPLETE corrected script in a single ```python block."},
        ],
    )
    text = response.content[0].text
    fixed = extract_python(text)
    if not fixed:
        print("[Agent] No script in revision response — keeping original.")
        return script, {}

    save_script(fixed)
    result = run_script(preview=False, strict=True)
    if result.get("success"):
        print("[Agent] Revision validated successfully.")
        preview = run_script(preview=True, strict=True)
        return fixed, preview if preview.get("success") else result
    else:
        print("[Agent] Revision failed to compile — restoring original validated script.")
        save_script(script)
        return script, {}


# ── Delivery ───────────────────────────────────────────────────────────────────

def save_review(review_text: str) -> None:
    REVIEW_PATH.parent.mkdir(exist_ok=True)
    REVIEW_PATH.write_text(review_text)


def deliver(spec: dict, run_result: dict, review_text: str) -> None:
    stl = run_result.get("stl") or "(not produced)"
    preview = run_result.get("preview")
    verdict, _ = _parse_verdict(review_text)

    print("\n" + "=" * 60)
    print("  DESIGNER AGENT — DELIVERY")
    print("=" * 60)
    print(f"  Object  : {spec.get('object')}")
    print(f"  Script  : {MODEL_SCRIPT_PATH}")
    print(f"  STL     : {stl}")
    if preview:
        print(f"  Preview : {preview}")
    print(f"  Verdict : {verdict}")
    print()
    print(review_text)
    print("=" * 60 + "\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def run() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY before running.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("\n" + "=" * 60)
    print("  3D PRINT DESIGNER / REVIEWER AGENT")
    print("=" * 60 + "\n")

    spec = load_spec()
    print(f"[Agent] Spec: {spec.get('object')}")

    if spec.get("dimensions_needed"):
        print("ERROR: Spec still has unresolved dimensions. Run the reference agent first.")
        print("  Missing:", ", ".join(spec["dimensions_needed"]))
        sys.exit(1)

    dims = spec.get("dimensions_known", {})
    if not dims:
        print("ERROR: dimensions_known is empty. Did the reference agent complete successfully?")
        sys.exit(1)

    print(f"[Agent] {len(dims)} dimensions loaded: "
          + ", ".join(f"{k}={v}mm" for k, v in list(dims.items())[:4])
          + ("..." if len(dims) > 4 else ""))

    cad_skill = load_text("skills/cad_skill.md")
    design_review = load_text("skills/design-review.md")

    # Phase 0: Design concept brief (Sonnet) — surface to UI before Opus begins
    brief = brief_phase(client, spec)

    # Phase 1: Generate + validate
    script, run_result = design_loop(client, spec, cad_skill, brief=brief)

    if not run_result.get("success"):
        print("\n[Agent] FAILED: Could not produce a valid model.")
        print("  Last error:", (run_result.get("stderr") or "")[:600])
        sys.exit(1)

    # Phase 2: Self-review
    review_text = review_design(client, spec, script, run_result, design_review)
    verdict, revision_notes = _parse_verdict(review_text)
    print(f"\n[Agent] Review verdict: {verdict}")

    # Phase 3: One revision pass if needed
    if verdict == "NEEDS_REVISION" and revision_notes and revision_notes != "None":
        revised_script, revised_result = revision_fix(
            client, spec, script, revision_notes, cad_skill
        )
        if revised_result.get("success"):
            script = revised_script
            run_result = revised_result
            # Re-review after revision
            review_text = review_design(client, spec, script, run_result, design_review)

    # Phase 4: Deliver
    save_review(review_text)
    deliver(spec, run_result, review_text)


if __name__ == "__main__":
    run()
