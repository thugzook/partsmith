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
Before executing a step, read the corresponding skill file listed below. The skill
files are the single source of truth for **how** each step works — CLAUDE.md
describes **when** to run each step. If the two ever conflict, the skill file wins.

| Step | Source of truth to read first |
|------|-------------------------------|
| Intent | `skills/cad_skill.md` (Requirements Gathering) + Step 1 below |
| Dimensions + Assets | Step 2 below + `skills/cad_skill.md` |
| Design | `skills/cad_skill.md` (Script Template, Key Rules) + `skills/mating_proxies.py` |
| Review | `skills/design-review.md` (incl. §0 The Board) |
| Log | Step 6 below (self-contained) |

Read the relevant section before proceeding, then execute the step natively with
your tools (Read, Edit, Bash).

---

## Modify, don't rebuild
When the user asks to **modify / extend / shrink / thin / tilt / tweak** an
existing model, EDIT the existing `model.py` surgically — change the named
parameters or add one feature block, and leave everything else byte-for-byte.
Do **not** regenerate the script from scratch: a rewrite silently drops working
features and reintroduces solved bugs (this is how a liked v1b lost its scallop
and gained a junk front lip). Rebuild from scratch only for a genuinely new object,
or when the user explicitly asks for one.

## The Board (design = lead → designer → reviewer)
Every model is reviewed by an **independent `cad-reviewer` agent before the user
sees it** — the builder is biased to declare success. The lead (you) holds the
intent and decides; spawn a separate `cad-designer` agent for new objects / major
redesigns, but for a small *modify* edit the lead designs and still runs the
reviewer. The full loop and the `review_render.py` helper live in
`skills/design-review.md` §0. **Honesty rule:** never describe a feature as present
unless you or the reviewer can point to it in the actual render — "gate green" ≠
"looks right"; if you can't verify it, say so.

**Models per role:** orchestrator = Opus (the lead's runtime model — it does the
judgment + the small modify-edits), designer = Opus (geometry correctness is the
highest-stakes reasoning), reviewer = Sonnet (renders/looks/measures on every
change; a cheaper, independent second opinion). Set in each agent's frontmatter;
the orchestrator's model is whatever you launch Claude Code on.

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
- `interaction_model` — how the object meets the thing it serves: one of `drops-in` / `sits-on` / `clips-on` / `screws-on` / `freestanding`. (Getting this wrong = full redesign, not a tweak — see [[feedback_design_concept_first]].)
- `critical_measurements` — the functional dimensions that actually define success, each with an **explicit datum** (see the datum rule below). List of objects:
  ```json
  {
    "name": "bowl_rest_height",
    "from": "print bed (z=0)",
    "to": "underside of bowl where it rests on the socket floor",
    "value_mm": 101.6, "tolerance_mm": 3.0,
    "source": "user: 'bowl sits 4 inches above the ground'"
  }
  ```
- `dimensions_needed` — list of dimension names not yet known
- `dimensions_known` — empty dict until Step 2
- `assets_needed` — list of reference asset descriptions not yet sourced (e.g. `"cartoon dog bone silhouette SVG"`)
- `assets_resolved` — empty dict until Step 2; filled with `{name: {file, format, geometry_notes}}`
- `reference_context` — any notes on products, images, or estimates

**Datum rule (mandatory).** Any measurement the user states with a vague reference — "4 inches above the ground", "X tall", "from the top", "high" — MUST be rewritten as `from` → `to` points before saving. If the target point is ambiguous (bowl *bottom*? food *surface*? *rim*?), **ask** — do not guess. State the value in mm **and** inches per [[feedback_units]]. Never store the raw phrase as the spec value (this is exactly how the bowl riser shipped at the wrong height — the "4 inches" mapped to nothing physical). Every entry in `critical_measurements` becomes an automated PASS/FAIL gate in Step 4, so name them the same as the variables the model will report.

**Touch point:** Present the spec — including `interaction_model` and every `critical_measurement` with its from→to datum — and explicitly ask the user to confirm or correct it before moving to Step 2.

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

