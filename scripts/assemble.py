#!/usr/bin/env python3
"""
assemble.py — Stage S4 (deterministic, no LLM).

Stitch the per-file HTML fragments and the whole-corpus skeleton into a single
self-contained ``overview.html`` by injecting generated HTML into the committed
design system's mount markers.

This module implements the SHELL + ZONE ASSEMBLY + FRAGMENT STITCH (plan Task 5).
The four hard-fail GATES and the failure-array fields of the JSON report are
added in Task 6; this file deliberately leaves a clearly-marked insertion point
for them (see ``# === GATES (Task 6) ===``) so the gate checks run BEFORE the
output page is written and can extend the report dict in place.

Usage:
    python3 assemble.py \
        --structure  structure.json \
        --analysis-dir analysis/ \
        --fragments-dir fragments/ \
        --findings   findings.json \
        --design     references/design-system.html \
        --out        overview.html \
        [--manifest  manifest.json]

Output:
    Writes ``--out`` (the assembled overview), and prints a JSON report to
    stdout. At this task the report has at least
    ``{files, sections, findings, graph_mode}``. Errors go to stderr with a
    non-zero exit.

Stdlib-only. Python 3.9+.
"""
import argparse
import glob
import html
import json
import os
import re
import sys

# Single-source constants — imported, never redefined here. The package import
# works when run as ``python3 -m scripts.assemble``; the sys.path shim below
# makes a direct ``python3 scripts/assemble.py`` invocation work too.
try:
    from scripts.constants import (
        ALLOWED_CHROME_ANCHORS,
        REQUIRED_CLASS_HOOKS,
        REQUIRED_MOUNT_MARKERS,
        normalize,
    )
    # Shared gate primitives live in scripts.gates so assemble.py and
    # reconcile.py close exactly what the gate checks (no drift). Re-exported
    # here so ``from scripts.assemble import contract_present`` keeps working.
    from scripts.gates import (
        _ContractTextExtractor,
        concatenated_contract_text,
        contract_blocks_in,
        contract_present,
        data_src_headings_in,
        file_heading_ids,
        structure_heading_ids,
    )
except ModuleNotFoundError:  # pragma: no cover - import shim for direct runs
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.constants import (  # noqa: E402
        ALLOWED_CHROME_ANCHORS,
        REQUIRED_CLASS_HOOKS,
        REQUIRED_MOUNT_MARKERS,
        normalize,
    )
    from scripts.gates import (  # noqa: E402
        _ContractTextExtractor,
        concatenated_contract_text,
        contract_blocks_in,
        contract_present,
        data_src_headings_in,
        file_heading_ids,
        structure_heading_ids,
    )

# Keep the re-exported gate names referenced so linters/readers see they are
# re-exports of the single source in scripts.gates (some are consumed only by
# the gate run below, others only by importers like reconcile.py / tests).
_ = (_ContractTextExtractor, contract_blocks_in, concatenated_contract_text,
     contract_present, structure_heading_ids, file_heading_ids)

# Keep the imported names referenced so linters / readers see they are the
# single source even though some are only consumed by Task 6 gates.
_ = (REQUIRED_CLASS_HOOKS, ALLOWED_CHROME_ANCHORS, normalize)


def esc(s) -> str:
    """HTML-escape a value for safe text/attribute interpolation."""
    return html.escape("" if s is None else str(s), quote=True)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_inputs(args):
    """Read structure.json, findings.json, and all analysis/*.json.

    Returns ``(structure, findings, analyses)`` where ``analyses`` maps slug ->
    analysis object. Missing/empty findings is tolerated so a no-findings run
    cannot crash.
    """
    structure = _load_json(args.structure)

    findings = {}
    if args.findings and os.path.exists(args.findings):
        try:
            findings = _load_json(args.findings)
        except (ValueError, OSError):
            findings = {}
    if not isinstance(findings, dict):
        findings = {}

    analyses: dict = {}
    if args.analysis_dir and os.path.isdir(args.analysis_dir):
        for p in sorted(glob.glob(os.path.join(args.analysis_dir, "*.json"))):
            try:
                obj = _load_json(p)
            except (ValueError, OSError):
                continue
            slug = obj.get("slug") if isinstance(obj, dict) else None
            if not slug:
                slug = os.path.splitext(os.path.basename(p))[0]
            analyses[slug] = obj
    return structure, findings, analyses


def read_fragment(fragments_dir: str, slug: str) -> str:
    """Read fragments/<slug>.html; return '' if it does not exist."""
    path = os.path.join(fragments_dir, f"{slug}.html")
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Mount-marker injection
# ---------------------------------------------------------------------------

def inject(document: str, zone: str, html_block: str) -> str:
    """Replace the span between a START/END marker pair, keeping the markers.

    ``zone`` is the bare zone name (e.g. ``STRAP``). The injected HTML is placed
    between the two comment markers; the comments themselves stay in place so a
    re-run is idempotent and the markers remain discoverable.
    """
    start = f"<!-- MDHV:{zone}:START -->"
    end = f"<!-- MDHV:{zone}:END -->"
    si = document.find(start)
    ei = document.find(end)
    if si == -1 or ei == -1 or ei < si:
        raise ValueError(f"design template missing or malformed {zone} mount markers")
    before = document[: si + len(start)]
    after = document[ei:]
    return f"{before}\n{html_block}\n{after}"


# ---------------------------------------------------------------------------
# Zone 1 — STRAP + TOC
# ---------------------------------------------------------------------------

def _first_anchor(file_obj) -> str:
    headings = file_obj.get("headings") or []
    if headings:
        return headings[0].get("anchor", "")
    return ""


