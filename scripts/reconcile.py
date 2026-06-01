#!/usr/bin/env python3
"""reconcile.py — Stage S2.5 (deterministic, no LLM).

Runs AFTER verify and BEFORE assemble, per selected file. It closes the two
*mechanical* gate properties — gate 2 (coverage) and gate 3 (contract fidelity) —
**losslessly** so ``assemble.py`` passes on the first attempt, and flags only
genuine content loss for a bounded renderer/verifier re-invoke.

It is the deterministic form of the join the verifier already performs in prose;
crucially it is **language-agnostic** (drives coverage from
``analysis.sections[].source_headings[]``, never from rendered heading TEXT) and
**lossless** (never snaps a contract across a negation, never drops a real
contract — the ONLY removal allowed is an exact-duplicate ``contracts[]`` entry).

What it does, per file ``<slug>`` that has BOTH ``fragments/<slug>.html`` and
``analysis/<slug>.json``:

* **COVERAGE (gate 2):** for every source heading id ``<slug>--<anchor>`` from
  the scoped ``structure.json`` that is NOT already emitted in the fragment's
  ``data-src-heading`` values, look for an ``analysis.sections[]`` entry whose
  ``source_headings[]`` contains the bare ``<anchor>``. If found, the content is
  represented and only the id bookkeeping was missed → append a sanctioned
  hidden anchor span carrying that exact id (gate-1-safe: the id IS in
  structure.json). If NO analysis section claims the anchor → genuine content
  loss → record under ``genuine_gaps.coverage`` and NEVER fabricate.

* **CONTRACTS (gate 3), lossless only:** for each ``analysis.contracts[].text``,
  with ``needle = normalize(text)``:
  (a) if ``contract_present(needle, src_norm)`` is TRUE but
  ``contract_present(needle, frag_norm)`` is FALSE → the contract is
  source-faithful but not shown: append an un-collapsed ``.mdhv-contract``
  callout carrying the HTML-escaped text (NO wording change) →
  ``auto_closed.contracts``.
  (b) if ``contract_present(needle, src_norm)`` is FALSE → do NOT snap or drop
  (a deterministic snap could cross a ``not``/``never``/``unless`` and invert
  meaning) → record under ``genuine_gaps.contracts`` and route to the verifier.
  The ONLY deterministic removal allowed is an EXACT-DUPLICATE ``contracts[]``
  entry (same normalized text twice) → remove the duplicate from analysis →
  ``auto_closed.contracts``.

* **ANCHORS (gate 1), DETECT-ONLY parity:** mirror ``assemble.py``'s gate-1
  exactly (same HTML-aware ``gates.hrefs_in`` over REAL ``<a>`` elements, same
  ``<slug>--<anchor>`` id space, same chrome set). A genuinely dangling in-page
  anchor is escalated under ``genuine_gaps.anchors`` (never auto-unwrapped), so a
  clean reconcile guarantees S4's anchor gate passes first try. This closes the
  prior gap where reconcile ignored gate 1 → exit 0 → assemble then failed on it.

Writes the fixed fragment(s) + analysis (dedupe only) in place, prints the JSON
report ``{auto_closed:{coverage:[],contracts:[],anchors:[]}, genuine_gaps:
{coverage:[],contracts:[],anchors:[]}}`` to stdout, and exits 0 iff
``genuine_gaps`` is empty (else non-zero).

Usage:
    python3 reconcile.py --session SESSION_DIR

Stdlib-only. Python 3.9+.
"""
import argparse
import html
import json
import os
import sys

# Single-source the gate primitives + normalize. The package import works when
# run as ``python3 -m scripts.reconcile``; the sys.path shim below makes a direct
# ``python3 scripts/reconcile.py`` invocation (which imports ``scripts.gates``)
# work too — the SAME shim assemble.py and gates.py use.
try:
    from scripts.constants import ALLOWED_CHROME_ANCHORS, normalize
    from scripts.gates import (
        concatenated_contract_text,
        contract_present,
        data_src_headings_in,
        file_heading_ids,
        hrefs_in,
        structure_heading_ids,
    )
except ModuleNotFoundError:  # pragma: no cover - import shim for direct runs
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.constants import ALLOWED_CHROME_ANCHORS, normalize  # noqa: E402
    from scripts.gates import (  # noqa: E402
        concatenated_contract_text,
        contract_present,
        data_src_headings_in,
        file_heading_ids,
        hrefs_in,
        structure_heading_ids,
    )


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _section_id_for_anchor(analysis, bare_anchor):
    """Return the analysis ``sections[].id`` whose ``source_headings[]`` contains
    the bare heading ``anchor``, or ``None`` if no section claims it.

    This is the language-agnostic JOIN: it matches on the source heading anchor
    recorded by the renderer, NOT on rendered heading text (which differs on a
    translation run). The bare anchor is what ``source_headings[]`` stores
    (schemas.md §2); the emitted page id is ``<slug>--<anchor>``.
    """
    for sec in analysis.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        src_headings = sec.get("source_headings") or []
        if bare_anchor in src_headings:
            return sec.get("id")
    return None


