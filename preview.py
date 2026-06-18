import numpy as np
import pyrender
import trimesh
from PIL import Image, ImageDraw

_ISO_W    = 600
_SIDE_W   = 440
_SIDE_H   = 206
_GRID_H   = _SIDE_H * 3        # 618
_TITLE_H  = 52
_FOOTER_H = 58
_TOTAL_W  = _ISO_W + _SIDE_W   # 1040
_BG       = [0.93, 0.93, 0.93, 1.0]
_AMBIENT  = np.array([0.3, 0.3, 0.3, 1.0])


def _look_at(eye, center, up):
    """Return a 4x4 camera/light pose: positioned at eye, looking at center."""
    eye = np.asarray(eye, dtype=float)
    center = np.asarray(center, dtype=float)
    up = np.asarray(up, dtype=float)
    z = eye - center
    z /= np.linalg.norm(z)
    x = np.cross(up, z)
    if np.linalg.norm(x) < 1e-6:
        up = np.array([0.0, 1.0, 0.0])
        x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    pose = np.eye(4)
    pose[:3, 0] = x
    pose[:3, 1] = y
    pose[:3, 2] = z
    pose[:3, 3] = eye
    return pose


def _make_grid_floor(tm):
    """Flat floor plane with a grid of lines positioned at the mesh base."""
    center = tm.bounds.mean(axis=0)
    extents = tm.extents
    z_floor = tm.bounds[0][2]

    size = max(extents[:2]) * 2.8
    spacing = max(extents[:2]) / 7.0
    line_w = size * 0.004
    n_half = max(3, min(15, int(size / (2 * spacing))))

    pieces = []

    floor = trimesh.creation.box([size, size, 1.0])
    floor.apply_translation([center[0], center[1], z_floor - 0.5])
    floor.visual.vertex_colors = np.full((len(floor.vertices), 4), [48, 48, 48, 255], dtype=np.uint8)
    pieces.append(floor)

    for k in range(-n_half, n_half + 1):
        offset = k * spacing

        lx = trimesh.creation.box([line_w, size, 1.0])
        lx.apply_translation([center[0] + offset, center[1], z_floor + 0.5])
        lx.visual.vertex_colors = np.full((len(lx.vertices), 4), [110, 110, 110, 255], dtype=np.uint8)
        pieces.append(lx)

        ly = trimesh.creation.box([size, line_w, 1.0])
        ly.apply_translation([center[0], center[1] + offset, z_floor + 0.5])
        ly.visual.vertex_colors = np.full((len(ly.vertices), 4), [110, 110, 110, 255], dtype=np.uint8)
        pieces.append(ly)

    return trimesh.util.concatenate(pieces)


def _render_panel(tm, eye_dir, up, w, h, floor_mesh=None, proxy_mesh=None):
    """Render one view. eye_dir is a direction vector (need not be normalized).

    Framing spans both the part and the proxy so a mating part that rises above
    the part (e.g. a bowl rim) is never clipped.
    """
    frame = [tm] + ([proxy_mesh] if proxy_mesh is not None else [])
    mins = np.min([m.bounds[0] for m in frame], axis=0)
    maxs = np.max([m.bounds[1] for m in frame], axis=0)
    center = (mins + maxs) / 2.0
    dist = max(maxs - mins) * 1.75
    eye = center + (np.asarray(eye_dir, dtype=float) / np.linalg.norm(eye_dir)) * dist

    scene = pyrender.Scene(ambient_light=_AMBIENT, bg_color=_BG)
    mat = pyrender.MetallicRoughnessMaterial(
        baseColorFactor=[0.62, 0.75, 0.88, 1.0],
        metallicFactor=0.0,
        roughnessFactor=0.45,
    )
    scene.add(pyrender.Mesh.from_trimesh(tm, smooth=False, material=mat))

    if proxy_mesh is not None:
        proxy_mat = pyrender.MetallicRoughnessMaterial(
            baseColorFactor=[0.95, 0.55, 0.35, 0.40],  # translucent amber mating part
            metallicFactor=0.0,
            roughnessFactor=0.6,
            alphaMode="BLEND",
        )
        scene.add(pyrender.Mesh.from_trimesh(proxy_mesh, smooth=False, material=proxy_mat))

    if floor_mesh is not None:
        scene.add(pyrender.Mesh.from_trimesh(floor_mesh, smooth=False))

    for ld, intensity in [([1, 1, 2], 3.0), ([-1, 0.5, 1.5], 1.5), ([0.2, -1, 0.4], 1.0)]:
        lp = center + np.array(ld, dtype=float) / np.linalg.norm(ld) * dist * 3
        scene.add(pyrender.DirectionalLight(color=np.ones(3), intensity=intensity),
                  pose=_look_at(lp, center, up))

    scene.add(pyrender.PerspectiveCamera(yfov=np.pi / 4, aspectRatio=w / h),
              pose=_look_at(eye, center, up))

    r = pyrender.OffscreenRenderer(w, h)
    color, _ = r.render(scene)
    r.delete()
    return Image.fromarray(color)