def render_strap(structure) -> str:
    meta = structure.get("meta") or {}
    files = structure.get("files") or []
    root = meta.get("root", ".")
    file_count = meta.get("file_count", len(files))
    total_tokens = sum(int(f.get("token_estimate") or 0) for f in files)
    language = meta.get("output_language", "source")
    # NOTE: no id="mdhv-top" here — the static <header id="mdhv-top"> in the
    # design template is the single canonical top landmark. Emitting it again on
    # this injected strap div would create a DUPLICATE id (invalid HTML, breaks
    # #mdhv-top jumps). Keep only the class.
    return (
        '<div class="mdhv-strap">'
        f'<span class="mdhv-strap-root">{esc(root)}</span>'
        f'<span class="mdhv-strap-files">{esc(file_count)} files</span>'
        f'<span class="mdhv-strap-tokens">~{esc(total_tokens)} tokens (estimate)</span>'
        f'<span class="mdhv-strap-language">language: {esc(language)}</span>'
        '</div>'
    )


def render_overview(structure, findings) -> str:
    """At-a-glance orientation panel (R1) — computed facts ONLY, no LLM prose.

    Emitted INSIDE the STRAP mount region (a flow-content ``<section>``, never a
    nested ``<header>``, and carrying NO ``id="mdhv-top"`` so the single top
    landmark is preserved). Every number/name is read straight from
    ``structure.json`` + ``findings.json``:

    - file count, strong + weak cross-reference counts, hub count (hubs reuse the
      module-level :func:`compute_hubs` so R1 and the graph's "filled = hub" agree);
    - a findings tally by severity (e.g. *2 high · 1 medium · 3 low*), each a
      jump-link to ``#mdhv-findings`` (an allowed chrome anchor); tolerates zero
      findings with a quiet "no cross-file findings" line;
    - up to five hub files (most-referenced) as chips linking to ``#{slug}--file``
      via the SAME :func:`_node_anchor` the graph already emits; the hub row is
      omitted entirely when there are no hubs.

    Pure function of its inputs (sorted iteration only), so it is deterministic.
    All sub-hooks (``mdhv-overview-stat`` / ``-hubs`` / ``-jump``) are CSS-only.
    """
    graph = structure.get("graph") or {}
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    files = structure.get("files") or []
    meta = structure.get("meta") or {}

    file_count = meta.get("file_count", len(files))
    strong = _strong_edges(edges)
    strong_count = len(strong)
    weak_count = len([e for e in edges if e.get("strength") == "weak"])
    hubs = compute_hubs(nodes, strong)

    # --- stat line: file / cross-reference / hub counts (all computed) ---------
    stats = (
        '<dl class="mdhv-overview-stat">'
        f'<div><dt>files</dt><dd>{esc(file_count)}</dd></div>'
        f'<div><dt>strong refs</dt><dd>{esc(strong_count)}</dd></div>'
        f'<div><dt>weak refs</dt><dd>{esc(weak_count)}</dd></div>'
        f'<div><dt>hubs</dt><dd>{esc(len(hubs))}</dd></div>'
        '</dl>'
    )

    # --- findings tally by severity, each a jump-link to #mdhv-findings --------
    items = findings.get("cross_file_findings", []) if isinstance(findings, dict) else []
    if not isinstance(items, list):
        items = []
    sev_counts = {"high": 0, "medium": 0, "low": 0}
    total_findings = 0
    for fi in items:
        if not isinstance(fi, dict):
            continue
        total_findings += 1
        sev = fi.get("severity")
        if sev in sev_counts:
            sev_counts[sev] += 1
    if total_findings:
        tally_links = []
        for sev in ("high", "medium", "low"):
            c = sev_counts[sev]
            if not c:
                continue
            icon = _SEVERITY_ICON.get(sev, "○")
            tally_links.append(
                f'<a class="mdhv-severity-{esc(sev)}-badge" href="#mdhv-findings">'
                f'<span aria-hidden="true">{icon}</span> '
                f'<span class="mdhv-severity-label">{esc(c)} {esc(sev)}</span>'
                f'</a>'
            )
        if tally_links:
            jump = (
                '<div class="mdhv-overview-jump">' + "".join(tally_links) + '</div>'
            )
        else:
            # findings exist but none carry a known severity — link the total.
            jump = (
                '<div class="mdhv-overview-jump">'
                f'<a class="mdhv-overview-jump-all" href="#mdhv-findings">'
                f'{esc(total_findings)} cross-file findings</a>'
                '</div>'
            )
    else:
        jump = (
            '<div class="mdhv-overview-jump">'
            '<a class="mdhv-overview-jump-all" href="#mdhv-findings">'
            'no cross-file findings</a>'
            '</div>'
        )

    # --- hub chips: up to five most-referenced files, link to #{slug}--file ----
    hubs_html = ""
    if hubs:
        incoming = {node.get("slug"): 0 for node in nodes}
        for e in strong:
            to = e.get("to")
            if to in incoming:
                incoming[to] += 1
        node_by_slug = {node.get("slug"): node for node in nodes}
        title_by_slug = _title_lookup(structure)
        # Rank by incoming-strong count desc, slug asc for a stable tie-break.
        ranked = sorted(hubs, key=lambda s: (-incoming.get(s, 0), s))[:5]
        chips = []
        for slug in ranked:
            node = node_by_slug.get(slug) or {"slug": slug}
            label = title_by_slug.get(slug, slug)
            chips.append(
                f'<a class="mdhv-node" href="{esc(_node_anchor(node))}">'
                f'{esc(label)}</a>'
            )
        hubs_html = (
            '<div class="mdhv-overview-hubs">'
            '<span class="mdhv-overview-hubs-label">Hub files</span>'
            + "".join(chips) +
            '</div>'
        )

    return (
        '<section class="mdhv-overview" aria-label="At a glance">'
        + stats
        + jump
        + hubs_html
        + '</section>'
    )


