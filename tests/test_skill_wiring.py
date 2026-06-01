"""Structural wiring lint for the orchestrator SKILL.md and the agent specs.

SKILL.md and agents/*.md are prose, not code, so they have no behavioural unit
tests. These structural checks pin the orchestrator's mechanical wiring so it
cannot silently drift from the scripts and agents it drives:

  * the script paths SKILL.md shells out to actually exist on disk;
  * every ``Agent``-invoked subagent name mentioned in SKILL.md resolves to a
    real ``name:`` value in an ``agents/*.md`` spec (catches typos / namespacing
    slips);
  * every ``agents/*.md`` carries valid frontmatter with ``name``,
    ``description``, and ``tools``;
  * the manifest step keys SKILL.md documents/uses are exactly the seven keys
    ``init_session.py`` seeds.

stdlib unittest only (no pytest), mirroring the v1 test style. Run with:

    python3 -m unittest tests.test_skill_wiring -v
"""

import ast
import os
import re
import sys
import unittest

# Make the repo root importable / discoverable regardless of CWD.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

SKILL_PATH = os.path.join(_REPO_ROOT, "SKILL.md")
AGENTS_DIR = os.path.join(_REPO_ROOT, "agents")
SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
INIT_SESSION_PATH = os.path.join(SCRIPTS_DIR, "init_session.py")
RENDERER_PATH = os.path.join(AGENTS_DIR, "mdhv-renderer.md")
VERIFIER_PATH = os.path.join(AGENTS_DIR, "mdhv-verifier.md")

# The seven manifest step keys, single-sourced by init_session.py. We assert
# SKILL.md uses exactly this set, and that it matches what the script actually
# seeds (parsed from the source, not duplicated here as a literal contract).
EXPECTED_STEP_KEYS = {
    "preflight", "parse", "render", "verify", "crossfile", "assemble", "report",
}

# Scripts SKILL.md is expected to shell out to.
EXPECTED_SCRIPTS = ("parse_structure.py", "init_session.py", "assemble.py")

# Deterministic-helper scripts that must exist on disk (the new glue replacing the
# orchestrator's ad-hoc python). Their presence is asserted independently of the
# ${CLAUDE_PLUGIN_ROOT} dollar-brace form check above.
EXPECTED_HELPER_SCRIPTS = ("check_session.py", "mark_step.py")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _split_frontmatter(text):
    """Return (frontmatter_str, body) for a leading '---' YAML block, or (None, text)."""
    if not text.startswith("---"):
        return None, text
    # Frontmatter is the block between the first two '---' delimiter lines.
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _parse_simple_frontmatter_keys(fm):
    """Top-level keys of a simple YAML frontmatter block (key: ... at col 0).

    Good enough for these specs: we only need the presence of `name`,
    `description`, `tools`, including block/folded scalars (`>-`, `|`) whose
    continuation lines are indented.
    """
    keys = {}
    cur_key = None
    cur_val_lines = []
    for line in fm.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s?(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            if cur_key is not None:
                keys[cur_key] = "\n".join(cur_val_lines).strip()
            cur_key = m.group(1)
            cur_val_lines = [m.group(2)]
        else:
            if cur_key is not None:
                cur_val_lines.append(line)
    if cur_key is not None:
        keys[cur_key] = "\n".join(cur_val_lines).strip()
    return keys


def _agent_files():
    return [
        os.path.join(AGENTS_DIR, f)
        for f in sorted(os.listdir(AGENTS_DIR))
        if f.endswith(".md")
    ]


def _agent_names():
    """Set of `name:` values declared across agents/*.md."""
    names = set()
    for path in _agent_files():
        fm, _ = _split_frontmatter(_read(path))
        assert fm is not None, f"{path} has no frontmatter"
        keys = _parse_simple_frontmatter_keys(fm)
        name = keys.get("name")
        assert name, f"{path} has no `name:` in frontmatter"
        names.add(name.strip())
    return names


def _init_session_step_keys():
    """The step keys init_session.py actually seeds, parsed from its AST.

    We find the dict literal assigned to a "steps" key in the manifest and read
    its string keys — no execution, no duplication of the contract here.
    """
    tree = ast.parse(_read(INIT_SESSION_PATH))
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "steps"
                    and isinstance(v, ast.Dict)
                ):
                    step_keys = {
                        kk.value
                        for kk in v.keys
                        if isinstance(kk, ast.Constant) and isinstance(kk.value, str)
                    }
                    if step_keys:
                        found = step_keys
    return found


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

