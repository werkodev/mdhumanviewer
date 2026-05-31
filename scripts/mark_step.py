#!/usr/bin/env python3
"""
mark_step.py — deterministic manifest step update (no LLM, no hand Edits).

Update ${SESSION}/manifest.json's `steps.<step>` entry to a new status (plus any
metadata) via a full read-modify-write. Replaces the orchestrator hand-editing
manifest.json, which is fragile and inconsistent per run.

Usage:
    python3 mark_step.py SESSION STEP STATUS [--meta KEY=VALUE ...]

  SESSION   path to the session directory (containing manifest.json)
  STEP      one of: preflight, parse, render, verify, crossfile, assemble, report
  STATUS    one of: pending, running, done, failed
  --meta    repeatable KEY=VALUE pairs merged into the step object alongside status

The step object is REPLACED with {"status": STATUS, ...meta}. The known step set
is validated against init_session.py's manifest skeleton; an unknown step key or
status is an error (message to stderr, non-zero exit).

CONCURRENCY (review #9): this is a full read-modify-write of manifest.json. Step
marking is SERIAL in the orchestrator — do NOT mark steps concurrently, or a
write can clobber a sibling's update.

stdlib only; Python 3.9+.
"""
import argparse
import json
import os
import sys

# The exact manifest step keys init_session.py seeds (one source of truth, mirrored).
KNOWN_STEPS = {
    "preflight", "parse", "render", "verify", "crossfile", "assemble", "report",
}
KNOWN_STATUSES = {"pending", "running", "done", "failed"}


def _parse_meta(pairs):
    """Turn a list of 'KEY=VALUE' strings into a dict. The first '=' splits;
    later '='s are part of the value. Empty key errors."""
    meta = {}
    for item in pairs or []:
        if "=" not in item:
            sys.stderr.write(
                f"Error: --meta expects KEY=VALUE, got {item!r}.\n"
            )
            sys.exit(1)
        key, value = item.split("=", 1)
        if key == "":
            sys.stderr.write(
                f"Error: --meta KEY must be non-empty, got {item!r}.\n"
            )
            sys.exit(1)
        meta[key] = value
    return meta


def main():
    ap = argparse.ArgumentParser(description="Update a manifest step status.")
    ap.add_argument("session", help="Path to the session directory.")
    ap.add_argument("step", help="Step key to update (one of the 7 known steps).")
    ap.add_argument("status", help="New status (pending|running|done|failed).")
    ap.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE",
                    help="Extra metadata merged into the step object (repeatable).")
    args = ap.parse_args()

    if args.step not in KNOWN_STEPS:
        sys.stderr.write(
            f"Error: unknown step {args.step!r}; "
            f"must be one of {sorted(KNOWN_STEPS)}.\n"
        )
        sys.exit(1)
    if args.status not in KNOWN_STATUSES:
        sys.stderr.write(
            f"Error: unknown status {args.status!r}; "
            f"must be one of {sorted(KNOWN_STATUSES)}.\n"
        )
        sys.exit(1)

    meta = _parse_meta(args.meta)

    manifest_path = os.path.join(args.session, "manifest.json")
    if not os.path.isfile(manifest_path):
        sys.stderr.write(f"Error: manifest not found: {manifest_path}\n")
        sys.exit(1)

    # Full read-modify-write (see CONCURRENCY note above).
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    steps = manifest.get("steps")
    if not isinstance(steps, dict):
        sys.stderr.write(
            f"Error: manifest has no 'steps' object: {manifest_path}\n"
        )
        sys.exit(1)

    # Replace the step object wholesale: status first, then any metadata.
    steps[args.step] = {"status": args.status, **meta}

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # Nothing on stdout on success; a one-line confirmation to stderr.
    sys.stderr.write(f"Step {args.step} -> {args.status} ({manifest_path})\n")


if __name__ == "__main__":
    main()
