"""Tests for scripts/reconcile.py — deterministic gate-2/gate-3 reconcile (S3.5).

Builds a temp session by hand (scoped structure.json + per-file fragment +
analysis sidecar + the real source .md on disk) and exercises reconcile.py
end-to-end via subprocess. Every test then asserts the post-reconcile session
ASSEMBLES clean (assemble.py exits 0 with EMPTY coverage_gaps and
contract_violations), proving the reconciler closes exactly what the gate checks.

Run ONLY this module from the plugin root:
    python3 -m unittest tests.test_reconcile -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECONCILE = os.path.join(REPO_ROOT, "scripts", "reconcile.py")
ASSEMBLE = os.path.join(REPO_ROOT, "scripts", "assemble.py")
DESIGN = os.path.join(REPO_ROOT, "references", "design-system.html")


# A contract whose normalized words are present in the source. We feed the
# source bytes verbatim so contract_present(needle, src_norm) is True.
NEGATION_CONTRACT = "If the token is empty the request MUST NOT be retried."


def run_reconcile(session_dir):
    return subprocess.run(
        [sys.executable, RECONCILE, "--session", session_dir],
        capture_output=True, text=True,
    )


def run_assemble(session_dir):
    return subprocess.run(
        [
            sys.executable, ASSEMBLE,
            "--structure", os.path.join(session_dir, "structure.json"),
            "--analysis-dir", os.path.join(session_dir, "analysis"),
            "--fragments-dir", os.path.join(session_dir, "fragments"),
            "--findings", os.path.join(session_dir, "findings.json"),
            "--design", DESIGN,
            "--out", os.path.join(session_dir, "overview.html"),
        ],
        capture_output=True, text=True,
    )


def build_session(base, *, files, source_text, analyses, fragments,
                  findings=None):
    """Create a session dir under `base`.

    `files`: list of {slug, file_path(rel), headings:[{level,text,anchor}]}.
    `source_text`: {rel_path -> raw .md bytes written to disk under base}.
    `analyses`: {slug -> analysis dict}.
    `fragments`: {slug -> fragment HTML string}.
    Returns the session dir path. file_path in structure is made ABSOLUTE so
    gate-3/reconcile read the right source bytes regardless of cwd.
    """
    session_dir = os.path.join(base, "session")
    os.makedirs(os.path.join(session_dir, "analysis"))
    os.makedirs(os.path.join(session_dir, "fragments"))

    # Write source .md files on disk (under base, not the session dir).
    for rel, text in (source_text or {}).items():
        abspath = os.path.join(base, rel)
        os.makedirs(os.path.dirname(abspath), exist_ok=True)
        with open(abspath, "w", encoding="utf-8") as f:
            f.write(text)

    # Make file_path absolute in the scoped structure.
    scoped_files = []
    for f in files:
        fc = dict(f)
        fc["file_path"] = os.path.join(base, f["file_path"])
        scoped_files.append(fc)

    structure = {
        "meta": {"root": base, "output_language": "source",
                 "file_count": len(scoped_files)},
        "files": scoped_files,
        "graph": {"nodes": [], "edges": []},
    }
    with open(os.path.join(session_dir, "structure.json"), "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)

    for slug, analysis in analyses.items():
        with open(os.path.join(session_dir, "analysis", slug + ".json"),
                  "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)

    for slug, frag in fragments.items():
        with open(os.path.join(session_dir, "fragments", slug + ".html"),
                  "w", encoding="utf-8") as f:
            f.write(frag)

    fpath = os.path.join(session_dir, "findings.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(findings or {"cross_file_findings": []}, f)

    return session_dir


def read_fragment(session_dir, slug):
    with open(os.path.join(session_dir, "fragments", slug + ".html"),
              "r", encoding="utf-8") as f:
        return f.read()


def read_analysis(session_dir, slug):
    with open(os.path.join(session_dir, "analysis", slug + ".json"),
              "r", encoding="utf-8") as f:
        return json.load(f)


class ReconcileCoverageTests(unittest.TestCase):
    def test_coverage_id_auto_closed_via_source_headings_join(self):
        """A source heading id missing from the fragment but represented by an
        analysis section (via source_headings) is auto-closed with a hidden
        anchor span; gate 2's emitted set then covers it."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [
                        {"level": 1, "text": "Doc", "anchor": "doc"},
                        {"level": 2, "text": "Usage", "anchor": "usage"},
                    ],
                }],
                source_text={"doc.md": "# Doc\n\n## Usage\n\nText.\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc", "kind": "summarized",
                         "source_headings": ["doc"]},
                        {"id": "doc--usage", "title": "Usage", "kind": "summarized",
                         "source_headings": ["usage"]},
                    ],
                    "contracts": [],
                }},
                # Fragment emits doc--doc but NOT doc--usage (id bookkeeping miss).
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc"><h2>Usage</h2>'
                    '<p>Text.</p></div>'
                )},
            )
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertIn("doc--usage", report["auto_closed"]["coverage"])
            self.assertEqual(report["genuine_gaps"]["coverage"], [])

            frag = read_fragment(sd, "doc")
            self.assertIn('data-src-heading="doc--usage"', frag)
            self.assertIn("aria-hidden", frag)

            # Gate 2 now passes through assemble.
            asm = run_assemble(sd)
            self.assertEqual(asm.returncode, 0, asm.stderr)
            rep = json.loads(asm.stdout)
            self.assertEqual(rep["coverage_gaps"], [])
            self.assertEqual(rep["contract_violations"], [])

    def test_translation_run_uses_join_not_text_zero_false_gaps(self):
        """A TRANSLATION-style fragment: the rendered heading TEXT is translated
        (differs from the source anchor), but reconcile drives coverage from the
        source_headings JOIN, not text — so it closes with ZERO false gaps."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [
                        {"level": 1, "text": "Introduction", "anchor": "introduction"},
                        {"level": 2, "text": "When to use", "anchor": "when-to-use"},
                    ],
                }],
                source_text={"doc.md": "# Introduction\n\n## When to use\n\nUse it.\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--introduction", "title": "Введение",
                         "kind": "summarized", "source_headings": ["introduction"]},
                        {"id": "doc--when-to-use", "title": "Когда использовать",
                         "kind": "summarized", "source_headings": ["when-to-use"]},
                    ],
                    "contracts": [],
                }},
                # Fragment renders TRANSLATED headings; emits NEITHER source id.
                fragments={"doc": (
                    '<div class="mdhv-section"><h1>Введение</h1></div>'
                    '<div class="mdhv-section"><h2>Когда использовать</h2>'
                    '<p>Используйте это.</p></div>'
                )},
            )
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            # Both ids closed via the join despite translated heading text.
            self.assertEqual(sorted(report["auto_closed"]["coverage"]),
                             ["doc--introduction", "doc--when-to-use"])
            # ZERO false coverage gaps (this is the review #1 lock).
            self.assertEqual(report["genuine_gaps"]["coverage"], [])

            asm = run_assemble(sd)
            self.assertEqual(asm.returncode, 0, asm.stderr)
            rep = json.loads(asm.stdout)
            self.assertEqual(rep["coverage_gaps"], [])

    def test_genuine_coverage_gap_reported_not_fabricated(self):
        """A source heading that NO analysis section claims is a genuine gap:
        reported under genuine_gaps.coverage, NEVER fabricated into the
        fragment."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [
                        {"level": 1, "text": "Doc", "anchor": "doc"},
                        {"level": 2, "text": "Lost", "anchor": "lost"},
                    ],
                }],
                source_text={"doc.md": "# Doc\n\n## Lost\n\nLost content.\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc", "kind": "summarized",
                         "source_headings": ["doc"]},
                        # No section claims the "lost" anchor.
                    ],
                    "contracts": [],
                }},
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc">Doc</div>'
                )},
            )
            before = read_fragment(sd, "doc")
            proc = run_reconcile(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertIn("doc--lost", report["genuine_gaps"]["coverage"])
            self.assertEqual(report["auto_closed"]["coverage"], [])
            # NOT fabricated: doc--lost must not appear in the fragment.
            after = read_fragment(sd, "doc")
            self.assertNotIn("doc--lost", after)
            # The fragment is unchanged (no coverage edit, no contract edit).
            self.assertEqual(before, after)


class ReconcileContractTests(unittest.TestCase):
    def test_source_faithful_contract_surfaced_in_fragment(self):
        """A contract present in source but absent from the callout gets a
        fragment-side .mdhv-contract appended (auto_closed), text unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [{"level": 1, "text": "Doc", "anchor": "doc"}],
                }],
                source_text={"doc.md": "# Doc\n\n" + NEGATION_CONTRACT + "\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc",
                         "kind": "verbatim_critical", "source_headings": ["doc"]},
                    ],
                    "contracts": [
                        {"text": NEGATION_CONTRACT, "source_anchor": "doc--doc"},
                    ],
                }},
                # Fragment covers the id but has NO .mdhv-contract callout.
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc">Doc</div>'
                )},
            )
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["genuine_gaps"]["contracts"], [])
            self.assertEqual(len(report["auto_closed"]["contracts"]), 1)

            frag = read_fragment(sd, "doc")
            self.assertIn('class="mdhv-contract"', frag)
            # Text unchanged (modulo HTML-escaping of nothing here): the
            # negation phrase appears verbatim.
            self.assertIn("MUST NOT be retried", frag)

            asm = run_assemble(sd)
            self.assertEqual(asm.returncode, 0, asm.stderr)
            rep = json.loads(asm.stdout)
            self.assertEqual(rep["contract_violations"], [])

    def test_contract_not_in_source_reported_never_snapped_or_dropped(self):
        """A contract NOT present in the source is a genuine gap: reported,
        never snapped to a high-overlap span, never dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [{"level": 1, "text": "Doc", "anchor": "doc"}],
                }],
                # Source does NOT contain the contract text at all.
                source_text={"doc.md": "# Doc\n\nUnrelated prose only.\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc",
                         "kind": "verbatim_critical", "source_headings": ["doc"]},
                    ],
                    "contracts": [
                        {"text": "The server MUST reject empty tokens with 401.",
                         "source_anchor": "doc--doc"},
                    ],
                }},
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc">Doc</div>'
                )},
            )
            before_frag = read_fragment(sd, "doc")
            before_analysis = read_analysis(sd, "doc")
            proc = run_reconcile(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertEqual(len(report["genuine_gaps"]["contracts"]), 1)
            self.assertEqual(report["auto_closed"]["contracts"], [])
            # Never snapped (no callout added) and never dropped (still in
            # analysis with identical text).
            after_frag = read_fragment(sd, "doc")
            after_analysis = read_analysis(sd, "doc")
            self.assertEqual(before_frag, after_frag)
            self.assertEqual(before_analysis["contracts"],
                             after_analysis["contracts"])

    def test_negation_contract_never_rewritten(self):
        """A negation-containing contract present in source is surfaced verbatim
        — the negation words are NEVER rewritten to a non-negated span."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [{"level": 1, "text": "Doc", "anchor": "doc"}],
                }],
                source_text={"doc.md": "# Doc\n\n" + NEGATION_CONTRACT + "\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc",
                         "kind": "verbatim_critical", "source_headings": ["doc"]},
                    ],
                    "contracts": [
                        {"text": NEGATION_CONTRACT, "source_anchor": "doc--doc"},
                    ],
                }},
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc">Doc</div>'
                )},
            )
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            frag = read_fragment(sd, "doc")
            # The literal negation token survives, in order, intact.
            self.assertIn("MUST NOT", frag)
            self.assertNotIn("MUST be retried", frag.replace("MUST NOT be retried", ""))
            # Analysis contract text is byte-for-byte unchanged.
            analysis = read_analysis(sd, "doc")
            self.assertEqual(analysis["contracts"][0]["text"], NEGATION_CONTRACT)

    def test_exact_duplicate_contract_removed(self):
        """An exact-duplicate contracts[] entry (same normalized text) is the
        ONLY deterministic removal allowed — the duplicate is dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [{"level": 1, "text": "Doc", "anchor": "doc"}],
                }],
                source_text={"doc.md": "# Doc\n\n" + NEGATION_CONTRACT + "\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc",
                         "kind": "verbatim_critical", "source_headings": ["doc"]},
                    ],
                    "contracts": [
                        {"text": NEGATION_CONTRACT, "source_anchor": "doc--doc"},
                        # Same normalized text (only punctuation/case differs).
                        {"text": "if the token is empty the request must not be retried",
                         "source_anchor": "doc--doc"},
                    ],
                }},
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc">Doc</div>'
                    '<div class="mdhv-contract">' + NEGATION_CONTRACT + '</div>'
                )},
            )
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["genuine_gaps"]["contracts"], [])
            # The duplicate removal is recorded under auto_closed.
            self.assertTrue(any("duplicate" in m
                                for m in report["auto_closed"]["contracts"]))
            analysis = read_analysis(sd, "doc")
            self.assertEqual(len(analysis["contracts"]), 1)

            asm = run_assemble(sd)
            self.assertEqual(asm.returncode, 0, asm.stderr)