class TestSkillExists(unittest.TestCase):
    def test_skill_md_present_with_name(self):
        self.assertTrue(os.path.isfile(SKILL_PATH), "SKILL.md missing at repo root")
        fm, _ = _split_frontmatter(_read(SKILL_PATH))
        self.assertIsNotNone(fm, "SKILL.md has no YAML frontmatter")
        keys = _parse_simple_frontmatter_keys(fm)
        self.assertEqual(
            keys.get("name"), "mdhumanviewer",
            "SKILL.md frontmatter `name:` must be 'mdhumanviewer'",
        )
        self.assertTrue(keys.get("description"), "SKILL.md needs a description")


class TestScriptPathsExist(unittest.TestCase):
    def test_referenced_scripts_exist_on_disk(self):
        for script in EXPECTED_SCRIPTS:
            self.assertTrue(
                os.path.isfile(os.path.join(SCRIPTS_DIR, script)),
                f"scripts/{script} does not exist",
            )

    def test_skill_references_each_script(self):
        text = _read(SKILL_PATH)
        for script in EXPECTED_SCRIPTS:
            self.assertIn(
                script, text,
                f"SKILL.md never references scripts/{script}",
            )

    def test_skill_uses_plugin_root_dollar_brace_form(self):
        """Script calls must use the ${CLAUDE_PLUGIN_ROOT}/scripts/... Bash form."""
        text = _read(SKILL_PATH)
        for script in EXPECTED_SCRIPTS:
            self.assertIn(
                f"${{CLAUDE_PLUGIN_ROOT}}/scripts/{script}", text,
                f"SKILL.md must invoke scripts/{script} via "
                "${CLAUDE_PLUGIN_ROOT}/scripts/ (dollar-brace Bash form)",
            )


class TestAgentFrontmatter(unittest.TestCase):
    def test_at_least_the_four_pipeline_agents_present(self):
        names = _agent_names()
        for required in (
            "mdhv-design-author",
            "mdhv-renderer",
            "mdhv-verifier",
            "mdhv-crossfile",
        ):
            self.assertIn(required, names, f"agent `{required}` missing from agents/")

    def test_every_agent_has_name_description_tools(self):
        for path in _agent_files():
            fm, _ = _split_frontmatter(_read(path))
            self.assertIsNotNone(fm, f"{path} has no frontmatter")
            keys = _parse_simple_frontmatter_keys(fm)
            for required in ("name", "description", "tools"):
                self.assertTrue(
                    keys.get(required),
                    f"{os.path.basename(path)} frontmatter missing `{required}`",
                )

    def test_every_agent_declares_exactly_the_expected_frontmatter_keys(self):
        """Each agent must declare exactly the four keys Claude Code reads —
        ``name``, ``description``, ``tools``, ``model`` — and nothing else. Catches
        a dropped ``model:`` (the agent would silently inherit an unintended
        default) and a stray/typo'd key that the loader would ignore.
        """
        expected = {"name", "description", "tools", "model"}
        for path in _agent_files():
            fm, _ = _split_frontmatter(_read(path))
            self.assertIsNotNone(fm, f"{path} has no frontmatter")
            keys = _parse_simple_frontmatter_keys(fm)
            self.assertEqual(
                set(keys), expected,
                f"{os.path.basename(path)} frontmatter keys {sorted(keys)} != "
                f"{sorted(expected)}",
            )
            model = keys.get("model", "")
            self.assertTrue(
                model == "inherit" or model.startswith("claude-"),
                f"{os.path.basename(path)} `model:` must be 'inherit' or a "
                f"'claude-*' id, got {model!r}",
            )

    def test_every_agent_frontmatter_is_yaml_safe(self):
        """A real YAML loader (what Claude Code uses) rejects a *plain* (unquoted)
        scalar value that contains a colon-space ``: `` or opens a flow collection
        (`{`/`[`) — it reads them as a nested mapping ("mapping values are not
        allowed here") and silently drops the whole frontmatter. The simple
        line-parser above does NOT catch this, so a description like
        ``... = {cross_file_findings: [...]} via three optics: contradictions``
        loaded with empty metadata until quoted. Guard every top-level scalar.
        """
        hazard = ": "  # colon-space: the YAML mapping indicator
        for path in _agent_files():
            fm, _ = _split_frontmatter(_read(path))
            for line in fm.splitlines():
                if line.startswith((" ", "\t")):
                    continue  # block-scalar continuation, not a top-level key
                m = re.match(r"^([A-Za-z0-9_-]+):\s?(.*)$", line)
                if not m:
                    continue
                value = m.group(2)
                if value[:1] in ('"', "'", "|", ">", ""):
                    continue  # quoted or block scalar (or empty) — safe
                where = f"{os.path.basename(path)} `{m.group(1)}:`"
                self.assertNotIn(
                    hazard, value,
                    f"{where} is an unquoted scalar containing '{hazard}' — this "
                    f"breaks YAML frontmatter parsing. Quote it (\"...\") or use a "
                    f"block scalar (>-).",
                )
                self.assertNotIn(
                    value[:1], "{[",
                    f"{where} is an unquoted scalar opening a flow collection — "
                    f"quote it or use a block scalar.",
                )


