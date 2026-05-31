#!/usr/bin/env python3
"""gates.py — shared gate library for the deterministic S4 pipeline.

The four hard-fail GATES (anchor resolution, coverage, contract fidelity, no
runtime fetch) are enforced by ``scripts/assemble.py``; the deterministic
reconcile pass (``scripts/reconcile.py``) closes exactly the mechanical
properties those gates check. To guarantee the reconciler closes *exactly* what
the gate verifies (no drift), the shared primitives live HERE and are imported
by BOTH modules:

* ``contract_present`` — the bounded-window subsequence contract match.
* ``structure_heading_ids`` / ``file_heading_ids`` — the ``<slug>--<anchor>``
  emitted-page id space.
* ``contract_blocks_in`` / ``concatenated_contract_text`` and the
  ``_ContractTextExtractor`` HTMLParser subclass that extracts ``.mdhv-contract``
  text from a fragment.
* ``data_src_headings_in`` — the emitted ``data-src-heading`` id set gate 2 reads
  and the reconciler closes.

Stdlib-only. Python 3.9+.
"""
import os
import re
import sys
from html.parser import HTMLParser

# Single-source constants — imported, never redefined here. The package import
# works when run as ``python3 -m scripts.gates`` or imported as a package
# module; the sys.path shim below makes a direct ``python3 scripts/<x>.py``
# invocation that imports ``scripts.gates`` work too.
try:
    from scripts.constants import normalize
except ModuleNotFoundError:  # pragma: no cover - import shim for direct runs
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.constants import normalize  # noqa: E402


def structure_heading_ids(structure) -> set:
    """Return ``{<slug>--<anchor>}`` for every heading in structure.json.

    This is the canonical emitted page id space (one id per heading). The
    anchor and coverage gates resolve against exactly this set.
    """
    ids = set()
    for f in structure.get("files") or []:
        slug = f.get("slug", "")
        for h in f.get("headings") or []:
            anchor = h.get("anchor", "")
            ids.add(f"{slug}--{anchor}")
    return ids


def file_heading_ids(file_obj) -> set:
    """Return ``{<slug>--<anchor>}`` for a single file's headings."""
    slug = file_obj.get("slug", "")
    out = set()
    for h in file_obj.get("headings") or []:
        out.add(f"{slug}--{h.get('anchor', '')}")
    return out


class _ContractTextExtractor(HTMLParser):
    """Collect the text content of every ``.mdhv-contract`` element in order.

    Tracks element nesting so the text of nested elements inside a
    ``.mdhv-contract`` is captured, and a ``.mdhv-contract`` nested inside
    another is not double-counted. Each top-level contract element's text is a
    separate entry in :attr:`blocks` (document order).
    """

    # HTML void elements never have an end tag, so they must not be pushed onto
    # the element stack (doing so would unbalance it and corrupt nesting).
    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        # One entry per currently-open (non-void) element: True iff it is a
        # .mdhv-contract element. Lets us match each </tag> to its opener and
        # know when the OUTERMOST contract region closes.
        self._stack = []
        self._depth = 0   # number of currently-open .mdhv-contract ancestors
        self._buf = []
        self.blocks = []  # list[str] — captured raw inner text per contract block

    @staticmethod
    def _is_contract(attrs) -> bool:
        for name, value in attrs:
            if name == "class" and value:
                if "mdhv-contract" in value.split():
                    return True
        return False

    # Block-level / line-breaking elements separate visual lines; a break
    # between two words must read as whitespace so normalize collapses
    # "a<br>b" to "a b" (not "ab"). Inline elements (e.g. <code>, <em>) must
    # NOT inject whitespace, so "&quot;<code>ok</code>&quot;" stays "\"ok\"".
    _BREAKING = frozenset({
        "br", "p", "div", "li", "ul", "ol", "tr", "td", "th", "table",
        "section", "article", "header", "footer", "h1", "h2", "h3", "h4",
        "h5", "h6", "blockquote", "pre", "hr",
    })

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._BREAKING and self._depth > 0:
            self._buf.append(" ")
        if tag.lower() in self._VOID:
            # Void element: opens no text region; do not touch the stack.
            return
        is_contract = self._is_contract(attrs)
        self._stack.append(is_contract)
        if is_contract:
            self._depth += 1

    def handle_startendtag(self, tag, attrs):
        # Explicitly self-closing element (e.g. <br/>): break if block-level.
        if tag.lower() in self._BREAKING and self._depth > 0:
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag.lower() in self._VOID:
            return
        if not self._stack:
            return
        if tag.lower() in self._BREAKING and self._depth > 0:
            self._buf.append(" ")
        was_contract = self._stack.pop()
        if was_contract:
            self._depth -= 1
            if self._depth == 0:
                # We just closed the outermost contract region: flush its text.
                self.blocks.append("".join(self._buf))
                self._buf = []

    def handle_data(self, data):
        if self._depth > 0:
            self._buf.append(data)