def _label(img, text):
    draw = ImageDraw.Draw(img)
    draw.text((10, 8), text, fill=(120, 120, 120))
    return img


def render_multi_view(tm, output_path, title=None, proxy_mesh=None,
                      measurements=None, gate=None):
    """Asymmetric layout: large isometric left + 3 stacked orthographic panels right.

    proxy_mesh (the mating part: bowl, phone) is drawn translucent over the part
    so the assembled state is visible. If a spec `gate` is supplied the footer
    shows actual vs target with PASS/FAIL marks; otherwise it falls back to the
    raw measured values.
    """
    floor_mesh = _make_grid_floor(tm)

    views = [
        ("Isometric",  [1.0,  1.0, 0.85], [0, 0, 1]),
        ("Front (Y-)", [0.0, -1.0, 0.35], [0, 0, 1]),
        ("Right (X+)", [1.0,  0.0, 0.35], [0, 0, 1]),
        ("Top (Z+)",   [0.0, 0.001, 1.0], [0, 1, 0]),
    ]

    iso_label, iso_eye, iso_up = views[0]
    iso_panel = _render_panel(tm, iso_eye, iso_up, _ISO_W, _GRID_H,
                              floor_mesh=floor_mesh, proxy_mesh=proxy_mesh)
    _label(iso_panel, iso_label)

    side_panels = []
    for label, eye, up in views[1:]:
        p = _render_panel(tm, eye, up, _SIDE_W, _SIDE_H,
                          floor_mesh=floor_mesh, proxy_mesh=proxy_mesh)
        _label(p, label)
        side_panels.append(p)

    canvas_h = _TITLE_H + _GRID_H + _FOOTER_H
    canvas = Image.new("RGB", (_TOTAL_W, canvas_h), (245, 245, 245))

    canvas.paste(iso_panel, (0, _TITLE_H))
    for i, panel in enumerate(side_panels):
        canvas.paste(panel, (_ISO_W, _TITLE_H + i * _SIDE_H))

    draw = ImageDraw.Draw(canvas)

    sep = (200, 200, 200)
    draw.line([(_ISO_W, _TITLE_H), (_ISO_W, _TITLE_H + _GRID_H)], fill=sep, width=1)
    for i in range(1, 3):
        y = _TITLE_H + i * _SIDE_H
        draw.line([(_ISO_W, y), (_TOTAL_W, y)], fill=sep, width=1)
    draw.line([(0, _TITLE_H), (_TOTAL_W, _TITLE_H)], fill=sep, width=1)
    draw.line([(0, _TITLE_H + _GRID_H), (_TOTAL_W, _TITLE_H + _GRID_H)], fill=sep, width=1)

    if title:
        try:
            tw = int(draw.textlength(title))
        except AttributeError:
            tw = len(title) * 7
        draw.text(((_TOTAL_W - tw) // 2, (_TITLE_H - 11) // 2), title, fill=(60, 60, 60))

    bb = tm.bounds
    d = bb[1] - bb[0]
    vol = abs(tm.volume)
    footer = "  Bounding box: {:.1f} x {:.1f} x {:.1f} mm  |  Volume: ~{:.0f} mm3".format(
        d[0], d[1], d[2], vol
    )
    draw.text((8, _TITLE_H + _GRID_H + 12), footer, fill=(100, 100, 100))

    mtext, all_pass = _format_measurements(measurements, gate)
    if mtext:
        color = (70, 110, 70) if all_pass else (170, 60, 60)
        draw.text((8, _TITLE_H + _GRID_H + 32), mtext, fill=color)

    canvas.save(output_path)


def _format_measurements(measurements, gate=None):
    """One-line footer. With a spec `gate`, show actual vs target + PASS/FAIL;
    otherwise show the raw measured scalars. Returns (text, all_passed)."""
    if gate and gate.get("results"):
        parts, all_pass = [], True
        for r in gate["results"][:4]:
            mark = "OK" if r["status"] == "PASS" else "X"
            if r["status"] != "PASS":
                all_pass = False
            a = "?" if r["actual"] is None else f"{r['actual']:.1f}"
            parts.append(f"{r['name']} {a}/{r['target']:.1f}mm [{mark}]")
        verdict = "GATE PASS" if all_pass else "GATE FAIL"
        return f"  {verdict}  " + "   ".join(parts), all_pass
    if measurements:
        scalars = [(k, v) for k, v in measurements.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if scalars:
            parts = [f"{k}: {v:.1f}mm ({v / 25.4:.2f}in)" for k, v in scalars[:4]]
            return "  Measured  " + "    ".join(parts), True
    return "", True


def render_single(tm, output_path, w=800, h=600):
    """Single isometric view."""
    img = _render_panel(tm, [1.0, 1.0, 0.85], [0, 0, 1], w, h)
    img.save(output_path)
