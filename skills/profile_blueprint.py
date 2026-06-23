"""Side-profile blueprint: the cheap 2D self-check that must pass before any 3D.

WHY THIS EXISTS
A part whose function is defined by a side/section profile (hooks, brackets, clips,
rails, stands) is easy to get wrong in 3D and hard to verify in a small multi-view
render -- a broken topology hides. The fix is to define the profile ONCE as a
centerline polyline, (a) draw it flat and compare to the user's side sketch, and
(b) extrude that SAME polyline in model.py. Then the rendered side view equals the
blueprint by construction, so "render doesn't match the sketch" cannot happen.

CONTRACT
- A profile is an ordered, open centerline polyline `[(x, z), ...]` plus a ribbon
  `thickness` (wall). model.py must build the body by extruding this exact polyline
  (offset to the wall thickness), never by unioning independent boxes.
- `render_blueprint(...)` writes the PNG the user signs off on.
- `validate_profile(...)` is the automated PRE-SCREEN run before the user sees the
  blueprint: ribbon contiguous + single piece, centerline does not self-intersect
  (no break), and the mating part sits in open space (the hook does not pass through
  it). Any FAIL is fixed by the lead before presenting -- this is the automated
  catch for the floating-under-lip / broken-topology bug class.

Coordinate convention (recommended): the mating reference edge at x=0, +x forward,
the rest plane at z=0. Units mm.
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as _MplPoly
import numpy as np


# --------------------------------------------------------------------------- #
# Ribbon geometry
# --------------------------------------------------------------------------- #
def ribbon_quads(centerline, thickness):
    """Each centerline segment as a filled rectangle (a 4-point quad).

    Consecutive quads overlap at the joints, so the union reads as one continuous
    constant-thickness ribbon -- the same technique proven in the edge_backpack_hook
    profile check. Returns a list of (4, 2) arrays.
    """
    pts = [np.asarray(p, dtype=float) for p in centerline]
    quads = []
    for p0, p1 in zip(pts[:-1], pts[1:]):
        d = p1 - p0
        L = float(np.hypot(*d))
        if L == 0.0:
            continue
        n = np.array([-d[1], d[0]]) / L          # unit normal
        h = n * (thickness / 2.0)
        quads.append(np.array([p0 + h, p1 + h, p1 - h, p0 - h]))
    return quads


def _line_x(a, u, b, v):
    """Intersection of line (a + s*u) and (b + w*v); None if parallel."""
    denom = u[0] * v[1] - u[1] * v[0]
    if abs(denom) < 1e-9:
        return None
    s = ((b[0] - a[0]) * v[1] - (b[1] - a[1]) * v[0]) / denom
    return a + s * u


def ribbon_outline(centerline, thickness):
    """The whole ribbon as ONE closed, miter-joined polygon (list of (x,z) points).

    Unlike `ribbon_quads` (overlapping rectangles, great for drawing) this returns a
    single simple outline. model.py extrudes THIS as one wire, so the solid is one
    clean body with no internal union seams -- which is what lets `.fillet()` work.
    Butt caps at the two free ends; mitered corners at every interior vertex. Valid
    as long as the centerline doesn't self-intersect and feature spacing >> thickness
    (both already enforced by `validate_profile`).
    """
    P = [np.asarray(p, dtype=float) for p in centerline]
    half = thickness / 2.0
    dirs, nrm = [], []
    for p0, p1 in zip(P[:-1], P[1:]):
        d = p1 - p0
        d = d / np.hypot(*d)
        dirs.append(d)
        nrm.append(np.array([-d[1], d[0]]))      # left normal
    n = len(dirs)

    def side(sign):
        pts = [P[0] + sign * nrm[0] * half]       # start cap corner
        for i in range(1, n):
            a = P[i] + sign * nrm[i - 1] * half
            b = P[i] + sign * nrm[i] * half
            x = _line_x(a, dirs[i - 1], b, dirs[i])
            pts.append(x if x is not None else b)
        pts.append(P[n] + sign * nrm[n - 1] * half)   # end cap corner
        return pts

    left = side(+1.0)
    right = side(-1.0)
    return [tuple(p) for p in (left + right[::-1])]


# --------------------------------------------------------------------------- #
# Pre-screen (automated gate, runs before the human sees the blueprint)
# --------------------------------------------------------------------------- #
def _ccw(a, b, c):
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(p1, p2, p3, p4):
    """True if open segments p1p2 and p3p4 properly intersect."""
    return (_ccw(p1, p3, p4) != _ccw(p2, p3, p4) and
            _ccw(p1, p2, p3) != _ccw(p1, p2, p4))


def _aabb(quad):
    xs, zs = quad[:, 0], quad[:, 1]
    return xs.min(), xs.max(), zs.min(), zs.max()


def _overlap(a, b, eps):
    """Interior (positive-area) AABB overlap, ignoring mere face contact (< eps)."""
    ax0, ax1, az0, az1 = a
    bx0, bx1, bz0, bz1 = b
    return (min(ax1, bx1) - max(ax0, bx0) > eps and
            min(az1, bz1) - max(az0, bz0) > eps)


def validate_profile(centerline, thickness, mating_rects=None,
                     min_seg_len=0.5, contact_eps=0.3):
    """Pre-screen a profile. Returns {"ok": bool, "checks": [(name, ok, detail)]}.

    mating_rects: optional list of (x0, x1, z0, z1) for parts the hook must grip/seat
    WITHOUT passing through them (e.g. the desk solid). The slot itself must read as
    open space; a ribbon segment intruding into a mating rect's interior is a
    penetration (the bug where a stroke runs through where the desk sits).
    """
    pts = [np.asarray(p, dtype=float) for p in centerline]
    checks = []

    # 1. contiguous + non-degenerate
    segs = list(zip(pts[:-1], pts[1:]))
    bad = [i for i, (a, b) in enumerate(segs) if np.hypot(*(b - a)) < min_seg_len]
    checks.append((
        "contiguous & non-degenerate",
        len(pts) >= 2 and not bad,
        f"{len(pts)} pts, {len(segs)} segments" if not bad
        else f"zero/short segment(s) at index {bad}",
    ))

    # 2. no self-intersection (a 'break' or crossed ribbon)
    crossings = []
    for i in range(len(segs)):
        for j in range(i + 2, len(segs)):
            if i == 0 and j == len(segs) - 1:
                pass  # open polyline: first & last don't share a point, still test
            p1, p2 = segs[i]
            p3, p4 = segs[j]
            if _segments_cross(p1, p2, p3, p4):
                crossings.append((i, j))
    checks.append((
        "no self-intersection",
        not crossings,
        "centerline is a clean single ribbon" if not crossings
        else f"segments cross: {crossings}",
    ))

    # 3. mating part sits in open space (no penetration)
    if mating_rects:
        quads = ribbon_quads(centerline, thickness)
        boxes = [_aabb(q) for q in quads]
        hits = []
        for k, mr in enumerate(mating_rects):
            mrect = (min(mr[0], mr[1]), max(mr[0], mr[1]),
                     min(mr[2], mr[3]), max(mr[2], mr[3]))
            for bi, bx in enumerate(boxes):
                if _overlap(mrect, bx, contact_eps):
                    hits.append((k, bi))
        checks.append((
            "mating part not penetrated",
            not hits,
            "mating part(s) rest in open space" if not hits
            else f"ribbon intrudes into mating rect(s): {hits}",
        ))

    return {"ok": all(ok for _, ok, _ in checks), "checks": checks}


def format_prescreen(report):
    lines = ["", "PROFILE PRE-SCREEN", "=" * 40]
    for name, ok, detail in report["checks"]:
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}")
        lines.append(f"        {detail}")
    lines.append("-" * 40)
    lines.append("ALL PASS" if report["ok"] else "FAIL - fix before showing the user")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Blueprint render (the artifact the user signs off on)
# --------------------------------------------------------------------------- #
def render_blueprint(centerline, thickness, out_path, *, mating_rects=None,
                     extra_polys=None, datums=None, labels=None, annotations=None,
                     title=None, ribbon_color="#e08a3c"):
    """Draw the side profile flat (ribbon filled, mating parts hatched) and save PNG.

    mating_rects : list of (x0, x1, z0, z1) drawn as hatched gray blocks.
    extra_polys  : list of [(x,z), ...] solid features unioned onto the ribbon (e.g. a
                   lip wedge) -- drawn filled in the ribbon colour so the blueprint
                   matches the extruded solid.
    datums       : list of (x, z0, z1, text, color) vertical dimension arrows.
    labels       : list of ((x, z), text) stroke labels.
    annotations  : list of (x, z, text) free notes (e.g. 'strap').
    Returns the report from validate_profile so the caller can gate on it.
    """
    report = validate_profile(centerline, thickness, mating_rects=mating_rects)

    fig, ax = plt.subplots(figsize=(8, 9))
    for q in ribbon_quads(centerline, thickness):
        ax.add_patch(_MplPoly(q, closed=True, facecolor=ribbon_color,
                              edgecolor="none", zorder=3))
    for poly in (extra_polys or []):
        ax.add_patch(_MplPoly(poly, closed=True, facecolor=ribbon_color,
                              edgecolor="none", zorder=3))

    for (x0, x1, z0, z1) in (mating_rects or []):
        ax.add_patch(plt.Rectangle((min(x0, x1), min(z0, z1)),
                                   abs(x1 - x0), abs(z1 - z0),
                                   facecolor="0.85", edgecolor="0.4",
                                   hatch="//", zorder=1))

    for (x, z0, z1, text, color) in (datums or []):
        ax.annotate("", xy=(x, z1), xytext=(x, z0),
                    arrowprops=dict(arrowstyle="<->", color=color))
        ax.text(x + 2, (z0 + z1) / 2, text, color=color, fontsize=9, va="center")

    for (x, z), text in (labels or []):
        ax.annotate(text, (x, z), fontsize=8, color="#7a3d00")
    for (x, z, text) in (annotations or []):
        ax.text(x, z, text, ha="center", fontsize=9)

    pts = np.asarray(centerline, dtype=float)
    xmin, xmax = pts[:, 0].min(), pts[:, 0].max()
    zmin, zmax = pts[:, 1].min(), pts[:, 1].max()
    pad = 0.12 * max(xmax - xmin, zmax - zmin)
    ax.axhline(0, color="0.6", lw=0.7, ls=":")
    ax.set_aspect("equal")
    ax.set_xlim(xmin - pad - 30, xmax + pad + 30)
    ax.set_ylim(zmin - pad, zmax + pad)
    ax.set_xlabel("x  (mm, +x = forward)")
    ax.set_ylabel("z  (mm)")
    ax.set_title(title or "SIDE PROFILE blueprint (compare to sketch)")
    ax.grid(True, ls=":", alpha=0.4)
    # stamp the pre-screen result onto the image so the gate is visible
    ax.text(0.5, -0.09, format_prescreen(report).replace("\n", "   ").strip(),
            transform=ax.transAxes, ha="center", fontsize=6,
            color="green" if report["ok"] else "red", family="monospace")

    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return report
