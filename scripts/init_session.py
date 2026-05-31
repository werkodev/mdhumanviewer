#!/usr/bin/env python3
"""
init_session.py — Stage S0 (deterministic, no LLM).

Create the dated session directory under .mdHumanViewer/ and write a manifest
skeleton seeded with the user's file selection. Prints the absolute session
directory path on stdout (last line) so the orchestrator can capture it and
thread it through every subsequent subagent call.

Three mutually-exclusive modes (exactly ONE required):

    # 1) legacy / back-compat: a hand- or script-built selection file
    python3 init_session.py --selection SELECTION.json [--root .] [--structure FULL.json]

    # 2) menu: print a ready-to-show markdown menu of the corpus, create nothing
    python3 init_session.py --list --structure FULL.json

    # 3) spec: resolve a comma-separated selection spec against the full
    #    structure and create the session itself (no hand-built selection)
    python3 init_session.py --select "<spec>" --structure FULL.json [--root .]

SELECTION.json (--selection) is either:
  - a JSON array of objects, each at least { "path": "...", "slug": "..." }, or
  - the full discover-shaped object (a dict with a "files" key, e.g. the output
    of parse_structure.py) — in which case the "files" array is used.

Each entry needs a slug and a source path. The path key may be either "path"
(the canonical selection key) or "file_path" (the key structure.json's files[]
use), so a subset of parse_structure.py's output can be fed straight through
without renaming. "token_estimate" is optional.

The --select spec is a comma-separated mix of slugs, ROOT-relative file paths,
directory names, globs (e.g. `skills/*`), and the reserved token `all`. The
reserved token `all` (matches every file) is resolved first; otherwise each
token is tried against the ordered categories slug -> exact path -> directory
-> glob and the FIRST category that yields >=1 match wins (not a union). A
token matching nothing is an error (non-zero exit).

Folder name format: yyyy-mm-dd_HH-MM  (sorts naturally, no ':' which breaks
some filesystems/shells). On collision within the same minute, a numeric suffix
is appended: _2, _3, ... The folder name uses LOCAL time for UX; the manifest
`created_at` is UTC (with a Z suffix) for unambiguous cross-machine comparison.
"""
import argparse
import datetime
import fnmatch
import json
import os
import sys


def _scope_file(f, selected_slugs):
    """Return a copy of a structure.json file entry with its links' resolved_slug
    dropped when the target is not in the selection, so the renderer never emits
    an in-page cross-file anchor to an unselected (un-rendered) file."""
    out = dict(f)
    links = f.get("links")
    if isinstance(links, list):
        scoped_links = []
        for link in links:
            lk = dict(link)
            if lk.get("resolved_slug") not in selected_slugs:
                lk.pop("resolved_slug", None)
            scoped_links.append(lk)
        out["links"] = scoped_links
    return out


def _dir_of(file_path):
    """Group/directory key for a file_path: dirname, or '.' when at ROOT."""
    d = os.path.dirname(file_path or "")
    return d if d else "."