def _render_toc_subnav(f) -> str:
    """Per-file nested heading sub-nav for the sticky TOC (Phase C).

    Each sub-link href is built from the SAME structure.json heading object that
    defines ``structure_heading_ids`` — ``#{slug}--{anchor}`` — so every emitted
    href is a member of that id space BY CONSTRUCTION (the injected TOC is not
    scanned by gate 1, so this is the property the membership test pins). The
    file's lead/title heading (index 0) is already the link target of the parent
    item, so it is skipped here to avoid a redundant entry.

    Headings carry a ``data-level`` so the CSS can indent by depth. Returns ''
    when the file has no sub-headings (the parent item then renders flat).
    """
    slug = f.get("slug", "")
    headings = f.get("headings") or []
    sub_items = []
    for h in headings[1:]:
        anchor = h.get("anchor", "")
        text = h.get("text") or anchor
        level = h.get("level") or 2
        href = f"#{slug}--{anchor}"
        sub_items.append(
            f'<li class="mdhv-toc-sub-item" data-level="{esc(level)}">'
            f'<a href="{esc(href)}">{esc(text)}</a>'
            f'</li>'
        )
    if not sub_items:
        return ""
    return (
        '<ul class="mdhv-toc-sub">' + "".join(sub_items) + '</ul>'
    )


def render_toc(structure) -> str:
    files = structure.get("files") or []
    items = []
    for f in files:
        slug = f.get("slug", "")
        title = f.get("title") or slug
        file_path = f.get("file_path", "")
        anchor = _first_anchor(f)
        href = f"#{slug}--{anchor}" if anchor else f"#{slug}"
        subnav = _render_toc_subnav(f)
        if subnav:
            # Collapsible per file (<details>) so a deep file stays compact. The
            # summary is the file link target; the nested heading list lives
            # inside the disclosure. No id/anchor — purely structural.
            items.append(
                f'<li class="mdhv-toc-item mdhv-toc-item--group">'
                f'<details class="mdhv-toc-disclosure" open>'
                f'<summary class="mdhv-toc-summary">'
                f'<a href="{esc(href)}">{esc(title)}</a>'
                f'</summary>'
                f'{subnav}'
                f'</details>'
                f'</li>'
            )
        else:
            items.append(
                f'<li class="mdhv-toc-item">'
                f'<a href="{esc(href)}">{esc(title)}</a>'
                f'</li>'
            )
    return (
        '<nav class="mdhv-toc" id="mdhv-toc">'
        '<ul>' + "".join(items) + '</ul>'
        '</nav>'
    )


# ---------------------------------------------------------------------------
# Zone 2 — GRAPH
# ---------------------------------------------------------------------------

def _node_sort_key(node):
    """Sort nodes by (directory, slug) so output is byte-identical per run.

    Directory comes from the node's file_path; slug breaks ties. This is the
    DETERMINISTIC order — never hash/iteration order.
    """
    fp = node.get("file_path", "") or ""
    directory = os.path.dirname(fp)
    return (directory, node.get("slug", ""))


def _node_anchor(node) -> str:
    """Anchor href into the node's file section in Zone 4."""
    return f"#{node.get('slug', '')}--file"


def select_graph_mode(n: int, s: int) -> str:
    """Deterministic render-mode arithmetic.

    N <= 3 OR S == 0           -> 'chips'
    4 <= N <= 8 AND S > 0      -> 'matrix'
    N >= 9 AND S > 0           -> 'svg'

    The S == 0 short-circuit is checked FIRST, so a 10-node corpus with no
    strong edges renders chips, not an SVG.
    """
    if n <= 3 or s == 0:
        return "chips"
    if 4 <= n <= 8:
        return "matrix"
    return "svg"


def _strong_edges(edges):
    return [e for e in edges if e.get("strength") == "strong"]


def compute_hubs(nodes, strong_edges) -> set:
    """Return the set of hub slugs: nodes with >= 2 INCOMING strong edges.

    A hub is a file referenced (strongly) by at least two others. Weak edges
    never count toward hub status. Pure function of its inputs (no I/O, no
    iteration-order dependence), so it is safe to reuse across renderers.
    """
    incoming_strong = {node.get("slug"): 0 for node in nodes}
    for e in strong_edges:
        to = e.get("to")
        if to in incoming_strong:
            incoming_strong[to] += 1
    return {slug for slug, c in incoming_strong.items() if c >= 2}


def render_graph(structure):
    """Render Zone 2 and return ``(html, mode)``."""
    graph = structure.get("graph") or {}
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    nodes_sorted = sorted(nodes, key=_node_sort_key)

    n = len(nodes_sorted)
    strong = _strong_edges(edges)
    s = len(strong)
    mode = select_graph_mode(n, s)

    if mode == "chips":
        body = _render_chip_strip(nodes_sorted, edges, strong)
    elif mode == "matrix":
        body = _render_adjacency_matrix(nodes_sorted, edges)
    else:
        body = _render_svg(nodes_sorted, edges, strong)

    return (
        f'<div class="mdhv-graph" id="mdhv-graph" data-mode="{mode}">'
        f'{body}'
        '</div>',
        mode,
    )


