"""Mating-part proxies + functional-fit helpers for the VERIFICATION block.

Every generated model.py must verify the *assembled* state, not just the printed
part on its own. The dog-bowl-riser bug shipped because the riser was modelled
without the bowl, so "4 inches above the ground" was never actually computed.

This module gives model scripts two things so they don't reinvent them:

1. Proxy builders — simple CadQuery solids for the mating part (bowl, phone, or a
   generic box/cylinder) placed in its functional position. Export the proxy with
   `export_proxy()` to a `<name>__proxy.stl` sibling; the runner treats that
   double-underscore file as preview-only, never a deliverable.
2. Fit helpers — pure arithmetic for the numbers that caused revisions
   (clearance, MagSafe puck protrusion, centre-of-gravity / tipping).

Import boilerplate for an outputs/<slug>/vN/model.py (root is three levels up):

    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "..", "skills"))
    import mating_proxies as mp

Finish every script with `mp.emit_measurements({...})` so verify_spec.py and the
preview can read the actual computed values.
"""
import json
import math
import os

try:
    import cadquery as cq
except ImportError:  # arithmetic helpers still work without the CAD kernel
    cq = None

MEASUREMENTS_PREFIX = "MEASUREMENTS_JSON:"
PROXY_SUFFIX = "__proxy.stl"


# --------------------------------------------------------------------------- #
# Proxy solids (need cadquery)
# --------------------------------------------------------------------------- #
def bowl_proxy(base_od_mm, depth_mm, rest_z_mm, top_od_mm=None):
    """A drop-in bowl proxy: a solid frustum standing on its resting plane.

    `rest_z_mm` is where the bowl underside sits (e.g. the socket floor). A solid
    frustum is intentionally used over a hollow shell — it never fails a boolean
    and reads clearly in the preview as "this is where the bowl ends up".

    Returns (solid, measurements) where measurements has bowl_rest_height (the
    underside height the user usually means by "sits X above the ground") and
    bowl_rim_height (the top edge).
    """
    if cq is None:
        raise RuntimeError("cadquery not available")
    if top_od_mm is None:
        top_od_mm = base_od_mm * 1.18  # typical bowl flare
    delta_r = (top_od_mm - base_od_mm) / 2.0
    taper_deg = math.degrees(math.atan2(delta_r, depth_mm))
    solid = (
        cq.Workplane("XY")
        .workplane(offset=rest_z_mm)
        .circle(base_od_mm / 2.0)
        .extrude(depth_mm, taper=-taper_deg)  # negative taper flares outward
    )
    info = {
        "bowl_rest_height": round(rest_z_mm, 2),
        "bowl_rim_height": round(rest_z_mm + depth_mm, 2),
    }
    return solid, info


def box_proxy(width_mm, depth_mm, height_mm, center=(0.0, 0.0), z0_mm=0.0):
    """Axis-aligned box proxy with its base at z0. Returns (solid, center_xyz)."""
    if cq is None:
        raise RuntimeError("cadquery not available")
    solid = (
        cq.Workplane("XY")
        .workplane(offset=z0_mm)
        .center(*center)
        .box(width_mm, depth_mm, height_mm, centered=(True, True, False))
    )
    c = solid.val().Center()
    return solid, (round(c.x, 2), round(c.y, 2), round(c.z, 2))


def cylinder_proxy(diameter_mm, height_mm, center=(0.0, 0.0), z0_mm=0.0):
    """Vertical cylinder proxy with its base at z0. Returns (solid, center_xyz)."""
    if cq is None:
        raise RuntimeError("cadquery not available")
    solid = (
        cq.Workplane("XY")
        .workplane(offset=z0_mm)
        .center(*center)
        .circle(diameter_mm / 2.0)
        .extrude(height_mm)
    )
    c = solid.val().Center()
    return solid, (round(c.x, 2), round(c.y, 2), round(c.z, 2))


def phone_proxy(width_mm, height_mm, thickness_mm, tilt_deg,
                back_face_y_mm=0.0, base_z_mm=0.0):
    """A phone slab leaned back by tilt_deg, back face starting at back_face_y_mm.

    width spans X, the screen plane runs up the tilt. Returns (solid, center_xyz)
    where center_xyz is the (mass) centre used for tipping checks.
    """
    if cq is None:
        raise RuntimeError("cadquery not available")
    t = math.radians(tilt_deg)
    slab = (
        cq.Workplane("XZ")
        .box(width_mm, height_mm, thickness_mm, centered=(True, False, False))
        .rotate((0, 0, 0), (1, 0, 0), -tilt_deg)
        .translate((0, back_face_y_mm + (thickness_mm / 2.0) * math.cos(t), base_z_mm))
    )
    c = slab.val().Center()
    return slab, (round(c.x, 2), round(c.y, 2), round(c.z, 2))


