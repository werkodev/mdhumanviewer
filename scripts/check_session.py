#!/usr/bin/env python3
"""
check_session.py — deterministic post-render / post-verify session validation.

Replaces the orchestrator's ad-hoc `python3 -c` existence/parse loops with one
tested helper. Given a session directory, it reads `${SESSION}/manifest.json`,
and for every selected slug verifies that:

  - `fragments/<slug>.html` exists and is non-empty, and
  - `analysis/<slug>.json` exists and parses as JSON.

Additionally, IF `${SESSION}/findings.json` exists it must parse as JSON and be
a root OBJECT carrying a `cross_file_findings` key whose value is a list (the
schema in references/schemas.md §4 — a bare `[]` or a `{}` missing the key is
malformed). An ABSENT findings.json is fine (it is written later in the pipeline).

Prints a JSON report to stdout:

    {"ok": bool,
     "missing_fragments": [<slug>, ...],
     "unparsable_analysis": [<slug>, ...],
     "bad_findings": bool}

Exits 0 iff ok (no missing fragments, no unparsable analysis, findings
ok-or-absent); non-zero otherwise. stdlib only.

Usage:
    python3 check_session.py SESSION_DIR
"""
import argparse
import json
import os
import sys


def _read_manifest(session_dir):
    path = os.path.join(session_dir, "manifest.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _findings_ok(session_dir):
    """Return True iff findings.json is absent OR a well-formed root object with
    a `cross_file_findings` list."""
    path = os.path.join(session_dir, "findings.json")
    if not os.path.exists(path):
        return True
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    cff = data.get("cross_file_findings")
    return isinstance(cff, list)


def check_session(session_dir):
    """Validate a session dir. Returns the report dict."""
    manifest = _read_manifest(session_dir)
    selected = manifest.get("selected_files") or []

    missing_fragments = []
    unparsable_analysis = []

    for entry in selected:
        slug = entry.get("slug")
        if not slug:
            continue

        frag = os.path.join(session_dir, "fragments", slug + ".html")
        if not (os.path.isfile(frag) and os.path.getsize(frag) > 0):
            missing_fragments.append(slug)

        analysis = os.path.join(session_dir, "analysis", slug + ".json")
        ok_analysis = False
        if os.path.isfile(analysis):
            try:
                with open(analysis, "r", encoding="utf-8") as f:
                    json.load(f)
                ok_analysis = True
            except (ValueError, OSError):
                ok_analysis = False
        if not ok_analysis:
            unparsable_analysis.append(slug)

    bad_findings = not _findings_ok(session_dir)

    ok = (not missing_fragments) and (not unparsable_analysis) and (not bad_findings)
    return {
        "ok": ok,
        "missing_fragments": missing_fragments,
        "unparsable_analysis": unparsable_analysis,
        "bad_findings": bad_findings,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", help="Path to the session directory.")
    args = ap.parse_args()

    session_dir = args.session
    manifest_path = os.path.join(session_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        sys.stderr.write(
            f"Error: manifest.json not found in session dir: {session_dir}\n"
        )
        sys.exit(2)

    try:
        report = check_session(session_dir)
    except (ValueError, OSError) as e:
        sys.stderr.write(f"Error: failed to read session: {e}\n")
        sys.exit(2)

    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
