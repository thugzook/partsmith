# Design Review & Iteration Guide

## Table of Contents
0. **The Board** — lead → designer → independent reviewer loop (read this first)
1. Preview-Analyze-Iterate Workflow
2. Visual Inspection Checklist
3. Dimensional Verification
4. Printability Analysis
5. Common Issues and Fixes

---

## 0. The Board (how review actually runs)

We design as a **board of three roles**, because the person who built the model is
biased to declare it done. This separation is what catches "looks-fine-but-wrong"
before the user ever sees it.

- **Lead / orchestrator** (you, the main session): holds the intent (the spec +
  what the user actually asked for this turn), runs the loop, and makes the final
  call. The lead never ships on its own say-so.
- **Designer**: writes/edits the CadQuery. For a brand-new object or a major
  redesign, spawn a **separate `cad-designer` agent** so the geometry gets full
  focus. For a small *modify* edit (a param change, one feature), the lead does
  the edit directly — spawning a cold agent to change one number is wasteful.
- **Reviewer**: a **separate `cad-reviewer` agent**, every time, before the user
  sees anything. Fresh eyes that judge the rendered image against the reference
  and the intent — explicitly NOT the numeric gate. It loops its findings back to
  the lead.

### The loop (runs autonomously after blueprint sign-off — the reviewer is the gate)
The user has already signed off the **2D profile blueprint** (CLAUDE.md Step 2.5). From
here the loop runs **without the user**; the `cad-reviewer`'s `VERDICT: APPROVE` is the
ship signal. The human is pulled back in only on non-convergence or an intent/topology change.

0. **Entry: blueprint is locked.** The model must have been built by **extruding the
   signed-off `v<N>/profile.py`**, not re-assembled from boxes — so the side view already
   matches the blueprint.
1. **Build** — run the model with `--preview` (and `--spec` when the spec's
   `critical_measurements` apply). Watertight + gate green is the *entry ticket*
   to review, not a pass. Then run the **intrinsic quality gate** `skills/print_checks.py`
   (printability in the declared orientation, min-wall, **finish/fillets present**,
   **lever ratio**, **economy**) — these are absolute standards that hold a clean-room
   first build to a high bar with no prior art. Any FAIL is a NEEDS_REVISION input.
2. **Render BIG** — the multi-view thumbnails are too small to judge aesthetics
   once downscaled (this is literally how a missing scallop / fake tilt / weird
   lip shipped). Use the single-subject helper, and pass a reference when one
   exists so the reviewer can diff shape-to-shape:
   ```bash
   .venv/bin/python3 review_render.py outputs/<slug>/v<N>/<name>.stl \
     --ref <reference.stl-or-3mf> --ref-scale <1.0, or 25.4 for an inch-authored 3mf> \
     --out /tmp/review_<slug>.png
   ```
3. **Independent review** — spawn the `cad-reviewer` agent (see
   `.claude/agents/cad-reviewer.md`). Give it the model path, the **blueprint PNG**, the
   reference path, the **previous approved version** (on a modify), and the intent (the
   features the design MUST have). It renders, *looks*, and returns a feature-by-feature
   **VISIBLE / MISSING / WRONG** table plus:
   - **profile match** — does the rendered side view match the signed-off blueprint? (catches
     a topology that drifted from the locked profile);
   - **regression diff** (modifies only) — compare to the previous approved version; flag any
     feature silently dropped;
   - the final `VERDICT: APPROVE | NEEDS_REVISION`.
4. **Lead fixes autonomously** — on NEEDS_REVISION the lead applies the **full batch** of
   fixes (or hands the findings to the designer) and re-enters at step 1. **Cap 3 rounds.**
   Do **not** stop for the user between rounds.
5. **Converge:**
   - **APPROVE + gate green + watertight ⇒ deliver + log automatically** (no human stop).
   - **3 rounds without APPROVE ⇒ escalate** to the user with the honest defect — never fake it.
   - **Any interaction-model / topology change or new-asset need ⇒ pause for the user**, even
     mid-loop.
   And the **honesty rule**: never describe a feature as present unless you (or the reviewer)
   can point to it in the render. "Gate green" ≠ "looks right." If you cannot verify something
   from the render, say so — do not assert it.

This governs Step 4 of the main workflow in CLAUDE.md.

---

## 1. Preview-Analyze-Iterate Workflow

After generating a model, ALWAYS run the preview-analyze-iterate loop before delivering the final STL.

### Step 1: Generate Preview

```bash
# Install dependencies (first time only)
pip install trimesh pyrender Pillow

# Generate multi-view preview
python3 preview.py model.stl preview.png --views multi
```

