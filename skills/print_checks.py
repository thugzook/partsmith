"""Intrinsic print-quality + economy gate — holds a part to an absolute bar with NO
reference model. This is what stops a clean-room first build from shipping bulky,
unfinished, or big-lever (the edge_backpack_hook v1 regression) without ever needing
to peek at prior art.

Checks (each PASS/FAIL, with the measured value):
- printable_overhangs : downward faces steeper than 45 deg, IN THE DECLARED PRINT
                        ORIENTATION (pass up_axis); ignores orientation artifacts.
- min_wall            : declared wall thickness >= 1.2 mm.
- finish             : fillet radius > 0 (outer corners are softened, not raw blocks).
- lever_ratio        : forward load overhang / grip overlap <= max_lever (keep the
                        load near the support; a long lever is wasteful + tippy).
- width_economy      : width <= load_width + 2*wall + slack (not arbitrarily wide).
- overlap_economy    : grip overlap <= 3x the load overhang (enough for stability,
                        not a slab).

`params` carries the functional numbers the model already knows; the mesh supplies
volume / bbox / overhangs. Reuses the overhang idiom from design-review.md sec 4.
"""
import json
import sys

import numpy as np
import trimesh

MIN_WALL_MM = 1.2
MAX_OVERHANG_PCT = 5.0
MAX_LEVER = 0.5
MAX_OVERLAP_RATIO = 3.0
WIDTH_SLACK_MM = 14.0   # strap/load width + 2*wall + a bit of cradle


def overhang_fraction(stl_path, up=(0.0, 1.0, 0.0), max_angle=45, bed_tol=0.6):
    """% of faces that are unsupported overhangs, for a build whose UP direction is
    `up` (up=+Y matches an extruded profile printed on its end face).

    A face is an overhang only if it faces downward AND is shallower than max_angle
    from horizontal AND is NOT resting on the bed. The bed exclusion matters: a flat
    face whose normal points straight down is the worst overhang IF it is a ceiling,
    but harmless when it is the part's footprint on the build plate -- by normal alone
    the two are identical, so we drop faces sitting at the minimum build height."""
    tm = trimesh.load(stl_path, force="mesh")
    up = np.asarray(up, dtype=float)
    up = up / np.linalg.norm(up)
    normals = tm.face_normals
    idx = np.where(normals @ up < 0)[0]                 # downward-facing
    if len(idx) == 0:
        return 0.0
    cos = (normals[idx] @ (-up)) / (np.linalg.norm(normals[idx], axis=1) + 1e-10)
    overhang_from_horizontal = 90.0 - np.degrees(np.arccos(np.clip(cos, -1, 1)))
    steep = overhang_from_horizontal > max_angle        # near-horizontal underside
    build = tm.triangles_center[idx] @ up
    on_bed = build <= (tm.vertices @ up).min() + bed_tol
    return float(np.sum(steep & ~on_bed)) / len(normals) * 100.0


def run_checks(stl_path, params, up_axis=(0.0, 1.0, 0.0)):
    """params keys: wall_thickness, fillet_radius, grip_overlap, load_overhang,
    load_width. Returns {"ok": bool, "checks": [(name, ok, detail)], "stats": {...}}."""
    tm = trimesh.load(stl_path, force="mesh")
    bb = tm.extents
    vol = float(tm.volume)

    wall = float(params["wall_thickness"])
    fillet = float(params.get("fillet_radius", 0.0))
    overlap = float(params["grip_overlap"])
    overhang = float(params["load_overhang"])
    load_w = float(params["load_width"])
    width = float(min(bb))   # the extrusion depth is the smallest bbox dim here

    lever = overhang / overlap if overlap else 999.0
    width_cap = load_w + 2 * wall + WIDTH_SLACK_MM
    overlap_ratio = overlap / overhang if overhang else 999.0
    oh_pct = overhang_fraction(stl_path, up=up_axis)

    checks = [
        ("printable_overhangs", oh_pct <= MAX_OVERHANG_PCT,
         f"{oh_pct:.1f}% over 45 deg (<= {MAX_OVERHANG_PCT}%), up={tuple(up_axis)}"),
        ("min_wall", wall >= MIN_WALL_MM, f"{wall:.1f} mm (>= {MIN_WALL_MM})"),
        ("finish", fillet > 0.0, f"fillet {fillet:.1f} mm (> 0)"),
        ("lever_ratio", lever <= MAX_LEVER,
         f"{lever:.2f} = {overhang:.0f}/{overlap:.0f} (<= {MAX_LEVER})"),
        ("width_economy", width <= width_cap,
         f"{width:.1f} mm (<= load {load_w:.0f} + 2*wall + slack = {width_cap:.0f})"),
        ("overlap_economy", overlap_ratio <= MAX_OVERLAP_RATIO,
         f"{overlap_ratio:.2f}x overhang (<= {MAX_OVERLAP_RATIO}x)"),
    ]
    return {
        "ok": all(ok for _, ok, _ in checks),
        "checks": checks,
        "stats": {"bbox_mm": [round(x, 1) for x in bb], "volume_mm3": round(vol)},
    }


def format_report(report):
    lines = ["", "PRINT-QUALITY + ECONOMY GATE", "=" * 44]
    for name, ok, detail in report["checks"]:
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    s = report["stats"]
    lines.append(f"     bbox {s['bbox_mm']} mm | volume {s['volume_mm3']} mm^3")
    lines.append("-" * 44)
    lines.append("ALL PASS" if report["ok"] else "FAIL - fix before review")
    return "\n".join(lines)


if __name__ == "__main__":
    # usage: print_checks.py <stl> '<params-json>' [ux uy uz]
    stl = sys.argv[1]
    params = json.loads(sys.argv[2])
    up = tuple(float(x) for x in sys.argv[3:6]) if len(sys.argv) >= 6 else (0.0, 1.0, 0.0)
    rep = run_checks(stl, params, up_axis=up)
    print(format_report(rep))
    sys.exit(0 if rep["ok"] else 1)
