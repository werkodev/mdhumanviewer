# Artifact schemas

This file is the single source of truth for the artifacts exchanged between
mdHumanViewer pipeline stages. Both the producer and the consumer of each
artifact must conform to the structures below. When in doubt, this file wins.

mdHumanViewer is built on a disk-as-contract backbone with strict slug/anchor
discipline. There is no heavy intermediate draft layer: the whole-corpus skeleton
lives in `structure.json`, and per-file semantic detail lives in
`analysis/<slug>.json` alongside an intelligently rendered `fragments/<slug>.html`.

All artifacts for one run live under a single dated session directory rooted at
the analyzed `<ROOT>`:

```
<ROOT>/.mdHumanViewer/{yyyy-mm-dd_HH-MM}/
├── manifest.json          # selection + per-step status + resume semantics
├── structure.json         # whole-corpus skeleton + dependency graph (S1, Python)
├── analysis/<slug>.json   # tldr + section kinds + normalized contracts[] (S2, LLM)
├── fragments/<slug>.html  # intelligent per-file HTML fragment (S2; edited in place by S2b)
├── findings.json          # cross-file findings (S3, LLM)
└── overview.html          # the deliverable (S4, Python)
```

## Slug & anchor rules (single-sourced in `parse_structure.py`)

`<slug>` is a filesystem- and anchor-safe identifier derived from the source
file path: lowercase, non-alphanumeric runs collapsed to `-`, path separators
folded in to keep it unique (e.g. `skills/auth/SKILL.md` -> `skills-auth-skill`).
Slugs MUST be unique within a run. When two or more files would produce the same
base slug, **all** colliding entries get a numeric suffix starting at `-1`
(`foo-1`, `foo-2`, ...) — there is no unnumbered first occurrence sitting
alongside numbered siblings.

Heading anchors are de-duplicated GitHub-style **within each file**: the first
occurrence of a given anchor keeps the bare form, each subsequent duplicate gets
a numeric suffix `-1`, `-2`, ... (the 2nd identical heading -> `-1`, the 3rd ->
`-2`).

A heading's **global id** is `<slug>--<anchor>` (slug, then a literal double
dash `--`, then the heading anchor). Slug uniqueness across the run combined
with per-file anchor de-duplication makes every `<slug>--<anchor>` globally
unique by construction. **The emitted page id space is exactly the set of those
ids** — one per heading in `structure.json` — and the anchor/coverage gates in
`assemble.py` resolve against that set.

---

## 1. structure.json  (Stage S1: Parse — deterministic Python, 0 LLM reads)

The whole-corpus skeleton plus the dependency graph. Computed entirely in
stdlib Python by `parse_structure.py`. No semantic compression here — that is
the renderer's job in S2.

```json
{
  "meta": {
    "session": "2026-05-22_14-30",
    "generated_at": "2026-05-22T14:42:00Z",
    "root": ".",
    "output_language": "source",
    "file_count": 40
  },

  "files": [
    {
      "slug": "skills-auth-skill",
      "file_path": "skills/auth/SKILL.md",
      "language": "en",
      "title": "Auth skill",
      "token_estimate": 1840,
      "token_estimate_method": "chars/4",
      "frontmatter": { "name": "auth", "description": "..." },
      "headings": [
        { "level": 1, "text": "Auth skill",   "anchor": "auth-skill" },
        { "level": 2, "text": "When to use",  "anchor": "when-to-use" }
      ],
      "links": [
        { "raw": "[refs](./references/tokens.md)", "target": "references/tokens.md", "type": "relative_md", "resolved_slug": "refs-tokens" },
        { "raw": "https://oauth.net",              "target": "https://oauth.net",     "type": "external_url" },
        { "raw": "`src/auth.go`",                  "target": "src/auth.go",           "type": "code_ref" }
      ]
    }
  ],

  "graph": {
    "nodes": [
      { "slug": "skills-auth-skill", "title": "Auth skill", "file_path": "skills/auth/SKILL.md" }
    ],
    "edges": [
      { "from": "skills-auth-skill", "to": "refs-tokens",          "strength": "strong", "reason": "direct relative link" },
      { "from": "skills-auth-skill", "to": "skills-session-skill", "strength": "weak",   "reason": "name match: 'session'" }
    ]
  }
}
```

