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
        hrefs_in,
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
        hrefs_in,
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


def select_graph_mode(n: int, e: int) -> str:
    """Deterministic render-mode arithmetic.

    N <= 3 OR E == 0           -> 'chips'   (too small / no relationships)
    otherwise                  -> 'diagram' (layered node-link flow)

    ``E`` is the TOTAL edge count (strong + weak). The E == 0 short-circuit is
    checked alongside N, so a corpus whose files never reference each other
    renders a flat chip strip rather than an empty canvas. Everything with at
    least one edge and more than three files renders the layered diagram — its
    depth then grows with how deeply the corpus is actually connected.
    """
    if n <= 3 or e == 0:
        return "chips"
    return "diagram"


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
    mode = select_graph_mode(n, len(edges))

    if mode == "chips":
        body = _render_chip_strip(nodes_sorted, edges, strong)
    else:
        body = _render_flow(nodes_sorted, edges, strong)

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


def _truncate(s: str, n: int) -> str:
    """Trim an SVG label to ``n`` chars with an ellipsis (full text in <title>)."""
    s = s or ""
    return s if len(s) <= n else s[: max(1, n - 1)].rstrip() + "…"


_LEGEND_NAME_RE = re.compile(r":\s*'[^']*'")


def _legend_label(reason: str) -> str:
    """Collapse per-name edge reasons to a bounded, generic legend label.

    Weak name-match reasons carry the specific matched name
    (``name match: 'session' (tentative)``); stripping the quoted name keeps
    the legend to a handful of distinct rationales (``name match (tentative)``)
    instead of one row per matched name, so it stays scannable on a large
    corpus. Structural reasons (``direct relative link``, ``inline reference``)
    have no quoted name and pass through unchanged.
    """
    return _LEGEND_NAME_RE.sub("", reason or "").strip()


# Layout constants for the layered flow diagram. Pure geometry; the same
# structure.json renders byte-identically because every coordinate is an
# integer/fixed-factor function of the deterministic node order.
_BOX_W, _BOX_H = 184, 48
_H_GAP, _V_GAP = 30, 64
_MARGIN = 24


def _layer_assignment(connected, edges):
    """Cycle-safe longest-path layering. Returns ``{slug: layer}``.

    Layers each node by the longest path from a source (a node nothing
    references) along a DAG built from the edges with back-edges removed.
    DEPTH (``max(layer) + 1``) therefore grows with how deeply the corpus is
    connected — a reference chain is deep, a flat hub-and-spoke is shallow.
    Pure function of the sorted node/edge order: iterative DFS (no recursion
    limit, no iteration-order dependence), sorted adjacency, sorted roots.
    """
    slugs = [n.get("slug") for n in connected]
    slug_set = set(slugs)

    adj = {s: [] for s in slugs}
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a in slug_set and b in slug_set and a != b:
            adj[a].append(b)
    adj = {s: sorted(set(v)) for s, v in adj.items()}

    # Iterative DFS dropping back-edges (a target currently on the stack) to
    # leave a DAG in ``forward``. Cross/forward edges to finished nodes stay.
    state = {s: 0 for s in slugs}          # 0 unseen, 1 on-stack, 2 done
    forward = {s: [] for s in slugs}
    for root in sorted(slugs):
        if state[root] != 0:
            continue
        stack = [[root, 0]]
        state[root] = 1
        while stack:
            u, i = stack[-1]
            if i < len(adj[u]):
                stack[-1][1] += 1
                v = adj[u][i]
                if state[v] == 1:
                    continue               # back-edge -> not in the DAG
                forward[u].append(v)
                if state[v] == 0:
                    state[v] = 1
                    stack.append([v, 0])
            else:
                state[u] = 2
                stack.pop()

    # Longest-path layering on the DAG (Kahn over forward edges).
    indeg = {s: 0 for s in slugs}
    for u in slugs:
        for v in forward[u]:
            indeg[v] += 1
    layer = {s: 0 for s in slugs}
    queue = sorted(s for s in slugs if indeg[s] == 0)
    while queue:
        u = queue.pop(0)
        for v in forward[u]:
            if layer[u] + 1 > layer[v]:
                layer[v] = layer[u] + 1
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
                queue.sort()
    return layer


