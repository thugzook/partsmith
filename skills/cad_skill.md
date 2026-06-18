# Parametric 3D Printing with CadQuery

## Overview

This skill generates parametric 3D models using **CadQuery** (Python) and exports them as STL files ready for slicing. CadQuery is preferred because it installs via pip, has a Pythonic API, and handles complex geometry (fillets, chamfers, booleans, assemblies) better than alternatives.

## Setup

```bash
# CadQuery requires Python 3.10-3.12 (OCC kernel lacks 3.13+ wheels)
python3.12 -m venv .venv && source .venv/bin/activate

# Install CadQuery and preview dependencies
pip install cadquery trimesh pyrender Pillow
```

CadQuery uses the OpenCASCADE kernel under the hood. trimesh, pyrender, and Pillow are used for the preview-analyze-iterate loop. No display server is needed; everything renders headlessly via pyrender's offscreen backend.

**If CadQuery fails to install** (OCC kernel build errors), try:
```bash
# Option 1: Use conda (CadQuery's officially recommended method)
conda install -c cadquery -c conda-forge cadquery

# Option 2: Use the pre-built wheels
pip install cadquery --find-links https://github.com/CadQuery/CadQuery/releases
```

## Real-World Dimension Research

When designing objects that interface with real products (phones, chargers, PCBs, connectors, etc.), **use web search to find accurate dimensions** before writing any geometry code. Don't guess or use approximate values. Even 1-2mm off can make a part unusable.

**What to research:**
- Connector/port dimensions (USB-C: 8.4 x 2.6mm opening, Lightning, barrel jacks)
- Device dimensions (phone width/thickness, PCB footprints, charger puck diameters)
- Mounting hole patterns and screw sizes (M2.5, M3, etc.)
- Standard component specs (MagSafe puck: 56mm diameter, 5.6mm thick)
- Cable bend radii and strain relief requirements

**How to use it:**
1. Search for "[product] dimensions mm" or "[component] datasheet"
2. Cross-reference at least 2 sources when precision matters
3. Add the sourced dimensions as comments in the PARAMETERS section:
   ```python
   # MagSafe puck dimensions (source: Apple spec + iFixit teardown)
   puck_diameter = 56.0    # mm
   puck_thickness = 5.6    # mm
   ```
4. When in doubt, add 0.3-0.5mm clearance to external dimensions

This is especially important for: phone cases/stands, charger mounts, PCB enclosures, cable management, adapter fittings, and anything that clips onto or wraps around an existing product.

## Core Workflow

1. **Gather requirements** (see Requirements Gathering below)
2. **Research dimensions** of any real-world products involved (see above)
3. **Phase 1, Base shape**: Build outer shell, preview, get user feedback
4. **Phase 2, Features**: Add functional details, preview, get user feedback
5. **Phase 3, Final delivery**: Fillets, cleanup, final preview + STL + print recommendations
6. **Offer parameter tweaks** after delivery

This is a **collaborative, show-as-you-go** process. Do NOT disappear and come back with a finished model. Show the user your progress at each phase and incorporate their feedback before moving on.

## Requirements Gathering

Before writing any code, walk through these topics with the user **conversationally**. Don't dump all questions at once. Ask the most important ones first, then follow up based on answers. Use reasonable defaults when the user doesn't specify.

**What is it?**
Object type, purpose, what it holds/protects/attaches to. Get a clear mental model of the object before anything else.

**Critical dimensions**
Must-fit measurements, like PCB size, phone width, screw spacing, diameter of the thing it wraps around, etc. These are non-negotiable and drive everything else.

**Critical measurements & datums (do this before any geometry)**
Any measurement the user states with a vague reference — "4 inches above the ground", "X tall", "from the top", "high" — is ambiguous and must be pinned to an explicit **datum** before it goes in the spec: *from what point, to what point?* "Sits 4 inches above the ground" could mean the bowl's **bottom**, the **food surface**, or the **rim** — three different models. Ask; never guess. Record each as a `critical_measurement` (`name`, `from`, `to`, `value_mm`, `tolerance_mm`, `source`) and capture the `interaction_model` (`drops-in` / `sits-on` / `clips-on` / `screws-on` / `freestanding`). These names become the keys the model reports and `verify_spec.py` gates on, so choose them to match the variables you'll compute. State values in mm **and** inches.

