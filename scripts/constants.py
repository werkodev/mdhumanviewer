"""Single source of truth for the mdHumanViewer design-system contract.

This module is the ONE place that defines the class-hook vocabulary, the
HTML mount markers, the allowed chrome anchors, and the gate-3 ``normalize``
function. It is imported by ``scripts/assemble.py`` (the deterministic
assembler) and by the lint tests so that the schema doc, the committed design
system, and the assembler cannot drift apart.

Stdlib-only. Python 3.9+.
"""

import html
import re

# Semantic class hooks the render agents are allowed to emit. The committed
# design system styles exactly these; the assembler lint asserts the design
# system covers every entry. Do not rename, reorder meaning, or add/remove
# entries without updating references/schemas.md and the design system.
REQUIRED_CLASS_HOOKS = [
    'mdhv-keypoints',
    'mdhv-contract',
    'mdhv-detail',
    'mdhv-src-link',
    'mdhv-section',
    'mdhv-strap',
    'mdhv-toc',
    'mdhv-toc-item',
    'mdhv-graph',
    'mdhv-node',
    'mdhv-edge',
    'mdhv-graph-caption',
    'mdhv-findings',
    'mdhv-finding',
    'mdhv-severity-high',
    'mdhv-severity-medium',
    'mdhv-severity-low',
    'mdhv-file',
    'mdhv-file-header',
]

# Paired HTML comment markers in the committed design system. The assembler
# injects each zone's generated HTML between a START/END pair.
REQUIRED_MOUNT_MARKERS = [
    '<!-- MDHV:STRAP:START -->',
    '<!-- MDHV:STRAP:END -->',
    '<!-- MDHV:TOC:START -->',
    '<!-- MDHV:TOC:END -->',
    '<!-- MDHV:GRAPH:START -->',
    '<!-- MDHV:GRAPH:END -->',
    '<!-- MDHV:FINDINGS:START -->',
    '<!-- MDHV:FINDINGS:END -->',
    '<!-- MDHV:FILES:START -->',
    '<!-- MDHV:FILES:END -->',
]

# The only ``href="#..."`` targets that may point at shell chrome rather than a
# ``<slug>--<anchor>`` section id. Every other in-page href must resolve to a
# section id from structure.json.
ALLOWED_CHROME_ANCHORS = [
    '#mdhv-top',
    '#mdhv-toc',
    '#mdhv-graph',
    '#mdhv-findings',
    '#mdhv-files',
]

# Matches a single HTML tag span: ``<``, a body with NO further ``<``, then the
# next ``>``. The body excludes ``<`` on purpose: a literal ``<`` in technical
# markdown (a heredoc ``python3 <<EOF``, a placeholder ``<slug>``/``<anchor>``)
# is NOT a tag opener. The old ``<[^>]*>`` let one match span from such a ``<``
# to a distant downstream ``>``, deleting real content — but only on the long
# source side, not the short contract side, which broke the gate-3 "applied
# symmetrically" invariant. Excluding ``<`` from the body bounds every match to
# a single tag.
_TAG_RE = re.compile(r'<[^<>]*>')

# Matches any run of NON-word characters (Unicode-aware) PLUS the underscore:
# whitespace, every punctuation/symbol/markdown-syntax char, and ``_``. Used to
# reduce both sides of a contract comparison to the same stream of content words
# (Unicode letters/digits — Cyrillic/Greek/CJK prose survives). The underscore is
# included as a separator so a snake_case identifier and its faithful
# human-readable render converge: a source path cell ``muted_keyword_filter.rs``
# and a render that writes ``muted keyword filter`` must normalize to the SAME
# word stream. It is the SAME class of separator as ``-`` / ``/`` / ``.`` (which
# ``\W`` already splits) — keeping ``_`` was an inconsistency that false-failed
# gate 3 on identifier-heavy table cells. CamelCase (``MutedKeywordFilter``) has
# no separator and stays one token on both sides, so it is unaffected.
_NONWORD_RE = re.compile(r'[\W_]+', re.UNICODE)


def normalize(s: str) -> str:
    """Normalize text for the gate-3 contract substring check.

    Applied to BOTH sides of every contract comparison (the contract text
    stored in analysis, the source bytes, and the concatenated fragment
    ``.mdhv-contract`` text). It reduces text to a stream of **content words**,
    because the two sides are fundamentally different surfaces — a markdown
    *source* keeps structural syntax (table pipes ``|``, list markers, link
    ``[text](url)``, backticks, emphasis, headings) while a faithful *render*
    transforms or drops that syntax (a table row becomes ``col1: col2`` or
    ``<td>`` cells, a link becomes ``<a>text</a>``). Comparing exact punctuation
    across those surfaces false-fails on faithful renders; comparing the word
    sequence does not. This still catches real distortions — a changed,
    dropped, or invented *word* breaks the substring; only presentation
    punctuation is ignored (consistent with the project rule that completeness
    is *information* completeness, not text 1:1). Steps, IN THIS ORDER:

    1. remove all HTML tags (regex strip of angle-bracket spans);
    2. unescape HTML entities via :func:`html.unescape`;
    3. replace every run of non-word characters AND underscores (whitespace,
       punctuation, symbols, markdown syntax, and ``_``) with a single space —
       leaving only content words (Unicode letters/digits) separated by spaces.
       The underscore is treated as a separator (the same class as ``-`` / ``/``
       / ``.``) so a snake_case identifier and its faithful human-readable render
       converge: ``muted_keyword_filter`` and ``muted keyword filter`` reduce to
       the same words;
    4. lowercase (case is presentation, not meaning — a render may re-case a
       heading or inline token; the verifier catches real modal/meaning
       distortions, which are word changes, not case changes);
    5. strip leading/trailing whitespace.

    Gate 3 then checks that the normalized contract is a substring of BOTH the
    normalized source bytes and the normalized fragment ``.mdhv-contract`` text.
    """
    s = _TAG_RE.sub('', s)
    s = html.unescape(s)
    s = _NONWORD_RE.sub(' ', s)
    return s.strip().lower()