class TestAgentNameCrossCheck(unittest.TestCase):
    """Every Agent-invoked subagent named in SKILL.md must resolve to a real agent.

    SKILL.md invokes subagents via 'Use the `mdhv-X` subagent.' lines (and
    documents the plugin-namespaced `mdhumanviewer:mdhv-X` alias). We extract
    every mdhv-* token from SKILL.md, strip any `mdhumanviewer:` prefix, and
    require each to be a declared agent name. This catches off-by-one and
    namespacing typos.

    NB: subagent names (`mdhv-renderer`, …) share the `mdhv-` prefix with the
    design-system CSS class vocabulary (`.mdhv-contract`, `.mdhv-keypoints`, …).
    The two are disambiguated by the leading dot: a class reference is always
    written `.mdhv-X`, an agent invocation never is — so the extraction skips any
    `mdhv-` token immediately preceded by a dot.
    """

    # mdhv-... tokens, optionally with the plugin namespace prefix; never a CSS
    # class reference (those are dot-prefixed: `.mdhv-contract`).
    _AGENT_TOKEN_RE = re.compile(r"(?<!\.)(?:mdhumanviewer:)?(mdhv-[a-z0-9-]+)")

    def test_invoked_agents_resolve_to_declared_names(self):
        text = _read(SKILL_PATH)
        declared = _agent_names()
        mentioned = set(self._AGENT_TOKEN_RE.findall(text))
        self.assertTrue(mentioned, "SKILL.md mentions no mdhv-* subagents at all")
        for name in mentioned:
            self.assertIn(
                name, declared,
                f"SKILL.md invokes subagent `{name}` but no agents/*.md "
                f"declares that name (declared: {sorted(declared)})",
            )

    def test_each_pipeline_agent_is_actually_invoked(self):
        """Sanity: the four pipeline agents are each referenced by SKILL.md."""
        text = _read(SKILL_PATH)
        mentioned = set(self._AGENT_TOKEN_RE.findall(text))
        for required in (
            "mdhv-design-author",
            "mdhv-renderer",
            "mdhv-verifier",
            "mdhv-crossfile",
        ):
            self.assertIn(
                required, mentioned,
                f"SKILL.md never invokes the `{required}` subagent",
            )