def _render_graph_key() -> str:
    """Fixed conventions key (G1) — HTML/CSS swatches, never inline <svg><line>.

    Three FIXED entries (no data dependence, no set iteration), so it is
    determinism-safe. Modelled on the existing ``mdhv-graph-legend`` so the
    ::before swatch can draw the solid/dashed rule and the filled-dot glyph.
    """
    return (
        '<ul class="mdhv-graph-key">'
        '<li class="mdhv-edge mdhv-edge-strong">solid line = strong cross-reference</li>'
        '<li class="mdhv-edge mdhv-edge-weak">dashed line = weak / name-match</li>'
        '<li class="mdhv-graph-key-hub">filled node = hub (referenced by >=2 files)</li>'
        '</ul>'
    )


def _render_chip_strip(nodes, edges, strong):
    chips = []
    for node in nodes:
        chips.append(
            f'<a class="mdhv-node" href="{esc(_node_anchor(node))}">'
            f'{esc(node.get("title") or node.get("slug"))}'
            f'</a>'
        )
    weak = [e for e in edges if e.get("strength") == "weak"]
    caption = (
        f'{len(nodes)} files · {len(strong)} strong cross-references · '
        f'{len(weak)} weak / name-match references'
    )
    if len(strong) == 0:
        caption += " — relationships inferred from name matches only; see Findings for tensions"
    return (
        '<div class="mdhv-graph-chips">' + "".join(chips) + '</div>'
        f'<p class="mdhv-graph-caption">{esc(caption)}</p>'
    )


def _render_adjacency_matrix(nodes, edges):
    # Build a lookup of edge strength keyed by (from, to).
    cell = {}
    for e in edges:
        key = (e.get("from"), e.get("to"))
        # strong wins over weak if both somehow present
        if cell.get(key) != "strong":
            cell[key] = e.get("strength")

    header_cells = ['<th class="mdhv-matrix-corner"></th>']
    for node in nodes:
        header_cells.append(
            f'<th><a class="mdhv-node" href="{esc(_node_anchor(node))}">'
            f'{esc(node.get("title") or node.get("slug"))}</a></th>'
        )
    rows = ['<tr>' + "".join(header_cells) + '</tr>']

    for src in nodes:
        cells = [
            f'<th><a class="mdhv-node" href="{esc(_node_anchor(src))}">'
            f'{esc(src.get("title") or src.get("slug"))}</a></th>'
        ]
        for dst in nodes:
            if src.get("slug") == dst.get("slug"):
                cells.append('<td class="mdhv-matrix-self"></td>')
                continue
            strength = cell.get((src.get("slug"), dst.get("slug")))
            if strength == "strong":
                cells.append('<td class="mdhv-edge mdhv-edge-strong" data-edge="strong">●</td>')
            elif strength == "weak":
                cells.append('<td class="mdhv-edge mdhv-edge-weak" data-edge="weak">○</td>')
            else:
                cells.append('<td></td>')
        rows.append('<tr>' + "".join(cells) + '</tr>')

    return (
        '<table class="mdhv-matrix"><tbody>' + "".join(rows) + '</tbody></table>'
        + _render_graph_key()
        + '<p class="mdhv-graph-caption">'
        'Each row references the columns marked below — '
        'filled = strong cross-reference, hollow = weak / name-match.'
        '</p>'
    )


