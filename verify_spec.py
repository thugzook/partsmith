#!/usr/bin/env python3
"""Spec-driven dimensional verification for generated CadQuery models.

The intent_spec.json declares the functional measurements that actually matter
(`critical_measurements`), each with an explicit datum (`from` -> `to`), a target
`value_mm`, and a `tolerance_mm`. A generated model.py computes those same
measurements on the real geometry and prints them on a single line:

    MEASUREMENTS_JSON: {"bowl_rest_height": 113.9, "socket_clearance": 4.7}

This script compares the two and prints a PASS/FAIL table (mm + inches). The spec
is the source of truth, so a script that "passes" by asserting against a stale
local constant still gets caught here. This is the numeric backbone of the Step 4
design review.

Usage:
    # compare a measurements JSON (file or inline) against the spec
    python3 verify_spec.py outputs/<slug>/intent_spec.json --measurements out.json
    python3 verify_spec.py outputs/<slug>/intent_spec.json --measurements '{"x": 1.0}'

    # run the model script and verify its emitted measurements in one shot
    python3 verify_spec.py outputs/<slug>/intent_spec.json --run outputs/<slug>/v6/model.py

    # or pipe a run's stdout in
    .venv/bin/python3 model.py | python3 verify_spec.py outputs/<slug>/intent_spec.json

Exit codes: 0 all pass, 1 one or more fail, 2 usage/parse error.
"""
import argparse
import json
import os
import re
import subprocess
import sys

MEASUREMENTS_PREFIX = "MEASUREMENTS_JSON:"


def parse_measurements_line(text):
    """Extract the measurements dict from a `MEASUREMENTS_JSON: {...}` line.

    Scans for the last such line (a re-run may print several) and json-parses
    everything after the prefix. Returns the dict, or None if no valid line.
    """
    found = None
    for line in text.splitlines():
        idx = line.find(MEASUREMENTS_PREFIX)
        if idx == -1:
            continue
        payload = line[idx + len(MEASUREMENTS_PREFIX):].strip()
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            found = parsed
    return found


def _mm_in(v):
    return f"{v:.1f} mm ({v / 25.4:.2f} in)"


def load_measurements(arg):
    """Resolve --measurements: a file path, a raw JSON string, or text holding a
    MEASUREMENTS_JSON line. Returns a dict or raises ValueError."""
    if os.path.isfile(arg):
        with open(arg) as f:
            text = f.read()
    else:
        text = arg

    # Prefer an explicit MEASUREMENTS_JSON line if present (run logs, stdout).
    line = parse_measurements_line(text)
    if line is not None:
        return line
    # Otherwise treat the whole thing as a JSON object.
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("measurements JSON must be an object")
    return parsed


def run_model(script_path):
    """Execute a model.py and pull its emitted MEASUREMENTS_JSON line.

    Measurements are parsed even if the script exited non-zero: the whole point
    is to report *which* measurement is off, not bail on the failure. Only a run
    that produced no measurements line at all (a real geometry crash) is fatal.
    """
    script_path = os.path.abspath(script_path)
    proc = subprocess.run(
        [sys.executable, script_path],
        capture_output=True, text=True,
        cwd=os.path.dirname(script_path) or ".",
    )
    measurements = parse_measurements_line(proc.stdout)
    if measurements is None:
        sys.stderr.write(proc.stderr)
        raise ValueError(
            f"model script exited {proc.returncode} and printed no "
            "MEASUREMENTS_JSON line (every model.py must end with a "
            "VERIFICATION block)"
        )
    return measurements


def _passes(comparison, actual, target, tol):
    """Apply the comparison operator. eq (default): within tolerance; min: >=;
    max: <=. tol acts as slack on the inequality bounds."""
    if comparison == "min":
        return actual >= target - tol
    if comparison == "max":
        return actual <= target + tol
    return abs(actual - target) <= tol  # eq


def _op_label(comparison, target, tol):
    if comparison == "min":
        return f">= {target:.1f} mm"
    if comparison == "max":
        return f"<= {target:.1f} mm"
    return f"{_mm_in(target)}  +/- {tol:.1f} mm"


def compare(spec, measurements):
    """Compare each spec critical_measurement against the actual value.

    Each measurement may set `comparison`: "eq" (default, within tolerance_mm),
    "min" (actual >= value_mm), or "max" (actual <= value_mm). Returns a list of
    result dicts for the report. Missing actuals are a FAIL.
    """
    results = []
    for cm in spec.get("critical_measurements", []):
        name = cm["name"]
        target = float(cm["value_mm"])
        tol = float(cm.get("tolerance_mm", 0.0))
        comparison = cm.get("comparison", "eq")
        actual = measurements.get(name)
        if actual is None:
            status, delta, actual_f = "MISSING", None, None
        else:
            actual_f = float(actual)
            delta = actual_f - target
            status = "PASS" if _passes(comparison, actual_f, target, tol) else "FAIL"
        results.append({
            "name": name, "target": target, "tol": tol, "comparison": comparison,
            "actual": actual_f, "delta": delta, "status": status,
            "from": cm.get("from", "?"), "to": cm.get("to", "?"),
        })
    return results


def format_table(results):
    lines = ["", "SPEC VERIFICATION (critical measurements)", "=" * 60]
    if not results:
        lines.append("  (spec declares no critical_measurements)")
        return "\n".join(lines)
    for r in results:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "MISSING": "[MISS]"}[r["status"]]
        lines.append(f"{mark} {r['name']}")
        lines.append(f"        from {r['from']} -> {r['to']}")
        lines.append(f"        target {_op_label(r['comparison'], r['target'], r['tol'])}")
        if r["actual"] is None:
            lines.append("        actual <not reported by model>")
        else:
            lines.append(f"        actual {_mm_in(r['actual'])}  (delta {r['delta']:+.1f} mm)")
    n_fail = sum(1 for r in results if r["status"] != "PASS")
    lines.append("-" * 60)
    lines.append(f"{len(results) - n_fail}/{len(results)} passed"
                 + (f"  ({n_fail} need fixing)" if n_fail else "  - ALL PASS"))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", help="Path to intent_spec.json")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--measurements", help="Measurements JSON: file path or inline string")
    src.add_argument("--run", help="Run this model.py and verify its emitted measurements")
    args = parser.parse_args()

    try:
        with open(args.spec) as f:
            spec = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"Cannot read spec: {e}\n")
        sys.exit(2)

    try:
        if args.run:
            measurements = run_model(args.run)
        elif args.measurements:
            measurements = load_measurements(args.measurements)
        else:
            measurements = load_measurements(sys.stdin.read())
    except (ValueError, json.JSONDecodeError) as e:
        sys.stderr.write(f"Cannot read measurements: {e}\n")
        sys.exit(2)

    results = compare(spec, measurements)
    print(format_table(results))
    sys.exit(1 if any(r["status"] != "PASS" for r in results) else 0)


if __name__ == "__main__":
    main()