**Mounting & attachment**
How does it connect to things? Screws (what size?), snap-fit, adhesive tape, magnets, freestanding on a desk? This affects wall thickness, boss placement, and overall structure.

**Printer & material**
What printer do they have? (Bambu, Prusa, Ender, etc.) Nozzle size? Material (PLA, PETG, TPU)? This directly affects tolerances, minimum feature sizes, and design constraints. Defaults: 0.4mm nozzle, PLA, 0.2mm layer height.

**Functional needs**
Ventilation/airflow, water resistance, cable routing, access panels, visibility windows, stacking, weight limits. Ask only what's relevant to the object.

**Aesthetic preferences**
Rounded vs sharp edges, minimal vs industrial look, color considerations (affects visibility of layer lines). Ask briefly. Most users care more about function than form.

Start with the first two (what + dimensions), then ask about mounting and material if relevant. Only ask about aesthetics if the user seems to care or if it affects structural choices.

## Progressive Preview Workflow

Build the model in phases. At each phase, export an STL, render a preview, self-review it, then **show it to the user and ask for feedback** before proceeding. This catches problems early and keeps the user involved.

### Preview recipe (use at every phase)

**One-shot (run script + render + gate + parse result as JSON):**
```bash
python3 run_cadquery_model.py model.py --preview --strict --spec intent_spec.json
```
This executes `model.py`, finds the STL it wrote, renders the multi-view preview (mating part translucent + measurements footer), runs the numeric gate from `--spec`, and emits a JSON result with `success`, `stdout`, `stderr`, `stl`, `preview`, `watertight`, `measurements`, and `gate` (`gate.passed` + per-measurement PASS/FAIL). With `--strict`, a non-watertight mesh is a hard failure. Use this as the default loop: if `success` is false, read the `stderr` field to fix the script; if `gate.passed` is false, fix the geometry so the failing measurement hits its target. (`--spec` is optional — omit it for quick geometry-only iteration.)

**Rendering only (when the STL already exists):**
```bash
python3 preview.py model.stl preview.png --views multi
```

Then view the preview image, self-review it against the checklist in `design-review.md`, and fix any issues you spot **before** showing it to the user.

---

### Phase 1: Base Shape

Build the basic outer form: overall dimensions, shell/walls, bottom plate. No cutouts, no fillets, no details yet.

1. Write the script with parameters and basic geometry
2. Export STL and render preview
3. Self-review: Does the shape and size look right? Is the bottom flat for printing?
4. **Show the preview to the user**: "Here's the basic shape. Does this look right before I add details?" Include key dimensions.
5. Wait for feedback. If the user wants changes, iterate here before moving on.

### Phase 2: Features

Add functional details: holes, cutouts, mounting bosses, cable slots, ventilation, snap-fits, internal structures.

1. Add features to the script
2. Export STL and render preview
3. Self-review: Are all features visible? Do booleans look clean? Are holes in the right positions?
4. **Show the preview to the user**: "I've added [list features]. Anything to change before I finalize?"
5. Wait for feedback. Iterate if needed.

### Phase 3: Final Delivery

Apply finishing touches: fillets, chamfers, edge cleanup. Do a full printability review.

1. Add fillets/chamfers (largest radius first, apply after shell)
2. Export final STL and render preview
3. **Full self-review** using the complete checklist from `design-review.md`: visual inspection, dimensional verification, printability analysis
4. Fix any issues found, re-export if needed
5. **Deliver to the user**: final STL + preview image + print recommendations (orientation, supports, infill, material notes)

---

**Important:** Do NOT skip phases or combine them unless the model is very simple (e.g., a flat bracket with two holes). For anything with enclosed geometry, multiple features, or tight tolerances, follow all three phases.

Read `design-review.md` for the full visual inspection checklist, dimensional verification code, and printability analysis helpers.

### Print Recommendations (final delivery)

When you deliver the final STL, always include a one-line slicer recipe plus a short rationale. Bambu Studio, PrusaSlicer, and OrcaSlicer already set sensible defaults from their filament + process presets, so **do not restate every slicer option**. Only tell the user what matters for *this* model: material, layer height, walls, infill, supports, and orientation. Tweak from the baseline below only when the model needs it.

