"""Tests for scripts/assemble.py (Task 5: shell + zone assembly + fragment stitch).

These tests use a MINIMAL inline temp design fixture (the required mount markers
plus a few class hooks) so they do NOT depend on references/design-system.html,
which is authored by a sibling agent in parallel.

Run ONLY this module (siblings write files in parallel; a full discover would
import half-written modules):

    python3 -m unittest tests.test_assemble -v
"""
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "assemble.py")

# Minimal self-contained design fixture: the five paired mount markers plus a
# handful of class hooks so injection has somewhere to land.
DESIGN_FIXTURE = """<!DOCTYPE html>
<html><head><style>.mdhv-strap{} .mdhv-toc{} .mdhv-graph{} .mdhv-findings{}
.mdhv-file{} .mdhv-file-header{} .mdhv-src-link{} .mdhv-node{} .mdhv-edge{}
.mdhv-graph-caption{}</style></head>
<body>
<header><!-- MDHV:STRAP:START --><!-- MDHV:STRAP:END --></header>
<aside><!-- MDHV:TOC:START --><!-- MDHV:TOC:END --></aside>
<section id="mdhv-graph-zone"><!-- MDHV:GRAPH:START --><!-- MDHV:GRAPH:END --></section>
<section id="mdhv-findings-zone"><!-- MDHV:FINDINGS:START --><!-- MDHV:FINDINGS:END --></section>
<main><!-- MDHV:FILES:START --><!-- MDHV:FILES:END --></main>
</body></html>
"""


def make_node(slug, file_path=None, title=None):
    return {
        "slug": slug,
        "title": title or slug,
        "file_path": file_path if file_path is not None else f"{slug}.md",
    }


def make_file(slug, file_path=None, title=None, headings=None, token_estimate=100):
    return {
        "slug": slug,
        "file_path": file_path if file_path is not None else f"{slug}.md",
        "language": "en",
        "title": title or slug,
        "token_estimate": token_estimate,
        "token_estimate_method": "chars/4",
        "headings": headings if headings is not None else [
            {"level": 1, "text": title or slug, "anchor": "overview"},
        ],
    }


def make_structure(n_files, edges=None, root=".", language="source"):
    files = [make_file(f"f{i}", file_path=f"dir{i}/f{i}.md") for i in range(n_files)]
    nodes = [make_node(f"f{i}", file_path=f"dir{i}/f{i}.md") for i in range(n_files)]
    return {
        "meta": {
            "session": "2026-05-30_00-00",
            "generated_at": "2026-05-30T00:00:00Z",
            "root": root,
            "output_language": language,
            "file_count": n_files,
        },
        "files": files,
        "graph": {"nodes": nodes, "edges": edges or []},
    }


def _default_fragment(file_obj):
    """A coverage-clean fragment: one .mdhv-section per source heading carrying
    its globally-unique <slug>--<anchor> id in data-src-heading, so gate 2
    (coverage) passes for tests that only care about shell/zone behavior."""
    slug = file_obj["slug"]
    ids = [f"{slug}--{h.get('anchor', '')}" for h in (file_obj.get("headings") or [])]
    if not ids:
        ids = [f"{slug}--overview"]
    sections = "".join(
        f'<div class="mdhv-section" id="{sid}" data-src-heading="{sid}">frag {sid}</div>'
        for sid in ids
    )
    return f'<div class="mdhv-keypoints">essence {slug}</div>{sections}'


def strong(a, b, reason="direct relative link"):
    return {"from": a, "to": b, "strength": "strong", "reason": reason}


def weak(a, b, reason="name match"):
    return {"from": a, "to": b, "strength": "weak", "reason": reason}


class AssembleHarness(unittest.TestCase):
    """Sets up a temp session dir and runs assemble.py against it."""

    def _run(self, structure, findings=None, fragments=None, check=True,
             analyses=None, sources=None, design=None, findings_obj_missing=False):
        """Set up a temp session dir and run assemble.py.

        ``analyses`` maps slug -> analysis dict (written to analysis/<slug>.json).
        ``sources`` maps a source ``file_path`` (as it appears in structure) ->
        raw source bytes; written under the temp root so gate 3 can read them.
        ``design`` overrides the design template (defaults to DESIGN_FIXTURE).
        Set ``findings_obj_missing`` to write a findings file with NO
        cross_file_findings key (a {} object).

        Returns ``(proc, report, document)``. The report is parsed from stdout
        on BOTH success and failure (assemble prints the full report either
        way); ``document`` is the written page, or None when nothing was
        written (the failure path must never write a partial page).
        """
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: _rmtree(tmp))

        structure_path = os.path.join(tmp, "structure.json")
        with open(structure_path, "w", encoding="utf-8") as f:
            json.dump(structure, f)

        findings_path = os.path.join(tmp, "findings.json")
        with open(findings_path, "w", encoding="utf-8") as f:
            if findings_obj_missing:
                json.dump({}, f)
            else:
                json.dump(findings if findings is not None else {"cross_file_findings": []}, f)

        analysis_dir = os.path.join(tmp, "analysis")
        os.makedirs(analysis_dir, exist_ok=True)
        analyses = analyses or {}
        for slug, obj in analyses.items():
            with open(os.path.join(analysis_dir, f"{slug}.json"), "w", encoding="utf-8") as fh:
                json.dump(obj, fh)

        # Write source .md files (for gate 3) at paths relative to the temp root,
        # then run assemble with cwd=tmp so file_path joins resolve.
        sources = sources or {}
        for rel_path, content in sources.items():
            abs_path = os.path.join(tmp, rel_path)
            os.makedirs(os.path.dirname(abs_path) or tmp, exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(content)

        fragments_dir = os.path.join(tmp, "fragments")
        os.makedirs(fragments_dir, exist_ok=True)
        fragments = fragments or {}
        for f_obj in structure.get("files", []):
            slug = f_obj["slug"]
            # Default fragment covers EVERY source heading id so gate 2
            # (coverage) passes for tests that do not care about fragment body.
            content = fragments.get(slug, _default_fragment(f_obj))
            with open(os.path.join(fragments_dir, f"{slug}.html"), "w", encoding="utf-8") as fh:
                fh.write(content)

        design_path = os.path.join(tmp, "design.html")
        with open(design_path, "w", encoding="utf-8") as f:
            f.write(design if design is not None else DESIGN_FIXTURE)

        out_path = os.path.join(tmp, "overview.html")

        proc = subprocess.run(
            [sys.executable, SCRIPT,
             "--structure", structure_path,
             "--analysis-dir", analysis_dir,
             "--fragments-dir", fragments_dir,
             "--findings", findings_path,
             "--design", design_path,
             "--out", out_path],
            capture_output=True, text=True, cwd=tmp,
        )
        if check and proc.returncode != 0:
            raise AssertionError(f"assemble failed: {proc.stderr!r}")

        report = None
        if proc.stdout.strip():
            try:
                report = json.loads(proc.stdout)
            except ValueError:
                report = None
        document = None
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                document = f.read()
        return proc, report, document


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


class TocTests(AssembleHarness):
    def test_toc_lists_every_file(self):
        structure = make_structure(4)
        _, report, document = self._run(structure)
        self.assertEqual(report["files"], 4)
        # one toc-item per file
        self.assertEqual(document.count('class="mdhv-toc-item"'), 4)
        # each links to <slug>--<firstAnchor>
        for i in range(4):
            self.assertIn(f'href="#f{i}--overview"', document)

    def test_markers_remain_in_place(self):
        structure = make_structure(2)
        _, _, document = self._run(structure)
        for marker in ("STRAP", "TOC", "GRAPH", "FINDINGS", "FILES"):
            self.assertIn(f"<!-- MDHV:{marker}:START -->", document)
            self.assertIn(f"<!-- MDHV:{marker}:END -->", document)


class NavigationDepthTests(AssembleHarness):
    """Phase C — navigation depth: single #mdhv-top landmark, a per-file
    heading sub-nav whose every injected href resolves, and a back-to-top
    control. Driven against the REAL design system so the static landmark /
    back-to-top chrome are exercised alongside the injected TOC."""

    def _real_design(self):
        with open(REAL_DESIGN, encoding="utf-8") as fh:
            return fh.read()

    def _multi_heading_structure(self):
        """A 2-file structure whose files have several headings each, so the
        TOC sub-nav actually emits nested sub-links."""
        f0 = file_with_headings(
            "f0", "dir0/f0.md",
            [heading(1, "F0 Top", "overview"),
             heading(2, "F0 Two", "two"),
             heading(3, "F0 Three", "three")],
            title="File Zero",
        )
        f1 = file_with_headings(
            "f1", "dir1/f1.md",
            [heading(1, "F1 Top", "overview"),
             heading(2, "F1 Two", "two")],
            title="File One",
        )
        return {
            "meta": {"root": ".", "output_language": "source", "file_count": 2},
            "files": [f0, f1],
            "graph": {"nodes": [make_node("f0", "dir0/f0.md"),
                                make_node("f1", "dir1/f1.md")],
                      "edges": []},
        }

    def _fragments_for(self, structure):
        return {f["slug"]: _default_fragment(f) for f in structure["files"]}

    def test_exactly_one_mdhv_top_id(self):
        # The static <header id="mdhv-top"> is the single canonical landmark;
        # the injected strap div must NOT re-emit the id (was a duplicate).
        structure = self._multi_heading_structure()
        _, _, document = self._run(
            structure, fragments=self._fragments_for(structure),
            design=self._real_design())
        occurrences = re.findall(r'id\s*=\s*["\']mdhv-top["\']', document)
        self.assertEqual(
            len(occurrences), 1,
            "assembled document must contain EXACTLY ONE id=\"mdhv-top\"",
        )

    def test_strap_div_does_not_carry_mdhv_top_id(self):
        structure = self._multi_heading_structure()
        _, _, document = self._run(
            structure, fragments=self._fragments_for(structure),
            design=self._real_design())
        # the injected strap div keeps its class but not the id
        self.assertIn('class="mdhv-strap"', document)
        self.assertNotRegex(
            document, r'<div class="mdhv-strap"[^>]*\bid="mdhv-top"',
            "the injected strap div must not carry id=\"mdhv-top\"",
        )

    def test_toc_subnav_emitted_under_each_file(self):
        structure = self._multi_heading_structure()
        _, _, document = self._run(
            structure, fragments=self._fragments_for(structure),
            design=self._real_design())
        # a nested heading sub-list appears (collapsible per file)
        self.assertIn("mdhv-toc-sub", document)
        self.assertIn("mdhv-toc-sub-item", document)
        # the non-lead headings are linked as sub-items
        self.assertIn('href="#f0--two"', document)
        self.assertIn('href="#f0--three"', document)
        self.assertIn('href="#f1--two"', document)

    def test_every_injected_toc_href_is_a_member_of_id_space_or_chrome(self):
        # Pin the un-gated injected-chrome surface: every href the strap +
        # overview + toc emits must resolve to a structure heading id, an
        # emitted file-section id (the {slug}--file family — the SAME anchors
        # _node_anchor / render_files emit), or an allowed chrome anchor. Built
        # from the SAME structure objects, so this holds by construction — the
        # test guards against a regression that breaks it. (R1's overview hub
        # chips put #{slug}--file hrefs onto this surface; they are resolvable
        # un-gated chrome, NOT structure heading ids — gate 1 never scans this.)
        sys.path.insert(0, REPO_ROOT)
        from scripts.assemble import render_toc, render_strap, render_overview
        from scripts.gates import structure_heading_ids
        from scripts.constants import ALLOWED_CHROME_ANCHORS

        # A structure whose graph has hubs so render_overview emits #{slug}--file
        # hub chips into the scanned surface (the property R1 must keep valid).
        structure = make_structure(
            5,
            edges=[strong("f0", "f4"), strong("f1", "f4"),  # f4 = hub (>=2 in)
                   strong("f2", "f3"), strong("f0", "f3"),   # f3 = hub
                   weak("f1", "f2")],
        )
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f4"], "title": "C1", "description": "d"},
        ]}
        id_space = structure_heading_ids(structure)
        # The {slug}--file family: the real emitted file-section ids in Zone 4
        # (render_files emits id="{slug}--file"); _node_anchor targets these.
        file_section_ids = {f["slug"] + "--file" for f in structure["files"]}
        allowed = set(ALLOWED_CHROME_ANCHORS)

        injected = (
            render_strap(structure)
            + render_overview(structure, findings)
            + render_toc(structure)
        )
        hrefs = re.findall(r"""href\s*=\s*['"](#[^'"]*)['"]""", injected)
        self.assertTrue(hrefs, "the injected chrome must emit at least one href")
        # The overview really did put a #{slug}--file hub chip on the surface
        # (otherwise this test would no longer be guarding what it claims to).
        self.assertTrue(
            any(h[1:] in file_section_ids for h in hrefs),
            "expected render_overview to emit at least one #{slug}--file hub chip",
        )
        for href in hrefs:
            if href == "#":
                continue
            target = href[1:]
            self.assertTrue(
                href in allowed
                or target in id_space
                or target in file_section_ids,
                "injected chrome href %r resolves to no heading id, file-section "
                "id, or chrome anchor (id_space size=%d)" % (href, len(id_space)),
            )

    def test_injected_chrome_membership_still_rejects_a_dangling_href(self):
        # The membership check must NOT be weakened to a no-op: a truly dangling
        # #... href (not a heading id, not a {slug}--file id, not chrome) is
        # still rejected by the same resolution the test above performs.
        sys.path.insert(0, REPO_ROOT)
        from scripts.gates import structure_heading_ids
        from scripts.constants import ALLOWED_CHROME_ANCHORS

        structure = make_structure(3)
        id_space = structure_heading_ids(structure)
        file_section_ids = {f["slug"] + "--file" for f in structure["files"]}
        allowed = set(ALLOWED_CHROME_ANCHORS)

        def resolves(href):
            target = href[1:]
            return (href in allowed or target in id_space
                    or target in file_section_ids)

        self.assertFalse(resolves("#totally-bogus-anchor"))
        self.assertTrue(resolves("#f0--file"))
        self.assertTrue(resolves("#mdhv-findings"))

    def test_back_to_top_control_targets_mdhv_top(self):
        structure = self._multi_heading_structure()
        _, _, document = self._run(
            structure, fragments=self._fragments_for(structure),
            design=self._real_design())
        # a back-to-top control exists and points at the allowed #mdhv-top anchor
        self.assertIn("mdhv-totop", document)
        self.assertRegex(
            document,
            r'class="mdhv-totop"[^>]*href="#mdhv-top"'
            r'|href="#mdhv-top"[^>]*class="mdhv-totop"',
            "a back-to-top control with href=\"#mdhv-top\" must exist",
        )

    def test_in_n_findings_backlink_when_file_in_findings(self):
        structure = self._multi_heading_structure()
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f1"], "title": "C1", "description": "d"},
            {"type": "coverage", "severity": "low",
             "files": ["f0"], "title": "C2", "description": "d"},
        ]}
        _, _, document = self._run(
            structure, findings=findings,
            fragments=self._fragments_for(structure),
            design=self._real_design())
        # f0 appears in 2 findings, f1 in 1 — both get a backlink to #mdhv-findings
        self.assertEqual(document.count('class="mdhv-file-findings-link"'), 2)
        self.assertIn('class="mdhv-file-findings-link" href="#mdhv-findings"', document)
        self.assertIn("in 2 findings", document)
        self.assertIn("in 1 finding", document)

    def test_no_findings_backlink_when_file_absent_from_findings(self):
        # A file in no finding gets NO backlink (no spurious "in 0 findings").
        structure = self._multi_heading_structure()
        findings = {"cross_file_findings": [
            {"type": "coverage", "severity": "low",
             "files": ["f1"], "title": "only f1", "description": "d"},
        ]}
        _, _, document = self._run(
            structure, findings=findings,
            fragments=self._fragments_for(structure),
            design=self._real_design())
        self.assertNotIn("in 0 finding", document)
        # exactly one backlink (for f1), not two. Count the rendered anchor
        # markup, not the bare class name (which also appears in the CSS).
        self.assertEqual(document.count('class="mdhv-file-findings-link"'), 1)