def blade_proxy(width_mm, height_mm, thickness_mm, recline_deg=0.0,
                center=(0.0, 0.0), base_z_mm=0.0):
    """A clipper-blade proxy slab, teeth-up, optionally reclined.

    Canonical pose (recline_deg=0): a rectangular slab with
      thickness  along X  (the row / pitch axis -- blades file edge-to-edge),
      width      along Y  (cutting width / slot-length axis; wide blades = bigger),
      height     along Z  (the teeth-up vertical dimension).
    `recline_deg` leans the blade backward about the X axis (0 = bolt upright).
    The slab is dropped so its lowest vertex sits at `base_z_mm` and it is
    centered at (cx, cy). Returns (solid, info) with info['blade_top_z'] -- the
    key number for blade_below_rim / stacking clearance.
    """
    if cq is None:
        raise RuntimeError("cadquery not available")
    cx, cy = center
    slab = (
        cq.Workplane("XY")
        .box(thickness_mm, width_mm, height_mm, centered=(True, True, False))
    )
    if recline_deg:
        slab = slab.rotate((0, 0, 0), (1, 0, 0), -recline_deg)
    zmin = slab.val().BoundingBox().zmin
    slab = slab.translate((cx, cy, base_z_mm - zmin))
    bb = slab.val().BoundingBox()
    info = {"blade_top_z": round(bb.zmax, 2), "blade_bottom_z": round(bb.zmin, 2)}
    return slab, info


def drawer_proxy(width_mm, depth_mm, height_mm, wall_mm=2.0,
                 center=(0.0, 0.0), z0_mm=0.0):
    """An open-top drawer shell (floor + 4 thin walls) for fitment context.

    Models the *interior* envelope the finished part has to live in, so the
    preview answers "does it actually fit the drawer?". width=X, depth=Y,
    height=Z. Returns (solid, info)."""
    if cq is None:
        raise RuntimeError("cadquery not available")
    outer = (cq.Workplane("XY").workplane(offset=z0_mm).center(*center)
             .box(width_mm, depth_mm, height_mm, centered=(True, True, False)))
    inner = (cq.Workplane("XY").workplane(offset=z0_mm + wall_mm).center(*center)
             .box(width_mm - 2 * wall_mm, depth_mm - 2 * wall_mm,
                  height_mm + 2.0, centered=(True, True, False)))
    shell = outer.cut(inner)  # inner taller than outer -> open top
    info = {"drawer_interior_mm": [round(width_mm - 2 * wall_mm, 1),
                                   round(depth_mm - 2 * wall_mm, 1),
                                   round(height_mm, 1)]}
    return shell, info


def export_proxy(solid, model_stl_path):
    """Write a proxy solid next to the model STL as <name>__proxy.stl.

    The runner excludes *__proxy.stl from the deliverable list and renders it
    translucent in the preview. Returns the path written.
    """
    if cq is None:
        raise RuntimeError("cadquery not available")
    base = model_stl_path[:-4] if model_stl_path.lower().endswith(".stl") else model_stl_path
    out = base + PROXY_SUFFIX
    cq.exporters.export(solid, out, tolerance=0.05, angularTolerance=0.3)
    return out


# --------------------------------------------------------------------------- #
# Fit helpers (pure arithmetic, no cadquery needed)
# --------------------------------------------------------------------------- #
def clearance(socket_id_mm, part_od_mm):
    """Diametral clearance. Positive = the part fits with room to spare."""
    return socket_id_mm - part_od_mm


def puck_protrusion(puck_thickness_mm, recess_depth_mm):
    """How far the MagSafe puck sits proud of the face. Must be > 0 or the phone
    back never touches the puck and the magnets pull across an air gap."""
    return puck_thickness_mm - recess_depth_mm


def combine_cog(items):
    """Volume-weighted centre of gravity. items = [(volume, (x, y, z)), ...].
    Returns (x, y, z). Use as a proxy for mass CoG when density is uniform."""
    total = sum(v for v, _ in items)
    if total <= 0:
        raise ValueError("total volume must be positive")
    return tuple(sum(v * c[i] for v, c in items) / total for i in range(3))


def cog_within_footprint(cog_xy, x_min, x_max, y_min, y_max, margin_mm=0.0):
    """True if the centre of gravity sits inside the base footprint (minus a
    safety margin). False => the assembly tips."""
    x, y = cog_xy
    return (x_min + margin_mm <= x <= x_max - margin_mm and
            y_min + margin_mm <= y <= y_max - margin_mm)


# --------------------------------------------------------------------------- #
# Output contract
# --------------------------------------------------------------------------- #
def emit_measurements(measurements):
    """Print the single machine-readable line that verify_spec.py and the runner
    parse. Round floats so the line stays readable."""
    clean = {}
    for k, v in measurements.items():
        if isinstance(v, float):
            clean[k] = round(v, 2)
        elif isinstance(v, (list, tuple)):
            clean[k] = [round(x, 2) if isinstance(x, float) else x for x in v]
        else:
            clean[k] = v
    print(f"{MEASUREMENTS_PREFIX} {json.dumps(clean)}")