def _render_svg(nodes, edges, strong):
    """Deterministic-layout SVG node-link diagram (N >= 9, S > 0).

    Layout is purely a function of the sorted node order, so the same
    structure.json renders to byte-identical SVG across runs.
    """
    import math

    # hub = node with >= 2 INCOMING strong edges (weak never counts).
    hubs = compute_hubs(nodes, strong)

    # Isolated nodes (no edges at all, strong or weak) are grouped separately.
    connected_slugs = set()
    for e in edges:
        connected_slugs.add(e.get("from"))
        connected_slugs.add(e.get("to"))
    isolated = [node for node in nodes if node.get("slug") not in connected_slugs]
    connected = [node for node in nodes if node.get("slug") in connected_slugs]

    width = 800
    main_h = 600
    cx, cy = width / 2.0, main_h / 2.0
    radius = 240.0

    # Position connected nodes on a circle, in deterministic sorted order.
    pos = {}
    count = max(1, len(connected))
    for i, node in enumerate(connected):
        angle = 2.0 * math.pi * i / count - math.pi / 2.0
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        pos[node.get("slug")] = (round(x, 3), round(y, 3))

    # Collapse bidirectional strong/weak pairs into one line. A pair (A->B and
    # B->A) is drawn ONCE with arrowheads at both ends. Key edges by an
    # order-independent frozenset of endpoints, but keep direction info.
    def edge_record(e):
        return {
            "from": e.get("from"),
            "to": e.get("to"),
            "strength": e.get("strength"),
            "reason": e.get("reason", ""),
        }

    by_pair: dict = {}
    for e in edges:
        rec = edge_record(e)
        a, b = rec["from"], rec["to"]
        if a not in pos or b not in pos:
            continue
        key = frozenset((a, b))
        by_pair.setdefault(key, []).append(rec)

    # Build line elements + collect legend reasons. Iterate pairs in a
    # deterministic order: sort by the sorted-tuple of their endpoints.
    def pair_sort_key(item):
        key = item[0]
        return tuple(sorted(key))

    line_svgs = []
    legend_reasons: dict = {}  # reason -> strength of first sighting (for legend)
    for key, recs in sorted(by_pair.items(), key=pair_sort_key):
        endpoints = sorted(key) if len(key) == 2 else list(key) * 2
        a, b = endpoints[0], endpoints[1]
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        # bidirectional iff both directions present
        dirs = {(r["from"], r["to"]) for r in recs}
        bidirectional = (a, b) in dirs and (b, a) in dirs
        # strong if any rec on this pair is strong
        is_strong = any(r["strength"] == "strong" for r in recs)
        stroke_class = "mdhv-edge-strong" if is_strong else "mdhv-edge-weak"
        dash = "" if is_strong else ' stroke-dasharray="6 4"'
        opacity = "" if is_strong else ' opacity="0.4"'
        marker_end = ' marker-end="url(#mdhv-arrow)"'
        marker_start = ' marker-start="url(#mdhv-arrow)"' if bidirectional else ""
        line_svgs.append(
            f'<line class="mdhv-edge {stroke_class}" '
            f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"'
            f'{dash}{opacity}{marker_start}{marker_end} />'
        )
        # identical reasons collapse to a SINGLE legend entry
        for r in recs:
            reason = r.get("reason", "")
            if reason and reason not in legend_reasons:
                legend_reasons[reason] = r["strength"]

    # Node circles + labels (connected nodes), deterministic order.
    node_svgs = []
    for node in connected:
        slug = node.get("slug")
        x, y = pos[slug]
        is_hub = slug in hubs
        r = 14 if is_hub else 9
        label_class = "mdhv-node-label-hub" if is_hub else "mdhv-node-label"
        node_svgs.append(
            f'<a class="mdhv-node" href="{esc(_node_anchor(node))}" '
            f'data-hub="{"true" if is_hub else "false"}">'
            f'<circle cx="{x}" cy="{y}" r="{r}" />'
            f'<text class="{label_class}" x="{x}" y="{y - r - 4}">'
            f'{esc(node.get("title") or slug)}</text>'
            f'</a>'
        )

    # Legend: one entry per distinct edge reason.
    legend_items = []
    for reason in sorted(legend_reasons):
        strength = legend_reasons[reason]
        legend_items.append(
            f'<li class="mdhv-edge mdhv-edge-{esc(strength)}">{esc(reason)}</li>'
        )
    legend_html = (
        '<ul class="mdhv-graph-legend">' + "".join(legend_items) + '</ul>'
        if legend_items else ''
    )

    # Isolated-node strip (grouped separately, never inside the main canvas).
    isolated_html = ""
    if isolated:
        iso_chips = "".join(
            f'<a class="mdhv-node" href="{esc(_node_anchor(node))}">'
            f'{esc(node.get("title") or node.get("slug"))}</a>'
            for node in isolated
        )
        isolated_html = (
            '<div class="mdhv-graph-isolated">'
            '<span class="mdhv-graph-isolated-label">isolated</span>'
            + iso_chips +
            '</div>'
        )

    # Plain-English reading of the diagram + the computed counts. The same
    # sentence is the <desc> (G2 a11y) and the visible caption (G1). FIXED
    # deterministic strings; only the counts vary with the corpus.
    counts = (
        f'{len(nodes)} files · {len(strong)} strong cross-references · '
        f'{len(hubs)} hub(s) · {len(isolated)} isolated'
    )
    reading = (
        'Arrows point from a file to the files it references; '
        'filled nodes are hubs (referenced by >=2 others). '
    )
    desc = reading + counts
    caption = reading + counts

    svg = (
        f'<svg class="mdhv-graph-svg" viewBox="0 0 {width} {main_h}" '
        f'width="{width}" height="{main_h}" role="img" '
        f'aria-labelledby="mdhv-graph-title mdhv-graph-desc">'
        '<title id="mdhv-graph-title">Cross-file reference graph</title>'
        f'<desc id="mdhv-graph-desc">{esc(desc)}</desc>'
        '<defs>'
        '<marker id="mdhv-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" />'
        '</marker>'
        '</defs>'
        + "".join(line_svgs)
        + "".join(node_svgs)
        + '</svg>'
    )
    # Fixed conventions key (G1): HTML/CSS swatches, NOT inline <svg><line> —
    # so document.count("<line") is unaffected. FIXED text, no data dependence.
    key_html = _render_graph_key()
    return (
        svg
        + key_html
        + legend_html
        + isolated_html
        + f'<p class="mdhv-graph-caption">{esc(caption)}</p>'
    )


# ---------------------------------------------------------------------------
# Zone 3 — FINDINGS
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_ICON = {"high": "⚠", "medium": "◆", "low": "○"}
_TYPE_LABELS = {
    "contradiction": "Contradictions",
    "coverage": "Coverage gaps",
    "signal_noise": "Signal / noise",
}


def _title_lookup(structure):
    """slug -> title, for rendering finding file chips."""
    out = {}
    for f in structure.get("files") or []:
        out[f.get("slug")] = f.get("title") or f.get("slug")
    return out


def _section_id_index(structure):
    """Build the ``(regex, id -> title)`` index used to linkify finding prose.

    Findings prose sometimes cites a section by its internal ``<slug>--<anchor>``
    id (the cross-file agent references analysis ``sections[]`` ids). Those ids are
    real targets in the assembled page, so we render each as a clickable link to
    that section — showing the section's human TITLE, which reads far better than
    the raw slug. Returns ``(pattern, title_by_id)`` where ``pattern`` matches any
    known id LONGEST-FIRST (so ``a--b-c`` wins over a prefix ``a--b``), or
    ``(None, {})`` when the structure has no ids.
    """
    title_by_id: dict = {}
    for f in structure.get("files") or []:
        slug = f.get("slug", "")
        if not slug:
            continue
        title_by_id[f"{slug}--file"] = f.get("title") or slug
        for h in f.get("headings") or []:
            anchor = h.get("anchor", "")
            title_by_id[f"{slug}--{anchor}"] = (
                h.get("text") or anchor or f"{slug}--{anchor}"
            )
    if not title_by_id:
        return (None, {})
    ids_by_len = sorted(title_by_id, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(i) for i in ids_by_len))
    return (pattern, title_by_id)


