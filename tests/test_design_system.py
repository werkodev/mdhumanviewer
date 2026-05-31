"""Self-containment + contract lint for the committed design system.

The committed ``references/design-system.html`` is the shell template that
``scripts/assemble.py`` injects generated zone HTML into. These tests pin it to
the single-source contract in ``scripts/constants.py`` so the schema doc, the
committed design system, and the assembler cannot drift apart.

stdlib unittest only (no pytest), mirroring the v1 test style. Run with:

    python3 -m unittest tests.test_design_system -v
"""

import os
import re
import sys
import unittest

# Make the repo root importable so ``scripts.constants`` resolves regardless of
# the directory unittest is invoked from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.constants import (  # noqa: E402
    ALLOWED_CHROME_ANCHORS,
    REQUIRED_CLASS_HOOKS,
    REQUIRED_MOUNT_MARKERS,
)

DESIGN_SYSTEM_PATH = os.path.join(_REPO_ROOT, "references", "design-system.html")

# Any in-page anchor: href="#..." or href='#...'.
_HREF_HASH_RE = re.compile(r"""href\s*=\s*['"](#[^'"]*)['"]""", re.IGNORECASE)
# id="..." or id='...'.
_ID_RE = re.compile(r"""\bid\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)


def _read_design_system():
    with open(DESIGN_SYSTEM_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


class DesignSystemFileTest(unittest.TestCase):
    def test_file_exists_and_is_a_full_document(self):
        self.assertTrue(
            os.path.isfile(DESIGN_SYSTEM_PATH),
            "references/design-system.html must be committed",
        )
        html = _read_design_system()
        low = html.lower()
        for needle in ("<html", "<head", "<style", "</head>", "<body", "<script", "</body>", "</html>"):
            self.assertIn(needle, low, "design system must be a complete html document; missing %r" % needle)


class HooksAndMarkersTest(unittest.TestCase):
    """(a) Every class hook (as ``.hook``) and every mount marker present."""

    def setUp(self):
        self.html = _read_design_system()

    def test_every_class_hook_is_styled(self):
        for hook in REQUIRED_CLASS_HOOKS:
            self.assertIn(
                "." + hook,
                self.html,
                "design system must define CSS for class hook .%s" % hook,
            )

    def test_every_mount_marker_present(self):
        for marker in REQUIRED_MOUNT_MARKERS:
            self.assertIn(
                marker,
                self.html,
                "design system must contain mount marker %r" % marker,
            )

    def test_mount_markers_in_reading_order_as_pairs(self):
        # STRAP, TOC, GRAPH, FINDINGS, FILES — each START before its END, and
        # zones in reading order.
        positions = [self.html.find(m) for m in REQUIRED_MOUNT_MARKERS]
        for marker, pos in zip(REQUIRED_MOUNT_MARKERS, positions):
            self.assertNotEqual(pos, -1, "missing marker %r" % marker)
        self.assertEqual(
            positions,
            sorted(positions),
            "mount markers must appear in reading order (STRAP, TOC, GRAPH, FINDINGS, FILES)",
        )


class NoRuntimeFetchTest(unittest.TestCase):
    """(b) The design system fetches nothing at runtime."""

    def setUp(self):
        self.html = _read_design_system()
        self.low = self.html.lower()

    def test_no_external_link_tag(self):
        # No <link ...> that pulls a stylesheet / resource over the network.
        for m in re.finditer(r"<link\b[^>]*>", self.low):
            self.assertNotIn(
                "href=", m.group(0),
                "design system must not include an external <link> tag: %r" % m.group(0),
            )

    def test_no_http_src_on_script_or_img(self):
        for tag in re.finditer(r"<(script|img)\b[^>]*>", self.low):
            chunk = tag.group(0)
            self.assertNotRegex(
                chunk,
                r"""src\s*=\s*['"]?\s*https?:""",
                "no <script>/<img> may load an http(s) src: %r" % chunk,
            )

    def test_no_import_of_remote_url(self):
        self.assertNotRegex(
            self.low,
            r"""@import\s+(url\(\s*)?['"]?\s*https?:""",
            "design system must not @import a remote url",
        )

    def test_no_url_http_in_styles(self):
        self.assertNotRegex(
            self.low,
            r"""url\(\s*['"]?\s*https?:""",
            "design system must not reference url(http...) in styles",
        )

    def test_no_remote_svg_image(self):
        # <image href="http..."> / xlink:href inside SVG.
        for tag in re.finditer(r"<image\b[^>]*>", self.low):
            chunk = tag.group(0)
            self.assertNotRegex(
                chunk,
                r"""(xlink:)?href\s*=\s*['"]?\s*https?:""",
                "no remote SVG <image> href permitted: %r" % chunk,
            )

    def test_no_protocol_relative_resource(self):
        # Belt-and-braces: no //cdn-style protocol-relative fetches in src/href.
        self.assertNotRegex(
            self.low,
            r"""(src|href)\s*=\s*['"]\s*//""",
            "design system must not use protocol-relative resource urls",
        )


class ChromeAnchorTest(unittest.TestCase):
    """(c) In-page hrefs are chrome-only; each chrome anchor has a matching id."""

    def setUp(self):
        self.html = _read_design_system()
        self.allowed = set(ALLOWED_CHROME_ANCHORS)
        self.ids = set(_ID_RE.findall(self.html))

    def test_no_inpage_href_outside_allowlist(self):
        hrefs = set(_HREF_HASH_RE.findall(self.html))
        # The bare "#" (no-op) and empty target are tolerated as non-targets.
        bad = {h for h in hrefs if h not in self.allowed and h not in ("#",)}
        self.assertEqual(
            set(),
            bad,
            "static template emits in-page anchor(s) outside ALLOWED_CHROME_ANCHORS: %r" % sorted(bad),
        )

    def test_every_chrome_anchor_has_matching_id(self):
        for anchor in ALLOWED_CHROME_ANCHORS:
            target = anchor.lstrip("#")
            self.assertIn(
                target,
                self.ids,
                "chrome anchor %s has no matching id=%r in the design system" % (anchor, target),
            )

    def test_all_five_chrome_anchors_are_referenced(self):
        # The template should actually exercise its chrome anchors (jump-nav /
        # toc / skip link) so the allowlist is meaningfully covered.
        hrefs = set(_HREF_HASH_RE.findall(self.html))
        for anchor in ALLOWED_CHROME_ANCHORS:
            self.assertIn(
                anchor,
                hrefs,
                "expected the template to reference chrome anchor %s" % anchor,
            )


class CssOnlyHookTest(unittest.TestCase):
    """CSS-only hooks (styled in design-system.html, NOT in REQUIRED_CLASS_HOOKS)
    must still be styled. mdhv-graph-key is the G1 conventions key."""

    def setUp(self):
        self.html = _read_design_system()

    def test_graph_key_is_styled(self):
        # The G1 conventions key is a CSS-only hook (NOT registered in
        # REQUIRED_CLASS_HOOKS) but still needs a style rule.
        self.assertNotIn(
            "mdhv-graph-key", REQUIRED_CLASS_HOOKS,
            "mdhv-graph-key must stay CSS-only (not added to REQUIRED_CLASS_HOOKS)",
        )
        self.assertIn(
            ".mdhv-graph-key", self.html,
            "design system must define CSS for the CSS-only hook .mdhv-graph-key",
        )
        # the hub-swatch variant is also styled so meaning != position alone
        self.assertIn(".mdhv-graph-key-hub", self.html)

    def test_overview_panel_hooks_are_styled(self):
        # R1's at-a-glance panel is a family of CSS-only hooks (NOT registered in
        # REQUIRED_CLASS_HOOKS) that must each carry a style rule.
        for hook in ("mdhv-overview", "mdhv-overview-stat",
                     "mdhv-overview-jump", "mdhv-overview-hubs"):
            self.assertNotIn(
                hook, REQUIRED_CLASS_HOOKS,
                "%s must stay CSS-only (not added to REQUIRED_CLASS_HOOKS)" % hook,
            )
            self.assertIn(
                "." + hook, self.html,
                "design system must define CSS for the CSS-only hook .%s" % hook,
            )
        # the panel prints (a print rule references it) and reflows on mobile.
        self.assertIn("@media print", self.html)
        self.assertRegex(
            self.html,
            r"@media \(max-width: 56rem\)[^@]*\.mdhv-overview",
            "the overview panel must have a <=56rem reflow rule",
        )

    def test_finding_compare_hooks_are_styled(self):
        # A<->B contradiction comparison grid: CSS-only hooks (NOT registered in
        # REQUIRED_CLASS_HOOKS) that must each carry a style rule.
        for hook in ("mdhv-finding-compare", "mdhv-finding-claim"):
            self.assertNotIn(
                hook, REQUIRED_CLASS_HOOKS,
                "%s must stay CSS-only (not added to REQUIRED_CLASS_HOOKS)" % hook,
            )
            self.assertIn(
                "." + hook, self.html,
                "design system must define CSS for the CSS-only hook .%s" % hook,
            )
        # the comparison grid collapses to stacked rows under 56rem.
        self.assertRegex(
            self.html,
            r"@media \(max-width: 56rem\)[^@]*\.mdhv-finding-compare",
            "the comparison grid must have a <=56rem stacked-rows rule",
        )


if __name__ == "__main__":
    unittest.main()