class ReconcileIdempotencyTests(unittest.TestCase):
    def test_clean_session_idempotent_no_file_changes(self):
        """A clean session (all ids emitted, contract already in callout) yields
        all-empty arrays AND leaves every file byte-identical."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [
                        {"level": 1, "text": "Doc", "anchor": "doc"},
                        {"level": 2, "text": "Usage", "anchor": "usage"},
                    ],
                }],
                source_text={"doc.md": "# Doc\n\n## Usage\n\n" + NEGATION_CONTRACT + "\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc", "kind": "summarized",
                         "source_headings": ["doc"]},
                        {"id": "doc--usage", "title": "Usage",
                         "kind": "verbatim_critical", "source_headings": ["usage"]},
                    ],
                    "contracts": [
                        {"text": NEGATION_CONTRACT, "source_anchor": "doc--usage"},
                    ],
                }},
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc">Doc</div>'
                    '<div class="mdhv-section" id="doc--usage" '
                    'data-src-heading="doc--usage">Usage'
                    '<div class="mdhv-contract">' + NEGATION_CONTRACT + '</div>'
                    '</div>'
                )},
            )
            before_frag = read_fragment(sd, "doc")
            before_analysis = read_analysis(sd, "doc")

            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["auto_closed"]["coverage"], [])
            self.assertEqual(report["auto_closed"]["contracts"], [])
            self.assertEqual(report["genuine_gaps"]["coverage"], [])
            self.assertEqual(report["genuine_gaps"]["contracts"], [])

            # No file changes.
            self.assertEqual(before_frag, read_fragment(sd, "doc"))
            self.assertEqual(before_analysis, read_analysis(sd, "doc"))

            # And it assembles clean.
            asm = run_assemble(sd)
            self.assertEqual(asm.returncode, 0, asm.stderr)
            rep = json.loads(asm.stdout)
            self.assertEqual(rep["coverage_gaps"], [])
            self.assertEqual(rep["contract_violations"], [])

    def test_reconcile_then_reconcile_is_stable(self):
        """Running reconcile twice: the second run is a clean no-op (auto_closed
        empty), proving the first run's edits are themselves gate-clean."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(
                tmp,
                files=[{
                    "slug": "doc", "file_path": "doc.md",
                    "headings": [
                        {"level": 1, "text": "Doc", "anchor": "doc"},
                        {"level": 2, "text": "Usage", "anchor": "usage"},
                    ],
                }],
                source_text={"doc.md": "# Doc\n\n## Usage\n\n" + NEGATION_CONTRACT + "\n"},
                analyses={"doc": {
                    "slug": "doc", "tldr": "x",
                    "sections": [
                        {"id": "doc--doc", "title": "Doc", "kind": "summarized",
                         "source_headings": ["doc"]},
                        {"id": "doc--usage", "title": "Usage",
                         "kind": "verbatim_critical", "source_headings": ["usage"]},
                    ],
                    "contracts": [
                        {"text": NEGATION_CONTRACT, "source_anchor": "doc--usage"},
                    ],
                }},
                fragments={"doc": (
                    '<div class="mdhv-section" id="doc--doc" '
                    'data-src-heading="doc--doc">Doc</div>'
                    '<div class="mdhv-section" id="doc--usage" '
                    'data-src-heading="doc--usage">Usage</div>'
                )},
            )
            first = run_reconcile(sd)
            self.assertEqual(first.returncode, 0, first.stderr)
            r1 = json.loads(first.stdout)
            # First run closes the missing contract callout.
            self.assertEqual(len(r1["auto_closed"]["contracts"]), 1)

            after_first = read_fragment(sd, "doc")
            second = run_reconcile(sd)
            self.assertEqual(second.returncode, 0, second.stderr)
            r2 = json.loads(second.stdout)
            self.assertEqual(r2["auto_closed"]["coverage"], [])
            self.assertEqual(r2["auto_closed"]["contracts"], [])
            self.assertEqual(r2["genuine_gaps"]["coverage"], [])
            self.assertEqual(r2["genuine_gaps"]["contracts"], [])
            # Second run made no edits.
            self.assertEqual(after_first, read_fragment(sd, "doc"))