def _linkify_section_ids(raw, index) -> str:
    """HTML-escape ``raw`` and turn any embedded real section id into a link.

    Each token equal to a known ``<slug>--<anchor>`` page id is replaced with
    ``<a href="#<id>"><section title></a>``; every other run is HTML-escaped
    verbatim. The id is real by construction, so the href always resolves (the
    findings zone is not gate-1 scanned, but these links resolve anyway).
    Deterministic, language-agnostic, no agent involvement. Returns ``esc(raw)``
    unchanged when there is nothing to link.
    """
    text = "" if raw is None else str(raw)
    pattern, title_by_id = index
    if pattern is None or not text:
        return esc(text)
    out = []
    last = 0
    for m in pattern.finditer(text):
        out.append(esc(text[last:m.start()]))
        tok = m.group(0)
        out.append(f'<a href="#{esc(tok)}">{esc(title_by_id.get(tok, tok))}</a>')
        last = m.end()
    out.append(esc(text[last:]))
    return "".join(out)


def _render_claims_compare(claims, titles, idindex) -> str:
    """Render a finding's optional A<->B comparison grid, or '' to fall back.

    ``claims`` is the finding's OPTIONAL ``claims`` field. A well-formed value is
    a list of >= 2 dicts each carrying a non-empty ``claim``; that yields a
    ``.mdhv-finding-compare`` grid with one ``.mdhv-finding-claim`` cell per
    claim, each headed by the file title (linked to ``#<slug>--file`` ONLY when
    ``file`` resolves to a known slug via ``titles``, else plain text) over the
    escaped claim text. ANY other shape returns ``''`` so the caller keeps the
    current prose card unchanged. Tolerant of every malformed shape — never
    raises (guarded with ``isinstance`` / ``.get`` / ``esc``).
    """
    if not isinstance(claims, list) or len(claims) < 2:
        return ""
    cells = []
    for c in claims:
        if not isinstance(c, dict):
            return ""
        text = c.get("claim")
        if not (isinstance(text, str) and text.strip()):
            return ""
        slug = c.get("file")
        if slug and slug in titles:
            label = (
                f'<a class="mdhv-node" href="#{esc(slug)}--file">'
                f'{esc(titles.get(slug, slug))}</a>'
            )
        else:
            label = f'<span class="mdhv-node">{esc(slug) if slug else ""}</span>'
        cells.append(
            f'<div class="mdhv-finding-claim">'
            f'<span class="mdhv-finding-claim-file">{label}</span>'
            f'<p>{_linkify_section_ids(text, idindex)}</p>'
            f'</div>'
        )
    return '<div class="mdhv-finding-compare">' + "".join(cells) + '</div>'


def render_findings(structure, findings) -> str:
    items = findings.get("cross_file_findings", []) if isinstance(findings, dict) else []
    if not isinstance(items, list):
        items = []

    titles = _title_lookup(structure)
    idindex = _section_id_index(structure)

    # Group by type; preserve a stable type ordering.
    groups: dict = {}
    for f in items:
        if not isinstance(f, dict):
            continue
        groups.setdefault(f.get("type", "other"), []).append(f)

    type_order = ["contradiction", "coverage", "signal_noise"]
    ordered_types = [t for t in type_order if t in groups]
    ordered_types += [t for t in sorted(groups) if t not in type_order]

    if not items:
        return (
            '<section class="mdhv-findings" id="mdhv-findings">'
            '<p class="mdhv-findings-empty">No cross-file findings.</p>'
            '</section>'
        )

    group_html = []
    for t in ordered_types:
        group = sorted(
            groups[t],
            key=lambda f: _SEVERITY_ORDER.get(f.get("severity"), 99),
        )
        cards = []
        for f in group:
            severity = f.get("severity", "low")
            sev_class = f"mdhv-severity-{severity}"
            icon = _SEVERITY_ICON.get(severity, "○")
            involved = [s for s in (f.get("files") or []) if s]
            file_chips = []
            for slug in involved:
                file_chips.append(
                    f'<a class="mdhv-node" href="#{esc(slug)}--file">'
                    f'{esc(titles.get(slug, slug))}</a>'
                )
            # Optional A<->B comparison grid (contradiction-oriented). Rendered
            # IN ADDITION to the prose card when 'claims' is a well-formed list
            # of >=2 dicts each carrying a non-empty 'claim'. ANY other shape
            # (absent / not a list / <2 / a dict missing 'claim') falls through
            # to the prose card unchanged. Never raises — guarded throughout.
            compare_html = _render_claims_compare(f.get("claims"), titles, idindex)
            cards.append(
                f'<article class="mdhv-finding {sev_class}">'
                f'<span class="{sev_class}-badge">'
                f'<span aria-hidden="true">{icon}</span> '
                f'<span class="mdhv-severity-label">{esc(severity.upper())}</span>'
                f'</span>'
                f'<h3>{_linkify_section_ids(f.get("title"), idindex)}</h3>'
                f'<p>{_linkify_section_ids(f.get("description"), idindex)}</p>'
                f'{compare_html}'
                f'<div class="mdhv-finding-files">{"".join(file_chips)}</div>'
                f'</article>'
            )
        group_html.append(
            f'<div class="mdhv-findings-group" data-type="{esc(t)}">'
            f'<h2>{esc(_TYPE_LABELS.get(t, t))}</h2>'
            + "".join(cards) +
            '</div>'
        )

    return (
        '<section class="mdhv-findings" id="mdhv-findings">'
        + "".join(group_html) +
        '</section>'
    )