Field notes:

- `meta`: run-level header. `output_language` is `source` (default) or a
  BCP-47-ish tag (`ru`, `en`).
- `files[].language`: detected primary language of the prose (ISO-639-1 where
  possible).
- `files[].title`: text of the first H1 if one exists; otherwise the filename
  without its `.md`/`.markdown` extension, in human-readable case
  (`auth-skill.md` -> `Auth skill`).
- `files[].token_estimate` / `token_estimate_method`: an estimate, never a
  guarantee. Method is `chars/4` or `tokenizer:<name>`; always disclosed.
- `files[].frontmatter`: **optional** — YAML front matter parsed into an object.
  Omit the field entirely when the file has no front matter; do **not** emit an
  empty `{}`.
- `files[].headings[]`: `{ level, text, anchor }`. `anchor` is the GitHub-style
  slug of the heading text, de-duplicated within the file per the rule above.
- `files[].links[]`: **the canonical name is `links[]`. Never use the name
  `outbound_links`.** Each
  entry is `{ raw, target, type, resolved_slug? }`:
  - `type` is one of:
    - `relative_md`  — link to another `.md` inside the repo (candidate graph edge);
    - `external_url` — http(s) link outside the repo;
    - `code_ref`     — reference to a code file/path (not an edge, but surfaced);
    - `other`        — anything else (images, bare anchors, mailto, ...).
  - `target` for `relative_md` is normalized relative to **ROOT** (the discovery
    root), so it can be matched against other files' `file_path` (also relative
    to ROOT).
  - `resolved_slug` (optional): the slug of the target file when it is part of
    this run — enables clickable in-HTML cross-references. Omitted/absent when
    the target is outside the selection.
- `graph.nodes[]`: one per file (`slug`, `title`, `file_path`).
- `graph.edges[]`: `{ from, to, strength, reason }`.
  - **strong** = a `relative_md` link whose ROOT-normalized `target` matches
    another node's `file_path`. Strong edges (and each link's `resolved_slug`)
    are a pure deterministic join computed in S1.
  - **weak** = a name match between files (marked tentative).

The set `{ "<slug>--<anchor>" for every heading in every files[].headings[] }`
is the canonical **emitted page id space**. Gates 1 and 2 in `assemble.py`
resolve against exactly this set (plus `ALLOWED_CHROME_ANCHORS` for chrome).

---

## 2. analysis/<slug>.json  (Stage S2: Render — one per file, LLM, 1 read)

Produced by an isolated renderer subagent that sees exactly ONE source file. No
cross-file knowledge at this stage. This sidecar is the machine-checkable
fidelity anchor consumed by gate 3 and by the S3 cross-file agent.

```json
{
  "slug": "skills-auth-skill",
  "tldr": "One-paragraph plain summary of what this file is and does.",
  "sections": [
    {
      "id": "skills-auth-skill--when-to-use",
      "title": "When to use",
      "kind": "summarized",
      "source_headings": ["when-to-use", "examples"]
    },
    {
      "id": "skills-auth-skill--contract",
      "title": "Input/output contract",
      "kind": "verbatim_critical",
      "source_headings": ["contract"]
    }
  ],
  "contracts": [
    {
      "text": "If the token is empty the request MUST be rejected with 401.",
      "source_anchor": "skills-auth-skill--contract"
    }
  ]
}
```

Field notes:

- `slug`: matches the source file's slug from `structure.json`.
- `tldr`: one-paragraph plain summary, carried into the keypoints essence block.
- `sections[]`: `{ id, title, kind, source_headings[] }`.
  - `id`: `<slug>--<heading-anchor>`, globally unique (see §slug rules), used as
    the HTML anchor target.
  - `kind`: `verbatim_critical` (contracts, rules, edge cases, warnings,
    input/output guarantees — carried near-verbatim, never reworded) or
    `summarized` (descriptive prose compressed to its meaning).
  - `source_headings[]`: the source-file heading anchors this section covers. A
    section may merge several adjacent headings into one logical block; this
    records exactly which ones, so a reviewer can map a section back to the
    precise span of the original `.md`.