class FindingsOrderTests(AssembleHarness):
    def test_findings_placed_before_files(self):
        structure = make_structure(3)
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f1"], "title": "Conflict", "description": "desc"},
        ]}
        _, _, document = self._run(structure, findings=findings)
        findings_pos = document.find('class="mdhv-findings"')
        files_pos = document.find('id="mdhv-files"')
        self.assertNotEqual(findings_pos, -1)
        self.assertNotEqual(files_pos, -1)
        self.assertLess(findings_pos, files_pos, "findings must be emitted before files")

    def test_missing_findings_key_does_not_crash(self):
        structure = make_structure(2)
        proc, report, document = self._run(structure, findings={})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["findings"], 0)
        self.assertIn("No cross-file findings", document)

    def test_findings_sorted_by_severity_desc(self):
        structure = make_structure(3)
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "low",
             "files": ["f0"], "title": "LowOne", "description": "d"},
            {"type": "contradiction", "severity": "high",
             "files": ["f1"], "title": "HighOne", "description": "d"},
            {"type": "contradiction", "severity": "medium",
             "files": ["f2"], "title": "MedOne", "description": "d"},
        ]}
        _, _, document = self._run(structure, findings=findings)
        hi = document.find("HighOne")
        med = document.find("MedOne")
        lo = document.find("LowOne")
        self.assertTrue(hi < med < lo, "high > medium > low order")

    def test_severity_has_icon_and_text_label(self):
        structure = make_structure(2)
        findings = {"cross_file_findings": [
            {"type": "coverage", "severity": "high",
             "files": ["f0"], "title": "Gap", "description": "d"},
        ]}
        _, _, document = self._run(structure, findings=findings)
        self.assertIn("mdhv-severity-high", document)
        self.assertIn("mdhv-severity-label", document)
        self.assertIn("HIGH", document)  # text label, not color alone

    def test_involved_file_is_anchor_chip(self):
        structure = make_structure(2)
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f1"], "title": "C", "description": "d"},
        ]}
        _, _, document = self._run(structure, findings=findings)
        self.assertIn('href="#f0--file"', document)
        self.assertIn('href="#f1--file"', document)


class FindingsCompareGridTests(AssembleHarness):
    """A<->B contradiction comparison grid (optional `claims` field). Fully
    BACK-COMPAT: any non-well-formed shape falls through to the prose card."""

    def test_claims_render_compare_grid_and_gates_pass(self):
        structure = make_structure(2)
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f1"], "title": "Conflict",
             "description": "Same input, different behavior.",
             "claims": [
                 {"file": "f0", "claim": "empty token is rejected"},
                 {"file": "f1", "claim": "empty token is anonymous"},
             ]},
        ]}
        proc, report, document = self._run(structure, findings=findings)
        self.assertEqual(proc.returncode, 0)
        # grid + both claim cells + both claim texts present.
        self.assertIn('class="mdhv-finding-compare"', document)
        self.assertEqual(document.count('class="mdhv-finding-claim"'), 2)
        self.assertIn("empty token is rejected", document)
        self.assertIn("empty token is anonymous", document)
        # each cell heads with the file title linked to #<slug>--file (known slug).
        self.assertIn('href="#f0--file"', document)
        self.assertIn('href="#f1--file"', document)
        # prose card coverage unchanged: title/severity/file-chips still present.
        self.assertIn("Conflict", document)
        self.assertIn("Same input, different behavior.", document)
        self.assertIn("mdhv-severity-high", document)
        self.assertIn('class="mdhv-finding-files"', document)
        # gates all pass (empty arrays).
        for gate in ("dangling_anchors", "coverage_gaps",
                     "contract_violations", "external_fetches"):
            self.assertEqual(report.get(gate), [], "%s must be empty" % gate)

    def test_unknown_slug_claim_renders_plain_text_not_link(self):
        # `file` that does not resolve to a known slug -> plain text label, no link.
        structure = make_structure(2)
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "medium",
             "files": ["f0"], "title": "C", "description": "d",
             "claims": [
                 {"file": "f0", "claim": "alpha says yes"},
                 {"file": "ghost-slug", "claim": "ghost says no"},
             ]},
        ]}
        proc, report, document = self._run(structure, findings=findings)
        self.assertEqual(proc.returncode, 0)
        self.assertIn('class="mdhv-finding-compare"', document)
        self.assertIn("alpha says yes", document)
        self.assertIn("ghost says no", document)
        self.assertIn('href="#f0--file"', document)        # known slug -> link
        self.assertNotIn('href="#ghost-slug--file"', document)  # unknown -> no link

    def test_malformed_claims_fall_back_to_prose_no_crash(self):
        # not-a-list / length<2 / a dict missing 'claim' / non-dict member:
        # every malformed shape -> prose fallback, no grid, no crash, gates pass.
        for bad in (
            "not-a-list",
            [],
            [{"file": "f0", "claim": "only one"}],
            [{"file": "f0"}, {"file": "f1", "claim": "has it"}],
            [{"file": "f0", "claim": "ok"}, {"file": "f1", "claim": ""}],
            [{"file": "f0", "claim": "ok"}, "not-a-dict"],
        ):
            with self.subTest(bad=bad):
                structure = make_structure(2)
                findings = {"cross_file_findings": [
                    {"type": "contradiction", "severity": "high",
                     "files": ["f0", "f1"], "title": "Conflict",
                     "description": "prose body here.", "claims": bad},
                ]}
                proc, report, document = self._run(structure, findings=findings)
                self.assertEqual(proc.returncode, 0)
                self.assertNotIn('class="mdhv-finding-compare"', document)
                self.assertIn("prose body here.", document)
                self.assertIn('class="mdhv-finding-files"', document)
                for gate in ("dangling_anchors", "coverage_gaps",
                             "contract_violations", "external_fetches"):
                    self.assertEqual(report.get(gate), [])

    def test_no_claims_finding_renders_exactly_as_before(self):
        # a normal finding with no `claims` -> no grid, prose card unchanged.
        structure = make_structure(2)
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f1"], "title": "Conflict",
             "description": "Same input, different behavior."},
        ]}
        proc, report, document = self._run(structure, findings=findings)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn('class="mdhv-finding-compare"', document)
        self.assertNotIn('class="mdhv-finding-claim"', document)
        self.assertIn("Conflict", document)
        self.assertIn("Same input, different behavior.", document)
        self.assertIn('href="#f0--file"', document)
        self.assertIn('href="#f1--file"', document)