class TestStepKeysMatchManifest(unittest.TestCase):
    def test_init_session_seeds_expected_keys(self):
        seeded = _init_session_step_keys()
        self.assertIsNotNone(
            seeded, "could not locate the steps dict in init_session.py",
        )
        self.assertEqual(
            seeded, EXPECTED_STEP_KEYS,
            "init_session.py manifest step keys drifted from the manifest contract",
        )

    def test_skill_documents_exactly_those_step_keys(self):
        """Every seeded step key must appear in SKILL.md as a manifest key, and
        SKILL.md must not invent step keys outside that set."""
        text = _read(SKILL_PATH)
        seeded = _init_session_step_keys() or EXPECTED_STEP_KEYS

        # Each seeded key must be documented in SKILL.md.
        for key in seeded:
            self.assertIn(
                key, text,
                f"SKILL.md never mentions manifest step key `{key}`",
            )

        # Every steps.<key> token used in SKILL.md must be a seeded key (catches
        # a renamed/invented key in the orchestrator that the script never seeds).
        used = set(re.findall(r"steps\.([a-z_]+)", text))
        self.assertTrue(used, "SKILL.md uses no steps.<key> tokens")
        for key in used:
            self.assertIn(
                key, seeded,
                f"SKILL.md uses manifest step key `{key}` that "
                f"init_session.py never seeds (seeded: {sorted(seeded)})",
            )


class TestDeterministicHelperWiring(unittest.TestCase):
    """Pin the Phase-1 deterministic glue: the orchestrator must drive S1-S4 with
    the new helper scripts (no hand-rolled python), and SKILL.md must say so.

    These lints lock the B1 rewrite so the orchestrator cannot silently regress to
    hand-building selection.json, extracting slices, or editing manifest.json.
    """

    def test_helper_scripts_exist_on_disk(self):
        for script in EXPECTED_HELPER_SCRIPTS:
            self.assertTrue(
                os.path.isfile(os.path.join(SCRIPTS_DIR, script)),
                f"scripts/{script} does not exist",
            )

    def test_skill_uses_init_session_list_and_select_modes(self):
        """S1 must drive the menu + scoped-session creation via init_session.py's
        new --list and --select modes (not by hand-building selection.json)."""
        text = _read(SKILL_PATH)
        self.assertIn(
            "--list", text,
            "SKILL.md S1 must show the menu via init_session.py --list",
        )
        self.assertIn(
            "--select", text,
            "SKILL.md S1 must create the scoped session via init_session.py "
            "--select (no hand-built selection.json)",
        )

    def test_skill_references_check_session_and_mark_step(self):
        """S2/S2b/S3 validate via check_session.py; steps are marked via
        mark_step.py — both must be referenced, with the dollar-brace form."""
        text = _read(SKILL_PATH)
        for script in EXPECTED_HELPER_SCRIPTS:
            self.assertIn(
                script, text,
                f"SKILL.md never references scripts/{script}",
            )
            self.assertIn(
                f"${{CLAUDE_PLUGIN_ROOT}}/scripts/{script}", text,
                f"SKILL.md must invoke scripts/{script} via "
                "${CLAUDE_PLUGIN_ROOT}/scripts/ (dollar-brace Bash form)",
            )

    def test_skill_mentions_slices_structure_slice_path(self):
        """The renderer is pointed at the slice file, not an inline-extracted
        slice — SKILL.md must mention slices/ and STRUCTURE_SLICE_PATH."""
        text = _read(SKILL_PATH)
        self.assertIn(
            "slices/", text,
            "SKILL.md must reference the per-file slices/ directory",
        )
        self.assertIn(
            "STRUCTURE_SLICE_PATH", text,
            "SKILL.md must pass the renderer STRUCTURE_SLICE_PATH "
            "(a slices/<slug>.json file path, not an inline-extracted slice)",
        )

    def test_skill_forbids_hand_built_glue(self):
        """The HARD RULE: never run python3 -c, never build selection.json or
        slices by hand, never manually edit manifest.json — the scripts do it."""
        low = _read(SKILL_PATH).lower()
        # No ad-hoc inline python.
        self.assertTrue(
            "python3 -c" in low or "heredoc" in low or "ad-hoc" in low,
            "SKILL.md must forbid python3 -c / heredocs / ad-hoc python glue",
        )
        # No hand-built selection / slices.
        self.assertIn(
            "selection.json", low,
            "SKILL.md must name selection.json in the no-hand-build prohibition",
        )
        self.assertIn(
            "by hand", low,
            "SKILL.md must forbid building selection/slices/manifest by hand",
        )
        # No manual manifest edits — mark_step.py is the only writer.
        self.assertIn(
            "manifest.json", low,
            "SKILL.md must name manifest.json in the no-hand-edit prohibition",
        )
        self.assertTrue(
            "hand-edit" in low or "by hand" in low,
            "SKILL.md must forbid hand-editing manifest.json",
        )