def _hidden_anchor_span(emit_id):
    """A sanctioned hidden anchor span carrying a valid ``<slug>--<anchor>`` id.

    The span is gate-1-safe (the id IS in structure.json) and gate-2-satisfying
    (its ``data-src-heading`` carries the id). It is visually hidden and
    contributes no prose, so it adds zero content — it only fixes the id
    bookkeeping the renderer missed.
    """
    sid = html.escape(emit_id, quote=True)
    return (
        f'<span id="{sid}" data-src-heading="{sid}" aria-hidden="true"></span>'
    )


def _insert_span_after_section(fragment, section_id, span):
    """Insert ``span`` right after the ``.mdhv-section`` whose ``id`` matches
    ``section_id`` (right after that opening tag's ``>``), else append at the end.

    A best-effort, lossless placement: putting the hidden span just inside the
    carrying section keeps the document order sensible. If the section id cannot
    be located by a simple ``id="..."`` scan, we fall back to appending at the
    fragment end — either way the id ends up emitted, which is all gate 2 needs.
    """
    if section_id:
        for quote in ('"', "'"):
            needle = f'id={quote}{section_id}{quote}'
            idx = fragment.find(needle)
            if idx != -1:
                close = fragment.find(">", idx)
                if close != -1:
                    return fragment[: close + 1] + span + fragment[close + 1:]
    return fragment + span


def _contract_callout(text):
    """An un-collapsed ``.mdhv-contract`` callout carrying the HTML-escaped
    contract text, with NO wording change."""
    return f'<div class="mdhv-contract">{html.escape(text)}</div>'


def reconcile_file(structure_file, analysis, fragment, src_path,
                   id_space=None, chrome=frozenset()):
    """Reconcile one file. Returns ``(new_fragment, new_analysis, result)``.

    ``result`` is a dict with per-file ``auto_closed`` / ``genuine_gaps`` id +
    contract + anchor lists. ``new_analysis`` is the (possibly dedup-edited)
    analysis. Pure on its inputs — no disk writes here; the caller writes back
    only if something changed.

    ``id_space`` (the run-wide ``{<slug>--<anchor>}`` set) and ``chrome`` (the
    allowed chrome anchors) drive the gate-1 PARITY check: when supplied,
    reconcile detects the SAME dangling in-page anchors ``assemble.py`` would, so
    a clean reconcile guarantees S4 passes first try. This pass is DETECT-ONLY —
    it never mutates the fragment (no silent ``<a>`` unwrap), only escalates a
    genuinely broken anchor to the verifier/renderer via ``genuine_gaps``.
    """
    slug = structure_file.get("slug", "")
    auto_coverage = []
    gap_coverage = []
    auto_contracts = []
    gap_contracts = []
    auto_anchors = []
    gap_anchors = []

    # ----- COVERAGE (gate 2), via the source_headings JOIN (NOT text) ---------
    source_ids = file_heading_ids(structure_file)  # {<slug>--<anchor>}
    emitted = data_src_headings_in(fragment)
    missing = source_ids - emitted

    for emit_id in sorted(missing):
        # Recover the bare anchor: strip the leading "<slug>--".
        prefix = f"{slug}--"
        bare_anchor = emit_id[len(prefix):] if emit_id.startswith(prefix) else emit_id
        section_id = _section_id_for_anchor(analysis, bare_anchor)
        if section_id is not None:
            # The content is represented; only the id bookkeeping was missed.
            fragment = _insert_span_after_section(
                fragment, section_id, _hidden_anchor_span(emit_id)
            )
            auto_coverage.append(emit_id)
        else:
            # No analysis section claims the anchor -> genuine content loss.
            gap_coverage.append(emit_id)

    # ----- CONTRACTS (gate 3), LOSSLESS only ----------------------------------
    contracts = analysis.get("contracts") or []
    new_contracts = []
    seen_norm = set()
    src_norm = normalize(_read_text(src_path)) if src_path and os.path.exists(src_path) else ""

    for c in contracts:
        if not isinstance(c, dict):
            new_contracts.append(c)
            continue
        text = c.get("text")
        needle = normalize(text) if text else ""

        # Exact-duplicate removal (the ONLY deterministic removal allowed).
        if needle and needle in seen_norm:
            auto_contracts.append(f"{slug}: duplicate contract removed: {needle!r}")
            continue  # drop the duplicate from analysis
        if needle:
            seen_norm.add(needle)
        new_contracts.append(c)

        if not needle:
            continue

        frag_norm = concatenated_contract_text(fragment)
        in_source = contract_present(needle, src_norm)
        in_fragment = contract_present(needle, frag_norm)

        if in_source and not in_fragment:
            # Source-faithful but not shown: fix only the FRAGMENT side, no
            # wording change. Append an un-collapsed .mdhv-contract callout.
            fragment = fragment + _contract_callout(text)
            auto_contracts.append(f"{slug}: contract surfaced in fragment: {needle!r}")
        elif not in_source:
            # NOT present in source -> never snap (could invert a negation) and
            # never drop (would paper over a real contract). Escalate.
            gap_contracts.append(
                f"{slug}: contract not found in source bytes: {needle!r}"
            )

    # ----- ANCHORS (gate 1), DETECT-ONLY parity with assemble -----------------
    # Mirror assemble.run_gates' gate-1 EXACTLY (same gates.hrefs_in, same id
    # space, same chrome set) so reconcile sees what assemble sees. We escalate a
    # genuinely dangling in-page anchor (a real <a href="#x"> that resolves to no
    # heading id and is not a chrome anchor) instead of mutating the fragment.
    # After the HTML-aware hrefs_in fix, documented href examples inside <code>
    # are no longer flagged, so this rarely fires — it is the parity guarantee.
    if id_space is not None:
        for href in hrefs_in(fragment):
            # bare "#" is a no-op anchor, tolerate it exactly as assemble's
            # gate 1 does (assemble.run_gates) so the two verdicts can't diverge.
            if href == "#":
                continue
            if href in chrome:
                continue
            target = href[1:] if href.startswith("#") else href
            if target not in id_space:
                gap_anchors.append(
                    f"{slug}: href {href!r} resolves to no heading id or chrome anchor"
                )

    new_analysis = dict(analysis)
    new_analysis["contracts"] = new_contracts

    result = {
        "auto_coverage": auto_coverage,
        "gap_coverage": gap_coverage,
        "auto_contracts": auto_contracts,
        "gap_contracts": gap_contracts,
        "auto_anchors": auto_anchors,
        "gap_anchors": gap_anchors,
    }
    return fragment, new_analysis, result