class GraphModeTests(AssembleHarness):
    def test_n3_is_chips(self):
        # N<=3 -> chips regardless of edges.
        structure = make_structure(3, edges=[strong("f0", "f1"), strong("f1", "f2")])
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "chips")
        self.assertIn('data-mode="chips"', document)
        self.assertIn("mdhv-graph-caption", document)

    def test_no_edges_is_chips(self):
        # Any N with zero edges -> chips: no relationships to lay out.
        structure = make_structure(10, edges=[])
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "chips")
        self.assertIn('data-mode="chips"', document)
        self.assertNotIn("<svg", document)

    def test_n4_with_one_edge_is_diagram(self):
        # N>=4 with at least one edge -> the layered flow diagram.
        structure = make_structure(4, edges=[strong("f0", "f1")])
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        self.assertIn('data-mode="diagram"', document)
        self.assertIn("<svg", document)

    def test_weak_only_edges_still_diagram(self):
        # Mode keys on TOTAL edge count, not strong count: weak-only still
        # lays out (as a dashed diagram), it no longer falls back to chips.
        edges = [weak("f0", "f1"), weak("f2", "f3")]
        structure = make_structure(6, edges=edges)
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        self.assertIn('data-mode="diagram"', document)
        self.assertIn("mdhv-edge-weak", document)

    def test_n10_strong_is_diagram(self):
        edges = [strong("f0", "f1"), strong("f1", "f2"), strong("f2", "f3")]
        structure = make_structure(10, edges=edges)
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        self.assertIn('data-mode="diagram"', document)
        self.assertIn("<svg", document)
        self.assertIn("<rect", document)


class GraphFlowDiagramTests(AssembleHarness):
    def _diagram(self, n, edges):
        structure = make_structure(n, edges=edges)
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        return document

    def test_nodes_are_boxes_with_path_sublabel(self):
        # Each connected node is a <rect> box with a file-path sub-label.
        doc = self._diagram(4, [strong("f0", "f1"), strong("f1", "f2")])
        self.assertIn("<rect", doc)
        self.assertIn('class="mdhv-node-sub"', doc)
        self.assertIn("dir1/f1.md", doc)          # sub-label carries the path
        self.assertIn("<title>", doc)             # full title/path in a tooltip

    def test_edges_are_arrowed_paths_one_per_edge(self):
        # Edges are <path> elements (not <line>), one per directed edge, each
        # carrying an arrowhead marker.
        edges = [strong("f0", "f1"), strong("f1", "f2"), strong("f2", "f3")]
        doc = self._diagram(5, edges)
        self.assertEqual(doc.count('<path class="mdhv-edge'), 3)
        self.assertEqual(doc.count('marker-end="url(#mdhv-arrow)"'), 3)
        self.assertNotIn("<line", doc)

    def test_two_cycle_draws_both_directions(self):
        # A->B and B->A are two real references -> two arrowed paths (the
        # renderer no longer collapses a pair into one line).
        edges = [strong("f0", "f1"), strong("f1", "f0"), strong("f2", "f3")]
        doc = self._diagram(5, edges)
        self.assertEqual(doc.count('<path class="mdhv-edge'), 3)
        self.assertNotIn("marker-start=", doc)

    def test_hub_box_filled_via_data_hub(self):
        edges = [strong("f0", "f5"), strong("f1", "f5"), strong("f2", "f5")]
        doc = self._diagram(10, edges)
        m = re.search(r'href="#f5--file"[^>]*data-hub="(\w+)"', doc)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "true")

    def test_hub_is_incoming_strong_only(self):
        # f5 has 3 INCOMING strong edges -> hub.
        # f0 has 3 OUTGOING strong edges and many INCOMING WEAK edges -> NOT a hub.
        edges = [
            strong("f0", "f5"), strong("f1", "f5"), strong("f2", "f5"),
            strong("f0", "f6"), strong("f0", "f7"),
            weak("f8", "f0"), weak("f9", "f0"), weak("f3", "f0"),
        ]
        doc = self._diagram(10, edges)
        m_hub = re.search(r'href="#f5--file"[^>]*data-hub="(\w+)"', doc)
        self.assertIsNotNone(m_hub)
        self.assertEqual(m_hub.group(1), "true", "f5 should be a hub (>=2 incoming strong)")
        m_f0 = re.search(r'href="#f0--file"[^>]*data-hub="(\w+)"', doc)
        self.assertIsNotNone(m_f0)
        self.assertEqual(m_f0.group(1), "false", "f0 must not be a hub (incoming weak never counts)")

    def test_identical_reason_collapsed_to_one_legend_entry(self):
        edges = [
            strong("f0", "f1", reason="shared reason"),
            strong("f2", "f3", reason="shared reason"),
            strong("f4", "f5", reason="shared reason"),
        ]
        doc = self._diagram(10, edges)
        li_count = len(re.findall(r'<li class="mdhv-edge mdhv-edge-strong">shared reason</li>', doc))
        self.assertEqual(li_count, 1, "identical reasons collapse to a single legend entry")

    def test_weak_edges_dashed_and_low_opacity(self):
        edges = [strong("f0", "f1"), strong("f1", "f2"), weak("f3", "f4")]
        doc = self._diagram(10, edges)
        self.assertIn("stroke-dasharray", doc)
        self.assertIn('opacity="0.45"', doc)

    def test_isolated_nodes_grouped_separately(self):
        # f0..f4 connected; f5..f9 isolated.
        edges = [strong("f0", "f1"), strong("f1", "f2"), strong("f2", "f3"), strong("f3", "f4")]
        doc = self._diagram(10, edges)
        self.assertIn("mdhv-graph-isolated", doc)
        self.assertIn(">isolated<", doc)


class GraphDepthTests(AssembleHarness):
    """Layer DEPTH is derived from connectivity (the user-visible promise:
    deeply-chained corpora go deep; flat hub-and-spoke stays shallow)."""

    def _rect_y(self, document):
        # slug -> rect y; only connected nodes have a <rect> (isolated -> chips).
        out = {}
        for m in re.finditer(
            r'href="#(\w+)--file"[^>]*>.*?<rect[^>]*?\by="([\d.]+)"', document
        ):
            out.setdefault(m.group(1), float(m.group(2)))
        return out

    def test_chain_is_deep(self):
        # f0->f1->f2->f3 : four layers, strictly increasing y, all distinct.
        edges = [strong("f0", "f1"), strong("f1", "f2"), strong("f2", "f3")]
        structure = make_structure(4, edges=edges)
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        ys = self._rect_y(document)
        self.assertLess(ys["f0"], ys["f1"])
        self.assertLess(ys["f1"], ys["f2"])
        self.assertLess(ys["f2"], ys["f3"])
        self.assertEqual(len({ys["f0"], ys["f1"], ys["f2"], ys["f3"]}), 4)

    def test_star_is_shallow(self):
        # f1,f2,f3 -> f0 : exactly two layers (referrers over the referenced hub).
        edges = [strong("f1", "f0"), strong("f2", "f0"), strong("f3", "f0")]
        structure = make_structure(4, edges=edges)
        _, _, document = self._run(structure)
        ys = self._rect_y(document)
        self.assertEqual(len(set(ys.values())), 2)
        self.assertGreater(ys["f0"], ys["f1"])

    def test_cycle_places_all_nodes_without_hanging(self):
        # 3-cycle f0->f1->f2->f0 still lays every node out (back-edge dropped).
        edges = [strong("f0", "f1"), strong("f1", "f2"), strong("f2", "f0")]
        structure = make_structure(4, edges=edges)  # f3 isolated
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        ys = self._rect_y(document)
        self.assertEqual(set(ys), {"f0", "f1", "f2"})


class GraphKeyAndA11yTests(AssembleHarness):
    """G1 conventions key + G2 svg title/desc (data-flow / a11y polish)."""

    def _svg_structure(self):
        edges = [strong("f0", "f1"), strong("f1", "f2"), strong("f2", "f3")]
        return make_structure(10, edges=edges)

    def test_svg_has_title_and_desc_with_fixed_ids(self):
        # G2: aria-labelledby + <title>/<desc> replace the bare aria-label.
        _, report, document = self._run(self._svg_structure())
        self.assertEqual(report["graph_mode"], "diagram")
        self.assertIn('aria-labelledby="mdhv-graph-title mdhv-graph-desc"', document)
        self.assertNotIn('aria-label="dependency graph"', document)
        self.assertIn('<title id="mdhv-graph-title">Cross-file reference graph</title>', document)
        self.assertIn('<desc id="mdhv-graph-desc">', document)
        # role="img" is preserved.
        self.assertIn('role="img"', document)

    def test_title_and_desc_are_first_children_of_svg(self):
        # They must precede <defs>/<path>/<a> so SR announces them first.
        _, _, document = self._run(self._svg_structure())
        svg_open = document.find("<svg")
        title_at = document.find("<title id=\"mdhv-graph-title\"", svg_open)
        desc_at = document.find("<desc id=\"mdhv-graph-desc\"", svg_open)
        defs_at = document.find("<defs>", svg_open)
        first_edge = document.find('<path class="mdhv-edge', svg_open)
        self.assertNotEqual(title_at, -1)
        self.assertNotEqual(desc_at, -1)
        self.assertTrue(svg_open < title_at < desc_at < defs_at,
                        "title then desc must be the first two children of <svg>")
        self.assertTrue(desc_at < first_edge,
                        "title/desc must precede the edge <path> elements")

    def test_desc_carries_plain_english_and_counts(self):
        _, _, document = self._run(self._svg_structure())
        d_start = document.find("<desc id=\"mdhv-graph-desc\">")
        d_end = document.find("</desc>", d_start)
        desc_text = document[d_start:d_end]
        self.assertIn("layered top-to-bottom from referrer to referenced", desc_text)
        self.assertIn("hubs", desc_text)
        self.assertIn("strong cross-references", desc_text)

    def test_graph_key_ul_with_three_conventions_in_diagram_mode(self):
        # G1: HTML conventions key (a <ul>, NOT inline <svg> drawing).
        _, report, document = self._run(self._svg_structure())
        self.assertEqual(report["graph_mode"], "diagram")
        self.assertIn('<ul class="mdhv-graph-key">', document)
        self.assertIn("solid line = strong cross-reference", document)
        self.assertIn("dashed line = weak / name-match", document)
        self.assertIn("filled node = hub (referenced by >=2 files)", document)
        # exactly three <li> inside the key
        k_start = document.find('<ul class="mdhv-graph-key">')
        k_end = document.find("</ul>", k_start)
        self.assertEqual(document[k_start:k_end].count("<li"), 3)

    def test_diagram_caption_has_plain_english_reading_plus_counts(self):
        _, _, document = self._run(self._svg_structure())
        cap_at = document.rfind('<p class="mdhv-graph-caption">')
        caption = document[cap_at:document.find("</p>", cap_at)]
        self.assertIn("layered top-to-bottom from referrer to referenced", caption)
        self.assertIn("filled nodes are hubs", caption)
        # the existing counts are preserved
        self.assertIn("strong cross-references", caption)
        self.assertIn("hub(s)", caption)
        self.assertIn("isolated", caption)

    def test_graph_key_adds_no_edge_paths(self):
        # The key is HTML (<li>), so the SVG edge count equals the edge count:
        # 4 directed edges -> 4 <path class="mdhv-edge"> (no pair collapse).
        edges = [
            strong("f0", "f1"), strong("f1", "f0"),
            strong("f2", "f3"), strong("f4", "f5"),
        ]
        structure = make_structure(10, edges=edges)
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        self.assertEqual(document.count('<path class="mdhv-edge'), 4)


