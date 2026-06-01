---
name: mdhumanviewer
description: "mdHumanViewer: assemble one self-contained, strongly human-readable HTML overview from a group of related Markdown files (skills, specs, RCA notes, docs) so a human grasps a whole system in minutes instead of reading dozens of files. Use whenever the user wants to understand, review, audit, map, or onboard onto a body of .md files AS A SYSTEM — phrases like \"what's actually in this project\", \"give me an overview of these skills\", \"contradictions between these docs\", \"render my markdown as a readable HTML map\", \"onboarding view of this folder\", \"big picture of ./references\", or points at a markdown directory and asks for the big picture. View, not storage: reads existing .md without modifying them; output is regenerated on demand. Read-once and fully parallel: each source enters an LLM context exactly twice (render + verify), both as N parallel agents in one turn; discovery, structure, graph, and final page assembly are pure deterministic Python off the LLM hot path."
---

# mdHumanViewer

Read a group of Markdown files and assemble from them a single self-contained
HTML representation, optimized for human perception of the system **as a whole**.
The design follows the project manifesto's render-lane / grep-lane split over a
disk-as-contract backbone, with two hard rules: each source `.md` is read into an
LLM context **exactly twice** (render + verify), **both fully parallel**, and there
are **zero serial whole-corpus generation passes** — discovery, structure, the
dependency graph, and final page assembly all run in stdlib Python off the LLM hot
path.

**Mission (from the origin manifesto):** the HTML is an *intelligent representation* of
the md — it loses no meaning, no section, no contract; its structure closely
mirrors the original; raw prose lives in the `.md` (linked). A human grasps
"what's there and how" in minutes and opens the md only for raw detail.

## Requirements (verified at S0, stated up front)

- The **`frontend-design`** skill must be installed/enabled — the
  `mdhv-design-author` agent uses it **once** to author (or `--reskin`) the committed
  `references/design-system.html`. The per-file render agents do **not** call it;
  they emit semantic HTML against the already-committed class vocabulary. S0 matches
  it from the system-reminder so the design-author can reach it.
- **Python 3.9+** on `PATH` — the orchestrator shells out to stdlib-only
  deterministic helpers and **never** hand-rolls glue: `scripts/parse_structure.py`,
  `scripts/init_session.py` (with `--list` / `--select`), `scripts/check_session.py`,
  `scripts/mark_step.py`, and `scripts/assemble.py`. 3.9 is the minimum because they
  use PEP 585 builtin generics.
- **Claude Code with root-level single-skill `SKILL.md` support** — this plugin
  uses the root-level single-skill layout.

## Operating principles (read before running)

- **View, not storage.** Never edit the source `.md`. Every run is a fresh full
  regeneration into a new dated session directory under `<ROOT>/.mdHumanViewer/`.
  The intermediate artifacts are disposable; only the current `overview.html`
  matters.
- **Disk is the contract between stages.** `structure.json` → `fragments/<slug>.html`
  + `analysis/<slug>.json` → `findings.json` → `overview.html`. Each stage reads
  from disk and writes to disk. This makes the pipeline restartable and keeps your
  context small.
