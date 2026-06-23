#!/usr/bin/env python3
"""Big single-subject renders for the design-review loop.

preview.py's multi-view panels are too small to judge aesthetics (scallops,
lips, gaps) once they get downscaled for viewing -- that is exactly how a
"missing scallop / weird lip / center gap" shipped while the numeric gate was
green. This renders ONE subject filling the frame at high resolution so the
features are actually visible, and (when a reference is given) places the
reference next to the model for a side-by-side feature diff.

Usage:
    python3 review_render.py MODEL.stl --out review.png
    python3 review_render.py MODEL.stl --ref REF.3mf --ref-scale 25.4 --out diff.png

The reviewer agent looks at the OUTPUT png; framing is per-panel so each subject
fills its cell regardless of absolute size (good for comparing shapes, not size).
"""
import argparse
import numpy as np
import trimesh
from PIL import Image, ImageDraw

import preview

# camera directions (eye relative to subject center); up is +Z except top view
_ISO   = ([1.1, -1.0, 0.85], [0, 0, 1])
_FRONT = ([0.0, -1.0, 0.22], [0, 0, 1])   # look toward +Y (the model's back)
_TOP   = ([0.0, 0.0001, 1.0], [0, 1, 0])


def _load(path, scale=1.0):
    m = trimesh.load(path, force="mesh")
    if isinstance(m, trimesh.Scene):
        m = trimesh.util.concatenate(tuple(m.geometry.values()))
    if scale != 1.0:
        m.apply_scale(scale)
    return m


def _panel(tm, cam, w, h, label):
    eye_dir, up = cam
    floor = preview._make_grid_floor(tm)
    img = preview._render_panel(tm, eye_dir, up, w, h, floor_mesh=floor)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(150, 150, 150))
    d.text((12, 10), label, fill=(40, 40, 40))
    return img


def _grid(rows):
    """rows = list of lists of PIL images (same size). Returns a composited image."""
    h = rows[0][0].height
    w = rows[0][0].width
    ncol = max(len(r) for r in rows)
    out = Image.new("RGB", (w * ncol, h * len(rows)), (255, 255, 255))
    for ri, row in enumerate(rows):
        for ci, im in enumerate(row):
            out.paste(im, (ci * w, ri * h))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ref", help="reference mesh (STL/3mf) to show alongside")
    ap.add_argument("--ref-scale", type=float, default=1.0,
                    help="scale factor for the reference (3mf authored in inches => 25.4)")
    ap.add_argument("--w", type=int, default=900)
    ap.add_argument("--hgt", type=int, default=680)
    args = ap.parse_args()

    model = _load(args.model)
    print("model bbox mm:", [round(x, 1) for x in model.extents])

    if args.ref:
        ref = _load(args.ref, scale=args.ref_scale)
        print("ref   bbox mm:", [round(x, 1) for x in ref.extents])
        rows = [
            [_panel(ref, _ISO, args.w, args.hgt, "REFERENCE  iso"),
             _panel(model, _ISO, args.w, args.hgt, "MODEL  iso")],
            [_panel(ref, _FRONT, args.w, args.hgt, "REFERENCE  front"),
             _panel(model, _FRONT, args.w, args.hgt, "MODEL  front")],
        ]
    else:
        rows = [
            [_panel(model, _ISO, args.w, args.hgt, "iso"),
             _panel(model, _FRONT, args.w, args.hgt, "front")],
            [_panel(model, _TOP, args.w, args.hgt, "top")],
        ]
    _grid(rows).save(args.out)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
