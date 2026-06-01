# Contributing to mdHumanViewer

Developer/maintainer guide for the plugin **source**. (This is not loaded or used
when the plugin is installed — runtime behavior lives entirely in `SKILL.md`,
`agents/`, `scripts/`, and the manifest.)

## Where things are

- **`SKILL.md`** — the authoritative runtime contract: the full S0–S5 pipeline,
  manifest discipline, honest resume, the `<slug>--<anchor>` id space, and the
  orchestration rules. This is the architecture document.
- **`references/schemas.md`** — the artifact schemas (`structure.json`,
  `analysis/<slug>.json`, the fragment HTML contract) and the gate definitions,
  kept in sync with `scripts/constants.py`.
- **`scripts/`** — the deterministic, stdlib-only helpers (parse, init-session,
  reconcile, assemble, gates, mark-step, check-session).

## Invariants (do not break)

- **`scripts/constants.py` is the single source of truth** for
  `REQUIRED_CLASS_HOOKS`, `REQUIRED_MOUNT_MARKERS`, `ALLOWED_CHROME_ANCHORS`, and
  `normalize()`. `assemble.py`, `gates.py`, and the lint tests import it — never
  redefine these elsewhere. `references/schemas.md` documents the same values;
  keep them in sync.
- **`gates.py` is the single source for the gate primitives** — `normalize`,
  `contract_present`, `hrefs_in` (HTML-aware: real `<a>` elements only),
  `structure_heading_ids`, `data_src_headings_in`. `assemble.py` and
  `reconcile.py` both import them so the deterministic reconcile (S2.5) closes
  exactly what the S4 gates check — never re-implement a gate inline.
- **`normalize()`** (gate-3 contract check) is applied **symmetrically** to source
  bytes, the stored `analysis.contracts[].text`, and the fragment
  `.mdhv-contract` text: strip HTML tags → unescape entities → collapse every run
  of non-word characters **and underscores** to a single space → strip ends →
  lowercase. See its docstring in `scripts/constants.py` for the full rationale.
- **Field naming:** `structure.json` files use `file_path` (not `path`) and
  `links[]` (never `outbound_links`). `init_session.py` accepts `path` *or*
  `file_path` in selection entries.
- **Global section id = `<slug>--<anchor>`.** Slugs are unique across the run;
  heading anchors are GitHub-style, de-duplicated within each file. The emitted
  page id space must equal `{<slug>--<anchor>}` from `structure.json`.
- **Fragments carry no inline `<style>`** — only the shared class vocabulary.
  Reskin = re-author `references/design-system.html` once (`mdhv-design-author
  --reskin`).
- **`assemble.py` must run with CWD at the analyzed ROOT** — gate 3 reads source
  bytes via `file_path` relative to CWD. It has a `sys.path` shim so a direct
  `python3 .../assemble.py` resolves `scripts.constants` from any CWD.

## Tests

Stdlib `unittest` + `subprocess` + `tempfile` (no pytest). From the repo root:

```
python3 -m unittest discover -s tests
```

When editing in parallel (e.g. multiple agents), run a **single module**
(`python3 -m unittest tests.test_x`) so an in-progress sibling module can't break
a full-discover run. Every code change ships its own success + error/edge tests.
Python 3.9+ (PEP 585 generics); JSON to stdout, errors to stderr, non-zero exit on
error.
