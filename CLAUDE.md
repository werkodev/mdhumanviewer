# CLAUDE.md — mdhumanviewer plugin

A Claude Code plugin that turns a group of related Markdown files into one
self-contained, human-readable HTML overview. **View, not storage:** never
modify source `.md`; regenerate on demand into a fresh dated session dir.

## Pipeline (S0–S5)

`SKILL.md` orchestrates; the heavy lifting is on disk and in subagents.

- **S0 Preflight** — match the `frontend-design` skill; ensure `references/design-system.html`; `init_session.py` creates `.mdHumanViewer/{stamp}/`.
- **S1 Parse** — `scripts/parse_structure.py` (deterministic, 0 LLM) → `structure.json`.
- **S2 Render** — N parallel `mdhv-renderer` agents, **1 read each** → `fragments/<slug>.html` + `analysis/<slug>.json`.
- **S2b Verify** — N parallel `mdhv-verifier` agents, **1 read each**, fix in place.
- **S3 Cross-file** — one `mdhv-crossfile` agent reads **only** `analysis/` + `structure.json` → `findings.json`.
- **S4 Assemble** — `scripts/assemble.py` (deterministic, 0 LLM) stitches + runs four hard-fail gates → `overview.html`.
- **S5 Report** — chat signpost.

Target: ~2 LLM reads/file, both parallel; zero serial whole-corpus passes; S1/S4 pure stdlib Python.

## Invariants (do not break)

- **`scripts/constants.py` is the single source of truth** for `REQUIRED_CLASS_HOOKS`, `REQUIRED_MOUNT_MARKERS`, `ALLOWED_CHROME_ANCHORS`, and `normalize()`. `assemble.py` and the lint tests import it — never redefine these elsewhere. `references/schemas.md` documents the same values; keep them in sync.
- **`normalize()`** (gate-3 contract check) is applied **symmetrically** to source bytes, the stored `analysis.contracts[].text`, and the fragment `.mdhv-contract` text. It strips HTML tags → unescapes entities → collapses every run of non-word characters **and underscores** (all whitespace, punctuation, symbols, markdown syntax, and `_`) to a single space → strips ends → lowercases. The underscore is a separator (same class as `-`/`/`/`.`), so a snake_case identifier and its humanized render converge (`muted_keyword_filter` ≡ `muted keyword filter`); non-Latin prose (Unicode letters/digits) survives. Reducing both sides to a case-insensitive stream of content words is what lets a faithful `<code>`-wrapped or table-row render not false-fail against the markdown source.
- **Field naming:** `structure.json` files use `file_path` (not `path`) and `links[]` (never `outbound_links`); these are the canonical names — do not reintroduce the alternates. `init_session.py` accepts `path` *or* `file_path` in selection entries.
- **Global section id = `<slug>--<anchor>`.** Slugs are unique across the run (colliding bases all get `-1/-2`); heading anchors are GitHub-style, de-duplicated within each file. The emitted page id space must equal `{<slug>--<anchor>}` from `structure.json`.
- **Fragments carry no inline `<style>`**; only the shared class vocabulary. Reskin = re-author `references/design-system.html` once (`mdhv-design-author --reskin`).
- **`assemble.py` must run with CWD at the analyzed ROOT** — gate 3 reads source bytes via `file_path` relative to CWD. It has a `sys.path` shim so a direct `python3 .../assemble.py` resolves `scripts.constants` from any CWD.
- **S4 consumes only `assemble.py`'s stdout JSON report + exit code.** The orchestrator must **not** `Read` `overview.html` (that would re-ingest the whole corpus and defeat the purpose).

## Tests

Stdlib `unittest` + `subprocess` + `tempfile` (no pytest). From the repo root:

```
python3 -m unittest discover -s tests
```

When editing in parallel (e.g. multiple agents), run a **single module**
(`python3 -m unittest tests.test_x`) so an in-progress sibling module can't break
a full-discover run. Every code change ships its own success + error/edge tests.
Python 3.9+ (PEP 585 generics); JSON to stdout, errors to stderr, non-zero exit on error.