def reconcile_session(session_dir):
    """Reconcile every selected file in a session. Returns the report dict."""
    structure = _load_json(os.path.join(session_dir, "structure.json"))
    files = structure.get("files") or []

    # Run-wide gate-1 inputs, computed once and shared with every file so
    # reconcile's anchor check is identical to assemble.run_gates'.
    id_space = structure_heading_ids(structure)
    chrome = set(ALLOWED_CHROME_ANCHORS)

    report = {
        "auto_closed": {"coverage": [], "contracts": [], "anchors": []},
        "genuine_gaps": {"coverage": [], "contracts": [], "anchors": []},
    }

    for f in files:
        slug = f.get("slug", "")
        if not slug:
            continue
        frag_path = os.path.join(session_dir, "fragments", slug + ".html")
        analysis_path = os.path.join(session_dir, "analysis", slug + ".json")
        if not (os.path.isfile(frag_path) and os.path.isfile(analysis_path)):
            # Nothing to reconcile against; leave it to check_session/assemble.
            continue

        try:
            analysis = _load_json(analysis_path)
        except (ValueError, OSError):
            continue
        if not isinstance(analysis, dict):
            continue
        fragment = _read_text(frag_path)
        src_path = f.get("file_path")

        new_fragment, new_analysis, res = reconcile_file(
            f, analysis, fragment, src_path, id_space=id_space, chrome=chrome
        )

        # Write back ONLY when something actually changed (idempotent on a clean
        # session: byte-identical files are never rewritten).
        if new_fragment != fragment:
            _write_text(frag_path, new_fragment)
        if new_analysis.get("contracts") != analysis.get("contracts"):
            with open(analysis_path, "w", encoding="utf-8") as fh:
                json.dump(new_analysis, fh, ensure_ascii=False, indent=2)

        report["auto_closed"]["coverage"].extend(res["auto_coverage"])
        report["auto_closed"]["contracts"].extend(res["auto_contracts"])
        report["auto_closed"]["anchors"].extend(res["auto_anchors"])
        report["genuine_gaps"]["coverage"].extend(res["gap_coverage"])
        report["genuine_gaps"]["contracts"].extend(res["gap_contracts"])
        report["genuine_gaps"]["anchors"].extend(res["gap_anchors"])

    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic reconcile (S2.5).")
    ap.add_argument("--session", required=True, help="Path to the session dir.")
    args = ap.parse_args(argv)

    session_dir = args.session
    structure_path = os.path.join(session_dir, "structure.json")
    if not os.path.isfile(structure_path):
        sys.stderr.write(
            f"Error: structure.json not found in session dir: {session_dir}\n"
        )
        return 2

    try:
        report = reconcile_session(session_dir)
    except (ValueError, OSError) as e:
        sys.stderr.write(f"Error: reconcile failed: {e}\n")
        return 2

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

    gaps = report["genuine_gaps"]
    has_gaps = (
        bool(gaps["coverage"]) or bool(gaps["contracts"]) or bool(gaps["anchors"])
    )
    return 1 if has_gaps else 0


if __name__ == "__main__":
    sys.exit(main())
