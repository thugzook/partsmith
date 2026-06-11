#!/usr/bin/env python3
"""
Pipeline orchestrator — runs agents in sequence.

Session 1: only the intent agent is wired up.
Sessions 2-4 will add reference, designer/reviewer, and logger agents.

Usage:
    python3 main.py                        # start from intent agent
    python3 main.py --image sketch.jpg     # include a reference image
    python3 main.py --skip-intent          # use existing outputs/intent_spec.json
"""
import argparse
import json
import sys
from pathlib import Path


def load_spec() -> dict:
    path = Path("outputs/intent_spec.json")
    if not path.exists():
        print("ERROR: No intent spec found. Run the intent agent first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def run_intent(image_path: str | None = None) -> None:
    from agents.intent_agent import run
    run(image_path=image_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="3D Print Agent Pipeline")
    parser.add_argument("--image", help="Reference image for the intent agent", default=None)
    parser.add_argument("--skip-intent", action="store_true",
                        help="Skip intent agent and use existing outputs/intent_spec.json")
    args = parser.parse_args()

    # ── Stage 1: Intent ───────────────────────────────────────────────────────
    if not args.skip_intent:
        run_intent(image_path=args.image)
    else:
        print("[Pipeline] Skipping intent agent — loading existing spec.")

    spec = load_spec()
    print(f"\n[Pipeline] Intent spec loaded. Object: {spec.get('object')}")

    # ── Stage 2: Reference ────────────────────────────────────────────────────
    if spec.get("dimensions_needed"):
        from agents.reference_agent import run as run_reference
        run_reference()
        spec = load_spec()  # reload — reference agent writes the updated spec
    else:
        print("[Pipeline] Dimensions already resolved — skipping Reference Agent.")

    # ── Stage 3: Designer / Reviewer ─────────────────────────────────────────
    from agents.designer_reviewer import run as run_designer
    run_designer()

    # ── Stage 4: Logger ───────────────────────────────────────────────────────
    from agents.logger_agent import run as run_logger
    run_logger()

    print("[Pipeline] Run complete. See outputs/session_log.json for the summary.")


if __name__ == "__main__":
    main()
