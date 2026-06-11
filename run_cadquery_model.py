#!/usr/bin/env python3
"""
Run a generated CadQuery model script in a subprocess and emit a structured
JSON result so Claude can parse success/failure without the user copy-pasting
tracebacks.

Usage:
    python3 run_cadquery_model.py path/to/model.py
    python3 run_cadquery_model.py path/to/model.py --preview            # also render
    python3 run_cadquery_model.py path/to/model.py --preview --strict   # fail on non-watertight

Emits a single JSON object to stdout:
    {
      "success": true/false,
      "script": "model.py",
      "stls": ["a.stl", ...],
      "stl": "a.stl",
      "previews": ["a_preview.png", ...],
      "preview": "a_preview.png",
      "watertight": true/false/null,
      "stdout": "...",
      "stderr": "...",
      "returncode": 0
    }

Exit codes: 0 success, 1 failure, 2 interpreter error, 3 timeout.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time


def _sibling(path, suffix):
    return os.path.splitext(path)[0] + suffix


def _append_stderr(result, msg):
    result["stderr"] = (result["stderr"] or "") + msg + "\n"


def _new_files_by_ext(script_dir, after_mtime, exts):
    """Return {ext: [paths]} for files written at or after after_mtime, newest first."""
    buckets = {ext: {} for ext in exts}
    for ext in exts:
        for case in (ext.lower(), ext.upper()):
            for path in glob.glob(os.path.join(script_dir, f"*.{case}")):
                real = os.path.realpath(path)
                if real in buckets[ext]:
                    continue
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime >= after_mtime:
                    buckets[ext][real] = (mtime, path)
    return {
        ext: [p for _, p in sorted(entries.values(), reverse=True)]
        for ext, entries in buckets.items()
    }


def _process_stls(stls, views, strict, want_preview):
    """Load each STL once — check watertightness and optionally render preview.

    pyrender is imported lazily so --strict alone stays headless-safe.
    """
    import mesh_io

    out = {"previews": [], "watertights": [], "error": None}

    for stl in stls:
        try:
            tm = mesh_io.load_mesh(stl)
        except ValueError as e:
            out["error"] = f"Mesh load failed ({stl}): {e}"
            return out

        watertight = bool(tm.is_watertight)
        out["watertights"].append(watertight)

        if strict and not watertight:
            out["error"] = f"Mesh {stl} is not watertight (--strict set)."
            return out

        if want_preview:
            import preview
            preview_path = _sibling(stl, "_preview.png")
            try:
                if views == "multi":
                    slug = os.path.splitext(os.path.basename(stl))[0]
                    title = re.sub(r"[_\-]+", " ", slug).title()
                    preview.render_multi_view(tm, preview_path, title=title)
                else:
                    preview.render_single(tm, preview_path)
            except Exception as e:
                out["error"] = f"Preview render failed ({stl}): {e}"
                return out
            out["previews"].append(preview_path)

    return out


def main():
    parser = argparse.ArgumentParser(
        description="Run a CadQuery model script and report a JSON result",
    )
    parser.add_argument("script", help="Path to the CadQuery .py file")
    parser.add_argument("--preview", action="store_true",
                        help="Render a multi-view preview PNG for every STL produced")
    parser.add_argument("--strict", action="store_true",
                        help="Fail if any STL is non-watertight or if no STL was produced")
    parser.add_argument("--views", choices=["iso", "multi"], default="multi",
                        help="Preview layout: iso or multi (default: multi)")
    parser.add_argument("--timeout", type=int, default=180,
                        help="Seconds before killing the script (default: 180)")
    args = parser.parse_args()

    script_path = os.path.abspath(args.script)
    script_dir = os.path.dirname(script_path) or "."

    result = {
        "success": False, "script": args.script,
        "stls": [], "stl": None,
        "previews": [], "preview": None,
        "threemfs": [], "threemf": None,
        "watertight": None, "stdout": "", "stderr": "", "returncode": -1,
    }

    started = time.time()

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True,
            timeout=args.timeout, cwd=script_dir,
        )
    except subprocess.TimeoutExpired as e:
        _append_stderr(result, f"Timeout after {args.timeout}s: {e}")
        print(json.dumps(result, indent=2))
        sys.exit(3)
    except FileNotFoundError as e:
        _append_stderr(result, f"Cannot launch interpreter: {e}")
        print(json.dumps(result, indent=2))
        sys.exit(2)

    result["stdout"] = proc.stdout
    result["stderr"] = proc.stderr
    result["returncode"] = proc.returncode
    result["success"] = proc.returncode == 0

    if result["success"]:
        found = _new_files_by_ext(script_dir, started, ("stl", "3mf"))
        result["stls"] = found["stl"]
        result["threemfs"] = found["3mf"]

    if args.strict and result["success"] and not result["stls"]:
        _append_stderr(result, "No STL files produced by the script (--strict set).")
        result["success"] = False

    needs_mesh_pass = args.preview or args.strict
    if needs_mesh_pass and result["success"] and result["stls"]:
        processed = _process_stls(result["stls"], args.views, args.strict,
                                   want_preview=args.preview)
        result["previews"] = processed["previews"]
        if processed["watertights"]:
            result["watertight"] = all(processed["watertights"])
        if processed["error"]:
            _append_stderr(result, processed["error"])
            result["success"] = False

    result["stl"] = result["stls"][0] if result["stls"] else None
    result["preview"] = result["previews"][0] if result["previews"] else None
    result["threemf"] = result["threemfs"][0] if result["threemfs"] else None

    print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