class SvgDeterminismTests(AssembleHarness):
    def _build_svg_doc(self):
        edges = [
            strong("f0", "f5"), strong("f1", "f5"), strong("f2", "f5"),
            strong("f3", "f4"), weak("f6", "f7"), strong("f8", "f9"),
        ]
        structure = make_structure(10, edges=edges)
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        return document

    def test_svg_byte_identical_across_runs(self):
        doc1 = self._build_svg_doc()
        doc2 = self._build_svg_doc()
        svg1 = doc1[doc1.find("<svg"):doc1.find("</svg>")]
        svg2 = doc2[doc2.find("<svg"):doc2.find("</svg>")]
        self.assertEqual(svg1, svg2, "same structure.json must render byte-identical SVG")

    def test_graph_region_byte_identical_across_runs(self):
        # Span the whole graph region (svg + the HTML key/legend/caption that
        # sit OUTSIDE </svg>) so the title/desc + new conventions key are also
        # covered by the determinism guarantee.
        doc1 = self._build_svg_doc()
        doc2 = self._build_svg_doc()
        g1 = doc1[doc1.find('<div class="mdhv-graph"'):]
        g2 = doc2[doc2.find('<div class="mdhv-graph"'):]
        self.assertEqual(g1, g2, "same structure.json must render byte-identical graph region")

    def test_node_order_sorted_by_directory_slug_not_hash(self):
        # Give files file_paths whose (directory, slug) order differs from
        # both insertion order and hash order, then confirm the node boxes are
        # emitted in (directory, slug) order.
        files = []
        nodes = []
        # deliberately scrambled insertion order; directories chosen so sorted
        # (directory, slug) order is z-dir/a, then a-dir/b, then m-dir/c ...
        spec = [
            ("c", "m-dir/c.md"),
            ("a", "a-dir/a.md"),
            ("b", "a-dir/b.md"),
            ("d", "z-dir/d.md"),
            ("e", "z-dir/e.md"),
            ("f", "b-dir/f.md"),
            ("g", "b-dir/g.md"),
            ("h", "c-dir/h.md"),
            ("i", "c-dir/i.md"),
            ("j", "d-dir/j.md"),
        ]
        for slug, fp in spec:
            files.append(make_file(slug, file_path=fp))
            nodes.append(make_node(slug, file_path=fp))
        # all connected so none are isolated and all get circle positions
        edges = [
            strong("a", "b"), strong("b", "c"), strong("c", "d"), strong("d", "e"),
            strong("e", "f"), strong("f", "g"), strong("g", "h"), strong("h", "i"),
            strong("i", "j"), strong("j", "a"),
            # make 'c' a hub via two incoming strong
            strong("e", "c"),
        ]
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 10},
            "files": files,
            "graph": {"nodes": nodes, "edges": edges},
        }
        _, report, document = self._run(structure)
        self.assertEqual(report["graph_mode"], "diagram")
        # Expected sorted (directory, slug) order:
        expected = ["a", "b", "f", "g", "h", "i", "j", "c", "d", "e"]
        # Order in which node anchors appear in the SVG <a class="mdhv-node" href="#..--file">
        appearance = re.findall(r'<a class="mdhv-node" href="#(\w+)--file" data-hub=', document)
        self.assertEqual(appearance, expected,
                         "SVG nodes must be ordered by (directory, slug), not hash/insertion order")


class FilesStitchTests(AssembleHarness):
    def test_fragments_stitched_in_structure_order(self):
        structure = make_structure(3)
        fragments = {
            "f0": '<div class="mdhv-section" data-src-heading="f0--overview">AAA</div>',
            "f1": '<div class="mdhv-section" data-src-heading="f1--overview">BBB</div>',
            "f2": '<div class="mdhv-section" data-src-heading="f2--overview">CCC</div>',
        }
        _, _, document = self._run(structure, fragments=fragments)
        pa = document.find("AAA")
        pb = document.find("BBB")
        pc = document.find("CCC")
        self.assertTrue(pa < pb < pc, "fragments stitched in structure.json file order")
        # each under a file header with a src-link to the source .md
        # (count the header element markup, not the bare class name which also
        # appears once inside the fixture's <style> block).
        self.assertEqual(document.count('class="mdhv-file-header"'), 3)
        self.assertIn('class="mdhv-src-link" href="dir0/f0.md"', document)

    def test_report_has_required_keys(self):
        structure = make_structure(2)
        _, report, _ = self._run(structure)
        for key in ("files", "sections", "findings", "graph_mode"):
            self.assertIn(key, report)

    def test_report_has_full_gate_arrays(self):
        # On success the report carries all four (empty) failure arrays.
        structure = make_structure(2)
        proc, report, _ = self._run(structure)
        self.assertEqual(proc.returncode, 0)
        for key in ("dangling_anchors", "coverage_gaps",
                    "contract_violations", "external_fetches"):
            self.assertIn(key, report)
            self.assertEqual(report[key], [])


class StrapTests(AssembleHarness):
    def test_strap_has_root_count_estimate_label_language(self):
        structure = make_structure(3, root="myroot", language="ru")
        _, _, document = self._run(structure)
        self.assertIn("mdhv-strap", document)
        self.assertIn("myroot", document)
        self.assertIn("3 files", document)
        self.assertIn("(estimate)", document)  # token estimate labelled as estimate
        self.assertIn("ru", document)

    def test_total_token_estimate_summed(self):
        files = [make_file("a", token_estimate=100), make_file("b", token_estimate=250)]
        nodes = [make_node("a"), make_node("b")]
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 2},
            "files": files,
            "graph": {"nodes": nodes, "edges": []},
        }
        _, _, document = self._run(structure)
        self.assertIn("~350 tokens", document)


