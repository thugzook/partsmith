#!/usr/bin/env python3
"""Regression tests for the measurement-gate guardrails.

No pytest dependency — run directly:

    .venv/bin/python3 tests/test_guardrails.py

Covers verify_spec (parsing + comparison operators) and mating_proxies (fit
arithmetic + proxy geometry). Proxy-geometry tests are skipped gracefully if
cadquery is unavailable so the arithmetic checks still run anywhere.
"""
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "skills"))

import verify_spec as vs
import mating_proxies as mp

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}")


# --- verify_spec.parse_measurements_line ---------------------------------- #
def test_parse():
    text = "noise\nMEASUREMENTS_JSON: {\"a\": 1.0}\nMEASUREMENTS_JSON: {\"a\": 2.0}\nend"
    check("parse takes the LAST measurements line", vs.parse_measurements_line(text) == {"a": 2.0})
    check("parse returns None when absent", vs.parse_measurements_line("nothing here") is None)
    check("parse ignores malformed json",
          vs.parse_measurements_line("MEASUREMENTS_JSON: {not json}") is None)


# --- verify_spec.compare (operators) -------------------------------------- #
def test_compare():
    spec = {"critical_measurements": [
        {"name": "h", "value_mm": 101.6, "tolerance_mm": 3.0},                     # eq
        {"name": "clr", "value_mm": 2.0, "comparison": "min"},                     # min
        {"name": "cap", "value_mm": 10.0, "comparison": "max"},                    # max
        {"name": "gone", "value_mm": 5.0, "tolerance_mm": 0.1},                    # missing
    ]}
    res = {r["name"]: r for r in vs.compare(spec, {"h": 103.0, "clr": 7.7, "cap": 12.0})}
    check("eq within tolerance -> PASS", res["h"]["status"] == "PASS")
    check("min satisfied -> PASS", res["clr"]["status"] == "PASS")
    check("max exceeded -> FAIL", res["cap"]["status"] == "FAIL")
    check("missing actual -> MISSING (non-pass)", res["gone"]["status"] == "MISSING")
    check("missing actual is not PASS", res["gone"]["status"] != "PASS")

    res2 = {r["name"]: r for r in vs.compare(spec, {"h": 113.9, "clr": 1.0, "cap": 9.0})}
    check("eq out of tolerance -> FAIL", res2["h"]["status"] == "FAIL")
    check("min not met -> FAIL", res2["clr"]["status"] == "FAIL")
    check("max within -> PASS", res2["cap"]["status"] == "PASS")


# --- mating_proxies fit arithmetic ---------------------------------------- #
def test_fit_helpers():
    check("clearance is diametral diff", abs(mp.clearance(119.0, 114.3) - 4.7) < 1e-9)
    check("puck_protrusion negative = air gap (v10 bug)", mp.puck_protrusion(5.6, 7.0) < 0)
    check("puck_protrusion positive = contact (v11 fix)", mp.puck_protrusion(5.6, 5.0) > 0)
    cog = mp.combine_cog([(100.0, (0, 0, 10)), (100.0, (0, 0, 30))])
    check("combine_cog averages by volume", abs(cog[2] - 20.0) < 1e-9)
    check("cog inside footprint", mp.cog_within_footprint((0, 0), -37, 37, -42, 42, margin_mm=5))
    check("cog outside footprint", not mp.cog_within_footprint((40, 0), -37, 37, -42, 42))


# --- mating_proxies.emit_measurements ------------------------------------- #
def test_emit():
    buf = io.StringIO()
    with redirect_stdout(buf):
        mp.emit_measurements({"x": 1.23456, "bbox": [1.0, 2.0, 3.0]})
    line = buf.getvalue().strip()
    parsed = vs.parse_measurements_line(line)
    check("emit prints a parseable MEASUREMENTS_JSON line", parsed is not None)
    check("emit rounds floats to 2dp", parsed and parsed["x"] == 1.23)


# --- mating_proxies proxy geometry (needs cadquery) ----------------------- #
def test_proxies():
    if mp.cq is None:
        print("  [SKIP] proxy geometry (cadquery unavailable)")
        return
    _, info = mp.bowl_proxy(base_od_mm=114.3, depth_mm=50.0, rest_z_mm=101.6)
    check("bowl rest height = resting plane", info["bowl_rest_height"] == 101.6)
    check("bowl rim = rest + depth", info["bowl_rim_height"] == 151.6)
    _, center = mp.box_proxy(20, 20, 40, z0_mm=10)
    check("box proxy base sits at z0 (center at z0+h/2)", abs(center[2] - 30.0) < 1e-6)


if __name__ == "__main__":
    for fn in (test_parse, test_compare, test_fit_helpers, test_emit, test_proxies):
        print(fn.__name__)
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
