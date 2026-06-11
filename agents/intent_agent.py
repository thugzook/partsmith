#!/usr/bin/env python3
"""
Intent Agent — conversational clarification loop that builds a structured
JSON spec for a 3D print design request.

Usage:
    python3 agents/intent_agent.py
    python3 agents/intent_agent.py --image path/to/sketch.jpg

Output:
    outputs/intent_spec.json  (written only when user types "proceed")
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

import anthropic

# ── Config ───────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"

# The system prompt tells Claude how to structure every response.
# Three-part format keeps parsing simple and output readable.
SYSTEM_PROMPT = """\
You are an intent agent for a 3D printing pipeline. Your job:
1. Understand what the user wants to print through friendly conversation.
2. Build a structured JSON spec incrementally — update it after every message.
3. Ask ONE targeted question at a time, starting with the most critical unknown.

After each user message, respond in EXACTLY this format (no deviations):

THINKING: [1-2 sentences on what you just learned and what the biggest gap is]

SPEC_UPDATE:
```json
{complete updated spec as valid JSON}
```

QUESTION: [one clear, specific question to fill the most important gap]

The spec must include these core fields (add project-specific fields as needed):
- "object": what they want to print (string or null)
- "purpose": what it needs to do (string or null)
- "quantity": how many items/slots (number or null)
- "material": PLA / PETG / TPU / etc. (string or null)
- "printer": their printer model (string or null)
- "dimensions_known": dict of known measurements in mm (empty dict if none)
- "dimensions_needed": list of measurements still unknown (empty list if none)
- "constraints": list of hard functional requirements (empty list if none)
- "confidence": "low", "medium", or "high" followed by a brief reason

Use null for unknown scalar fields. Prioritize: functional constraints and
critical dimensions first, material/printer second, aesthetics last.

When confidence reaches "high", remind the user they can type "proceed" to
save the spec and move to the next agent, or keep refining."""

# ── Helpers ──────────────────────────────────────────────────────────────────

def display_spec(spec: dict) -> None:
    """Pretty-print the current spec so the user can see it at a glance."""
    print("\n" + "─" * 52)
    print("  CURRENT SPEC")
    print("─" * 52)
    for key, value in spec.items():
        if value is None:
            display_val = "[ unknown ]"
        elif value in ([], {}):
            display_val = "[ none yet ]"
        elif isinstance(value, (dict, list)):
            display_val = json.dumps(value)
        else:
            display_val = str(value)
        # Truncate long values so the table stays readable
        if len(display_val) > 55:
            display_val = display_val[:52] + "..."
        print(f"  {key:<26} {display_val}")
    print("─" * 52 + "\n")


def parse_response(text: str) -> tuple[str, dict | None, str]:
    """
    Split Claude's structured response into (thinking, spec, question).
    Returns None for spec if the JSON block can't be parsed — the caller
    keeps the previous spec in that case rather than wiping it.
    """
    thinking, spec, question = "", None, ""

    if "THINKING:" in text:
        start = text.index("THINKING:") + len("THINKING:")
        end = text.find("SPEC_UPDATE:", start)
        thinking = text[start:end if end != -1 else None].strip()

    if "```json" in text:
        js = text.index("```json") + 7
        je = text.index("```", js)
        try:
            spec = json.loads(text[js:je].strip())
        except json.JSONDecodeError:
            pass  # Keep caller's existing spec; non-fatal

    if "QUESTION:" in text:
        q = text.rindex("QUESTION:") + len("QUESTION:")
        question = text[q:].strip()

    return thinking, spec, question


def encode_image(path: str) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file."""
    ext = Path(path).suffix.lower()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode(), media_type


def save_spec(spec: dict) -> None:
    """Write the frozen spec to outputs/intent_spec.json."""
    out = Path("outputs")
    out.mkdir(exist_ok=True)
    path = out / "intent_spec.json"
    with open(path, "w") as f:
        json.dump(spec, f, indent=2)
    print(f"\n[Agent] Spec saved → {path}")
    print("[Agent] Ready for Session 2: Reference Agent will resolve dimensions.\n")
    display_spec(spec)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(image_path: str | None = None) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY before running.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    history: list[dict] = []   # Full conversation sent to the API each turn
    spec: dict = {}             # Tracks the latest parsed spec

    print("\n" + "=" * 52)
    print("  3D PRINT INTENT AGENT")
    print("  Describe what you want to print.")
    print("  Type 'proceed' to save the spec.  'quit' to exit.")
    print("=" * 52 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting without saving.")
            sys.exit(0)

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("Exiting without saving.")
            sys.exit(0)

        # "proceed" gate — only saves after explicit user approval
        if user_input.lower() == "proceed":
            if not spec:
                print("[Agent] Nothing to save yet — describe your project first.\n")
                continue
            confidence = str(spec.get("confidence", "")).lower()
            if confidence.startswith("low"):
                print("[Agent] Confidence is still low. Type 'proceed' again to save anyway.")
                confirm = input("You: ").strip()
                if confirm.lower() != "proceed":
                    print("[Agent] OK, let's keep refining.\n")
                    continue
            save_spec(spec)
            break

        # Build message content — attach image on first turn only if provided
        if image_path and not history:
            img_data, img_media = encode_image(image_path)
            content = [
                {"type": "image",
                 "source": {"type": "base64", "media_type": img_media, "data": img_data}},
                {"type": "text", "text": user_input},
            ]
        else:
            content = user_input

        history.append({"role": "user", "content": content})

        # Call Claude — full history sent each turn for complete context
        print("\n[thinking...]\n")
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=history,
            )
        except anthropic.APIError as e:
            print(f"[Agent] API error: {e}\n")
            history.pop()   # Remove the message that failed so user can retry
            continue

        assistant_text = response.content[0].text
        history.append({"role": "assistant", "content": assistant_text})

        thinking, new_spec, question = parse_response(assistant_text)

        if thinking:
            print(f"[Thinking] {thinking}\n")

        if new_spec:
            spec = new_spec
            display_spec(spec)

        if question:
            print(f"Agent: {question}\n")
        else:
            # Fallback if Claude didn't follow the format
            print(f"Agent: {assistant_text}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clarify a 3D print request into a structured JSON spec"
    )
    parser.add_argument(
        "--image",
        help="Optional path to a sketch or reference image (JPEG, PNG, WebP)",
        default=None,
    )
    args = parser.parse_args()

    if args.image and not Path(args.image).exists():
        print(f"ERROR: Image not found: {args.image}")
        sys.exit(1)

    run(image_path=args.image)