**Baseline recipe (0.4mm nozzle, typical FDM):**
> PLA, 0.2mm layer, 2 walls, 15% gyroid infill, no supports, orientation: flat side on bed.

**When to deviate from the baseline:**
- **Load-bearing brackets / hooks / hinges**: bump infill to 25-40%, 3-4 walls, consider PETG over PLA for toughness.
- **Thin decorative walls or vases**: 0 infill, vase mode or 1 wall.
- **Tall narrow parts**: add a brim for bed adhesion.
- **Flexible parts (gaskets, grips)**: TPU 95A, 0.2mm layer, slower speed, no supports.
- **Functional overhangs the geometry can't avoid**: tree supports, or call them out so the user knows.
- **Outdoor / hot environments**: PETG or ASA, not PLA.
- **Food / skin contact**: call out that FDM parts are not food-safe and recommend a food-safe coating.

**Format at delivery time:**
```
Print settings: PLA, 0.2mm layer, 2 walls, 15% gyroid infill, no supports.
Orientation: place flat back side on the bed (front face up).
Why: the case has no overhangs above 45°, and 15% infill is plenty for a
TPU-adjacent protective shell.
```

Keep it to ~3 lines. Never dump every slicer setting; the slicer already knows.

## Script Template

ALWAYS structure scripts like this:

```python
import cadquery as cq
import os, sys
# Make the shared helpers importable from outputs/<slug>/vN/model.py (root is 3 up).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "skills"))
import mating_proxies as mp

# ============================================================
# PARAMETERS - Edit these to customize the model
# ============================================================
# Overall dimensions
width = 60.0        # mm - outer width
depth = 40.0        # mm - outer depth  
height = 25.0       # mm - outer height

# Wall and structural
wall = 2.0          # mm - wall thickness (min 1.2 for FDM)
corner_r = 2.0      # mm - corner fillet radius

# Tolerances
fit_clearance = 0.3 # mm - clearance for press-fit (adjust per printer)

# ============================================================
# MODEL
# ============================================================
result = (
    cq.Workplane("XY")
    .box(width, depth, height, centered=(True, True, False))
    # ... build geometry using parameters above (bottom at Z=0)
)

# ============================================================
# EXPORT
# ============================================================
# Use tolerance=0.01, angularTolerance=0.1 for consistent tessellation
# across models. Defaults give coarser, wildly variable STL sizes.
STL = "output.stl"
cq.exporters.export(result, STL, tolerance=0.01, angularTolerance=0.1)

# ============================================================
# VERIFICATION — assembled state + numeric gate (MANDATORY)
# ============================================================
# 1) Build a proxy of the mating part in its functional position and export it
#    so the preview shows the ASSEMBLED state. (Skip the proxy only for truly
#    freestanding objects with no mating part — still do steps 2 & 3.)
bowl, _ = mp.bowl_proxy(base_od_mm=bowl_od, depth_mm=bowl_depth,
                        rest_z_mm=socket_floor_z)
mp.export_proxy(bowl, STL)          # writes output__proxy.stl (never printed)

# 2) Compute every critical_measurement on the REAL geometry, not on assumptions.
#    Use the SAME names as the spec's critical_measurements.
bb = result.val().BoundingBox()
mp.emit_measurements({
    "bowl_rest_height": socket_floor_z,
    "socket_clearance": mp.clearance(socket_id, bowl_od),
    "bbox": [bb.xlen, bb.ylen, bb.zlen],
})                                  # prints: MEASUREMENTS_JSON: {...}

# 3) Print a quick self-check, but do NOT hard-abort on a mismatch — always let
#    the build finish so the assembled preview renders even when a value is off
#    (that picture is how you debug it). The authoritative gate is verify_spec.py
#    in Step 4.
ok = abs(socket_floor_z - 101.6) <= 3.0
print(f"CHECK bowl_rest_height: {'PASS' if ok else 'FAIL'} "
      f"({socket_floor_z:.1f} mm vs 101.6 +/- 3 mm)")
print(f"Exported: {bb.xlen:.0f}x{bb.ylen:.0f}x{bb.zlen:.0f}mm")
```