class TestReconcileWiring(unittest.TestCase):
    """Pin the Phase-2 deterministic reconcile (S2.5) into the orchestrator prose.

    `reconcile.py` runs after verify (S2b) and before crossfile (S3)/assemble
    (S4); it closes the coverage + lossless-contract gate properties so assemble
    passes on the FIRST attempt. SKILL.md must reference it via the
    ${CLAUDE_PLUGIN_ROOT}/scripts/ dollar-brace form, the script must exist on
    disk, and the reconcile step must be positioned before the cross-file and
    assemble steps in the document.
    """

    def test_reconcile_script_exists_on_disk(self):
        self.assertTrue(
            os.path.isfile(os.path.join(SCRIPTS_DIR, "reconcile.py")),
            "scripts/reconcile.py does not exist",
        )

    def test_skill_references_reconcile_dollar_brace_form(self):
        """SKILL.md must invoke reconcile.py via the dollar-brace Bash form, with
        the --session ${SESSION} argument the script expects."""
        text = _read(SKILL_PATH)
        self.assertIn(
            "reconcile.py", text,
            "SKILL.md never references scripts/reconcile.py",
        )
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py", text,
            "SKILL.md must invoke scripts/reconcile.py via "
            "${CLAUDE_PLUGIN_ROOT}/scripts/ (dollar-brace Bash form)",
        )
        self.assertIn(
            "--session", text,
            "SKILL.md must pass --session ${SESSION} to reconcile.py",
        )

    def test_reconcile_step_precedes_assemble_step(self):
        """The reconcile step must be positioned BEFORE the assemble step in the
        document (S2.5 runs before S4), so the orchestrator reconciles the gate
        properties before assembling.

        We anchor on the step section headers (not the first incidental mention
        of a script name, which appears earlier in the Requirements list), and on
        the reconcile/assemble script *invocation* (the dollar-brace command), to
        pin the actual ordering of the two stages."""
        text = _read(SKILL_PATH)

        # The reconcile section header must precede the assemble section header.
        reconcile_hdr = text.find("## S2.5 — Reconcile")
        assemble_hdr = text.find("## S4 — Assemble")
        self.assertNotEqual(reconcile_hdr, -1, "SKILL.md has no '## S2.5 — Reconcile' section")
        self.assertNotEqual(assemble_hdr, -1, "SKILL.md has no '## S4 — Assemble' section")
        self.assertLess(
            reconcile_hdr, assemble_hdr,
            "SKILL.md must position the reconcile step (S2.5) before the assemble "
            "step (S4)",
        )

        # The reconcile command invocation must precede the assemble command.
        reconcile_cmd = text.find("${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py")
        assemble_cmd = text.find("${CLAUDE_PLUGIN_ROOT}/scripts/assemble.py")
        self.assertNotEqual(reconcile_cmd, -1, "SKILL.md never invokes reconcile.py")
        self.assertNotEqual(assemble_cmd, -1, "SKILL.md never invokes assemble.py")
        self.assertLess(
            reconcile_cmd, assemble_cmd,
            "SKILL.md must invoke reconcile.py before assemble.py (S2.5 before S4)",
        )

    def test_reconcile_section_precedes_crossfile_and_assemble_sections(self):
        """Pin the detailed-section reorder (S2.5 runs after S2b Verify and before
        S3 Cross-file): the Reconcile section header must precede the Cross-file
        section header, which must precede the Assemble section header. This
        guards the documented order from regressing back to placing the detailed
        Reconcile section after Cross-file."""
        text = _read(SKILL_PATH)

        reconcile_hdr = text.find("## S2.5 — Reconcile")
        crossfile_hdr = text.find("## S3 — Cross-file")
        assemble_hdr = text.find("## S4 — Assemble")
        self.assertNotEqual(reconcile_hdr, -1, "SKILL.md has no '## S2.5 — Reconcile' section")
        self.assertNotEqual(crossfile_hdr, -1, "SKILL.md has no '## S3 — Cross-file' section")
        self.assertNotEqual(assemble_hdr, -1, "SKILL.md has no '## S4 — Assemble' section")
        self.assertLess(
            reconcile_hdr, crossfile_hdr,
            "SKILL.md must position the reconcile section (S2.5) before the "
            "cross-file section (S3)",
        )
        self.assertLess(
            crossfile_hdr, assemble_hdr,
            "SKILL.md must position the cross-file section (S3) before the "
            "assemble section (S4)",
        )

    def test_skill_states_reconcile_runs_before_assemble_and_after_verify(self):
        """SKILL.md prose must place reconcile after verify and before assemble,
        and explain it makes assemble pass on the first attempt."""
        low = _read(SKILL_PATH).lower()
        self.assertIn(
            "genuine_gaps", low,
            "SKILL.md must name reconcile's genuine_gaps escalation token",
        )
        self.assertTrue(
            "first attempt" in low or "first try" in low,
            "SKILL.md must state reconcile makes assemble pass on the first "
            "attempt",
        )