- `contracts[]`: `{ text, source_anchor }`.
  - `text` is stored **already NORMALIZED**, per the gate-3 `normalize` defined
    in `scripts/constants.py`. `normalize` reduces text to a stream of **content
    words** (HTML tags stripped -> entities unescaped -> every run of non-word
    characters — whitespace, punctuation, symbols, and all markdown syntax —
    collapsed to a single space -> ends stripped -> lowercased). It compares
    *words* case-insensitively, not
    exact punctuation, because the markdown **source** keeps structural syntax
    (table pipes `|`, list markers, `[text](url)` links, backticks, emphasis)
    while a faithful **render** transforms or drops it (a table row becomes
    `col1: col2` or `<td>` cells). Comparing punctuation across those two
    surfaces false-fails faithful renders; comparing words does not, and a
    changed/dropped/invented *word* is still caught. This matches the project
    rule that completeness is *information* completeness, not text 1:1. The
    renderer round-trips the normalized form so gate 3 can compare it against
    both the normalized source bytes and the normalized fragment contract text.
    The match accepts a contiguous word-substring **or** an order-preserving
    **subsequence within a bounded window** (`assemble.py`'s `contract_present`):
    a contract lifted from a markdown TABLE ROW keeps the salient cells in source
    order but drops the intervening cell/connector words, so it is a tight
    subsequence of the row, not a contiguous run. The bounded window still rejects
    an invented word (breaks the subsequence) and words scraped from scattered,
    unrelated parts of the document (span too wide). **`normalize` is
    single-sourced in `scripts/constants.py`; do not re-implement it.**
  - `source_anchor` is **informational** — the `<slug>--<anchor>` the contract
    sits under. It is **not used for matching** in gate 3.

---

## 3. Fragment HTML contract — `fragments/<slug>.html`

Emitted by the S2 renderer, edited in place by the S2b verifier, stitched and
checked by the S4 assembler (`assemble.py`). A fragment is an HTML body
fragment (no `<html>`/`<head>`); the assembler wraps and stitches it into the
shell.

### Class vocabulary (the `mdhv-*` hooks)

Fragments may use **only** the shared class vocabulary; the committed design
system (`references/design-system.html`) styles exactly these. The full hook
list lives in `scripts/constants.py` as `REQUIRED_CLASS_HOOKS` (see §6). The
hooks a fragment uses directly:

- `.mdhv-section`     — one rendered section (carries `data-src-heading`).
- `.mdhv-keypoints`   — the essence block on top (tldr + key points + surfaced contracts).
- `.mdhv-contract`    — a bordered callout holding `verbatim_critical` text.
- `.mdhv-detail`      — used on/inside native `<details>` for long descriptive prose.
- `.mdhv-src-link`    — the link back to the source `.md`.

### Rules

- **Layered.** A `.mdhv-keypoints` essence block sits on top (tldr + key points
  + surfaced contracts), then the full content follows **in source heading
  order**.
- **Verbatim contracts.** `verbatim_critical` text is rendered in
  `.mdhv-contract` callouts — **never collapsed, never reworded**. (It may be
  HTML-escaped, and inline tokens may be wrapped in `<code>`; gate 3 normalizes
  before comparing, so faithful escaping does not break the byte check.)
- **Long prose collapses.** Long descriptive prose goes in native `<details>`
  (intelligently rendered detail — **not** raw markdown).
- **Every source heading is represented.** Each rendered `.mdhv-section`
  carries `data-src-heading` holding the **globally-unique** `<slug>--<anchor>`
  id(s) it covers. A merged section carries several ids (space-separated, or via
  hidden anchor spans). The section's own `id` is likewise a `<slug>--<anchor>`.
- **Source link.** Each fragment includes a `.mdhv-src-link` to the source
  `.md`.
- **Cross-file links** use `href="#<resolved_slug>--<anchor>"` (the `--` is the
  literal double dash). Cross-file hrefs resolve against `structure.json`
  headings, not against other fragments' internals.
- **In-document section cross-references.** When prose refers to **another
  section of the same file** — by section number / `§` (`§2.4`, `(2.4)`,
  `раздел 2.4`, `section 3`, `п. 2.3`, matched against the leading number in a
  heading's `text`) or by an unambiguous named section that maps 1:1 to one of
  this file's headings — the renderer **wraps the existing reference phrase** in
  `href="#<slug>--<anchor>"` using **this** file's own `slug` plus the matching
  heading `anchor`. It is **wrap-only** (the visible text is never reworded or
  reordered), uses **only anchors present in this file's headings** (an
  unmappable reference stays plain text), and is **never** placed inside a
  `.mdhv-contract` callout. These hrefs resolve against the same `structure.json`
  id space as cross-file links; gate 1 rejects any anchor that does not exist, so
  a broken in-document link can never ship. `normalize()` strips the `<a>` tag,
  so the wrap is invisible to the gate-3 contract check.
- **No inline `<style>`.** No `<style>` element and no inline `style=`
  styling — classes only, drawn from the shared vocabulary. All styling lives in
  the committed design system.

### Coverage invariant

The **union of all fragments' emitted ids** — every `.mdhv-section` `id` and
every value in every `data-src-heading` — **must equal** the set
`{ "<slug>--<anchor>" for every heading in structure.json }`. Gate 2 enforces
this per file as a subset check (every source heading id appears among that
fragment's `data-src-heading` values; extra renderer-introduced grouping ids are
allowed).

---

## 4. findings.json  (Stage S3: Cross-file — LLM, reads only `analysis/`)

A **root object** (never a bare array). Consumers read the `cross_file_findings`
key.

```json
{
  "cross_file_findings": [
    {
      "type": "contradiction",
      "severity": "high",
      "files": ["skills-auth-skill", "skills-session-skill"],
      "title": "Conflicting behavior on empty token",
      "description": "Auth says empty token -> reject; Session says empty token -> anonymous. Same input, different behavior."
    }
  ]
}
```

Field notes:

- Root is an object `{ "cross_file_findings": [ ... ] }`. **An empty run emits
  `{"cross_file_findings": []}` — never a bare `[]` and never `{}`.** The S4
  assembler reads `findings.json.get("cross_file_findings", [])` so a missing or
  empty key cannot crash Zone 3.
- Each finding: `{ type, severity, files, title, description }`.
  - `type`: `contradiction` | `coverage` | `signal_noise`.
  - `severity`: `high` | `medium` | `low`.
  - `files`: slugs involved (one or more).
- **`claims` (OPTIONAL, contradiction-oriented).** A finding MAY additionally
  carry `claims: [{ file: <slug>, claim: <text> }]` — the concrete, per-file
  statements that conflict, so the assembler can render an *A says ⇄ B says*
  comparison grid. It is **optional and never required**: a finding without
  `claims` (or with a malformed `claims`) is fully valid and renders as the
  normal prose card. When present and well-formed (a list of **≥ 2** objects,
  each with a non-empty `claim`), the assembler renders one cell per claim,
  heading each with that `file`'s title (linked to `#<slug>--file` when `file`
  resolves to a known slug). `file` is the slug of the file the claim is
  attributable to; `claim` is the exact statement that file makes. Best suited
  to `type: "contradiction"`.