class OverviewPanelTests(AssembleHarness):
    """R1 — the at-a-glance orientation panel emitted in the STRAP region."""

    def _hub_structure(self):
        # f4 is a hub (>=2 incoming strong); f3 has only 1 incoming strong; one
        # weak edge so the weak-count is non-zero.
        return make_structure(
            5,
            edges=[strong("f0", "f4"), strong("f1", "f4"), strong("f2", "f4"),
                   strong("f0", "f3"), weak("f1", "f2")],
        )

    def _render(self, structure, findings):
        sys.path.insert(0, REPO_ROOT)
        from scripts.assemble import render_overview
        return render_overview(structure, findings)

    def test_panel_emits_computed_counts(self):
        structure = self._hub_structure()
        html_out = self._render(structure, {"cross_file_findings": []})
        self.assertIn('class="mdhv-overview"', html_out)
        self.assertIn('class="mdhv-overview-stat"', html_out)
        # 5 files, 4 strong edges, 1 weak edge, 1 hub (only f4 has >=2 incoming).
        self.assertIn("<dd>5</dd>", html_out)   # files
        self.assertRegex(html_out, r"strong refs</dt><dd>4</dd>")
        self.assertRegex(html_out, r"weak refs</dt><dd>1</dd>")
        self.assertRegex(html_out, r"hubs</dt><dd>1</dd>")

    def test_findings_tally_per_severity_links_to_findings(self):
        structure = self._hub_structure()
        findings = {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f4"], "title": "A", "description": "d"},
            {"type": "contradiction", "severity": "high",
             "files": ["f1", "f4"], "title": "B", "description": "d"},
            {"type": "coverage", "severity": "medium",
             "files": ["f0"], "title": "C", "description": "d"},
            {"type": "signal_noise", "severity": "low",
             "files": ["f2"], "title": "D", "description": "d"},
            {"type": "signal_noise", "severity": "low",
             "files": ["f3"], "title": "E", "description": "d"},
            {"type": "signal_noise", "severity": "low",
             "files": ["f3"], "title": "F", "description": "d"},
        ]}
        html_out = self._render(structure, findings)
        # 2 high · 1 medium · 3 low — each a jump-link to #mdhv-findings, each a
        # severity badge (glyph + label) so meaning is never colour-only.
        self.assertIn('class="mdhv-overview-jump"', html_out)
        self.assertIn(
            '<a class="mdhv-severity-high-badge" href="#mdhv-findings">', html_out)
        self.assertIn(
            '<a class="mdhv-severity-medium-badge" href="#mdhv-findings">', html_out)
        self.assertIn(
            '<a class="mdhv-severity-low-badge" href="#mdhv-findings">', html_out)
        self.assertIn("2 high", html_out)
        self.assertIn("1 medium", html_out)
        self.assertIn("3 low", html_out)
        # every jump-link points only at the allowed chrome anchor
        self.assertNotIn('href="#"', html_out)

    def test_zero_findings_renders_quiet_line_no_crash(self):
        structure = self._hub_structure()
        html_out = self._render(structure, {"cross_file_findings": []})
        self.assertIn("no cross-file findings", html_out)
        self.assertIn('href="#mdhv-findings"', html_out)
        # no severity badges when there are no findings
        self.assertNotIn("mdhv-severity-high-badge", html_out)

    def test_missing_findings_object_does_not_crash(self):
        structure = self._hub_structure()
        # a {} findings object (no cross_file_findings key) must be tolerated
        html_out = self._render(structure, {})
        self.assertIn("no cross-file findings", html_out)

    def test_hub_chips_target_existing_file_sections(self):
        structure = self._hub_structure()
        findings = {"cross_file_findings": []}
        _, _, document = self._run(structure, findings=findings)
        # the panel emits a hub row whose chip targets #f4--file ...
        self.assertIn('class="mdhv-overview-hubs"', document)
        self.assertIn('href="#f4--file"', document)
        # ... and that file section id actually EXISTS in the assembled output.
        self.assertIn('id="f4--file"', document)

    def test_hubs_ranked_by_incoming_strong_and_capped_at_five(self):
        # six hubs (each with >=2 incoming strong); the panel shows at most five,
        # ranked by incoming-strong count desc then slug asc.
        nodes = [make_node(f"f{i}") for i in range(13)]
        files = [make_file(f"f{i}") for i in range(13)]
        edges = []
        # f0 gets 3 incoming; f1..f5 get 2 incoming each (sources f6..f12).
        edges += [strong("f6", "f0"), strong("f7", "f0"), strong("f8", "f0")]
        edges += [strong("f6", "f1"), strong("f7", "f1")]
        edges += [strong("f6", "f2"), strong("f7", "f2")]
        edges += [strong("f8", "f3"), strong("f9", "f3")]
        edges += [strong("f10", "f4"), strong("f11", "f4")]
        edges += [strong("f12", "f5"), strong("f6", "f5")]
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 13},
            "files": files,
            "graph": {"nodes": nodes, "edges": edges},
        }
        html_out = self._render(structure, {"cross_file_findings": []})
        chips = re.findall(r'class="mdhv-node" href="#([^"]+)--file"', html_out)
        self.assertEqual(len(chips), 5, "hub chips must be capped at five")
        # f0 has the most incoming strong, so it is first.
        self.assertEqual(chips[0], "f0")
        # hub count in the stat readout reflects ALL hubs, not the capped chips.
        self.assertRegex(html_out, r"hubs</dt><dd>6</dd>")

    def test_no_hubs_omits_hub_row(self):
        # a corpus with no node having >=2 incoming strong renders no hub row.
        structure = make_structure(3, edges=[strong("f0", "f1")])
        html_out = self._render(structure, {"cross_file_findings": []})
        self.assertNotIn("mdhv-overview-hubs", html_out)
        # but the stat + jump rows still render
        self.assertIn("mdhv-overview-stat", html_out)
        self.assertIn("mdhv-overview-jump", html_out)

    def test_panel_lands_inside_strap_region_with_one_top_id(self):
        structure = self._hub_structure()
        _, _, document = self._run(
            structure, findings={"cross_file_findings": []},
            design=self._real_design())
        # exactly one id="mdhv-top" survives (panel carries none)
        occurrences = re.findall(r'id\s*=\s*["\']mdhv-top["\']', document)
        self.assertEqual(len(occurrences), 1)
        # the panel sits inside the <header id="mdhv-top"> STRAP region: between
        # the top landmark open and the next major landmark (the TOC nav).
        top_idx = document.find('id="mdhv-top"')
        panel_idx = document.find('class="mdhv-overview"')
        toc_idx = document.find('id="mdhv-toc"')
        self.assertNotEqual(panel_idx, -1, "the overview panel must be emitted")
        self.assertLess(top_idx, panel_idx)
        self.assertLess(panel_idx, toc_idx)

    def test_panel_never_nests_a_header_or_reemits_top_id(self):
        structure = self._hub_structure()
        html_out = self._render(structure, {"cross_file_findings": []})
        self.assertNotIn("<header", html_out)
        self.assertNotIn("mdhv-top", html_out)

    def test_e2e_gates_pass_with_panel(self):
        structure = self._hub_structure()
        _, report, document = self._run(
            structure, findings={"cross_file_findings": [
                {"type": "contradiction", "severity": "high",
                 "files": ["f0", "f4"], "title": "A", "description": "d"}]},
            design=self._real_design())
        # the four gate arrays are all empty (panel introduced no dangling
        # anchor / coverage gap / contract violation / external fetch).
        for key in ("dangling_anchors", "coverage_gaps",
                    "contract_violations", "external_fetches"):
            self.assertEqual(report.get(key), [], "gate %r not empty: %r" % (
                key, report.get(key)))
        self.assertIn('class="mdhv-overview"', document)

    def _real_design(self):
        with open(REAL_DESIGN, encoding="utf-8") as fh:
            return fh.read()


# ===========================================================================
# Task 6 — the FOUR hard-fail gates
# ===========================================================================

REAL_DESIGN = os.path.join(REPO_ROOT, "references", "design-system.html")


def heading(level, text, anchor):
    return {"level": level, "text": text, "anchor": anchor}


def file_with_headings(slug, file_path, headings, title=None, token_estimate=100):
    return {
        "slug": slug,
        "file_path": file_path,
        "language": "en",
        "title": title or slug,
        "token_estimate": token_estimate,
        "token_estimate_method": "chars/4",
        "headings": headings,
    }


def section_div(slug, anchor, body="content"):
    """A coverage-clean .mdhv-section carrying its <slug>--<anchor> id."""
    sid = f"{slug}--{anchor}"
    return (
        f'<section class="mdhv-section" id="{sid}" data-src-heading="{sid}">'
        f'<h3>{anchor}</h3><p>{body}</p></section>'
    )