class TestCoverageGuaranteeWiring(unittest.TestCase):
    """Pin the deterministic per-file heading-coverage guarantee into the prose.

    The renderer must promise it; the verifier must check it deterministically;
    and SKILL.md must wire the targeted-repair loop (re-invoke on coverage_gaps).
    These are kept robust to surrounding wording by matching on the exact
    headings/tokens the contract single-sources, not on full sentences.
    """

    def test_renderer_declares_coverage_guarantee_heading(self):
        text = _read(RENDERER_PATH)
        self.assertIn(
            "## Coverage guarantee", text,
            "agents/mdhv-renderer.md must carry the exact heading "
            "'## Coverage guarantee'",
        )

    def test_verifier_declares_deterministic_coverage_check_heading(self):
        text = _read(VERIFIER_PATH)
        self.assertIn(
            "## Coverage check (deterministic)", text,
            "agents/mdhv-verifier.md must carry the exact heading "
            "'## Coverage check (deterministic)'",
        )

    def test_skill_wires_coverage_gap_targeted_repair(self):
        text = _read(SKILL_PATH)
        self.assertIn(
            "coverage_gaps", text,
            "SKILL.md must name the literal `coverage_gaps` report token",
        )
        self.assertIn(
            "re-invoke", text,
            "SKILL.md must document the targeted-repair guidance "
            "(re-invoke on a coverage gap)",
        )

    def test_skill_scopes_structure_via_init_session(self):
        """S1 must hand the full structure to init_session.py via --structure so
        the session structure.json is scoped to the selection — the deterministic
        step that stops assemble's coverage gate from failing on unselected files
        (the real-run bug where the orchestrator hand-edited structure.json)."""
        text = _read(SKILL_PATH)
        self.assertIn("--structure", text,
                      "SKILL.md S1 must pass --structure to init_session.py")
        self.assertIn("scoped", text.lower(),
                      "SKILL.md must explain the session structure.json is scoped")

    def test_verifier_declares_deterministic_contract_check_heading(self):
        text = _read(VERIFIER_PATH)
        self.assertIn(
            "## Contract check (deterministic)", text,
            "agents/mdhv-verifier.md must carry the exact heading "
            "'## Contract check (deterministic)' so contract_violations self-heal",
        )

    def test_skill_forbids_hand_debugging_the_pipeline(self):
        """The orchestrator must never debug/patch the pipeline by hand on a gate
        failure (no inspecting the scripts, no ad-hoc python, no editing
        artifacts) — only a bounded targeted re-invoke, then stop+report. This
        guards against the real-run behavior where the orchestrator grepped
        assemble.py and rewrote analysis JSON to force a gate to pass."""
        low = _read(SKILL_PATH).lower()
        # Names the scripts it must not inspect.
        self.assertIn("assemble.py", low)
        self.assertIn("constants.py", low)
        # Forbids ad-hoc python and hand-editing artifacts.
        self.assertTrue(
            "python3 -c" in low or "heredoc" in low or "ad-hoc python" in low,
            "SKILL.md must forbid ad-hoc python to inspect/patch artifacts",
        )
        self.assertIn("hand-edit", low,
                      "SKILL.md must forbid hand-editing analysis/fragments artifacts")
        # Bounded retries then stop-and-report.
        self.assertIn("stop and report", low,
                      "SKILL.md must require stop-and-report after bounded retries")


if __name__ == "__main__":
    unittest.main()