- **The orchestrator stays light.** You (the main agent) hold only paths, the
  session directory, the matched `frontend-design` name, and step statuses. All
  heavy reading and reasoning happens inside subagents and Python scripts whose
  results land on disk. **Never read the full content of all source files into
  your own context** — that defeats the entire purpose. In particular you **must
  not `Read` `overview.html`** (S4's output): it embeds the whole corpus and
  re-reading it re-ingests everything you spent the pipeline avoiding.

- **HARD RULE — the scripts do all the glue; you never hand-roll it.** Every
  deterministic step has exactly one owning plugin script, and you only ever
  *invoke* that script and read its stdout + exit code. You **must NEVER**:
  - run `python3 -c …` or `python3 <<EOF` heredocs (or any ad-hoc inline Python)
    to read, filter, format, or check artifacts — `parse_structure.py` /
    `init_session.py --list` / `check_session.py` already produce that output;
  - **build `selection.json` by hand** or extract a renderer's slice by hand —
    `init_session.py --select` writes `selection.json`, the scoped
    `structure.json`, and one `slices/<slug>.json` per file for you;
  - **`Edit` `manifest.json` by hand** — `mark_step.py` is the only writer of step
    statuses.
  If you find yourself about to write inline Python, build JSON, or edit the
  manifest, stop: the correct move is to call the owning script.

The artifact schemas are the source of truth. Read them before assembly:
`${CLAUDE_PLUGIN_ROOT}/references/schemas.md`.

---

## How subagent invocations are written below

Every stage's invocation block looks like this:

> Use the `mdhv-X` subagent.
> KEY = `value`
> KEY = `value`
> Follow your instructions and ...

This is shorthand for **one real `Agent` tool call**, not a Bash command:
- `subagent_type` = the `mdhv-X` name on the first line.
- `prompt` = the rest of the block (the `KEY = value` lines plus the final
  imperative sentence), pasted verbatim into the prompt string.

The four pipeline subagents are `mdhv-design-author`, `mdhv-renderer`,
`mdhv-verifier`, and `mdhv-crossfile`.

**Subagent naming under plugin namespacing.** When this plugin is installed,
Claude Code namespaces each subagent as `mdhumanviewer:mdhv-X` (e.g.
`mdhumanviewer:mdhv-renderer`). The bare `mdhv-X` form below is the short
alias; if a particular Claude Code version or environment does not resolve the
bare name, pass the fully qualified `mdhumanviewer:mdhv-X` to `subagent_type`
instead. Both refer to the same subagent — pick whichever your runtime accepts.

Subagents only receive a single prompt string — there are no structured
parameters — so the `KEY = value` lines are how parameters reach them. Keep the
formatting (Markdown blockquote, backticks around values) so the subagent parses
them reliably.

**The parallelism convention (S2 and S2b).** When a stage fans out per file, **all
N `Agent` calls must go out in the same assistant turn** — as multiple tool-use
blocks in one message, not back-to-back across turns. That is what actually makes
them run concurrently (true parallelism). Claude Code runs all N subagents in
parallel and delivers all responses before you proceed, so you see all results in
one batch. The cost of S2 / S2b is therefore **max-of-files**, never
sum-of-files, and there is **no serial whole-corpus pass**.

---

## Pipeline overview (S0–S5)

| Stage | What | How | Reads src | Output |
|---|---|---|---|---|
| S0 Preflight | match `frontend-design`; ensure design system; create session | skill + python | 0 | session dir + `manifest.json` |
| S1 Parse | discover + structure + dependency graph | **Python, 0 LLM** | 0 | `structure.json` |
| S2 Render | intelligent per-file HTML fragment + analysis sidecar | **N parallel LLM, 1 read each** | 1 | `fragments/<slug>.html`, `analysis/<slug>.json` |
| S2b Verify | re-read source, check fragment fidelity, fix in place | **N parallel LLM, 1 read each** | 1 | revised `fragments/<slug>.html` + verdict |
| S3.5 Reconcile | losslessly close coverage + contract gates so assemble passes first try | **Python, 0 LLM** | 0 | fixed `fragments/` + `analysis/` in place |
| S3 Cross-file | contradictions / coverage / signal-noise | **1 LLM, reads only `analysis/`** | 0 | `findings.json` |
| S4 Assemble | shell + zones + stitch fragments + 4 hard-fail gates | **Python, 0 LLM** | 0 | `overview.html` + JSON report |
| S5 Report | signpost in chat | skill | 0 | chat message |

**Net:** each source enters an LLM context **exactly twice** (S2 + S2b), **both
parallel**; **zero** serial whole-corpus passes; S1 and S4 are pure Python.

`${SESSION}` below is the absolute session directory path returned by S1's
`init_session.py` (resolvable from the current working directory). Thread it into
every subagent call and every script invocation.

### Manifest discipline (honest resume)

The manifest's `steps` object is seeded **all `pending`** by `init_session.py`
with **exactly** these seven keys: `preflight`, `parse`, `render`, `verify`,
`crossfile`, `assemble`, `report`. After **each** stage's main action succeeds,
set `steps.<key>.status = "done"` — **including `preflight` and `parse`**, which
complete during S0/S1 (the script cannot know they ran, so if you skip marking
them the resume record stays misleadingly `pending`).

**Mechanics:** set every step status with `mark_step.py` — **never** `Edit`
`manifest.json` by hand:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION} <step> done
```
`<step>` is one of the seven keys above; `mark_step.py` validates the step key
against the known set, does a single read-modify-write of the `steps.<step>`
entry, and exits non-zero on an unknown key. Step marking is **serial** in the
orchestrator — mark one step at a time, never concurrently — so the
read-modify-write cannot clobber a sibling status. The seven step entries it can
write are exactly `steps.preflight`, `steps.parse`, `steps.render`,
`steps.verify`, `steps.crossfile`, `steps.assemble`, and `steps.report`.

**Honest resume.** On a restart with an existing session, read the manifest and
**skip a stage if and only if** its `steps.<key>.status == "done"` **AND** its
output artifact exists and validates:

| Stage | status key | artifact must exist + validate |
|---|---|---|
| S0 Preflight | `preflight` | `${SESSION}/manifest.json` parses; `references/design-system.html` exists |
| S1 Parse | `parse` | `${SESSION}/structure.json` parses |
| S2 Render | `render` | every `fragments/<slug>.html` AND `analysis/<slug>.json` exists and `analysis` parses |
| S2b Verify | `verify` | every `fragments/<slug>.html` still exists |
| S3 Cross-file | `crossfile` | `${SESSION}/findings.json` parses (has `cross_file_findings`) |
| S4 Assemble | `assemble` | `${SESSION}/overview.html` exists (do **not** `Read` it; an existence check is enough) |
| S5 Report | `report` | n/a (chat) |

If `status == "done"` but the artifact is missing or fails to validate, **re-run
that stage** — never trust a `done` flag without its artifact. Resume is honest: a
stage is skipped only when its status is `done` AND its output artifact exists and
validates.

---

## S0 — Preflight (match dependency, ensure design system, create session)

1. **Match the `frontend-design` skill from the system-reminder.** Look at the
   list of available skills in your system context (the `<system-reminder>` block
   listing skills). Match by **suffix** `frontend-design`. **Accept
   plugin-namespaced variants** (`document-skills:frontend-design`,
   `example-skills:frontend-design`, `claude-api:frontend-design`, or a bare
   `frontend-design`). Remember the matched name as `FRONTEND_DESIGN_SKILL` — the
   `mdhv-design-author` agent needs it (the per-file render agents do not). Do
   **not** verify via Bash or by invoking the skill.

   If no match appears and you cannot see the skills list, ask the user **once**
   for the exact name; hard-abort only if they confirm it is not installed:

   > mdHumanViewer uses the `frontend-design` skill to author its design system,
   > but I cannot confirm it from my current context. Is it installed, and if so
   > what is its exact name (e.g.
   > `document-skills:frontend-design` or a bare `frontend-design`)?

2. **Ensure the committed design system exists.** Check that
   `${CLAUDE_PLUGIN_ROOT}/references/design-system.html` exists (a Bash `test -f`
   or `ls` is fine — this is metadata, not corpus content). If it is **missing**,
   author it **once** via the design-author subagent, then continue:

   > Use the `mdhv-design-author` subagent.
   > DESIGN_OUT = `${CLAUDE_PLUGIN_ROOT}/references/design-system.html`
   > CONSTANTS_PATH = `${CLAUDE_PLUGIN_ROOT}/scripts/constants.py`
   > FRONTEND_DESIGN_SKILL = `<matched name from step 1>`
   > Author the single self-contained design system per your instructions.

   This is a **one-time / `--reskin`** path only; on a normal run the design
   system is already committed and you skip straight past this.

3. **Create the session.** This happens at the end of S1 (after selection), via
   `init_session.py` — see S1 step 3. The created `manifest.json` seeds all seven
   step statuses `pending`.

After S0 + S1 run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION}
preflight done`.