class Gate1AnchorResolutionTests(AssembleHarness):
    def test_dangling_in_fragment_anchor_hard_fails(self):
        # Fragment links to an id that is NOT a heading id and NOT chrome.
        f0 = file_with_headings("f0", "f0.md", [heading(1, "Top", "top")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        fragments = {
            "f0": (
                section_div("f0", "top")
                + '<a href="#f0--does-not-exist">dangling</a>'
            )
        }
        proc, report, document = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(report["dangling_anchors"], "expected a dangling anchor")
        self.assertIn("f0--does-not-exist", " ".join(report["dangling_anchors"]))
        # nothing written on failure
        self.assertIsNone(document, "no partial page may be written on gate failure")
        self.assertIn("dangling_anchors", proc.stderr)

    def test_dangling_cross_file_anchor_hard_fails(self):
        # f0 links cross-file to an anchor that does NOT exist in f1's headings.
        f0 = file_with_headings("f0", "f0.md", [heading(1, "A", "a")])
        f1 = file_with_headings("f1", "f1.md", [heading(1, "B", "b")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 2},
            "files": [f0, f1],
            "graph": {"nodes": [make_node("f0", "f0.md"), make_node("f1", "f1.md")],
                      "edges": []},
        }
        fragments = {
            "f0": section_div("f0", "a") + '<a href="#f1--nonexistent">x-ref</a>',
            "f1": section_div("f1", "b"),
        }
        proc, report, _ = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("f1--nonexistent", " ".join(report["dangling_anchors"]))

    def test_valid_cross_file_and_chrome_anchors_pass(self):
        # f0 links to f1's real heading id AND a chrome anchor -> both resolve.
        f0 = file_with_headings("f0", "f0.md", [heading(1, "A", "a")])
        f1 = file_with_headings("f1", "f1.md", [heading(1, "B", "b")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 2},
            "files": [f0, f1],
            "graph": {"nodes": [make_node("f0", "f0.md"), make_node("f1", "f1.md")],
                      "edges": []},
        }
        fragments = {
            "f0": (section_div("f0", "a")
                   + '<a href="#f1--b">x-ref</a>'
                   + '<a href="#mdhv-top">top</a>'),
            "f1": section_div("f1", "b"),
        }
        proc, report, _ = self._run(structure, fragments=fragments)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["dangling_anchors"], [])


class Gate1HtmlAwareHrefTests(unittest.TestCase):
    """The gate-1 href scan is HTML-AWARE (the dogfood 14-16 regression fix).

    ``hrefs_in`` collects the ``href`` of REAL ``<a>`` start tags only, filtered
    to in-page (``#``-prefixed) targets. A literal ``href="#x"`` written inside a
    ``<code>`` block is character data and an escaped ``&lt;a href="#x"&gt;`` is
    text — NEITHER is a link, so NEITHER is collected — which is what stops the
    self-referential corpus (docs that merely quote the anchor scheme) from
    false-failing gate 1. A genuinely broken ``<a href="#typo">`` in body text
    (even inside ``<pre>``) IS a real element and IS still collected, so no
    protection is lost. External/relative hrefs were never in scope and stay out.
    """

    def setUp(self):
        sys.path.insert(0, REPO_ROOT)
        from scripts.assemble import hrefs_in  # re-exported from scripts.gates
        self.hrefs_in = hrefs_in

    def test_href_inside_code_block_is_not_a_link(self):
        # `<code>href="#x"</code>` is character data, not an <a> element.
        self.assertEqual(self.hrefs_in('<code>href="#x"</code>'), [])

    def test_escaped_anchor_example_is_not_a_link(self):
        # An escaped `<a href="#x">t</a>` rendered as text is not an element.
        self.assertEqual(
            self.hrefs_in('&lt;a href="#x"&gt;t&lt;/a&gt;'), [])

    def test_real_in_page_anchor_is_collected(self):
        self.assertEqual(self.hrefs_in('<a href="#good">x</a>'), ["#good"])

    def test_real_dangling_anchor_still_collected(self):
        # A genuinely broken in-page anchor is a real <a> element -> still caught.
        self.assertEqual(self.hrefs_in('<a href="#typo">x</a>'), ["#typo"])

    def test_real_anchor_inside_pre_is_collected(self):
        # A real <a> inside <pre> is still an element (only escaped/<code> text
        # is exempt), so it is collected and remains gate-1 enforceable.
        self.assertEqual(
            self.hrefs_in('<pre><a href="#inpre">x</a></pre>'), ["#inpre"])

    def test_external_and_relative_hrefs_omitted(self):
        # In-page scope is preserved: non-#-prefixed hrefs are never returned.
        self.assertEqual(self.hrefs_in('<a href="https://x">x</a>'), [])
        self.assertEqual(self.hrefs_in('<a href="rel.html#f">x</a>'), [])

    def test_quote_styles_and_uppercase_tag(self):
        # Single + double quotes and an uppercase <A HREF> are all real anchors.
        self.assertEqual(
            self.hrefs_in("<a href='#sq'>x</a>"), ["#sq"])
        self.assertEqual(
            self.hrefs_in('<A HREF="#up">x</A>'), ["#up"])

    def test_entity_in_href_is_decoded(self):
        # Pin the parser-decode behavior: href="#a&amp;b" -> '#a&b'.
        self.assertEqual(self.hrefs_in('<a href="#a&amp;b">x</a>'), ["#a&b"])

    def test_anchor_with_name_but_no_href_yields_nothing(self):
        self.assertEqual(self.hrefs_in('<a name="x">y</a>'), [])


class Gate1HtmlAwareRunGatesTests(unittest.TestCase):
    """End-to-end gate-1 over ``run_gates`` (the exact 14-16 regression): a
    `<code>`-quoted anchor scheme must NOT register as a dangling anchor, while a
    real dangling `<a>` in body text still does."""

    def setUp(self):
        sys.path.insert(0, REPO_ROOT)
        from scripts.assemble import run_gates
        self.run_gates = run_gates

    def _structure(self):
        # One file, slug 's', one heading anchor 'h' -> id space = {'s--h'}.
        s = file_with_headings("s", "s.md", [heading(1, "Heading", "h")])
        return {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [s],
            "graph": {"nodes": [make_node("s", "s.md")], "edges": []},
        }

    def test_code_quoted_anchor_scheme_is_not_dangling(self):
        # .mdhv-section emits data-src-heading="s--h" (gate 2 clean), and the
        # body merely QUOTES an anchor inside <code>: gate 1 must not flag it.
        structure = self._structure()
        fragment = (
            '<section class="mdhv-section" id="s--h" data-src-heading="s--h">'
            '<h3>h</h3>'
            '<p>use <code>href="#nonexistent--anchor"</code> like so</p>'
            '</section>'
        )
        report = self.run_gates(structure, {}, {"s": fragment})
        self.assertEqual(report["dangling_anchors"], [])
        self.assertEqual(report["coverage_gaps"], [])

    def test_real_dangling_anchor_in_body_still_hard_fails(self):
        # Same coverage-clean section, but now a REAL <a href="#nope"> in body
        # text: gate 1 must still report it as dangling.
        structure = self._structure()
        fragment = (
            '<section class="mdhv-section" id="s--h" data-src-heading="s--h">'
            '<h3>h</h3>'
            '<p>see <a href="#nope">this</a></p>'
            '</section>'
        )
        report = self.run_gates(structure, {}, {"s": fragment})
        self.assertTrue(report["dangling_anchors"],
                        "a real dangling in-page <a> must still hard-fail gate 1")
        self.assertIn("#nope", " ".join(report["dangling_anchors"]))


class Gate2CoverageTests(AssembleHarness):
    def test_missing_coverage_hard_fails(self):
        # f0 has TWO headings; fragment only covers one -> coverage gap.
        f0 = file_with_headings(
            "f0", "f0.md", [heading(1, "A", "a"), heading(2, "B", "b")]
        )
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        fragments = {"f0": section_div("f0", "a")}  # 'b' uncovered
        proc, report, document = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(report["coverage_gaps"])
        self.assertIn("f0--b", " ".join(report["coverage_gaps"]))
        self.assertIsNone(document)
        self.assertIn("coverage_gaps", proc.stderr)

    def test_subset_semantics_extra_ids_allowed(self):
        # Fragment carries an EXTRA renderer grouping id beyond the source set;
        # coverage holds because source ids are a SUBSET of emitted ids.
        f0 = file_with_headings(
            "f0", "f0.md", [heading(1, "A", "a"), heading(2, "B", "b")]
        )
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        # one merged section covers both a and b, plus an extra grouping id.
        fragments = {
            "f0": (
                '<section class="mdhv-section" id="f0--group" '
                'data-src-heading="f0--a f0--b f0--group">'
                '<h3>merged</h3></section>'
            )
        }
        proc, report, _ = self._run(structure, fragments=fragments)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["coverage_gaps"], [])

    def test_coverage_uses_globally_unique_ids_not_bare_anchors(self):
        # Two files both have a heading anchor "overview"; f1's fragment carries
        # only f0--overview (wrong file). Bare-anchor comparison would wrongly
        # pass; <slug>--<anchor> comparison correctly flags f1.
        f0 = file_with_headings("f0", "f0.md", [heading(1, "Overview", "overview")])
        f1 = file_with_headings("f1", "f1.md", [heading(1, "Overview", "overview")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 2},
            "files": [f0, f1],
            "graph": {"nodes": [make_node("f0", "f0.md"), make_node("f1", "f1.md")],
                      "edges": []},
        }
        fragments = {
            "f0": section_div("f0", "overview"),
            # f1 mistakenly tags the OTHER file's id
            "f1": ('<section class="mdhv-section" id="f1--x" '
                   'data-src-heading="f0--overview">x</section>'),
        }
        proc, report, _ = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("f1--overview", " ".join(report["coverage_gaps"]))

    def test_merged_section_multi_id_data_src_heading_covers_all(self):
        # Lock the merged-coverage mechanism: a SINGLE rendered section may carry
        # several space-separated source ids in one data-src-heading attribute,
        # and that satisfies gate 2 for every id it lists. The file has THREE
        # source headings; ONE merged section covers two of them (a + b) and a
        # second section covers the third (c) -> exit 0, no coverage gaps.
        f0 = file_with_headings(
            "f0", "f0.md",
            [heading(1, "A", "a"), heading(2, "B", "b"), heading(2, "C", "c")],
        )
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        fragments = {
            "f0": (
                # ONE merged section lists two source ids space-separated.
                '<section class="mdhv-section" id="f0--a" '
                'data-src-heading="f0--a f0--b">'
                '<h3>A &amp; B</h3><p>merged lead + non-lead heading</p></section>'
                # the third heading is covered by its own section.
                + section_div("f0", "c")
            )
        }
        proc, report, document = self._run(structure, fragments=fragments)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["coverage_gaps"], [])
        self.assertIsNotNone(document)

    def test_many_heading_file_full_coverage_then_drop_one_id(self):
        # Mirrors a real large-README coverage failure at scale: a file with
        # 13 source headings. The fragment covers EVERY id, spread across
        # sections including at least one MERGED multi-id section -> exit 0.
        # Then drop ONE id from that merged section -> non-zero exit and the
        # dropped id named in coverage_gaps.
        anchors = [f"h{i}" for i in range(13)]
        headings = [heading(1 if i == 0 else 2, f"Heading {i}", a)
                    for i, a in enumerate(anchors)]
        f0 = file_with_headings("readme", "README.md", headings, title="readme")
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("readme", "README.md")], "edges": []},
        }

        def build_fragment(covered_anchors):
            """Cover ``covered_anchors`` (a list of anchors) using a mix of a
            merged multi-id section (the first three) plus one section per
            remaining anchor. Returns the fragment HTML."""
            ids = [f"readme--{a}" for a in covered_anchors]
            merged = ids[:3]
            rest = ids[3:]
            html_parts = [
                '<section class="mdhv-section" id="{lead}" '
                'data-src-heading="{joined}"><h3>merged lead</h3>'
                '<p>merged section covering several source headings</p>'
                '</section>'.format(lead=merged[0], joined=" ".join(merged))
            ]
            for sid in rest:
                html_parts.append(
                    f'<section class="mdhv-section" id="{sid}" '
                    f'data-src-heading="{sid}"><h3>{sid}</h3><p>body</p></section>'
                )
            return "".join(html_parts)

        # Full coverage -> clean run.
        fragments = {"readme": build_fragment(anchors)}
        proc, report, document = self._run(structure, fragments=fragments)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["coverage_gaps"], [])
        self.assertIsNotNone(document)

        # Drop ONE id (h1, which lives inside the merged multi-id section) ->
        # gate 2 hard-fails and names exactly that id.
        dropped = "h1"
        remaining = [a for a in anchors if a != dropped]
        fragments = {"readme": build_fragment(remaining)}
        proc, report, document = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(report["coverage_gaps"])
        self.assertIn(f"readme--{dropped}", " ".join(report["coverage_gaps"]))
        self.assertIsNone(document, "no partial page may be written on gate failure")
        self.assertIn("coverage_gaps", proc.stderr)


class Gate3ContractTests(AssembleHarness):
    def _structure_one_file(self, src_path="f0.md"):
        f0 = file_with_headings("f0", src_path, [heading(1, "Rules", "rules")])
        return {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", src_path)], "edges": []},
        }

    def test_contract_present_in_both_sides_passes(self):
        contract = "If the token is empty the request MUST be rejected with 401."
        structure = self._structure_one_file()
        sources = {"f0.md": "# Rules\n\n" + contract + "\n\nmore prose.\n"}
        analyses = {"f0": {
            "slug": "f0", "tldr": "t",
            "sections": [], "contracts": [{"text": contract, "source_anchor": "f0--rules"}],
        }}
        fragments = {"f0": (
            section_div("f0", "rules")
            + f'<div class="mdhv-contract"><p>{contract}</p></div>'
        )}
        proc, report, _ = self._run(structure, fragments=fragments,
                                    analyses=analyses, sources=sources)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["contract_violations"], [])

    def test_contract_missing_from_fragment_hard_fails(self):
        # Contract is in the SOURCE but the fragment never renders it as a
        # .mdhv-contract -> mismatch on side (b).
        contract = "Empty token MUST be rejected with 401."
        structure = self._structure_one_file()
        sources = {"f0.md": "# Rules\n\n" + contract + "\n"}
        analyses = {"f0": {
            "slug": "f0", "tldr": "t", "sections": [],
            "contracts": [{"text": contract, "source_anchor": "f0--rules"}],
        }}
        # fragment has NO .mdhv-contract carrying the text
        fragments = {"f0": section_div("f0", "rules", body="some summary")}
        proc, report, document = self._run(structure, fragments=fragments,
                                           analyses=analyses, sources=sources,
                                           check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(report["contract_violations"])
        self.assertIn("fragment", " ".join(report["contract_violations"]).lower())
        self.assertIsNone(document)
        self.assertIn("contract_violations", proc.stderr)

    def test_contract_mismatch_against_source_hard_fails(self):
        # Contract text the renderer claims is NOT actually in the source bytes.
        structure = self._structure_one_file()
        sources = {"f0.md": "# Rules\n\nThe real rule is totally different.\n"}
        bogus = "This sentence does not appear in the source at all."
        analyses = {"f0": {
            "slug": "f0", "tldr": "t", "sections": [],
            "contracts": [{"text": bogus, "source_anchor": "f0--rules"}],
        }}
        fragments = {"f0": (
            section_div("f0", "rules")
            + f'<div class="mdhv-contract"><p>{bogus}</p></div>'
        )}
        proc, report, _ = self._run(structure, fragments=fragments,
                                    analyses=analyses, sources=sources, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(report["contract_violations"])
        self.assertIn("source", " ".join(report["contract_violations"]).lower())

    def test_contract_survives_html_escaping_and_code_wrapping(self):
        # verbatim_critical with markdown/HTML special chars. The fragment
        # HTML-escapes them and wraps an inline token in <code>; normalize()
        # must make the check pass anyway.
        contract = 'If a < b && c > d, return "ok" with <token> & exit 0.'
        structure = self._structure_one_file()
        sources = {"f0.md": "# Rules\n\n" + contract + "\n"}
        analyses = {"f0": {
            "slug": "f0", "tldr": "t", "sections": [],
            # stored already-normalized (the renderer round-trips normalize())
            "contracts": [{"text": contract, "source_anchor": "f0--rules"}],
        }}
        # Fragment escapes the chars and splits a token into <code>...</code>,
        # also splitting the contract across two block elements.
        escaped = html.escape(contract)  # &lt; &amp; &gt; &quot; etc.
        # wrap the word "ok" in <code> by replacing its escaped form
        escaped_codey = escaped.replace("&quot;ok&quot;",
                                        '&quot;<code>ok</code>&quot;')
        fragments = {"f0": (
            section_div("f0", "rules")
            + '<div class="mdhv-contract"><p>'
            + escaped_codey
            + '</p></div>'
        )}
        proc, report, _ = self._run(structure, fragments=fragments,
                                    analyses=analyses, sources=sources)
        self.assertEqual(proc.returncode, 0,
                         f"normalized contract check must survive escaping: {proc.stderr}")
        self.assertEqual(report["contract_violations"], [])

    def test_contract_with_void_element_inside_does_not_corrupt_extraction(self):
        # A <br> inside the .mdhv-contract must not unbalance the parser stack;
        # the following section's text must NOT leak into the contract block.
        contract = "Line one. Line two."
        structure = self._structure_one_file()
        sources = {"f0.md": "# Rules\n\n" + contract + "\n"}
        analyses = {"f0": {
            "slug": "f0", "tldr": "t", "sections": [],
            "contracts": [{"text": contract, "source_anchor": "f0--rules"}],
        }}
        fragments = {"f0": (
            section_div("f0", "rules")
            + '<div class="mdhv-contract"><p>Line one.<br>Line two.</p></div>'
            + '<p>UNRELATED PROSE THAT MUST NOT COUNT AS CONTRACT</p>'
        )}
        proc, report, _ = self._run(structure, fragments=fragments,
                                    analyses=analyses, sources=sources)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["contract_violations"], [])

    def test_contract_spanning_multiple_contract_blocks(self):
        # A contract may span MULTIPLE .mdhv-contract elements in one fragment;
        # gate 3 concatenates them (document order, space-joined) before check.
        contract = "Reject empty tokens. Always return 401 on auth failure."
        structure = self._structure_one_file()
        sources = {"f0.md": "# Rules\n\n" + contract + "\n"}
        analyses = {"f0": {
            "slug": "f0", "tldr": "t", "sections": [],
            "contracts": [{"text": contract, "source_anchor": "f0--rules"}],
        }}
        fragments = {"f0": (
            section_div("f0", "rules")
            + '<div class="mdhv-contract"><p>Reject empty tokens.</p></div>'
            + '<div class="mdhv-contract"><p>Always return 401 on auth failure.</p></div>'
        )}
        proc, report, _ = self._run(structure, fragments=fragments,
                                    analyses=analyses, sources=sources)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["contract_violations"], [])