# ---------------------------------------------------------------------------
# Zone 4 — FILES
# ---------------------------------------------------------------------------

def _finding_file_counts(findings) -> dict:
    """slug -> number of cross-file findings that involve that slug.

    Tolerant of any malformed findings shape (non-dict findings object, non-list
    ``cross_file_findings``, non-dict finding, non-list ``files``); a bad shape
    simply contributes nothing rather than raising. Used for the per-file
    "in N findings" backlink (Phase C).
    """
    counts: dict = {}
    items = findings.get("cross_file_findings", []) if isinstance(findings, dict) else []
    if not isinstance(items, list):
        return counts
    for fi in items:
        if not isinstance(fi, dict):
            continue
        involved = fi.get("files")
        if not isinstance(involved, list):
            continue
        seen = set()
        for slug in involved:
            if slug and slug not in seen:
                seen.add(slug)
                counts[slug] = counts.get(slug, 0) + 1
    return counts


def render_files(structure, analyses, fragments_dir, findings=None) -> str:
    files = structure.get("files") or []
    finding_counts = _finding_file_counts(findings or {})
    blocks = []
    for f in files:
        slug = f.get("slug", "")
        title = f.get("title") or slug
        file_path = f.get("file_path", "")
        fragment = read_fragment(fragments_dir, slug)
        # Per-file "in N findings" backlink — only when this file appears in at
        # least one finding. Targets the findings zone (#mdhv-findings, an
        # ALLOWED chrome anchor). Absent for files with no findings.
        n = finding_counts.get(slug, 0)
        backlink = ""
        if n > 0:
            label = f"in {n} finding" + ("" if n == 1 else "s")
            backlink = (
                f'<a class="mdhv-file-findings-link" href="#mdhv-findings">'
                f'{esc(label)}</a>'
            )
        blocks.append(
            f'<section class="mdhv-file" id="{esc(slug)}--file" data-slug="{esc(slug)}">'
            f'<header class="mdhv-file-header">'
            f'<h2>{esc(title)}</h2>'
            f'<a class="mdhv-src-link" href="{esc(file_path)}">{esc(file_path)}</a>'
            f'{backlink}'
            f'</header>'
            f'{fragment}'
            f'</section>'
        )
    return (
        '<div id="mdhv-files">'
        + "".join(blocks) +
        '</div>'
    )


# ---------------------------------------------------------------------------
# Gate helpers (Task 6)
# ---------------------------------------------------------------------------

# An in-page href: href="#..." (single or double quoted).
_HREF_HASH_RE = re.compile(r"""href\s*=\s*['"](#[^'"]*)['"]""", re.IGNORECASE)


def hrefs_in(fragment_html: str) -> list:
    """All in-page ``href="#..."`` targets (with the leading ``#``) in order."""
    return _HREF_HASH_RE.findall(fragment_html)


def run_gates(structure, analyses, fragments):
    """Run the four hard-fail gates.

    ``fragments`` maps slug -> fragment HTML string (for files present in
    structure.json). Returns a dict with the four failure arrays:
    ``dangling_anchors``, ``coverage_gaps``, ``contract_violations``,
    ``external_fetches``. Each is a list of human-readable strings; empty means
    the gate passed.
    """
    id_space = structure_heading_ids(structure)
    chrome = set(ALLOWED_CHROME_ANCHORS)
    files = structure.get("files") or []
    by_slug = {f.get("slug"): f for f in files}

    dangling_anchors = []
    coverage_gaps = []
    contract_violations = []

    # --- Gate 1: anchor resolution (over fragment hrefs, in-fragment + cross). ---
    for f in files:
        slug = f.get("slug", "")
        frag = fragments.get(slug, "")
        for href in hrefs_in(frag):
            # bare "#" is a no-op anchor, tolerate it like the design lint does.
            if href == "#":
                continue
            if href in chrome:
                continue
            target = href[1:] if href.startswith("#") else href
            if target not in id_space:
                dangling_anchors.append(
                    f"{slug}: href {href!r} resolves to no heading id or chrome anchor"
                )

    # --- Gate 2: coverage (per file, subset). source ids ⊆ emitted ids. ---
    for f in files:
        slug = f.get("slug", "")
        frag = fragments.get(slug, "")
        emitted = data_src_headings_in(frag)
        for src_id in sorted(file_heading_ids(f)):
            if src_id not in emitted:
                coverage_gaps.append(
                    f"{slug}: source heading id {src_id!r} not covered by any "
                    f"data-src-heading in the fragment"
                )

    # --- Gate 3: contract check (normalized substring of BOTH sides). ---
    for slug, analysis in analyses.items():
        if not isinstance(analysis, dict):
            continue
        contracts = analysis.get("contracts") or []
        if not contracts:
            continue
        file_obj = by_slug.get(slug)
        # Read + normalize the source bytes once per file.
        src_norm = None
        if file_obj is not None:
            src_path = file_obj.get("file_path")
            if src_path and os.path.exists(src_path):
                try:
                    with open(src_path, "r", encoding="utf-8") as fh:
                        src_norm = normalize(fh.read())
                except OSError:
                    src_norm = None
        frag = fragments.get(slug, "")
        frag_contract_norm = concatenated_contract_text(frag)
        for c in contracts:
            if not isinstance(c, dict):
                continue
            text = c.get("text")
            if not text:
                continue
            needle = normalize(text)
            if not needle:
                continue
            if src_norm is None or not contract_present(needle, src_norm):
                contract_violations.append(
                    f"{slug}: contract not found in source bytes: {needle!r}"
                )
                continue
            if not contract_present(needle, frag_contract_norm):
                contract_violations.append(
                    f"{slug}: contract not found in fragment .mdhv-contract text: "
                    f"{needle!r}"
                )

    return {
        "dangling_anchors": dangling_anchors,
        "coverage_gaps": coverage_gaps,
        "contract_violations": contract_violations,
    }