def _order_layers(connected, edges, layer):
    """Order nodes within each layer to reduce crossings (deterministic).

    Start each layer in slug order, then run a fixed number of barycenter
    sweeps (down then up) keyed on the mean index of a node's neighbours in
    the adjacent layer, with a slug tie-break. No randomness, fixed sweep
    count -> identical output every run.
    """
    by_layer: dict = {}
    for n in connected:
        by_layer.setdefault(layer[n.get("slug")], []).append(n)
    depth = max(by_layer) + 1 if by_layer else 0
    layers = [sorted(by_layer.get(L, []), key=lambda n: n.get("slug", ""))
              for L in range(depth)]

    cslugs = {n.get("slug") for n in connected}
    nbr: dict = {s: set() for s in cslugs}
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a in cslugs and b in cslugs and a != b:
            nbr[a].add(b)
            nbr[b].add(a)

    pos = {}
    for L in range(depth):
        for i, n in enumerate(layers[L]):
            pos[n.get("slug")] = i

    def bary(n, adjacent):
        idx = [pos[m] for m in nbr[n.get("slug")] if layer.get(m) == adjacent]
        return sum(idx) / len(idx) if idx else pos[n.get("slug")]

    for _ in range(3):
        for L in range(1, depth):
            layers[L].sort(key=lambda n: (bary(n, L - 1), n.get("slug", "")))
            for i, n in enumerate(layers[L]):
                pos[n.get("slug")] = i
        for L in range(depth - 2, -1, -1):
            layers[L].sort(key=lambda n: (bary(n, L + 1), n.get("slug", "")))
            for i, n in enumerate(layers[L]):
                pos[n.get("slug")] = i
    return layers