**3. Functional Fit Math** — a table that works out, *numerically and before any code*, how every `critical_measurement` and mating interface resolves. Each row is `formula → result → target → ✓/✗`. This is the cheapest gate: if the math misses the target here, the design is wrong before a single solid is built. Examples mapping to past failures:
```
Quantity          | Formula                              | Result   | Target       | ?
------------------|--------------------------------------|----------|--------------|----
bowl_rest_height  | flare_h + pedestal_h + transition_h  | 113.9 mm | 101.6 ±3 mm  | ✗  ← fix before coding
socket_clearance  | socket_id − bowl_od                  | 4.7 mm   | ≥ 2.0 mm     | ✓
puck_protrusion   | puck_thk − recess_depth              | -1.4 mm  | > 0 mm       | ✗  ← MagSafe air gap
tip_margin (CoG)  | cog_x within base footprint          | yes      | inside       | ✓
cable_slot        | connector_w + clearance              | 6.75 mm  | ≥ measured   | ✓
```

**4. Assembled-state sketch requirement** — the ASCII sketch in part 2 MUST include the mating part (the bowl, the phone) drawn in place and a datum line at each target height. You are designing the *assembly*, not the lone part. Restate, in one line, where the mating part ends up (e.g. "bowl underside rests at 101.6 mm; rim at ~157 mm").

**5. Stated Assumptions** — anything not explicitly in the spec that the design will assume (angles, proportions, feature placement).

**6. Open Questions** — flag anything still ambiguous before committing to code.

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

1. Write the complete CadQuery script to `outputs/<slug>/v<N>/model.py`. It **must** end with a `# === VERIFICATION ===` block (see the Script Template in `skills/cad_skill.md`) that:
   - builds a proxy of the mating part in its functional position using `skills/mating_proxies.py` (`bowl_proxy`, `phone_proxy`, `box_proxy`, …) and exports it via `mp.export_proxy(...)` to `<name>__proxy.stl`;
   - computes every `critical_measurement` on the real geometry plus fit clearances;
   - calls `mp.emit_measurements({...})` to print the `MEASUREMENTS_JSON:` line, using the same names as the spec's `critical_measurements`;
   - prints a quick `CHECK <name>: PASS/FAIL` self-check but does **not** hard-abort — always let the build finish so the assembled preview renders even when a value is off (the authoritative gate is `verify_spec.py` in Step 4).
2. Run it — always with `--preview` and pass `--spec` so the build and the numeric gate happen in **one command**:
   ```bash
   .venv/bin/python3 run_cadquery_model.py outputs/<slug>/v<N>/model.py --preview --strict --spec outputs/<slug>/intent_spec.json
   ```
   The preview shows the mating part translucent over the model with each measurement's **actual/target + PASS/FAIL** in the footer; the JSON result carries `measurements` and `gate` (`gate.passed` + per-measurement results).
3. If `success` is false, read `stderr`, fix the geometry, re-run. Repeat until success (a build failure here means invalid geometry, not a wrong dimension — that is caught by the gate in Step 4).
4. **Touch point:** Show the user the assembled preview and the measured `critical_measurements` (mm + inches). Ask if anything needs to change before running the design review.

All dimensions must come from `dimensions_known` — never invent values.

**Mid-design asset rule:** If at any point during Step 3 (including iteration) you realise
a new external asset is needed — an icon, silhouette, texture, or reference image —
**stop immediately**. Add the description to `assets_needed`, re-run Step 2 (Reference
Resolution) for that asset only, and wait for `assets_resolved` to be populated before
writing or continuing the CadQuery script. Never approximate or invent a shape that
should come from a sourced reference.

### Step 4 — Design Review
Read `skills/design-review.md` (start with §0, **The Board**). The review is
**assertion-first** (numeric gate) **and** independently reviewed: after the gate,
render big with `review_render.py` and spawn the **`cad-reviewer` agent** to judge
the actual render against the reference + intent before you present anything. The
gate and watertightness are the entry ticket to review, not a pass. Apply the
**honesty rule** — claim a feature only if it's visible in the render.