The VERIFICATION block is what turns "looks right" into "is right". `verify_spec.py`
re-checks the emitted `MEASUREMENTS_JSON` against the spec (the source of truth), so
the names must match the spec's `critical_measurements`. See `skills/mating_proxies.py`
for the full proxy + fit-helper API (`bowl_proxy`, `phone_proxy`, `box_proxy`,
`cylinder_proxy`, `clearance`, `puck_protrusion`, `combine_cog`, `cog_within_footprint`).

## Design Brief (mandatory before writing CadQuery)

Before writing any CadQuery script, produce a **Design Concept Brief** and get confirmation. This prevents committing to the wrong form factor before any code runs.

The brief must include:

1. **Component inventory** — a table of every solid body with name, rough W×D×H, and role.
2. **ASCII side-view or top-view sketch** — a plain-text cross-section showing how components relate spatially. It **must include the mating part** (bowl, phone, …) drawn in place with a datum line at each target height — you are designing the assembly, not the lone part.
3. **Functional Fit Math** — a `formula → result → target → ✓/✗` table proving every `critical_measurement` and interface resolves correctly *before any code runs*. If a row fails here, fix the design now; this is the cheapest possible gate:
   ```
   Quantity          | Formula                              | Result   | Target       | ?
   ------------------|--------------------------------------|----------|--------------|----
   bowl_rest_height  | flare_h + pedestal_h + transition_h  | 113.9 mm | 101.6 ±3 mm  | ✗
   socket_clearance  | socket_id − bowl_od                  | 4.7 mm   | ≥ 2.0 mm     | ✓
   puck_protrusion   | puck_thk − recess_depth              | -1.4 mm  | > 0 mm       | ✗
   ```
4. **Stated assumptions** — anything the design will assume that isn't explicit in the spec (angles, proportions, feature placement).
5. **Open questions** — anything still ambiguous that should be resolved before writing code.

Present the brief and wait for user confirmation before writing any CadQuery. If running end-to-end (autonomous mode), include the brief in output and note it was auto-confirmed.

---

## Key Rules

### Parameters First
- ALL dimensions go in the PARAMETERS section at the top
- Use descriptive names: `screw_hole_d`, not `d1`
- Add units in comments (always mm)
- Group related parameters with blank lines and section comments

### Print-Friendly Defaults
Key FDM design defaults:

| Property | Minimum | Recommended |
|----------|---------|-------------|
| Wall thickness | 1.2mm | 2.0mm |
| Layer height | 0.08mm | 0.2mm |
| Hole clearance | 0.2mm | 0.3mm |
| Press-fit interference | 0.1mm | 0.15mm |
| Min feature size | 0.4mm (nozzle) | 0.8mm |
| Fillet radius (bottom) | 0.5mm | 1.0mm |
| Bridge span | - | < 20mm unsupported |
| Overhang angle | - | < 45 degrees from vertical |

**Material-specific adjustments:** TPU needs larger clearances (~0.5mm) due to flex. PETG is stickier, so add +0.1mm to fit clearances. ABS shrinks ~0.5-0.7%, so scale critical dimensions up slightly. When in doubt, print a small test piece first.

### Orientation Awareness
- Design with print orientation in mind
- Flat bottom surfaces print best
- Avoid supports when possible by designing around overhangs
- Add chamfers to bottom edges instead of fillets (fillets need supports)
- Comment the intended print orientation in the script

### CadQuery Patterns

Common patterns to know:

**Hollow enclosure (boolean subtraction, preferred):**
```python
outer = (
    cq.Workplane("XY")
    .box(width, depth, height, centered=(True, True, False))
    .edges("|Z").fillet(corner_r)
)
inner = (
    cq.Workplane("XY")
    .workplane(offset=floor_t)
    .box(width - 2*wall, depth - 2*wall, height, centered=(True, True, False))
    .edges("|Z").fillet(max(0.1, corner_r - wall))
)
result = outer.cut(inner)
```

**Screw boss:**
```python
.pushPoints([(x, y)])
.circle(boss_od / 2).extrude(boss_h)
.pushPoints([(x, y)])
.hole(screw_d + fit_clearance)
```

**Snap-fit clip:**
```python
# Cantilever beam with overhang hook
.workplane(offset=wall)
.moveTo(x, y).rect(clip_w, clip_l).extrude(clip_h)
# Add hook at tip with a small overhang (< 45 deg)
```