def _render_flow(nodes, edges, strong):
    """Deterministic layered node-link flow diagram.

    Boxed nodes (title + file-path sub-label) on connectivity-derived layers,
    arrowed cubic-bezier edges (solid = strong, dashed = weak), legend, and an
    isolated-node strip — the same structure.json renders byte-identically.
    """
    hubs = compute_hubs(nodes, strong)

    connected_slugs = set()
    for e in edges:
        connected_slugs.add(e.get("from"))
        connected_slugs.add(e.get("to"))
    isolated = [n for n in nodes if n.get("slug") not in connected_slugs]
    connected = [n for n in nodes if n.get("slug") in connected_slugs]

    layer = _layer_assignment(connected, edges)
    layers = _order_layers(connected, edges, layer)
    depth = len(layers)

    # Coordinates: rows top-to-bottom by layer, each row centred in the canvas.
    row_w = [len(L) * _BOX_W + max(0, len(L) - 1) * _H_GAP for L in layers]
    canvas_w = (max(row_w) if row_w else _BOX_W) + 2 * _MARGIN
    canvas_h = 2 * _MARGIN + depth * _BOX_H + max(0, depth - 1) * _V_GAP
    xy = {}
    for L in range(depth):
        x0 = (canvas_w - row_w[L]) / 2.0
        y = _MARGIN + L * (_BOX_H + _V_GAP)
        for i, n in enumerate(layers[L]):
            xy[n.get("slug")] = (round(x0 + i * (_BOX_W + _H_GAP), 2), round(y, 2))

    # Edges: one path per directed edge (correct arrow direction), routed by
    # the relative layer of source and target. Deterministic (from, to) order.
    edge_svgs = []
    legend_reasons: dict = {}
    for e in sorted(edges, key=lambda e: (e.get("from", ""), e.get("to", ""))):
        a, b = e.get("from"), e.get("to")
        if a == b or a not in xy or b not in xy:
            continue
        ax, ay = xy[a]
        bx, by = xy[b]
        la, lb = layer[a], layer[b]
        if lb > la:                                   # target below
            sx, sy, tx, ty = ax + _BOX_W / 2, ay + _BOX_H, bx + _BOX_W / 2, by
            my = sy + (ty - sy) * 0.5
            c1x, c1y, c2x, c2y = sx, my, tx, my
        elif lb < la:                                 # target above
            sx, sy, tx, ty = ax + _BOX_W / 2, ay, bx + _BOX_W / 2, by + _BOX_H
            my = sy + (ty - sy) * 0.5
            c1x, c1y, c2x, c2y = sx, my, tx, my
        else:                                         # same layer -> side arc
            # Defensive: longest-path layering never assigns the two ends of an
            # edge to the same layer (a kept edge forces layer[to] > layer[from];
            # a dropped back-edge points at an ancestor, layer[to] < layer[from]).
            # Kept so a future layering change can't reach an undefined path.
            if bx >= ax:
                sx, sy, tx, ty = ax + _BOX_W, ay + _BOX_H / 2, bx, by + _BOX_H / 2
            else:
                sx, sy, tx, ty = ax, ay + _BOX_H / 2, bx + _BOX_W, by + _BOX_H / 2
            arc = sy - 34
            c1x, c1y, c2x, c2y = (sx + tx) / 2, arc, (sx + tx) / 2, arc
        is_strong = e.get("strength") == "strong"
        cls = "mdhv-edge-strong" if is_strong else "mdhv-edge-weak"
        dash = "" if is_strong else ' stroke-dasharray="6 4"'
        op = "" if is_strong else ' opacity="0.45"'
        d = (f'M{round(sx, 2)},{round(sy, 2)} '
             f'C{round(c1x, 2)},{round(c1y, 2)} '
             f'{round(c2x, 2)},{round(c2y, 2)} '
             f'{round(tx, 2)},{round(ty, 2)}')
        edge_svgs.append(
            f'<path class="mdhv-edge {cls}" d="{d}"{dash}{op} '
            f'marker-end="url(#mdhv-arrow)" />'
        )
        reason = _legend_label(e.get("reason", ""))
        if reason and reason not in legend_reasons:
            legend_reasons[reason] = e.get("strength")

    # Boxed nodes: title + file-path sub-label; hubs are filled (data-hub).
    node_svgs = []
    for n in connected:
        slug = n.get("slug")
        x, y = xy[slug]
        is_hub = slug in hubs
        title = n.get("title") or slug
        path = n.get("file_path") or ""
        label_class = "mdhv-node-label-hub" if is_hub else "mdhv-node-label"
        cx = round(x + _BOX_W / 2, 2)
        node_svgs.append(
            f'<a class="mdhv-node" href="{esc(_node_anchor(n))}" '
            f'data-hub="{"true" if is_hub else "false"}">'
            f'<title>{esc(title)} — {esc(path)}</title>'
            f'<rect x="{x}" y="{y}" width="{_BOX_W}" height="{_BOX_H}" rx="8" />'
            f'<text class="{label_class}" x="{cx}" y="{round(y + _BOX_H / 2 - 2, 2)}">'
            f'{esc(_truncate(title, 24))}</text>'
            f'<text class="mdhv-node-sub" x="{cx}" y="{round(y + _BOX_H / 2 + 14, 2)}">'
            f'{esc(_truncate(path, 28))}</text>'
            f'</a>'
        )

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

    isolated_html = ""
    if isolated:
        iso_chips = "".join(
            f'<a class="mdhv-node" href="{esc(_node_anchor(n))}">'
            f'{esc(n.get("title") or n.get("slug"))}</a>'
            for n in isolated
        )
        isolated_html = (
            '<div class="mdhv-graph-isolated">'
            '<span class="mdhv-graph-isolated-label">isolated</span>'
            + iso_chips +
            '</div>'
        )

    counts = (
        f'{len(nodes)} files · {len(strong)} strong cross-references · '
        f'{len(hubs)} hub(s) · {len(isolated)} isolated'
    )
    reading = (
        'Files are layered top-to-bottom from referrer to referenced; arrows '
        'point to the file being referenced, and filled nodes are hubs '
        '(referenced by >=2 others). '
    )
    desc = reading + counts
    caption = reading + counts

    svg = (
        f'<svg class="mdhv-graph-svg" viewBox="0 0 {int(canvas_w)} {int(canvas_h)}" '
        f'width="{int(canvas_w)}" height="{int(canvas_h)}" role="img" '
        f'aria-labelledby="mdhv-graph-title mdhv-graph-desc">'
        '<title id="mdhv-graph-title">Cross-file reference graph</title>'
        f'<desc id="mdhv-graph-desc">{esc(desc)}</desc>'
        '<defs>'
        '<marker id="mdhv-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" />'
        '</marker>'
        '</defs>'
        + "".join(edge_svgs)
        + "".join(node_svgs)
        + '</svg>'
    )
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

# Gate 1 reads in-page hrefs via the HTML-AWARE ``hrefs_in`` single-sourced in
# scripts.gates (imported above): it collects href only from REAL ``<a>`` start
# tags, so an ``href="#..."`` quoted inside a ``<code>`` example or an escaped
# ``&lt;a href=...&gt;`` is correctly NOT treated as a link. The old raw-byte
# regex lived here and false-flagged those documentation snippets as dangling
# anchors on self-referential corpora — replaced, not redefined, to keep one
# source of truth.


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
