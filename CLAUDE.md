# 3D Print Agent — Project Instructions

## What this project does
Generates 3D-printable STL files from a conversational description, following a
structured multi-step workflow. Skills and utilities live in `skills/` and the project
root. All outputs land in `outputs/`.

## Output folder structure

Every object lives in its own versioned folder:

```
outputs/
  <object-slug>/          e.g. dog_water_bowl/
    intent_spec.json      spec for this object (all versions share one spec)
    assets/               reference assets downloaded by the Reference Agent
      <name>.svg          vector silhouette / icon
      <name>_polygon.json fallback: outline as [[x,y], ...] points
    v1/
      model.py            generated CadQuery script
      <name>.stl          STL output
      <name>_preview.png  preview image
    v2/                   created only when the user requests a redesign
      ...
  session_log.json        append-only log of all completed sessions (root level)
```

**Object slug** — snake_case, derived from `object` field in spec (e.g. "dog water bowl" → `dog_water_bowl`).

**Version** — always `v1` for a new object. When redoing an existing object, find the highest existing `vN` and use `v(N+1)`. Never overwrite previous versions.

---

## Interaction mode

**Default: interactive.** Pause at every touch point listed in the workflow steps below,
present your work, and wait for the user's confirmation or feedback before continuing.

**One-shot / autonomous mode:** Only skip touch points if the user explicitly says so in
the request (e.g. "run it end-to-end", "don't interrupt me", "do it automatically").
Even then, present the final preview + verdict before closing out.

## When to run the design workflow
When the user asks to design, build, make, model, or print a 3D object — or says
"start a new project" or "new print" — run the full workflow below from Step 1.

If the user says "continue" or `outputs/<slug>/intent_spec.json` already exists, skip to
the appropriate step based on what the spec already contains.

---

## Anti-Drift Rule
Before executing any step, read the corresponding agent file listed below. The agent
files are the single source of truth for what each step does — CLAUDE.md describes
WHEN to run each step, the agent files describe HOW. If the two ever conflict, the
agent file wins.

| Step | Agent file to read first |
|------|--------------------------|
| Intent | `agents/intent_agent.py` |
| Dimensions | `agents/reference_agent.py` |
| Design + Review | `agents/designer_reviewer.py` |
| Log | `agents/logger_agent.py` |

Read the agent's module docstring and key functions before proceeding. Mirror its
behavior natively using your tools (Read, Edit, Bash) rather than calling the API.

---

## The Workflow

### Step 1 — Intent Gathering
Gather requirements conversationally. Ask the most important question first; don't
dump all questions at once. Follow the Requirements Gathering section in
`skills/cad_skill.md`.

Determine the object slug from the user's description. Build and save `outputs/<slug>/intent_spec.json` with these fields:
- `object` — what it is
- `purpose` — what problem it solves
- `constraints` — list of hard requirements
- `material` and `printer`
- `dimensions_needed` — list of dimension names not yet known
- `dimensions_known` — empty dict until Step 2
- `assets_needed` — list of reference asset descriptions not yet sourced (e.g. `"cartoon dog bone silhouette SVG"`)
- `assets_resolved` — empty dict until Step 2; filled with `{name: {file, format, geometry_notes}}`
- `reference_context` — any notes on products, images, or estimates

**Touch point:** Present the spec and explicitly ask the user to confirm or correct it before moving to Step 2.

### Step 2 — Reference Resolution (dimensions + assets)

**Dimensions** — for every entry in `dimensions_needed`:
1. Research published product specs (named products, standard hardware sizes)
2. Present each found value and its source to the user — they confirm or correct
3. For anything requiring physical measurement, ask the user explicitly:
   *"Grab your tape measure — I need [X]"*
4. Update `dimensions_known`, clear `dimensions_needed`, set `confidence: high`

**Assets** — for every entry in `assets_needed`, follow this priority order and stop at the first successful step:

1. **Search and compare** — find 3–5 candidate SVGs/PNGs (free/CC0/public domain).
   Present each candidate to the user: name, source URL, license, and a one-line description
   of what the shape looks like. Ask the user to pick one. Do NOT auto-download anything.