1. **Numeric gate (must pass to approve).** If you ran Step 3 with `--spec`, the gate is already in the JSON (`gate.passed`) and the preview footer. To re-check without rebuilding:
   ```bash
   .venv/bin/python3 verify_spec.py outputs/<slug>/intent_spec.json --run outputs/<slug>/v<N>/model.py
   ```
   Every `critical_measurement` must report PASS (actual within target ± tolerance, shown in mm + inches; supports `eq`/`min`/`max` comparisons). The spec is the source of truth, so this also catches a script that "passed" by asserting against a stale constant. **Any FAIL ⇒ verdict is NEEDS_REVISION** — no amount of qualitative review overrides it.
2. **Qualitative checks** (on top of the gate):
   - Every constraint from the spec (PASS / FAIL / UNCERTAIN)
   - Printability: overhangs, wall thickness ≥ 1.2mm, flat print surface, supports needed
   - Dimensional correctness: bounding box matches spec
   - Assembled state: the proxy in the preview rests where intended

Fix and re-run if anything fails. Produce a final verdict: APPROVED (gate green + qualitative clean) or NEEDS_REVISION.

**Batch-tuning rule (fewer revisions).** Before writing `v(N+1)`, list **all** known issues and fix them in one pass — never ship a single-parameter nudge while other functional checks are still pending (this is what turned the phone stand into 11 versions). Keep "functional must-pass" (the numeric gate) separate from "aesthetic": an aesthetic-only change must still re-pass the full gate before delivery.

**Touch point:** Present the full review (numeric gate table + constraint results + printability + verdict). If NEEDS_REVISION, explain the complete set of changes and get user sign-off before applying fixes.

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

Write this entry yourself — the orchestrator (lead) owns logging; append the JSON
directly. Sub-agents (designer/reviewer) do not log.

---

## Skipping Steps
- If `outputs/<slug>/intent_spec.json` exists and `dimensions_needed` is empty AND `assets_needed` is empty → skip Steps 1–2
- If any `v<N>` folder exists under `outputs/<slug>/` → ask the user whether to reuse the
  latest version or create a new `v(N+1)` before running Step 3

## Key Files
| File | Purpose |
|------|---------|
| `skills/cad_skill.md` | CadQuery patterns, rules, Script Template (incl. VERIFICATION block) |
| `skills/design-review.md` | Visual inspection checklist, printability analysis, assembled-state checks |
| `skills/mating_proxies.py` | Reusable mating-part proxies (bowl/phone/box) + fit helpers + `emit_measurements` |
| `run_cadquery_model.py` | Runs a CadQuery script, returns JSON result (incl. parsed `measurements`) |
| `verify_spec.py` | Numeric gate: PASS/FAIL of measured values vs spec `critical_measurements` |
| `preview.py` | Renders multi-view previews (mating part translucent, measurements in footer) |
| `outputs/<slug>/intent_spec.json` | Per-object spec (shared across all versions) |
| `outputs/<slug>/v<N>/<name>__proxy.stl` | Proxy of the mating part — preview/verification only, never printed |
| `outputs/<slug>/assets/<name>.svg` | Reference SVG sourced in Step 2 (Reference Resolution) |
| `outputs/<slug>/assets/<name>_polygon.json` | Fallback polygon outline (if no SVG) |
| `outputs/<slug>/v<N>/model.py` | Generated CadQuery script (versioned) |
| `outputs/<slug>/v<N>/<name>.stl` | STL output (versioned, never overwritten) |
| `outputs/<slug>/v<N>/<name>_preview.png` | Preview image (versioned) |
| `outputs/session_log.json` | Append-only log of all completed sessions |
| `.claude/agents/cad-designer.md` | Designer subagent (new builds / major redesigns) |
| `.claude/agents/cad-reviewer.md` | Independent reviewer subagent (runs before delivery) |
| `review_render.py` | Big single-subject + reference side-by-side render for review |