class IntraDocXrefTests(AssembleHarness):
    """In-document section cross-reference links (renderer enrichment). The
    agent edits are doc/LLM, but the gate behavior they rely on is testable and
    pinned here: gate 1 makes a valid intra-file anchor gate-safe and rejects a
    dangling one (and nothing else does); a body link adjacent to a contract
    never corrupts the gate-3 contract extraction."""

    def test_intra_file_anchor_is_gate_safe(self):
        # A fragment that links to ANOTHER section of the SAME file via
        # #{slug}--{real-anchor} -> gate 1 resolves it; all four gates clean.
        f0 = file_with_headings(
            "f0", "f0.md",
            [heading(1, "Top", "top"), heading(2, "2.4 Decay", "24-decay")],
        )
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        fragments = {
            "f0": (
                section_div(
                    "f0", "top",
                    body='see <a href="#f0--24-decay">§2.4</a> for the rule',
                )
                + section_div("f0", "24-decay")
            )
        }
        proc, report, document = self._run(structure, fragments=fragments)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for gate in ("dangling_anchors", "coverage_gaps",
                     "contract_violations", "external_fetches"):
            self.assertEqual(report[gate], [], "%s must be empty" % gate)
        self.assertIsNotNone(document)
        self.assertIn('href="#f0--24-decay"', document)

    def test_dangling_intra_file_anchor_rejected_by_gate1_only(self):
        # A coverage-clean fragment PLUS a same-file link to a non-existent
        # anchor -> gate 1 (anchor resolution) is the SOLE rejecting gate.
        f0 = file_with_headings("f0", "f0.md", [heading(1, "Top", "top")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        fragments = {
            "f0": (
                section_div("f0", "top")
                + '<a href="#f0--no-such">see other section</a>'
            )
        }
        proc, report, document = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIsNone(document, "no partial page may be written on gate failure")
        self.assertTrue(report["dangling_anchors"], "expected a dangling anchor")
        self.assertIn("f0--no-such", " ".join(report["dangling_anchors"]))
        # gate 1 is the SOLE rejecting gate — coverage/contract stay clean.
        self.assertEqual(report["coverage_gaps"], [])
        self.assertEqual(report["contract_violations"], [])
        self.assertIn("dangling_anchors", proc.stderr)

    def test_cross_file_anchor_is_gate_safe(self):
        # The cross-file path the renderer already uses stays valid: a link to
        # ANOTHER file's real heading id resolves through gate 1.
        f0 = file_with_headings("f0", "f0.md", [heading(1, "A", "a")])
        f1 = file_with_headings("f1", "f1.md", [heading(1, "B", "b")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 2},
            "files": [f0, f1],
            "graph": {"nodes": [make_node("f0", "f0.md"), make_node("f1", "f1.md")],
                      "edges": []},
        }
        fragments = {
            "f0": section_div("f0", "a") + '<a href="#f1--b">cross-file ref</a>',
            "f1": section_div("f1", "b"),
        }
        proc, report, document = self._run(structure, fragments=fragments)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["dangling_anchors"], [])
        self.assertIsNotNone(document)

    def test_body_link_near_contract_does_not_corrupt_gate3(self):
        # A verbatim_critical contract in a .mdhv-contract callout, plus the SAME
        # phrase wrapped in a same-file <a> link in a SEPARATE section body. The
        # body link must NOT leak into / corrupt the .mdhv-contract extraction.
        contract = "Empty token MUST be rejected with 401."
        f0 = file_with_headings(
            "f0", "f0.md",
            [heading(1, "Rules", "rules"), heading(2, "Notes", "notes")],
        )
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        sources = {"f0.md": "# Rules\n\n" + contract + "\n\n## Notes\n\nmore.\n"}
        analyses = {"f0": {
            "slug": "f0", "tldr": "t", "sections": [],
            "contracts": [{"text": contract, "source_anchor": "f0--rules"}],
        }}
        fragments = {"f0": (
            # the verbatim contract lives in its callout (no link inside it)
            '<section class="mdhv-section" id="f0--rules" '
            'data-src-heading="f0--rules"><h3>rules</h3>'
            f'<div class="mdhv-contract"><p>{contract}</p></div></section>'
            # a SEPARATE section body carries the SAME phrase wrapped in an <a>
            '<section class="mdhv-section" id="f0--notes" '
            'data-src-heading="f0--notes"><h3>notes</h3>'
            f'<p>as in <a href="#f0--rules">{contract}</a></p></section>'
        )}
        proc, report, document = self._run(structure, fragments=fragments,
                                           analyses=analyses, sources=sources)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["contract_violations"], [])
        self.assertIsNotNone(document)