---

## 5. manifest.json  (owned by the orchestrator)

Created at the start of a run by `init_session.py`; updated as stages complete.
Lets a run be inspected or resumed.

```json
{
  "session": "2026-05-22_14-30",
  "session_dir": ".mdHumanViewer/2026-05-22_14-30",
  "created_at": "2026-05-22T14:30:05Z",
  "root": ".",
  "output_language": "source",
  "selected_files": [
    { "path": "skills/auth/SKILL.md", "slug": "skills-auth-skill", "token_estimate": 1840 }
  ],
  "steps": {
    "preflight": { "status": "pending" },
    "parse":     { "status": "pending" },
    "render":    { "status": "pending" },
    "verify":    { "status": "pending" },
    "crossfile": { "status": "pending" },
    "assemble":  { "status": "pending" },
    "report":    { "status": "pending" }
  }
}
```

Field notes:

- `steps.*` keys are **exactly** `preflight`, `parse`, `render`, `verify`,
  `crossfile`, `assemble`, `report` — and only those.
- Each step has a `status`, one of `pending | running | done | failed`.
- `init_session.py` seeds **all** steps as `pending` (the script cannot know
  whether preflight/parse already ran). The **orchestrator** marks each step
  `done` as it completes — including `preflight` and `parse`, which complete
  during S0/S1 (otherwise the resume record stays misleadingly `pending`).