---

## S1 — Parse (deterministic, no LLM)

S1 is **three script calls and one question** — `parse_structure.py` →
`init_session.py --list` (show the menu) → ask the user → `init_session.py
--select "<spec>"`. **You write NO Python, build NO `selection.json`, and extract
NO slices by hand** (the HARD RULE above): the scripts produce every artifact.

1. **Parse** the target root (current directory unless the user named one). Use
   the **`${CLAUDE_PLUGIN_ROOT}` dollar-brace Bash form** so the script resolves
   regardless of install location, and **save the output to a file**:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parse_structure.py <ROOT> \
     > <ROOT>/.mdHumanViewer/structure.full.json
   ```
   It prints the **whole-ROOT** `structure.json`: `meta`, `files[]` (slug,
   file_path, language, title, token_estimate, headings[], links[]),
   `groups[]` (directory grouping), and `graph {nodes[], edges[]}`. This is the
   `<full>` structure you hand to `init_session.py` in steps 2 and 3 so the
   session gets a copy **scoped to the selection**. It is whole-corpus and almost
   always lists MORE files than the user will select. The script already skips the
   usual noise (`.git`, `.claude`, `node_modules`, `.mdHumanViewer`, `dist`,
   `build`, `.venv`, `venv`, `__pycache__`). If the tree
   has additional large dirs to skip, pass `--exclude DIR1 DIR2 ...` (this
   **overrides** the defaults entirely, so re-list the standard noise names if you
   still want them skipped).

2. **Show the grouped menu** — do **not** format it yourself with Python. Run
   `init_session.py --list` against the saved `<full>` structure; it prints a
   ready-to-show **markdown** menu grouped by directory (per group: dir · file
   count · summed token estimate; per file: slug · title · ~token estimate):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/init_session.py \
     --structure <ROOT>/.mdHumanViewer/structure.full.json --list
   ```
   **Display its stdout verbatim** to the user (this is the menu — no session is
   created in `--list` mode). Then **ask what to include** in one clear prompt —
   accept a group/directory, individual files (by slug or ROOT-relative path), a
   glob (e.g. `skills/*`), or `all`. Do **not** skip selection even for an
   "obvious" all-files run: the selection defines the system boundary the overview
   describes.