2. **User upload** — if no suitable candidates exist, or user rejects all options, say:
   *"I couldn't find a clean option — can you drop a PNG or SVG here?"*
   Wait for the user to provide a file before proceeding.
3. **Generated geometry** — only if the user has explicitly said to generate it after options
   1 and 2 are exhausted. State clearly: *"I'm generating an approximation — this may not
   match a real [shape]."* Never silently fall back to generated geometry.

Once a source is chosen (step 1 or 2), save the file to `outputs/<slug>/assets/` and extract
the contour using image processing — **do not compute geometry manually**:
```python
from PIL import Image
import numpy as np
# threshold → find edge pixels → order into contour → downsample to 50–80 pts → normalize
```
This applies to any black-on-white (or high-contrast) silhouette PNG/SVG.
Record path, format, license, and geometry notes in `assets_resolved`. Clear `assets_needed`.

If the user pastes or uploads the image directly into the conversation, save it to
`outputs/<slug>/assets/` first, then run the same image-processing extraction.

**Touch point:** Present each resolved dimension and asset (with source and preview of geometry notes) to the user. Wait for confirmation before saving and moving to Step 3.

### Step 2.5 — Design Concept Brief

**Before writing any code**, produce a Design Concept Brief and get explicit user confirmation.

The brief must contain:

**1. Component Inventory** — a table of every solid body:
```
Component       | Dimensions (W × D × H)  | Notes
----------------|-------------------------|------------------------------
e.g. base slab  | 105 × 90 × 8 mm         | flat on print bed
e.g. panel      | 76 × 16 × 112 mm        | tilted 10° rearward
e.g. socket     | 55.8 mm dia, 3.5 deep   | cut into panel front face
```

**2. ASCII Side-View (or Top-View) Sketch** — a plain-text cross-section showing how components relate spatially:
```
   side view
      ┌───┐
  ╱   │ ○ │  ← panel, socket = ○
╱    │   │
      └─┬─┘
  ──────────
     base
```

**3. Stated Assumptions** — anything not explicitly in the spec that the design will assume (angles, proportions, feature placement).

**4. Open Questions** — flag anything still ambiguous before committing to code.

**Touch point:** Present the brief and **wait for the user to confirm or correct it** before writing any CadQuery. This step is mandatory in interactive mode. Only skip it if the user explicitly requested "end-to-end" or "don't interrupt me" in their original request — even then, include the brief in your output and note it was auto-confirmed.

**Scaffold Preview (optional):** For any model with 3+ components or complex spatial relationships, offer a scaffold script — plain positioned boxes/cylinders, no features or cuts, runs in ~5 seconds. Offer this when the ASCII sketch alone may not be enough to validate layout:
```bash
.venv/bin/python3 run_cadquery_model.py outputs/<slug>/v<N>/scaffold.py --preview --strict
```
Present the scaffold preview and wait for user sign-off before writing the full script.

---

### Step 3 — Design Generation
Before writing any code, check `outputs/design_lessons.json` (if it exists) for transferable lessons from past sessions that apply to this object type or design approach.

Read `skills/cad_skill.md` fully before writing any code.
Follow the Script Template and all Key Rules in that file.

Determine the version directory: check existing `vN` folders under `outputs/<slug>/`, use `v(N+1)`. For a new object this is always `v1`.

1. Write the complete CadQuery script to `outputs/<slug>/v<N>/model.py`
2. Run it — always with `--preview` so every build produces an image:
   ```bash
   .venv/bin/python3 run_cadquery_model.py outputs/<slug>/v<N>/model.py --preview --strict
   ```
3. If `success` is false, read `stderr`, fix the script, re-run. Repeat until success.
4. **Touch point:** Show the user the preview image and key dimensions. Ask if anything needs to change before running the design review.

All dimensions must come from `dimensions_known` — never invent values.

**Mid-design asset rule:** If at any point during Step 3 (including iteration) you realise
a new external asset is needed — an icon, silhouette, texture, or reference image —
**stop immediately**. Add the description to `assets_needed`, re-run Step 2 (Reference
Resolution) for that asset only, and wait for `assets_resolved` to be populated before
writing or continuing the CadQuery script. Never approximate or invent a shape that
should come from a sourced reference.