- `session_dir` is the session directory relative to the current working
  directory. The `session` folder name uses local time for UX; `created_at` is
  UTC for unambiguous cross-machine comparison.
- `output_language` is `source` (default) or a BCP-47-ish tag.
- `selected_files[].token_estimate` is omitted (not null) when the selection did
  not supply it.

### Resume semantics

On restart the orchestrator reads `manifest.json` and **skips a stage iff its
`status == done` AND its output artifact exists and validates**. A `done` status
with a missing or invalid artifact does not count — the stage re-runs. (Resume is
honest: a `done` flag is only trusted together with a valid output artifact.)

---

## 6. Design-system contract (single-sourced in `scripts/constants.py`)

`REQUIRED_CLASS_HOOKS`, `REQUIRED_MOUNT_MARKERS`, and `ALLOWED_CHROME_ANCHORS`
are defined **once** in `scripts/constants.py` and imported by both
`assemble.py` and the lint test, so the schema doc, the committed design system
(`references/design-system.html`), and the script cannot drift. The actual
values are listed here so this doc and the code stay aligned.

### `REQUIRED_CLASS_HOOKS`

The semantic class vocabulary the render agents may emit and the design system
must style:

```
mdhv-keypoints, mdhv-contract, mdhv-detail, mdhv-src-link, mdhv-section,
mdhv-strap, mdhv-toc, mdhv-toc-item, mdhv-graph, mdhv-node, mdhv-edge,
mdhv-graph-caption, mdhv-findings, mdhv-finding, mdhv-severity-high,
mdhv-severity-medium, mdhv-severity-low, mdhv-file, mdhv-file-header
```

### `REQUIRED_MOUNT_MARKERS`

Paired HTML comment markers in the committed design system; `assemble.py`
injects each zone's HTML between a START/END pair:

```
<!-- MDHV:STRAP:START -->     <!-- MDHV:STRAP:END -->
<!-- MDHV:TOC:START -->       <!-- MDHV:TOC:END -->
<!-- MDHV:GRAPH:START -->     <!-- MDHV:GRAPH:END -->
<!-- MDHV:FINDINGS:START -->  <!-- MDHV:FINDINGS:END -->
<!-- MDHV:FILES:START -->     <!-- MDHV:FILES:END -->
```

### `ALLOWED_CHROME_ANCHORS`

The only `href="#..."` targets that may point at shell chrome rather than a
`<slug>--<anchor>` section id. Every other in-page href (in-fragment and
cross-file) must resolve to a section id from `structure.json`; the design
system must not emit `href="#..."` outside this set, and each allowlisted anchor
must have a matching chrome id:

```
#mdhv-top, #mdhv-toc, #mdhv-graph, #mdhv-findings, #mdhv-files
```

### `normalize(s)` (gate 3)

The gate-3 contract normalizer is **also** single-sourced in
`scripts/constants.py` (`normalize`). It applies, in order: (1) strip all HTML
tags, (2) `html.unescape`, (3) replace every run of **non-word** characters
**and underscores** (whitespace, punctuation, symbols, markdown syntax, and `_`)
with a single space — leaving only content words (Unicode letters/digits)
separated by spaces, so a snake_case identifier and its humanized render converge
(`muted_keyword_filter` ≡ `muted keyword filter`), (4) strip ends, (5) lowercase. Gate 3 applies it to both sides of every
contract comparison; `analysis.contracts[].text` is stored already-normalized.
Stdlib-only (`re`, `html`).