def contract_blocks_in(fragment_html: str) -> list:
    """Raw inner text of each top-level ``.mdhv-contract`` element, in order."""
    parser = _ContractTextExtractor()
    try:
        parser.feed(fragment_html)
        parser.close()
    except Exception:  # pragma: no cover - malformed fragment is non-fatal here
        pass
    return parser.blocks


def concatenated_contract_text(fragment_html: str) -> str:
    """normalize() each ``.mdhv-contract`` block, space-join in document order."""
    parts = [normalize(b) for b in contract_blocks_in(fragment_html)]
    parts = [p for p in parts if p]
    return " ".join(parts)


def contract_present(needle_norm: str, hay_norm: str) -> bool:
    """True if the normalized contract ``needle_norm`` is faithfully present in
    the normalized text ``hay_norm`` — as a contiguous substring OR an
    order-preserving subsequence within a bounded window.

    Gate 3 originally required a contiguous word-substring of the source. That
    false-fails legitimate ``verbatim_critical`` contracts lifted from a markdown
    TABLE ROW: the renderer keeps the salient cells in source order but drops the
    intervening cell/connector words, so the contract's words form a *subsequence*
    of the row, not a contiguous run. We accept that subsequence ONLY when its
    matched span in the haystack is tight (``<= max(3*len, len+20)`` words), which
    still rejects an invented word (breaks the subsequence) and words scraped from
    scattered, unrelated parts of the document (span too wide).
    """
    if not needle_norm:
        return True
    if needle_norm in hay_norm:  # fast path: contiguous
        return True
    needle = needle_norm.split()
    hay = hay_norm.split()
    if not needle or len(needle) > len(hay):
        return False
    max_span = max(len(needle) * 3, len(needle) + 20)
    first = needle[0]
    # Try every start position whose word matches the contract's first word; take
    # the tightest greedy forward match and accept if it fits the window.
    for i, w in enumerate(hay):
        if w != first:
            continue
        j = i
        k = 0
        while j < len(hay) and k < len(needle):
            if hay[j] == needle[k]:
                k += 1
                last = j
            j += 1
        if k == len(needle) and (last - i + 1) <= max_span:
            return True
    return False


# Matches a ``data-src-heading="..."`` (or single-quoted) attribute value.
_DATA_SRC_RE = re.compile(r"""data-src-heading\s*=\s*['"]([^'"]*)['"]""", re.IGNORECASE)


def data_src_headings_in(fragment_html: str) -> set:
    """Union of all whitespace-split ``data-src-heading`` values in a fragment.

    This is the emitted ``<slug>--<anchor>`` id set gate 2 (assemble.py) checks
    coverage against and the reconciler (reconcile.py) closes — single-sourced
    here so the two cannot drift."""
    out = set()
    for raw in _DATA_SRC_RE.findall(fragment_html):
        for tok in raw.split():
            if tok:
                out.add(tok)
    return out