### Step 4 — Design Review
Read `skills/design-review.md`. For the generated script and run output, check:
- Every constraint from the spec (PASS / FAIL / UNCERTAIN)
- Printability: overhangs, wall thickness ≥ 1.2mm, flat print surface, supports needed
- Dimensional correctness: bounding box matches spec

Fix and re-run if any constraint fails. Produce a final verdict: APPROVED or NEEDS_REVISION.

**Touch point:** Present the full review (constraint results + printability + verdict). If NEEDS_REVISION, explain what will change and get user sign-off before applying fixes.

### Step 5 — Deliver
Report to the user:
- STL file path: `outputs/<slug>/v<N>/<name>.stl`
- Preview image path: `outputs/<slug>/v<N>/<name>_preview.png`
- Print settings: material, layer height, walls, infill, supports, orientation
- Parameters table of key values the user might want to tweak
- Any model-specific print recommendations

### Step 6 — Log
After delivery, append one entry to `outputs/session_log.json` (always at the root level, shared across all objects):

```json
{
  "timestamp": "<ISO 8601 UTC>",
  "object": "<from spec>",
  "material": "<from spec>",
  "printer": "<from spec>",
  "dimensions_known": {},
  "slug": "<object-slug>",
  "version": "v<N>",
  "stl": "outputs/<slug>/v<N>/<name>.stl",
  "preview": "outputs/<slug>/v<N>/<name>_preview.png",
  "watertight": true,
  "verdict": "APPROVED",
  "print_settings": {
    "material": "...",
    "layer_height": "...",
    "walls": "...",
    "infill": "...",
    "supports": "...",
    "orientation": "..."
  },
  "key_decisions": ["<why decision 1>", "<why decision 2>"],
  "lessons_learned": ["<what failed → what worked → why>"]
}
```

Write this entry yourself — do NOT call `logger_agent.py` in Claude Code sessions.
`logger_agent.py` is used only by the Python/UI pipeline.

---

## Skipping Steps
- If `outputs/<slug>/intent_spec.json` exists and `dimensions_needed` is empty AND `assets_needed` is empty → skip Steps 1–2
- If any `v<N>` folder exists under `outputs/<slug>/` → ask the user whether to reuse the
  latest version or create a new `v(N+1)` before running Step 3

## Key Files
| File | Purpose |
|------|---------|
| `skills/cad_skill.md` | CadQuery patterns, rules, Script Template |
| `skills/design-review.md` | Visual inspection checklist, printability analysis |
| `run_cadquery_model.py` | Runs a CadQuery script, returns JSON result |
| `preview.py` | Renders multi-view and single-view preview PNGs from trimesh |
| `outputs/<slug>/intent_spec.json` | Per-object spec (shared across all versions) |
| `outputs/<slug>/assets/<name>.svg` | Reference SVG downloaded by Reference Agent |
| `outputs/<slug>/assets/<name>_polygon.json` | Fallback polygon outline (if no SVG) |
| `outputs/<slug>/v<N>/model.py` | Generated CadQuery script (versioned) |
| `outputs/<slug>/v<N>/<name>.stl` | STL output (versioned, never overwritten) |
| `outputs/<slug>/v<N>/<name>_preview.png` | Preview image (versioned) |
| `outputs/session_log.json` | Append-only log of all completed sessions |

## Python Agents (UI Pipeline Only)
The files in `agents/` are for the Streamlit UI pipeline — they call the Anthropic API
directly and should NOT be run during Claude Code sessions. In Claude Code, you (Claude)
execute the workflow natively using your tools.

| Agent | Role |
|-------|------|
| `agents/intent_agent.py` | Conversational spec builder |
| `agents/reference_agent.py` | Dimension + asset resolver |
| `agents/designer_reviewer.py` | CadQuery generator + validator (Opus 4.8) |
| `agents/logger_agent.py` | Session summarizer (Sonnet) |
| `main.py` | Orchestrator for the full Python pipeline |