3. **Create the scoped session** from the user's selection with
   `init_session.py --select "<spec>"` — this single call resolves the spec,
   writes `selection.json`, the **scoped** `${SESSION}/structure.json`, the
   per-file `${SESSION}/slices/<slug>.json`, and the manifest. **You never build
   `selection.json` or the slices by hand:**
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/init_session.py \
     --structure <ROOT>/.mdHumanViewer/structure.full.json --root <ROOT> \
     --select "<spec>" --language <source|tag>
   ```
   `<spec>` is the user's selection as a comma-separated mix of slugs,
   ROOT-relative file paths, directory names, globs (`skills/*`), and the reserved
   token `all` (resolution: `all` first; otherwise each token tries slug → exact
   path → directory → glob and the first category with ≥1 match wins; a token
   matching nothing exits non-zero — re-ask the user). `--language` is `source`
   unless the user explicitly asked for the overview in another language (normalize
   to a BCP-47 tag: `ru`, `en`, `de`, …). The script prints the session dir path on
   its **last stdout line** — **capture it as `${SESSION}`**; it is an absolute path
   usable directly from your current directory.

   **`--select` writes `${SESSION}/structure.json` scoped to your selection**
   (selected slugs only; graph pruned; whole-corpus `groups[]` dropped; cross-file
   `resolved_slug` dropped for unselected targets) **plus one
   `${SESSION}/slices/<slug>.json` per selected file** (the renderer's
   `STRUCTURE_SLICE`, built from the scoped data so it never points at an
   un-rendered file). Always use that scoped `${SESSION}/structure.json` for
   S2/S3/S4 — never the whole-ROOT `structure.full.json`. This is what keeps
   `assemble.py`'s coverage and anchor gates from failing on unselected files that
   were never rendered. **Never hand-edit `${SESSION}/structure.json`, the
   `slices/`, or `selection.json`** — they are produced only by `init_session.py`;
   if one looks wrong, the selection spec or the `--structure` path is wrong, not
   the artifact.

   (The legacy `--selection <SELECTION.json>` mode still works for a
   pre-built selection file, but it carries no headings/links so it writes **no**
   `slices/` — prefer `--select` so the renderer gets `STRUCTURE_SLICE_PATH`.)

After this stage you hold: `${SESSION}`, the list of `{path, slug}`, the output
language, and `FRONTEND_DESIGN_SKILL`. That is all you keep in context. Mark the
step done with the script (never by editing `manifest.json`):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION} parse done
```

---

## S2 — Render (N parallel subagents)

Invoke the **`mdhv-renderer`** subagent once per selected file, **all in the
same assistant turn** (the parallelism convention above), so they run in parallel
and finish together. Each sees exactly one file and knows nothing of the others.

For each selected `{path, slug}`, emit one `Agent` call with this prompt:

> Use the `mdhv-renderer` subagent.
> SOURCE_PATH = `<path>`
> SLUG = `<slug>`
> STRUCTURE_SLICE_PATH = `${SESSION}/slices/<slug>.json`
> FRAGMENT_OUT = `${SESSION}/fragments/<slug>.html`
> ANALYSIS_OUT = `${SESSION}/analysis/<slug>.json`
> OUTPUT_LANGUAGE = `<value of manifest.output_language, e.g. source or ru>`
> CLASS_VOCAB = `${CLAUDE_PLUGIN_ROOT}/references/schemas.md`
> Read the source once and write the layered fragment + analysis sidecar.

`STRUCTURE_SLICE_PATH` **points at the slice file** `init_session.py` wrote in S1
(`${SESSION}/slices/<slug>.json`) — the renderer reads it itself. Do **not**
inline-extract this file's headings/links from `structure.json` by hand; pass the
path.

When all complete, **verify the batch deterministically** — do **not** write an
ad-hoc existence/parse loop. Run `check_session.py`, which checks every selected
slug has a non-empty `fragments/<slug>.html` and a parseable `analysis/<slug>.json`:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_session.py ${SESSION}
```
It prints `{ok, missing_fragments:[], unparsable_analysis:[], bad_findings}` and
exits non-zero when not ok. On `!ok`, **re-invoke only the named-failing slug(s)**
(a fresh `mdhv-renderer` call for just those slugs, from `missing_fragments` /
`unparsable_analysis`) — never redo the whole batch — then re-run
`check_session.py`. If a slug still fails after a second attempt, report which file
is broken and ask the user whether to skip it or abort.

Mark the step done with the script:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION} render done
```

---

## S2b — Verify (N parallel subagents)

Invoke the **`mdhv-verifier`** subagent once per selected file, again **all in
the same assistant turn**. Each re-reads exactly one source once, checks the
fragment for completeness and distortion, and fixes defects **in place** via
`Edit` (preserving the class vocabulary and `data-src-heading`).

For each selected `{path, slug}`, emit one `Agent` call:

> Use the `mdhv-verifier` subagent.
> SOURCE_PATH = `<path>`
> FRAGMENT_PATH = `${SESSION}/fragments/<slug>.html`
> ANALYSIS_PATH = `${SESSION}/analysis/<slug>.json`
> Re-read the source once, verify completeness/fidelity, fix in place, return your verdict.

Each returns a verdict `{needs_revision, issues[]}`. When all complete, **validate
the batch again with `check_session.py`** instead of an ad-hoc loop:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_session.py ${SESSION}
```
On `!ok`, **re-invoke only the named-failing slug(s)** (a fresh `mdhv-verifier`
call for just those slugs), then re-run `check_session.py`. Carry any unresolved
`issues[]` into the S5 report so the user knows. Mark the step done with the
script:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION} verify done
```

---

## S3 — Cross-file (1 subagent, reads only analysis/)

Invoke the **`mdhv-crossfile`** subagent **once**. It reads **only**
`analysis/*.json` plus `structure.json` — it **never** re-reads the source `.md`
(that read-once guarantee is the whole point) — and writes the cross-file
findings.

> Use the `mdhv-crossfile` subagent.
> ANALYSIS_DIR = `${SESSION}/analysis`
> STRUCTURE_PATH = `${SESSION}/structure.json`
> FINDINGS_OUT = `${SESSION}/findings.json`
> Apply the three optics and write the root-object findings.json.

It writes the **root object** `findings.json` = `{"cross_file_findings": [...]}`
(an empty run emits `{"cross_file_findings": []}`, never a bare `[]` or `{}`).
**Validate it with `check_session.py`** (its `bad_findings` flag is true unless
`findings.json` is a well-formed root object carrying a `cross_file_findings`
list) — do **not** write an ad-hoc parse loop:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_session.py ${SESSION}
```
On `bad_findings`, re-invoke `mdhv-crossfile` once, then re-run
`check_session.py`. Mark the step done with the script:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION} crossfile done
```

---

## S3.5 — Reconcile (deterministic, no LLM)

Run **after verify (S2b), before crossfile/assemble** — `reconcile.py` is a
sanctioned plugin script, **not** improvisation (it is *not* `python3 -c` and it
is *not* hand-editing artifacts; the HARD RULE about ad-hoc python and manual
artifact edits below still stands in full). It deterministically closes the two
*mechanical* gate properties — gate 2 (per-file coverage) and gate 3 (contract
fidelity) — **losslessly**, so `assemble.py` (S4) passes on the **FIRST attempt**
in the common case, with **no** verifier re-invoke round. Run it once:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py --session ${SESSION}
```

It reads each selected file's `fragments/<slug>.html` + `analysis/<slug>.json`,
attaches missing `<slug>--<anchor>` coverage ids via the language-agnostic
`analysis.sections[].source_headings[]` join, surfaces source-faithful contracts
into the fragment, and removes only exact-duplicate contract entries — all
losslessly (it never snaps a contract across a `not`/`never`/`unless`, never
drops a real contract, never weakens a gate). It writes the lossless fixes in
place and prints the JSON report:
`{auto_closed:{coverage:[],contracts:[]}, genuine_gaps:{coverage:[],contracts:[]}}`.

- **Exit 0** (`genuine_gaps` empty) → coverage + lossless contract contracts are
  closed deterministically. Go **straight to S4 assemble** — no re-invoke round.
- **Non-zero exit** (`genuine_gaps` non-empty) → there is genuine content loss
  that cannot be closed losslessly. **Re-invoke ONLY the named slug's agent**, by
  gap kind (each entry names its slug):
  - a `genuine_gaps.coverage` entry (a source heading no analysis section claims)
    → re-invoke that slug's **`mdhv-renderer`**;
  - a `genuine_gaps.contracts` entry (a stored contract not present in the source
    bytes — a semantic judgment, never snapped or dropped automatically) →
    re-invoke that slug's **`mdhv-verifier`**.

  Then **re-run `reconcile.py`**. This is **bounded — at most two rounds** for a
  given failing slug. If `genuine_gaps` is still non-empty after that, **stop and
  report** the reconcile JSON verbatim (the genuine gap plus the offending
  ids/contracts) and ask how to proceed — do **not** improvise a workaround, do
  **not** shrink the selection, and do **not** weaken a contract to force the gate
  green (fidelity is the mission).

`reconcile.py` is the deterministic form of the join the verifier already
performs in prose; the verifier's prose coverage/contract sections stay as
belt-and-suspenders. There is no manifest step for reconcile — it sits between
`verify` and `crossfile`/`assemble` and is gated by their step statuses.

---

## S4 — Assemble (deterministic, no LLM)

Run the assembler. Use the `${CLAUDE_PLUGIN_ROOT}` dollar-brace Bash form and
pass the committed design system as `--design`:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/assemble.py \
  --structure   ${SESSION}/structure.json \
  --analysis-dir ${SESSION}/analysis \
  --fragments-dir ${SESSION}/fragments \
  --findings    ${SESSION}/findings.json \
  --design      ${CLAUDE_PLUGIN_ROOT}/references/design-system.html \
  --out         ${SESSION}/overview.html \
  --manifest    ${SESSION}/manifest.json
```

Consume **only the stdout JSON report and the exit code**. The report shape is:
`{files, sections, findings, graph_mode, dangling_anchors:[], coverage_gaps:[],
contract_violations:[], external_fetches:[]}`.

- **Exit 0** → the page was written to `${SESSION}/overview.html` and all four
  hard-fail gates passed (anchor resolution, per-file coverage, contract check,
  no-runtime-fetch). **Do NOT `Read` `overview.html`** — it embeds the entire
  corpus and reading it re-ingests everything the read-once design avoided. The
  stdout report is all you need.
- **Non-zero exit** → one or more gate arrays
  (`dangling_anchors` / `coverage_gaps` / `contract_violations` /
  `external_fetches`) are non-empty and **no page was written**. **Surface the
  report** to the user: name the failing gate(s) and the specific offending
  ids/anchors/contracts from the arrays, so they can see exactly what is missing
  or wrong. Do not paper over a dropped contract to make the stage "pass".

**Targeted repair, not a broad redo.** A gate failure is a *per-file* fact:
every entry in the gate arrays names a specific slug, so route the fix to that
slug alone — never re-render or re-verify the whole corpus, and **never shrink
the selection to dodge the gate**.

- When `assemble.py` reports a **non-empty `coverage_gaps` array**, the
  orchestrator must **re-invoke** ONLY the `mdhv-verifier` subagent for the
  specific slug(s) named in `coverage_gaps`, passing the exact missing
  `SLUG--anchor` ids so the verifier knows precisely which source headings lack a
  `data-src-heading` id, then re-run `assemble.py`. The missing ids are a
  mechanical fix the verifier closes **in place** (a merged section carries the
  extra ids space-separated in `data-src-heading`, or a hidden anchor span is
  added for each non-lead heading — both already allowed by the fragment
  contract). Do NOT re-render or re-verify the whole corpus to close a coverage
  gap.
- A non-empty **`contract_violations`** array routes to the named slug's
  **`mdhv-verifier`**, passing the exact offending contract text(s). The verifier
  re-derives that `analysis.contracts[].text` so its words are faithful to the
  source (gate 3 compares content words, so a contract lifted from a table row or
  list need only carry the same words, in order — not the source's `|`/`-`/`[]()`
  punctuation), and/or fixes the `.mdhv-contract` callout. Then re-run `assemble.py`.
- A non-empty **`dangling_anchors`** array routes to the named slug's
  `mdhv-renderer` / `mdhv-verifier` to fix the offending `href`/id, then re-run.

**Never debug or patch the pipeline by hand — this is a hard rule, not a
preference.** Every artifact has exactly one producer (a script or a subagent);
the orchestrator only *invokes* producers and reads their stdout + exit code. On
a gate failure you MUST NOT:
- read, `grep`, `cat`, `sed`, or otherwise **inspect the plugin's own scripts**
  (`parse_structure.py`, `init_session.py`, `assemble.py`, `constants.py`) to
  reason about the failure — the stdout report already names the exact offending
  ids / anchors / contracts;
- write **ad-hoc Python** (`python3 -c …`, `python3 <<EOF` heredocs) to inspect
  artifacts, re-implement `normalize` or any gate, or compute/apply a fix;
- **hand-edit** `analysis/*.json`, `fragments/*.html`, `structure.json`, or
  `findings.json` — these are produced ONLY by their owning subagent/script.

The ONLY allowed response to a gate failure is the **targeted re-invoke** above,
then re-running `assemble.py`.

**Bounded, then stop.** Attempt at most **two** targeted re-invoke rounds for a
given failing slug. If the gate still fails, **stop and report** the assembler's
JSON report verbatim to the user (the failing gate plus the offending items) and
ask how to proceed. Do NOT improvise a workaround, do NOT shrink the selection to
dodge the gate, and do NOT write a partial or lossy page.

On success mark the step done with the script (never by editing `manifest.json`):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION} assemble done
```

---

## S5 — Report (you, in chat)

Finish in the main conversation with a short, scannable signpost (the HTML is the
deliverable; the report is the signpost):
- Where the output is: `${SESSION}/overview.html` (and how to open it).
- What was processed: file count, total token estimate (labelled an estimate),
  output language, and the graph mode (`chip` / `matrix` / `svg`) from the report.
- The headline of the system map: how many files, the main groups.
- **Cross-file findings up front** — especially `high`-severity contradictions and
  coverage gaps from S3, with the files involved. This is the part the user most
  needs and the hardest to get any other way.
- Any unresolved S2b verifier `issues[]`.
- A pointer that detail is collapsible in the HTML and links back to the source
  `.md` for raw digging.

After sending the chat report, mark the step done with the script (never by
editing `manifest.json`):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mark_step.py ${SESSION} report done
```

---

## Failure handling

- A failed render/verify subagent: re-invoke just that slug's `mdhv-renderer` /
  `mdhv-verifier`; never redo the batch.
- Malformed `analysis/<slug>.json`: re-run that file's `mdhv-renderer` once; if it
  still fails, report which file is broken rather than silently proceeding.
- An S4 gate failure: surface the report's failure arrays; never write or present a
  partial `overview.html`. Surfacing a gap is more valuable than a clean-looking but
  lossy overview. Then repair it **targeted, per slug** (see S4's "Targeted repair"
  note): a non-empty `coverage_gaps` re-invokes ONLY the named slug's
  `mdhv-verifier` with the exact missing `SLUG--anchor` ids; `dangling_anchors` /
  `contract_violations` route back to the named slug's renderer/verifier. Never
  re-render/re-verify the whole corpus and never shrink the selection to dodge the
  gate.
