# Changelog

All notable changes to this plugin are documented in this file. The format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- _Nothing yet._

## [0.2.1] - 2026-06-01

### Fixed

- **Gate 1 (dangling-anchor) no longer false-fails on self-referential corpora.**
  `hrefs_in()` in `scripts/gates.py` is now **HTML-aware** (an `HTMLParser`
  subclass that collects the `href` of real `<a>` start tags only): a `#`-anchor
  quoted inside a `<code>` block is character data, and an escaped
  `&lt;a href="#x"&gt;` is text — **neither** is flagged, while a real
  `<a href="#typo">` (even inside `<pre>`) still is. So a corpus that talks about
  its own anchor markup (the plugin's own docs) no longer trips gate 1.
  `assemble.py` now imports `hrefs_in` from `gates` (its old raw-regex
  `_HREF_HASH_RE` is gone); the gate-1 decision logic is otherwise unchanged.
- **S3.5 reconcile now runs the same gate-1 check (detect-only parity).**
  `reconcile.py` imports `hrefs_in` + `structure_heading_ids` from `gates` and
  scans for genuinely dangling in-page anchors, appending them to a new
  `genuine_gaps.anchors` key **without mutating the fragment** (no `<a>` unwrap).
  The report shape is now
  `{auto_closed:{coverage,contracts,anchors}, genuine_gaps:{coverage,contracts,anchors}}`,
  and `main()` exits non-zero if **any** `genuine_gaps` list is non-empty. S3.5
  now guarantees S4 passes first try, ending the S3.5↔S4 verifier re-invoke loop
  that left self-runs unable to assemble.

### Documentation

- **Renderer/verifier prompts corrected to state the gate's real contract rule.**
  The `mdhv-renderer` / `mdhv-verifier` prompts now describe gate 3 as
  **contiguous-or-tight-subsequence** (an order-preserving subsequence within a
  bounded window, not contiguous-only), matching `gates.contract_present()`, and
  instruct agents to keep contracts **tight** — no merging or summarizing across
  contracts.

## [0.2.0] - 2026-06-01

### Fixed

- **The dependency graph now builds.** Edges were derived only from
  `[text](file.md)` links, but these corpora cross-reference each other almost
  entirely by backtick filename (`` `SKILL.md` ``, `` `references/schemas.md` ``) —
  which `parse_structure.py` dropped on the floor (its inline-code path detector
  excludes `.md`). So a richly cross-referenced corpus (the plugin's own docs)
  produced a near-empty graph that collapsed to a flat chip list. Inline-code
  references to another markdown node are now captured as a new `md_ref` link
  type and become graph edges — **strong** on an exact ROOT-relative path match,
  **weak** (tentative) on a bare-name match — alongside the existing
  `relative_md` edges. References to non-md files (`` `scripts/x.py` ``) stay
  `code_ref` and never create node edges.

### Changed

- **Zone 2 is now a layered flow diagram.** The radial node-link layout and the
  adjacency-matrix mode are replaced by a single deterministic, cycle-safe
  **layered** diagram: boxed nodes (title + file-path sub-label), arrowed
  cubic-bezier edges (solid = strong, dashed = weak), hub fills, and an
  isolated-node strip. Layer **depth is derived from connectivity** (longest-path
  layering) — a reference chain renders deep, a flat hub-and-spoke stays shallow.
  Render modes are now just `chips` (≤3 files or no edges) and `diagram`; the
  same `structure.json` still renders byte-identically.

## [0.1.2] - 2026-06-01

### Documentation

- Aligned every documentation surface with the code and the actual run order
  (docs/docstring only — no logic, execution order, or stage numbering changed):
  - `normalize()`'s final steps are now described as **strip ends → lowercase**
    (matching `s.strip().lower()`) in `references/schemas.md` and the
    `scripts/constants.py` docstring, resolving reversed wording that also
    contradicted `schemas.md` itself. `strip().lower()` ≡ `lower().strip()`, so
    the change is wording-only with no behavioral effect.
  - The pipeline quick-reference tables in `SKILL.md` and `README.md`, plus a new
    `CLAUDE.md` bullet, now place **S3.5 Reconcile before S3 Cross-file**, matching
    how the orchestrator actually runs (verify → reconcile → cross-file → assemble).

## [0.1.1] - 2026-06-01

### Fixed

- **Gate-3 `normalize()` no longer treats a literal `<` as an HTML tag.**
  `_TAG_RE` was `<[^>]*>`, so a literal `<` in technical markdown — a heredoc
  `python3 <<EOF`, a placeholder `<slug>`/`<anchor>` — opened a pseudo-tag that
  the regex extended to the next `>` anywhere downstream, deleting real content
  from the long _source_ side but not the short _contract_ side. That broke the
  "applied symmetrically" invariant and made faithful contracts fail gate 3
  ("not found in source bytes"), stalling the S3.5 reconcile pass before assembly
  on contract-dense, angle-bracket-heavy corpora (the plugin's own `SKILL.md`).
  The tag body now excludes `<` (`<[^<>]*>`), bounding every match to a single
  tag; genuine tags still strip wholesale. Added regression, symmetry, parity,
  and edge tests in `tests/test_constants.py`.

## [0.1.0] - 2026-05-31

First public release. mdHumanViewer grew out of the `mdHumanViewer.md` manifesto's
render-lane / grep-lane split and "view, not storage" stance, and lands it as a
read-once, fully-parallel pipeline over a disk-as-contract backbone: deterministic
discovery and assembly, intelligent per-file rendering, and a final deterministic
stitch with hard-fail fidelity gates — no heavy intermediate draft layer and no
serial whole-corpus render+review loop.

### Architecture

- **S0–S5 pipeline** (with an S3.5 reconcile step): Preflight → Parse
  (deterministic Python) → Render (N parallel per-file LLM agents) → Verify
  (N parallel per-file LLM agents) → Cross-file (one LLM over the `analysis/`
  sidecars) → Reconcile (deterministic Python) → Assemble (deterministic Python
  with four hard-fail gates) → Report.
- **About 2 LLM reads per file**, both passes fully parallel; **zero serial
  whole-corpus generation passes**. A source file is never pulled into an LLM
  context more than render + verify, and an embarrassingly-parallel corpus is
  never funneled through a single serial whole-corpus agent.
- **Per-file render** produces an intelligent semantic-HTML fragment plus an
  `analysis/<slug>.json` sidecar; assembly stitches the verified fragments
  deterministically rather than re-emitting `O(total-content)` from an LLM.
- **Shared design system authored once.** CSS/JS, mount markers, and the class
  vocabulary live in a committed, self-contained `references/design-system.html`.
  Render agents emit semantic HTML with no inline `<style>`; reskin = re-author
  the design once via the `mdhv-design-author` agent in `--reskin` mode.
- **Honest resume.** `init_session.py` seeds every manifest step `pending`; the
  orchestrator marks each `done`; on restart a stage is skipped only when its
  status is `done` and its output artifact exists and validates.

### Gates & fidelity

- **Four hard-fail assemble-time gates**: anchor resolution, per-file coverage,
  contract check, and no-runtime-fetch. A failing gate writes **no page** and
  exits non-zero, reporting the offending slug(s).
- **Contract check `normalize`** reduces both sides to a case-insensitive stream
  of content words: strip HTML tags → unescape entities → collapse every run of
  non-word characters and underscores (whitespace, punctuation, symbols, markdown
  syntax, `_`) to a single space → strip ends → lowercase, so a snake_case
  identifier and its humanized render converge. Applied **symmetrically** to source
  bytes, the stored `analysis.contracts[].text`, and the fragment
  `.mdhv-contract` text, so a faithful `<code>`-wrapped or markdown-table render
  doesn't false-fail against the source while real word-level distortions still
  fail.
- **Contract presence** accepts a contiguous substring **or** an order-preserving
  **subsequence within a bounded window**, so `verbatim_critical` contracts
  lifted from markdown table rows (salient cells kept in source order, connectors
  dropped) pass — while invented words and scattered cross-document scraping are
  still rejected.
- **Selection scoping.** `init_session.py --structure <full>` writes a session
  `structure.json` scoped to the selection (selected slugs only; graph pruned to
  selected nodes/edges; cross-file `resolved_slug` dropped for unselected
  targets), so the coverage and anchor gates never trip on unselected files and
  the orchestrator never hand-edits `structure.json`.
- **Self-healing coverage & contracts.** The renderer guarantees a
  `data-src-heading` id for every source heading; the verifier closes coverage
  and contract gaps deterministically in place; the S3.5 reconcile step closes
  any remainder losslessly. On an S4 gate failure the only response is a bounded
  (≤2-round) targeted re-invoke of the named slug's agent — never a whole-corpus
  redo or hand-patching of artifacts — then **stop and report** verbatim.

### Added

- `SKILL.md` orchestrator (S0–S5) at the plugin root (root-level single-skill
  layout).
- Deterministic scripts: `parse_structure.py` (discovery + heading/link structure
  + dependency graph), `init_session.py` (session dir + scoped manifest),
  `reconcile.py` (S3.5 lossless gate reconciliation), `assemble.py` (shell + zones
  + fragment stitch + four hard-fail gates), `check_session.py`, `mark_step.py`,
  `gates.py`, and `constants.py` (single source of the design-system class hooks,
  mount markers, and allowed chrome anchors).
- Pipeline agents: `mdhv-renderer` (S2), `mdhv-verifier` (S2b), `mdhv-crossfile`
  (S3), and the one-time / `--reskin` `mdhv-design-author`.
- `references/schemas.md` documenting every artifact contract (`structure.json`,
  `analysis/<slug>.json`, the fragment HTML contract, `findings.json`,
  `manifest.json`, and the design-system contract).
- `references/design-system.html`: the committed, self-contained shared design.
- `.claude-plugin/plugin.json` (the plugin manifest — single source of truth) and
  `.claude-plugin/marketplace.json` (a one-plugin marketplace with display name +
  listing metadata) for manual (`--plugin-dir`) and marketplace installation.
- `tests/` with stdlib (`unittest` + `subprocess` + `tempfile`, no `pytest`)
  coverage for the deterministic scripts, the four assemble-time gates, the
  design-system self-containment lint, and the SKILL/agent wiring. Run via
  `python3 -m unittest discover -s tests` from the repo root.
- `.github/workflows/ci.yml`: three parallel jobs — **guards** (privacy + rename
  tripwires), **lint** (`ruff` report-only, `actionlint`, `markdownlint-cli2`,
  `typos`, lychee link-check report-only, and JSON-schema validation of both
  manifests against their schemastore schemas), and **test** (the stdlib suite on
  Python 3.9–3.14) — under least-privilege `permissions: contents: read`,
  run-deduping `concurrency`, and SHA-pinned actions.
- `.github/scripts/guard.sh` (forbidden-token + rename tripwires), `ruff.toml`,
  `.markdownlint-cli2.yaml`, `typos.toml` (lint configs), and
  `.github/dependabot.yml` (keeps the GitHub-Actions pins current).
- `README.md`, `CLAUDE.md`, `LICENSE` (MIT), and this `CHANGELOG.md`.

### Notes

- The pipeline uses no heavy intermediate `draft.json` layer and no serial LLM
  render+review loop; fragments are stitched deterministically at assembly time.
- The output directory is `.mdHumanViewer/{yyyy-mm-dd_HH-MM}/` — one fresh dated
  session dir per run, so regenerating never clobbers a previous overview. Only
  `overview.html` is the deliverable; the rest are disposable working artifacts.
- `init_session.py` accepts `file_path` (the key `structure.json` uses) as an
  alias for `path` in selection entries, so a subset of `parse_structure.py`
  output feeds straight through without a rename.