class Gate4NoRuntimeFetchTests(AssembleHarness):
    def test_injected_external_script_hard_fails(self):
        f0 = file_with_headings("f0", "f0.md", [heading(1, "A", "a")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        fragments = {"f0": (
            section_div("f0", "a")
            + '<script src="https://evil.example.com/x.js"></script>'
        )}
        proc, report, document = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(report["external_fetches"])
        self.assertIsNone(document)
        self.assertIn("external_fetches", proc.stderr)

    def test_injected_external_img_hard_fails(self):
        f0 = file_with_headings("f0", "f0.md", [heading(1, "A", "a")])
        structure = {
            "meta": {"root": ".", "output_language": "source", "file_count": 1},
            "files": [f0],
            "graph": {"nodes": [make_node("f0", "f0.md")], "edges": []},
        }
        fragments = {"f0": (
            section_div("f0", "a")
            + '<img src="http://cdn.example.com/logo.png">'
        )}
        proc, report, _ = self._run(structure, fragments=fragments, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(report["external_fetches"])


# ===========================================================================
# Task 6 — END-TO-END against the REAL committed design system
# ===========================================================================

class EndToEndTests(AssembleHarness):
    """Drive assemble.py with the REAL references/design-system.html plus a
    fixture corpus exercising chip / matrix / SVG graph modes, verbatim +
    summarized + merged-heading sections, findings of every severity, and a
    verbatim_critical contract carrying markdown/HTML special chars."""

    def _corpus(self, n_files, edges):
        """Build a structure + matching fragments + analyses + sources that all
        pass the gates. Each file has two headings (overview + detail); one
        file additionally carries a special-char contract."""
        files, nodes, fragments, analyses, sources = [], [], {}, {}, {}
        special_contract = 'If x < 1 && y > 2, MUST return "stop" & set <flag>.'
        for i in range(n_files):
            slug = f"f{i}"
            fp = f"dir{i % 3}/f{i}.md"
            headings = [heading(1, f"File {i}", "overview"),
                        heading(2, "Detail", "detail")]
            files.append(file_with_headings(slug, fp, headings, title=f"File {i}"))
            nodes.append(make_node(slug, fp, title=f"File {i}"))

            # source bytes
            body = f"# File {i}\n\nSummary prose for file {i}.\n\n## Detail\n\nMore detail.\n"
            contracts = []
            contract_html = ""
            if i == 0:
                body += "\n" + special_contract + "\n"
                contracts = [{"text": special_contract,
                              "source_anchor": f"{slug}--detail"}]
                esc = html.escape(special_contract).replace(
                    "&quot;stop&quot;", "&quot;<code>stop</code>&quot;")
                contract_html = f'<div class="mdhv-contract"><p>{esc}</p></div>'
            sources[fp] = body

            # fragment: keypoints essence + verbatim section (overview) +
            # summarized/merged section (detail). A cross-file link to the next
            # file's overview heading exercises cross-file anchor resolution.
            xref = ""
            if n_files > 1:
                nxt = f"f{(i + 1) % n_files}"
                xref = f'<a href="#{nxt}--overview">see next</a>'
            fragments[slug] = (
                f'<div class="mdhv-keypoints">essence of file {i} {xref}</div>'
                f'<section class="mdhv-section" id="{slug}--overview" '
                f'data-src-heading="{slug}--overview"><h3>File {i}</h3>'
                f'<p>verbatim-ish summary</p>{contract_html}</section>'
                f'<section class="mdhv-section" id="{slug}--detail" '
                f'data-src-heading="{slug}--detail"><h3>Detail</h3>'
                f'<details class="mdhv-detail"><summary>more</summary>'
                f'<p>rendered detail</p></details></section>'
            )

            analyses[slug] = {
                "slug": slug, "tldr": f"file {i} tldr",
                "sections": [
                    {"id": f"{slug}--overview", "title": f"File {i}",
                     "kind": "verbatim_critical", "source_headings": ["overview"]},
                    {"id": f"{slug}--detail", "title": "Detail",
                     "kind": "summarized", "source_headings": ["detail"]},
                ],
                "contracts": contracts,
            }

        structure = {
            "meta": {"session": "2026-05-30_00-00",
                     "generated_at": "2026-05-30T00:00:00Z",
                     "root": "corpus", "output_language": "source",
                     "file_count": n_files},
            "files": files,
            "graph": {"nodes": nodes, "edges": edges},
        }
        return structure, fragments, analyses, sources

    def _all_severities_findings(self, n_files):
        return {"cross_file_findings": [
            {"type": "contradiction", "severity": "high",
             "files": ["f0", "f1"], "title": "High conflict",
             "description": "high desc"},
            {"type": "coverage", "severity": "medium",
             "files": ["f1"], "title": "Medium gap", "description": "med desc"},
            {"type": "signal_noise", "severity": "low",
             "files": ["f0"], "title": "Low noise", "description": "low desc"},
        ]}

    def _read_real_design(self):
        with open(REAL_DESIGN, encoding="utf-8") as fh:
            return fh.read()

    def test_e2e_chip_mode(self):
        # N=3 -> chip strip. All gates pass against the real design system.
        structure, fragments, analyses, sources = self._corpus(
            3, edges=[strong("f0", "f1")])
        findings = self._all_severities_findings(3)
        proc, report, document = self._run(
            structure, findings=findings, fragments=fragments,
            analyses=analyses, sources=sources, design=self._read_real_design())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["graph_mode"], "chips")
        self._assert_clean(report, document)

    def test_e2e_diagram_mode_midsize(self):
        # 4<=N<=8 with edges -> layered flow diagram, clean through all gates
        # against the REAL design system.
        structure, fragments, analyses, sources = self._corpus(
            6, edges=[strong("f0", "f1"), strong("f2", "f3")])
        findings = self._all_severities_findings(6)
        proc, report, document = self._run(
            structure, findings=findings, fragments=fragments,
            analyses=analyses, sources=sources, design=self._read_real_design())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["graph_mode"], "diagram")
        self._assert_clean(report, document)
        self.assertIn("<svg", document)
        self.assertIn("<rect", document)

    def test_e2e_diagram_mode_large(self):
        # N>=9 with edges (incl. a cycle + a weak edge) -> layered diagram.
        edges = [strong("f0", "f1"), strong("f1", "f2"), strong("f2", "f0"),
                 strong("f3", "f1"), weak("f7", "f8")]
        structure, fragments, analyses, sources = self._corpus(10, edges=edges)
        findings = self._all_severities_findings(10)
        proc, report, document = self._run(
            structure, findings=findings, fragments=fragments,
            analyses=analyses, sources=sources, design=self._read_real_design())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["graph_mode"], "diagram")
        self._assert_clean(report, document)
        self.assertIn("<svg", document)

    def _assert_clean(self, report, document):
        self.assertIsNotNone(document, "overview must be written on success")
        for key in ("dangling_anchors", "coverage_gaps",
                    "contract_violations", "external_fetches"):
            self.assertEqual(report[key], [], f"{key} must be empty on a clean run")
        # the real design system's mount markers survive injection
        for marker in ("STRAP", "TOC", "GRAPH", "FINDINGS", "FILES"):
            self.assertIn(f"<!-- MDHV:{marker}:START -->", document)
        # findings of every severity rendered
        self.assertIn("mdhv-severity-high", document)
        self.assertIn("mdhv-severity-medium", document)
        self.assertIn("mdhv-severity-low", document)

    def test_e2e_empty_findings_object_clean_zone3(self):
        # Explicit {"cross_file_findings": []} -> exit 0, empty Zone 3.
        structure, fragments, analyses, sources = self._corpus(
            3, edges=[strong("f0", "f1")])
        proc, report, document = self._run(
            structure, findings={"cross_file_findings": []}, fragments=fragments,
            analyses=analyses, sources=sources, design=self._read_real_design())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["findings"], 0)
        self.assertIsNotNone(document)
        self.assertIn("No cross-file findings", document)

    def test_e2e_missing_findings_key_clean_zone3(self):
        # A findings file that is a bare {} (no cross_file_findings key) -> the
        # assembler tolerates it: exit 0, empty Zone 3.
        structure, fragments, analyses, sources = self._corpus(
            3, edges=[strong("f0", "f1")])
        proc, report, document = self._run(
            structure, fragments=fragments, analyses=analyses, sources=sources,
            design=self._read_real_design(), findings_obj_missing=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["findings"], 0)
        self.assertIsNotNone(document)
        self.assertIn("No cross-file findings", document)

    def test_e2e_special_char_contract_intact(self):
        # Prove the verbatim_critical contract with markdown/HTML special chars
        # survives HTML-escaping through the normalized gate-3 check end to end.
        structure, fragments, analyses, sources = self._corpus(
            3, edges=[strong("f0", "f1")])
        proc, report, document = self._run(
            structure, findings={"cross_file_findings": []}, fragments=fragments,
            analyses=analyses, sources=sources, design=self._read_real_design())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["contract_violations"], [])
        # the escaped contract sits in the page, in a contract callout
        self.assertIn("mdhv-contract", document)
        self.assertIn("&lt;flag&gt;", document)  # HTML-escaped in the page


class ContractPresentTests(unittest.TestCase):
    """gate-3 contract matching accepts a contiguous substring OR an
    order-preserving subsequence within a bounded window (table-derived contracts
    pick salient cells in source order, dropping connectors), but still rejects
    invented words and words scraped from scattered parts of the document."""

    def setUp(self):
        sys.path.insert(0, REPO_ROOT)
        from scripts.assemble import contract_present
        from scripts.constants import normalize
        self.present = contract_present
        self.norm = normalize

    def test_contiguous_substring_passes(self):
        src = self.norm("Tokens MUST expire after 3600 seconds")
        self.assertTrue(self.present(self.norm("MUST expire after 3600"), src))

    def test_table_row_subsequence_passes(self):
        # source row with cells separated by pipes + a code path between cells;
        # the contract keeps the salient cells in order, dropping the middle ones.
        src = self.norm("| spam | matches a muted keyword | home-mixer/filters/x.rs:9 | hard drop |")
        contract = self.norm("spam matches a muted keyword hard drop")
        self.assertTrue(self.present(contract, src))

    def test_invented_word_fails(self):
        src = self.norm("Tokens MUST expire after 3600 seconds")
        self.assertFalse(self.present(self.norm("MUST expire after teleport"), src))

    def test_reordered_fails(self):
        src = self.norm("alpha beta gamma delta")
        self.assertFalse(self.present(self.norm("gamma beta"), src))  # wrong order

    def test_scattered_words_outside_window_fail(self):
        # two words present but separated by a long run -> span exceeds the window.
        src = self.norm("alpha " + ("x " * 60) + "omega")
        self.assertFalse(self.present(self.norm("alpha omega"), src))


class FindingsSectionIdLinkifyTests(AssembleHarness):
    """render_findings turns any real <slug>--<anchor> section id appearing in a
    finding's title/description/claim text into a clickable link to that section,
    shown with the section's human TITLE (not the raw slug). Deterministic; only
    REAL ids are linked; the href always resolves; HTML is escaped."""

    def _struct(self):
        s = make_structure(2)
        s["files"][0]["headings"] = [
            {"level": 1, "text": "Overview", "anchor": "overview"},
            {"level": 2, "text": "Главное ограничение", "anchor": "main-limit"},
        ]
        s["files"][1]["headings"] = [
            {"level": 1, "text": "Stat", "anchor": "overview"},
        ]
        return s

    def _finding(self, **over):
        f = {"type": "signal_noise", "severity": "high", "files": ["f0"],
             "title": "T", "description": "d"}
        f.update(over)
        return {"cross_file_findings": [f]}

    def test_real_section_id_in_description_becomes_titled_link(self):
        _, report, doc = self._run(
            self._struct(),
            findings=self._finding(description="See f0--main-limit for the caveat."),
        )
        self.assertIn('<a href="#f0--main-limit">Главное ограничение</a>', doc)
        for k in ("dangling_anchors", "coverage_gaps", "contract_violations",
                  "external_fetches"):
            self.assertEqual(report[k], [])

    def test_file_section_id_links_with_file_title(self):
        s = self._struct()
        s["files"][1]["title"] = "Stat doc"
        _, _, doc = self._run(
            s, findings=self._finding(description="Compare f1--file overall."))
        self.assertIn('<a href="#f1--file">Stat doc</a>', doc)

    def test_no_id_description_is_plain_escaped_text(self):
        _, _, doc = self._run(
            self._struct(), findings=self._finding(description="Just prose here."))
        self.assertIn(">Just prose here.</p>", doc)

    def test_fake_id_token_is_not_linked(self):
        _, _, doc = self._run(
            self._struct(),
            findings=self._finding(description="Refers to faux--bar nowhere."))
        self.assertNotIn('href="#faux--bar"', doc)
        self.assertIn("faux--bar", doc)

    def test_longest_id_wins_over_prefix(self):
        s = make_structure(1)
        s["files"][0]["headings"] = [
            {"level": 1, "text": "Alpha", "anchor": "a"},
            {"level": 2, "text": "AlphaBeta", "anchor": "a-b"},
        ]
        _, _, doc = self._run(
            s, findings=self._finding(files=["f0"], description="ref f0--a-b done"))
        # the full id matched (prefix f0--a did NOT swallow it)
        self.assertIn('<a href="#f0--a-b">AlphaBeta</a>', doc)

    def test_html_in_description_is_escaped_around_link(self):
        _, _, doc = self._run(
            self._struct(),
            findings=self._finding(description="danger <b>x</b> see f0--main-limit"))
        self.assertIn("&lt;b&gt;", doc)
        self.assertNotIn("<b>x</b>", doc)
        self.assertIn('<a href="#f0--main-limit">Главное ограничение</a>', doc)

    def test_claim_text_section_id_is_linked(self):
        findings = {"cross_file_findings": [{
            "type": "contradiction", "severity": "high", "files": ["f0", "f1"],
            "title": "T", "description": "d",
            "claims": [
                {"file": "f0", "claim": "f0 says per f0--main-limit"},
                {"file": "f1", "claim": "f1 disagrees"},
            ],
        }]}
        _, _, doc = self._run(self._struct(), findings=findings)
        self.assertIn('class="mdhv-finding-compare"', doc)
        self.assertIn('<a href="#f0--main-limit">Главное ограничение</a>', doc)


if __name__ == "__main__":
    unittest.main()