### Step 2: View the Preview

Read the generated PNG file to inspect it visually. The multi-view layout shows the model from 4 angles (isometric, front, top, right) with dimensions in the footer.

### Step 3: Analyze the Preview

Check against this visual inspection list:

**Shape & Proportions**
- Does the overall shape match what was requested?
- Are proportions reasonable (not too thin, not too bulky)?
- Are all requested features visible (holes, slots, cutouts)?

**Printability**
- Is there a flat bottom surface for bed adhesion?
- Are there visible overhangs > 45 degrees that need supports?
- Do thin features look thick enough to print (> 1.2mm)?
- Are screw bosses and standoffs properly connected to walls?

**Geometry Issues**
- Are there any obviously missing features?
- Do boolean operations look clean (no floating geometry)?
- Are fillets and chamfers applied to the right edges?
- Does the interior look correctly hollowed out?

**Dimensions**
- Check bounding box in the preview footer
- Compare against user's stated requirements
- Verify critical dimensions from the code match visual

### Step 4: Fix Issues

If problems are found:
1. Identify the specific issue from the preview
2. Trace it to the relevant code section
3. Fix the code
4. Re-export STL
5. Re-generate preview
6. Verify the fix

### Step 5: Deliver

Once the preview passes inspection:
1. Export final STL (and optionally STEP)
2. Generate final preview image
3. Share both the STL and preview with the user
4. Note any print recommendations (orientation, supports, infill)

---

## 2. Visual Inspection Checklist

Use this as a structured review after viewing the preview:

```
VISUAL REVIEW
=============
[ ] Overall shape matches request
[ ] All features present (holes, cutouts, mounts, etc.)
[ ] Proportions look correct
[ ] No floating or disconnected geometry
[ ] Flat bottom for printing
[ ] No extreme overhangs visible
[ ] Fillets/chamfers applied correctly
[ ] Wall thickness appears sufficient
[ ] Interior properly hollowed (if applicable)
[ ] Dimensions match requirements (check footer)
[ ] Mating part (proxy) shown in preview and rests where intended
[ ] verify_spec.py numeric gate is green (all critical_measurements PASS)
```

---

## 3. Dimensional Verification

**The numeric gate comes first — it is not optional.** A part can look perfect and
still be functionally wrong (the dog-bowl riser looked fine; the bowl sat inches
too high). Before any qualitative judgement, run the spec checker against the
model's emitted measurements:

```bash
.venv/bin/python3 verify_spec.py outputs/<slug>/intent_spec.json --run outputs/<slug>/v<N>/model.py
```

This compares each `critical_measurement` (the spec is the source of truth) to the
actual value the model reported in its `MEASUREMENTS_JSON` line, and prints a
PASS/FAIL table in mm + inches. **Any FAIL ⇒ verdict is NEEDS_REVISION**, full
stop — no qualitative review overrides the gate. Because the spec drives the
comparison, this also catches a script that "passed" by asserting against a stale
local constant.

Beyond the gate, the model's own VERIFICATION block emits these measurements and
prints a `CHECK <name>: PASS/FAIL` self-check (without aborting, so the assembled
preview always renders). The bounding-box pattern below is the in-script idiom
used inside that block:

```python
import cadquery as cq

# After building the model:
bb = result.val().BoundingBox()
print(f"Width  (X): {bb.xlen:.1f} mm")
print(f"Depth  (Y): {bb.ylen:.1f} mm") 
print(f"Height (Z): {bb.zlen:.1f} mm")
print(f"Volume: {result.val().Volume():.0f} mm³")

# Check specific dimensions
assert abs(bb.xlen - expected_width) < 0.1, f"Width mismatch: {bb.xlen} vs {expected_width}"
```

### Key Checks
- Bounding box matches expected outer dimensions
- Volume is reasonable (not suspiciously small or large)
- Volume > 0 confirms the model is a valid solid
- Compare volume with/without shell to verify wall thickness

### Watertight Check

Non-watertight meshes are the most common cause of slicing failures. Always verify:

```python
import trimesh

tm = trimesh.load("model.stl", force="mesh")
if tm.is_watertight:
    print("Mesh is watertight (good)")
else:
    print("WARNING: Mesh is NOT watertight — will cause slicing issues")
    # Common causes: unclosed shells, boolean artifacts, zero-thickness faces
```

Note: `preview.py` checks watertight status automatically when generating previews.

---

## 3b. Functional / Assembled-State Verification