class ReconcileAnchorParityTests(unittest.TestCase):
    """F2 — reconcile gate-1 PARITY (detect-only). Reconcile mirrors assemble's
    HTML-aware gate-1 over REAL <a href="#..."> elements: it escalates a genuinely
    dangling in-page anchor under genuine_gaps.anchors WITHOUT mutating the
    fragment, while documented href examples inside <code> never escalate."""

    # A coverage- and contract-clean single-file session is the isolation base
    # for every anchor test below; we vary ONLY the fragment's anchor content.
    _FILES = [{
        "slug": "doc", "file_path": "doc.md",
        "headings": [
            {"level": 1, "text": "Doc", "anchor": "doc"},
            {"level": 2, "text": "Usage", "anchor": "usage"},
        ],
    }]
    _SOURCE = {"doc.md": "# Doc\n\n## Usage\n\nText.\n"}
    _ANALYSES = {"doc": {
        "slug": "doc", "tldr": "x",
        "sections": [
            {"id": "doc--doc", "title": "Doc", "kind": "summarized",
             "source_headings": ["doc"]},
            {"id": "doc--usage", "title": "Usage", "kind": "summarized",
             "source_headings": ["usage"]},
        ],
        "contracts": [],
    }}

    def _clean_section_html(self):
        """Both source ids emitted (coverage clean), no contracts (gate-3 clean).
        Tests append their own anchor content to isolate the gate-1 signal."""
        return (
            '<div class="mdhv-section" id="doc--doc" '
            'data-src-heading="doc--doc"><h1>Doc</h1></div>'
            '<div class="mdhv-section" id="doc--usage" '
            'data-src-heading="doc--usage"><h2>Usage</h2><p>Text.</p>'
        )

    def _build(self, tmp, fragment_body):
        return build_session(
            tmp,
            files=self._FILES,
            source_text=self._SOURCE,
            analyses=self._ANALYSES,
            fragments={"doc": fragment_body + "</div>"},
        )

    def test_report_has_anchors_key_under_both_buckets(self):
        """The reconcile JSON report exposes 'anchors' under BOTH auto_closed and
        genuine_gaps (the new report shape)."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._build(tmp, self._clean_section_html())
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertIn("anchors", report["auto_closed"])
            self.assertIn("anchors", report["genuine_gaps"])

    def test_real_dangling_anchor_is_genuine_gap_and_exits_nonzero(self):
        """A REAL <a href="#nope"> (#nope is neither a heading id nor a chrome
        anchor) is recorded under genuine_gaps.anchors AND drives a non-zero exit.
        Coverage + contracts are clean, isolating the anchor gap."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._build(
                tmp,
                self._clean_section_html()
                + '<p>See <a href="#nope">this</a>.</p>',
            )
            proc = run_reconcile(sd)
            # The anchor gap alone must flip the exit code.
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            # Isolation: coverage + contracts stayed clean.
            self.assertEqual(report["genuine_gaps"]["coverage"], [])
            self.assertEqual(report["genuine_gaps"]["contracts"], [])
            # The dangling anchor is escalated under genuine_gaps.anchors.
            self.assertEqual(len(report["genuine_gaps"]["anchors"]), 1)
            self.assertIn("#nope", report["genuine_gaps"]["anchors"][0])

    def test_real_dangling_anchor_is_detect_only_fragment_unchanged(self):
        """DETECT-ONLY: after escalating the dangling anchor, reconcile must NOT
        unwrap/mutate the fragment — the <a href="#nope"> survives byte-for-byte."""
        with tempfile.TemporaryDirectory() as tmp:
            body = (
                self._clean_section_html()
                + '<p>See <a href="#nope">this</a>.</p>'
            )
            sd = self._build(tmp, body)
            before = read_fragment(sd, "doc")
            proc = run_reconcile(sd)
            self.assertNotEqual(proc.returncode, 0)
            after = read_fragment(sd, "doc")
            # Fragment untouched: still carries the real anchor verbatim.
            self.assertEqual(before, after)
            self.assertIn('<a href="#nope">', after)

    def test_code_documented_href_is_not_escalated(self):
        """A documented href inside <code> (NOT a real link) is character data,
        not an <a> element — so genuine_gaps.anchors stays empty (no false
        escalation). This is the self-referential-doc regression."""
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._build(
                tmp,
                self._clean_section_html()
                + '<p>Write <code>href="#nope"</code> in the link.</p>',
            )
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["genuine_gaps"]["anchors"], [])

    def test_clean_anchors_parity_with_assemble(self):
        """PARITY: a session where every in-page <a href> resolves to a real id
        yields genuine_gaps.anchors == [] AND, on the SAME fragments,
        assemble.run_gates(...)['dangling_anchors'] == [] — reconcile's verdict
        matches assemble's."""
        with tempfile.TemporaryDirectory() as tmp:
            # Real anchors that resolve: a section id and an allowed chrome anchor.
            body = (
                self._clean_section_html()
                + '<p>Jump to <a href="#doc--usage">usage</a> or '
                '<a href="#mdhv-toc">contents</a>.</p>'
            )
            sd = self._build(tmp, body)
            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["genuine_gaps"]["anchors"], [])

            # Now run assemble.run_gates over the SAME on-disk structure +
            # fragments + analyses (in-process) and assert identical anchor verdict.
            if REPO_ROOT not in sys.path:
                sys.path.insert(0, REPO_ROOT)
            from scripts import assemble  # noqa: E402

            with open(os.path.join(sd, "structure.json"), encoding="utf-8") as fh:
                structure = json.load(fh)
            analyses = {"doc": read_analysis(sd, "doc")}
            fragments = {"doc": read_fragment(sd, "doc")}
            gates = assemble.run_gates(structure, analyses, fragments)
            self.assertEqual(gates["dangling_anchors"], [])

    def test_bare_hash_anchor_parity_with_assemble(self):
        """PARITY (tolerated href): a no-op `<a href="#">` is tolerated by BOTH
        sides. assemble.run_gates special-cases bare '#'; reconcile must too, or
        it would needlessly escalate it to a verifier/renderer re-invoke. Pins the
        blind spot where reconcile was stricter than assemble."""
        with tempfile.TemporaryDirectory() as tmp:
            body = (
                self._clean_section_html()
                + '<p><a href="#">back to top</a></p>'
            )
            sd = self._build(tmp, body)

            proc = run_reconcile(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["genuine_gaps"]["anchors"], [])

            if REPO_ROOT not in sys.path:
                sys.path.insert(0, REPO_ROOT)
            from scripts import assemble  # noqa: E402

            with open(os.path.join(sd, "structure.json"), encoding="utf-8") as fh:
                structure = json.load(fh)
            analyses = {"doc": read_analysis(sd, "doc")}
            fragments = {"doc": read_fragment(sd, "doc")}
            gates = assemble.run_gates(structure, analyses, fragments)
            self.assertEqual(gates["dangling_anchors"], [])


class ReconcileErrorTests(unittest.TestCase):
    def test_missing_structure_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_reconcile(tmp)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("structure.json", proc.stderr)


if __name__ == "__main__":
    unittest.main()