# Gate 4 — no runtime fetch — scans the WHOLE emitted document.
_FETCH_SCANS = (
    (re.compile(r"<link\b[^>]*\bhref\s*=", re.IGNORECASE),
     "external <link href> tag"),
    (re.compile(r"""<(?:script|img)\b[^>]*\bsrc\s*=\s*['"]?\s*https?:""", re.IGNORECASE),
     "<script>/<img> with http(s) src"),
    (re.compile(r"""@import\s+(?:url\(\s*)?['"]?\s*https?:""", re.IGNORECASE),
     "@import of a remote url"),
    (re.compile(r"""url\(\s*['"]?\s*https?:""", re.IGNORECASE),
     "url(http...) in styles"),
    (re.compile(r"""<image\b[^>]*\b(?:xlink:)?href\s*=\s*['"]?\s*https?:""", re.IGNORECASE),
     "remote SVG <image> href"),
    (re.compile(r"""(?:src|href)\s*=\s*['"]\s*//""", re.IGNORECASE),
     "protocol-relative resource url"),
)


def scan_external_fetches(document: str) -> list:
    """Return a list of external-fetch findings over the whole document."""
    out = []
    for pattern, label in _FETCH_SCANS:
        m = pattern.search(document)
        if m:
            snippet = m.group(0)
            if len(snippet) > 80:
                snippet = snippet[:80] + "..."
            out.append(f"{label}: {snippet!r}")
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble(args):
    """Build the overview document and the report dict.

    Returns ``(document, report)``. The caller writes the document only after
    the (Task 6) gates pass.
    """
    structure, findings, analyses = load_inputs(args)

    with open(args.design, "r", encoding="utf-8") as f:
        document = f.read()

    files = structure.get("files") or []

    # Read every fragment once: reused by the file stitch AND by the gates so a
    # fragment is never read twice from disk.
    fragments = {}
    for f in files:
        slug = f.get("slug", "")
        fragments[slug] = read_fragment(args.fragments_dir, slug)

    # STRAP region carries the masthead metadata line AND the R1 at-a-glance
    # overview panel (both flow content, valid inside <header id="mdhv-top">).
    strap_html = render_strap(structure) + render_overview(structure, findings)
    toc_html = render_toc(structure)
    graph_html, graph_mode = render_graph(structure)
    findings_html = render_findings(structure, findings)
    files_html = render_files(structure, analyses, args.fragments_dir, findings)

    # Inject each zone between its mount markers (markers stay in place).
    document = inject(document, "STRAP", strap_html)
    document = inject(document, "TOC", toc_html)
    document = inject(document, "GRAPH", graph_html)
    # FINDINGS is emitted before FILES by marker order in the template.
    document = inject(document, "FINDINGS", findings_html)
    document = inject(document, "FILES", files_html)

    section_count = sum(len(f.get("headings") or []) for f in files)
    finding_items = (
        findings.get("cross_file_findings", []) if isinstance(findings, dict) else []
    )

    report = {
        "files": len(files),
        "sections": section_count,
        "findings": len(finding_items) if isinstance(finding_items, list) else 0,
        "graph_mode": graph_mode,
    }

    # === GATES (Task 6) ===
    # The four hard-fail gates run HERE, before --out is written, and extend
    # `report` with the failure arrays. The caller (main) refuses to write the
    # output document if any array is non-empty — never a partial page.
    #
    # Gates 1-3 work over the per-file fragment bodies + analysis sidecars + the
    # source bytes; gate 4 scans the WHOLE assembled document.
    gates = run_gates(structure, analyses, fragments)
    external_fetches = scan_external_fetches(document)

    report["dangling_anchors"] = gates["dangling_anchors"]
    report["coverage_gaps"] = gates["coverage_gaps"]
    report["contract_violations"] = gates["contract_violations"]
    report["external_fetches"] = external_fetches

    return document, report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Assemble overview.html (S4).")
    ap.add_argument("--structure", required=True)
    ap.add_argument("--analysis-dir", required=True)
    ap.add_argument("--fragments-dir", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--design", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args(argv)

    try:
        document, report = assemble(args)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: missing input file: {e}\n")
        return 1
    except (ValueError, KeyError) as e:
        sys.stderr.write(f"Error: assembly failed: {e}\n")
        return 1

    # The four hard-fail gate arrays. ANY non-empty array means the page is not
    # trustworthy: print the FULL report (so the caller can surface it), write
    # NOTHING to --out (never a partial page), and exit non-zero.
    gate_keys = (
        "dangling_anchors",
        "coverage_gaps",
        "contract_violations",
        "external_fetches",
    )
    failures = {k: report.get(k, []) for k in gate_keys if report.get(k)}

    if failures:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        for key in gate_keys:
            for msg in report.get(key, []):
                sys.stderr.write(f"GATE FAILURE [{key}]: {msg}\n")
        sys.stderr.write(
            "Error: assembly gate(s) failed (%s); overview.html NOT written.\n"
            % ", ".join(sorted(failures))
        )
        return 1

    # On success: write the page, then print the full report.
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(document)

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