The part almost never works alone — it cradles a bowl, holds a phone, mates with a
connector. Review the **assembly**, not the lone part:

- **Mating part is modelled.** The model's VERIFICATION block must build a proxy
  (`mating_proxies.bowl_proxy` / `phone_proxy` / `box_proxy` / `cylinder_proxy`)
  in its functional position and export it as `<name>__proxy.stl`. The preview
  shows it translucent over the part — confirm it rests exactly where intended.
- **Datum is the functional one.** Verify the measured value is taken from/to the
  points the user meant (bowl *underside* vs *rim*, puck *face* vs *recess floor*).
  A correct number against the wrong datum is still a failure.
- **Fit / contact / clearance.**
  - `clearance(socket_id, part_od)` ≥ the spec's fit clearance (part drops in).
  - `puck_protrusion(puck_thk, recess_depth)` > 0 (MagSafe contacts the phone —
    a negative value is the exact v10→v11 air-gap bug).
- **Tipping / stability.** For stands and risers, the combined centre of gravity
  (`combine_cog` of part + mating proxy) must sit inside the base footprint
  (`cog_within_footprint` with a safety margin).

Any of these failing is a NEEDS_REVISION even if the bounding box is perfect.

---

## 4. Printability Analysis

### Overhang Detection (Approximate)

```python
import trimesh
import numpy as np

def check_overhangs(stl_path, max_angle=45):
    """Check for faces with overhang angle beyond threshold.

    Overhang angle is measured from the horizontal plane.
    A flat bottom (normal pointing straight down) = 0 deg overhang (safe).
    A face angled 50 deg from horizontal = needs supports if max_angle=45.

    Returns percentage of total faces that may need supports.
    """
    tm = trimesh.load(stl_path, force="mesh")
    normals = tm.face_normals

    # Only check downward-facing normals (potential overhangs)
    downward = normals[:, 2] < 0
    if not downward.any():
        return 0.0

    down = np.array([0, 0, -1])
    down_normals = normals[downward]

    # Angle between each normal and straight-down vector
    cos_angles = np.dot(down_normals, down) / (
        np.linalg.norm(down_normals, axis=1) + 1e-10
    )
    angles_from_down = np.degrees(np.arccos(np.clip(cos_angles, -1, 1)))

    # angle_from_down=0 means flat bottom (safe)
    # angle_from_down=90 means vertical face (safe)
    # Overhang angle from horizontal = 90 - angle_from_down
    overhang_from_horizontal = 90 - angles_from_down
    problem_faces = np.sum(overhang_from_horizontal > max_angle)

    return problem_faces / len(normals) * 100

pct = check_overhangs("model.stl")
if pct > 5:
    print(f"WARNING: {pct:.0f}% faces may need supports")
else:
    print(f"OK: Model looks printable without supports ({pct:.1f}% overhang)")
```

### Thin Wall Detection

```python
def estimate_min_wall(result):
    """Rough check - compare shelled vs non-shelled volume."""
    vol = result.val().Volume()
    bb = result.val().BoundingBox()
    outer_vol = bb.xlen * bb.ylen * bb.zlen
    fill_ratio = vol / outer_vol
    
    if fill_ratio < 0.15:
        print("Very thin walls - may be fragile")
    elif fill_ratio < 0.3:
        print("Thin-walled design (typical for enclosures)")
    else:
        print("Solid/thick-walled design")
    
    return fill_ratio
```

---

## 5. Common Issues and Fixes

### Issue: Feature not visible in preview
**Cause**: Boolean operation failed silently
**Fix**: Check that cutting bodies actually overlap the main body. Print intermediate volumes.

### Issue: Model looks correct but walls are paper-thin
**Cause**: Shell thickness too small, or shell removed wrong faces
**Fix**: Verify shell direction (negative = inward) and thickness >= 1.2mm

### Issue: Standoffs appear disconnected
**Cause**: Union failed because standoffs don't touch the body
**Fix**: Ensure standoff base cylinder intersects with the floor/wall of the body

### Issue: Cutout in wrong position
**Cause**: Coordinate system confusion - CadQuery centers at origin by default
**Fix**: Use `centered=(True, True, False)` and verify offsets from known reference points

### Issue: Fillets look wrong or preview crashes
**Cause**: Fillet radius too large for the edge
**Fix**: Reduce fillet radius. Rule of thumb: fillet radius < half the smallest adjacent face dimension

### Issue: Preview shows correct shape but dimensions are wrong
**Cause**: Forgot to update a parameter or used wrong variable
**Fix**: Add assertions comparing bounding box to expected dimensions