def _load_structure(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _render_menu(full):
    """Build a ready-to-show markdown menu of the corpus, grouped by directory.

    For each group (os.path.dirname of file_path, '.' when at ROOT), a heading
    line with the dir, file count, and summed token_estimate (labelled
    estimate); then one line per file: slug — title — ~token_estimate.
    Groups sorted by name, files by path."""
    files = full.get("files", []) or []
    groups = {}
    for f in files:
        groups.setdefault(_dir_of(f.get("file_path", "")), []).append(f)

    lines = []
    for group in sorted(groups):
        entries = sorted(groups[group], key=lambda f: f.get("file_path", ""))
        total = sum(int(f.get("token_estimate") or 0) for f in entries)
        n = len(entries)
        plural = "file" if n == 1 else "files"
        lines.append(f"## {group} — {n} {plural} — ~{total} tokens (estimate)")
        for f in entries:
            slug = f.get("slug", "")
            title = f.get("title", "") or ""
            est = int(f.get("token_estimate") or 0)
            lines.append(f"- {slug} — {title} — ~{est} tokens")
        lines.append("")
    # Trailing blank line removed for a tidy menu.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _resolve_spec(full, spec):
    """Resolve a comma-separated --select spec to a set of selected slugs.

    Reserved token `all` is handled first; otherwise each token is tried
    against ordered categories (slug exact, file_path exact, directory, glob)
    and the FIRST category yielding >=1 match wins. A token matching nothing
    raises ValueError. Returns the set of resolved slugs."""
    files = full.get("files", []) or []
    by_slug = {f.get("slug"): f for f in files if f.get("slug")}

    tokens = [t.strip() for t in spec.split(",")]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise ValueError("--select spec is empty")

    selected = set()
    for tok in tokens:
        if tok == "all":
            selected.update(s for s in by_slug)
            continue

        # (a) slug exact
        if tok in by_slug:
            selected.add(tok)
            continue

        # (b) file_path exact (ROOT-relative)
        path_hits = {f["slug"] for f in files
                     if f.get("slug") and f.get("file_path") == tok}
        if path_hits:
            selected.update(path_hits)
            continue

        # (c) directory — all files directly in that dir or under it
        norm = tok.rstrip("/")
        dir_hits = set()
        for f in files:
            if not f.get("slug"):
                continue
            fp = f.get("file_path", "")
            d = os.path.dirname(fp)
            if d == norm or d.startswith(norm + "/"):
                dir_hits.add(f["slug"])
        if dir_hits:
            selected.update(dir_hits)
            continue

        # (d) glob (fnmatch on file_path)
        glob_hits = {f["slug"] for f in files
                     if f.get("slug") and fnmatch.fnmatch(f.get("file_path", ""), tok)}
        if glob_hits:
            selected.update(glob_hits)
            continue

        raise ValueError(f"--select token matched nothing: {tok!r}")

    return selected


def _write_session(args, selected, full):
    """Create the session dir, write the manifest (and, when `full` is given,
    the scoped structure.json + per-file slices). `selected` is the validated
    list of selection entries (dicts with at least path/file_path + slug).
    Prints the abspath of the session dir as the final stdout line."""
    base = os.path.join(args.root, ".mdHumanViewer")
    os.makedirs(base, exist_ok=True)  # created if absent, ignored if present

    # folder name in local time for UX; manifest timestamp in UTC for unambiguous comparison.
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    local_now = utc_now.astimezone()
    stamp = local_now.strftime("%Y-%m-%d_%H-%M")
    session = stamp
    session_dir = os.path.join(base, session)
    n = 2
    while os.path.exists(session_dir):
        session = f"{stamp}_{n}"
        session_dir = os.path.join(base, session)
        n += 1
    # Both per-file artifact subdirectories are created up front.
    os.makedirs(os.path.join(session_dir, "analysis"))
    os.makedirs(os.path.join(session_dir, "fragments"))

    manifest = {
        "session": session,
        "session_dir": os.path.normpath(session_dir),
        "created_at": utc_now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "root": args.root,
        "output_language": args.language,
        "selected_files": [
            {"path": s.get("path") or s.get("file_path"), "slug": s["slug"],
             **({"token_estimate": s["token_estimate"]}
                if s.get("token_estimate") is not None else {})}
            for s in selected
        ],
        # manifest step keys — EXACTLY these, all seeded pending. The orchestrator
        # marks each done as it completes (the script cannot know whether
        # preflight/parse already ran).
        "steps": {
            "preflight": {"status": "pending"},
            "parse":     {"status": "pending"},
            "render":    {"status": "pending"},
            "verify":    {"status": "pending"},
            "crossfile": {"status": "pending"},
            "assemble":  {"status": "pending"},
            "report":    {"status": "pending"},
        },
    }
    with open(os.path.join(session_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # Scope structure.json to the selection. parse_structure.py emits the WHOLE
    # ROOT corpus; only the selected files get rendered, so the session must hold
    # a structure.json restricted to the selected slugs (with the graph pruned)
    # — otherwise assemble.py's coverage/anchor gates fail on every unselected
    # file that has no fragment. This is the single deterministic step that keeps
    # the orchestrator from ever needing to hand-edit structure.json.
    if full is not None:
        selected_slugs = {s["slug"] for s in selected}
        scoped = dict(full)  # shallow copy; we replace files + graph below
        scoped_files = [
            _scope_file(f, selected_slugs)
            for f in full.get("files", []) if f.get("slug") in selected_slugs
        ]
        scoped["files"] = scoped_files
        graph = full.get("graph") or {}
        scoped["graph"] = {
            "nodes": [nd for nd in graph.get("nodes", [])
                      if nd.get("slug") in selected_slugs],
            "edges": [e for e in graph.get("edges", [])
                      if e.get("from") in selected_slugs
                      and e.get("to") in selected_slugs],
        }
        if isinstance(scoped.get("meta"), dict):
            scoped["meta"] = {**scoped["meta"], "file_count": len(scoped["files"])}
        # groups[] is whole-corpus only — drop it so stale whole-corpus groups
        # never leak into the scoped artifact (review #5).
        scoped.pop("groups", None)
        with open(os.path.join(session_dir, "structure.json"), "w",
                  encoding="utf-8") as f:
            json.dump(scoped, f, ensure_ascii=False, indent=2)
            f.write("\n")

        # Per-file slices, built from the SCOPED files[] (post-_scope_file) so
        # resolved_slug to unselected files is already pruned — the renderer's
        # STRUCTURE_SLICE. One slice per selected file.
        slices_dir = os.path.join(session_dir, "slices")
        os.makedirs(slices_dir)
        for f in scoped_files:
            slug = f.get("slug")
            slice_obj = {
                "slug": slug,
                "file_path": f.get("file_path"),
                "title": f.get("title"),
                "language": f.get("language"),
                "headings": f.get("headings", []),
                "links": f.get("links", []),
            }
            with open(os.path.join(slices_dir, f"{slug}.json"), "w",
                      encoding="utf-8") as sf:
                json.dump(slice_obj, sf, ensure_ascii=False, indent=2)
                sf.write("\n")

        missing = selected_slugs - {f.get("slug") for f in full.get("files", [])}
        if missing:
            sys.stderr.write(
                "Warning: selected slugs not found in structure.json: "
                f"{sorted(missing)}\n"
            )

    # Human-readable confirmation, then the absolute path as the final line.
    sys.stderr.write(f"Session created: {session_dir} ({len(selected)} files)\n")
    # The orchestrator runs from CWD; emit an absolute, CWD-resolvable path so the
    # captured ${SESSION} works regardless of where downstream steps run.
    print(os.path.abspath(session_dir))


def _validate_selection(selected):
    """Validate a selection list (array of entries). Errors to stderr + exit."""
    if not isinstance(selected, list) or len(selected) == 0:
        sys.stderr.write("Error: selection must be a non-empty JSON array.\n")
        sys.exit(1)
    for i, s in enumerate(selected):
        if not isinstance(s, dict):
            sys.stderr.write(f"Error: selection entry #{i} is not an object.\n")
            sys.exit(1)
        # `path` is the canonical key; `file_path` (structure.json's key) is
        # accepted as an alias so parse_structure.py output feeds through as-is.
        if not (s.get("path") or s.get("file_path")):
            sys.stderr.write(
                f"Error: selection entry #{i} is missing required field 'path'.\n"
            )
            sys.exit(1)
        if not s.get("slug"):
            sys.stderr.write(
                f"Error: selection entry #{i} is missing required field 'slug'.\n"
            )
            sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", default=None,
                    help="Path to JSON file with the selected files array "
                         "(or the discover-shaped object with a 'files' key).")
    ap.add_argument("--list", dest="list_mode", action="store_true",
                    help="Print a ready-to-show markdown menu of the corpus "
                         "(requires --structure); create no session.")
    ap.add_argument("--select", default=None,
                    help="Comma-separated selection spec (slugs, paths, dirs, "
                         "globs, or 'all') resolved against --structure.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--language", default="source",
                    help="Output language for the final HTML ('source' or a tag like 'ru').")
    ap.add_argument("--structure", default=None,
                    help="Path to the full structure.json from parse_structure.py. "
                         "When given, a SCOPED structure.json (selected slugs only, "
                         "graph pruned) + per-file slices are written into the "
                         "session dir. Required by --list and --select.")
    args = ap.parse_args()

    # Exactly ONE of --list / --select / --selection must be supplied.
    modes_supplied = sum([
        bool(args.list_mode),
        args.select is not None,
        args.selection is not None,
    ])
    if modes_supplied == 0:
        sys.stderr.write(
            "Error: exactly one of --list / --select / --selection is required.\n"
        )
        sys.exit(2)
    if modes_supplied > 1:
        sys.stderr.write(
            "Error: --list, --select and --selection are mutually exclusive; "
            "supply exactly one.\n"
        )
        sys.exit(2)

    # --list and --select both REQUIRE --structure.
    if (args.list_mode or args.select is not None) and not args.structure:
        sys.stderr.write("Error: --structure is required with --list/--select.\n")
        sys.exit(2)

    # --list MODE: print the menu, create nothing, exit 0.
    if args.list_mode:
        full = _load_structure(args.structure)
        print(_render_menu(full))
        return

    # --select MODE: resolve the spec against the full structure, then create.
    if args.select is not None:
        full = _load_structure(args.structure)
        try:
            slugs = _resolve_spec(full, args.select)
        except ValueError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
        # Build the selection list from the resolved slugs, preserving the order
        # files[] appear in the structure for stable output.
        selected = []
        for f in full.get("files", []):
            if f.get("slug") in slugs:
                selected.append({
                    "path": f.get("file_path"),
                    "slug": f.get("slug"),
                    **({"token_estimate": f["token_estimate"]}
                       if f.get("token_estimate") is not None else {}),
                })
        _validate_selection(selected)
        _write_session(args, selected, full)
        return

    # --selection MODE (legacy / back-compat).
    with open(args.selection, "r", encoding="utf-8") as f:
        selected = json.load(f)
    # Accept either a bare array or the discover-shaped object containing files.
    if isinstance(selected, dict) and "files" in selected:
        selected = selected["files"]
    _validate_selection(selected)

    full = None
    if args.structure:
        full = _load_structure(args.structure)
    _write_session(args, selected, full)


if __name__ == "__main__":
    main()