**Ventilation grid:**
```python
.pushPoints(vent_positions)
.slot2D(slot_l, slot_w).cutThruAll()
```

Other patterns: mounting brackets, cable routing channels, text/labels (`.text()`), multi-part assemblies with alignment pins.

### Common Pitfalls

- **Hollowing: prefer boolean subtraction over `.shell()`**. `.shell()` is fragile on tapered/lofted shapes, unions of multiple primitives, and anything with many fillets. The reliable pattern is boolean subtraction (see above). Only reach for `.shell()` when the body is a single simple primitive with uniform wall thickness on all sides.
- **Build order: fillet → cut, not cut → fillet**. Apply fillets while the geometry is still a clean primitive. Once you have cut holes/slots/pockets into a body, filleting the resulting edges often fails.
- **Fillet failures**: Apply fillets from largest to smallest radius. **Do not wrap fillets in `try/except` to silently shrink the radius.** A fillet failure means the geometry or the radius is wrong; find the root cause and fix it.
- **Zero-thickness geometry**: Ensure boolean operations don't create infinitely thin walls. Add a small epsilon (0.01mm) when cutting bodies that are meant to pass just through a surface.
- **Coordinate system**: CadQuery centers geometry at origin by default. Use `centered=(True, True, False)` on `.box()` to place the bottom at Z=0.
- **Hole direction**: `.hole()` cuts through the entire part by default. Use `.cboreHole()` or `.cskHole()` for counterbore/countersink.
- **Taper direction**: In `.extrude(taper=angle)`, a positive taper angle narrows the shape (draft inward), negative flares it outward.
- **Loft is fragile**: `.loft()` fails on many cross-section combinations. Prefer `.extrude(taper=angle)` when transitioning between shapes.
- **Export errors / non-watertight STL**: If the preview reports a non-watertight mesh, the geometry is invalid. Fix the cause, don't paper over it.

## Export

```python
# STL (for slicing) - always set tolerance + angularTolerance for
# consistent tessellation. Defaults produce variable file sizes.
cq.exporters.export(result, "model.stl",
                    tolerance=0.01, angularTolerance=0.1)

# STEP (for further CAD editing)
cq.exporters.export(result, "model.step")
```

Always export STL for printing. Optionally export STEP if the user might want to edit in Fusion 360 or similar.

## Multi-Part Models

For models with multiple parts (e.g., enclosure + lid):

```python
cq.exporters.export(body, "enclosure_body.stl")
cq.exporters.export(lid, "enclosure_lid.stl")
```

Name files descriptively so the user knows which part is which.

## Parameter Adjustment Offer

After delivering the final model, **always present the key parameters as a summary table** and offer to tweak them:

```
Here's your final model! Current parameters:

| Parameter       | Value  |
|----------------|--------|
| Width          | 90 mm  |
| Depth          | 65 mm  |
| Height         | 30 mm  |
| Wall thickness | 4 mm   |
| Cable slot     | 18 mm  |
| Corner radius  | 3 mm   |
| Fit clearance  | 0.3 mm |

Want me to adjust anything? Just say e.g. "make it 5mm taller" or "wider cable slot."
```

Only include parameters the user would plausibly want to change.

## Output Checklist

Before delivering a model, verify:
- [ ] All dimensions are parameterized (no magic numbers in geometry code)
- [ ] Wall thickness >= 1.2mm
- [ ] Designed for printability (minimal overhangs/supports)
- [ ] Print orientation noted in comments
- [ ] STL exported and file size is reasonable (not 0 bytes)
- [ ] Clear parameter names with units
- [ ] Script runs without errors
- [ ] **VERIFICATION block present**: mating-part proxy exported, `emit_measurements()` called, every `critical_measurement` asserted
- [ ] **Numeric gate green**: `verify_spec.py --run model.py` reports PASS for all critical measurements
- [ ] **Multi-view preview generated and visually inspected**
- [ ] **Preview shows correct shape, features, proportions, and the mating part resting where intended**
- [ ] **Bounding box dimensions match requirements**
- [ ] Both STL and preview PNG delivered to user (the `__proxy.stl` is NOT delivered)
