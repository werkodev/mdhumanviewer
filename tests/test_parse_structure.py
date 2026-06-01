"""Tests for scripts/parse_structure.py (Stage S1 — discovery + structure + graph).

Mirrors the v1 unittest + subprocess + tempfile style. Run from the repo root:
    python3 -m unittest tests.test_parse_structure -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "parse_structure.py")

# Import the module directly so the ROOT-relative recovery helper can be
# unit-tested in isolation (alongside the subprocess-driven graph tests).
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import parse_structure  # noqa: E402
from parse_structure import build_graph, name_tokens_in  # noqa: E402


def run(root, *extra_args):
    """Invoke parse_structure.py against `root`, return parsed JSON stdout."""
    proc = subprocess.run(
        [sys.executable, SCRIPT, root, *extra_args],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def write(path, body=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def by_slug(out):
    return {f["slug"]: f for f in out["files"]}


class StructureShapeTests(unittest.TestCase):
    def test_basic_shape_and_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skills", "auth", "SKILL.md"), "# Auth skill\n")
            write(os.path.join(tmp, "refs", "tokens.md"), "# Tokens\n")
            out = run(tmp)
            self.assertEqual(out["meta"]["file_count"], 2)
            self.assertEqual(out["meta"]["output_language"], "source")
            self.assertIn("session", out["meta"])
            self.assertIn("generated_at", out["meta"])
            slugs = set(by_slug(out))
            self.assertEqual(slugs, {"skills-auth-skill", "refs-tokens"})
            for f in out["files"]:
                self.assertEqual(f["token_estimate_method"], "chars/4")
                self.assertGreater(f["token_estimate"], 0)
                self.assertIn("headings", f)
                self.assertIn("links", f)

    def test_links_field_present_outbound_links_absent(self):
        """The field MUST be links[] (canonical); outbound_links must NOT exist."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"),
                  "# A\n[b](./b.md) and https://x.test\n")
            write(os.path.join(tmp, "b.md"), "# B\n")
            out = run(tmp)
            a = by_slug(out)["a"]
            self.assertIn("links", a)
            self.assertNotIn("outbound_links", a)
            # No file record should ever carry the legacy key.
            for f in out["files"]:
                self.assertNotIn("outbound_links", f)

    def test_title_first_h1_else_humanized_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "with-h1.md"), "intro\n# Real Title\n## sub\n")
            write(os.path.join(tmp, "no-h1.md"), "## only a sub\nbody\n")
            out = run(tmp)
            files = by_slug(out)
            self.assertEqual(files["with-h1"]["title"], "Real Title")
            # Humanized filename: no-h1 -> "No h1".
            self.assertEqual(files["no-h1"]["title"], "No h1")

    def test_language_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "en.md"), "# Hello\nThis is plain english prose.\n")
            write(os.path.join(tmp, "ru.md"),
                  "# Привет\nЭто текст на русском языке для проверки определения.\n")
            out = run(tmp)
            files = by_slug(out)
            self.assertEqual(files["en"]["language"], "en")
            self.assertEqual(files["ru"]["language"], "ru")


class SlugTests(unittest.TestCase):
    def test_slug_collision_numbers_every_occurrence(self):
        """Colliding base slugs ALL get a numeric suffix -1/-2 (no clean first)."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a", "foo-bar.md"), "# 1\n")
            write(os.path.join(tmp, "a", "foo!bar.md"), "# 2\n")
            out = run(tmp)
            slugs = sorted(f["slug"] for f in out["files"])
            self.assertEqual(slugs, ["a-foo-bar-1", "a-foo-bar-2"])

    def test_unique_slug_stays_bare(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a", "only.md"), "# 1\n")
            out = run(tmp)
            self.assertEqual(out["files"][0]["slug"], "a-only")


class ExcludeTests(unittest.TestCase):
    def test_default_excludes_skip_noise_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "good.md"), "# good\n")
            write(os.path.join(tmp, ".git", "noise.md"), "noise\n")
            write(os.path.join(tmp, ".claude", "x.md"), "x\n")
            write(os.path.join(tmp, "node_modules", "y.md"), "y\n")
            write(os.path.join(tmp, ".mdHumanViewer", "s", "z.md"), "z\n")
            write(os.path.join(tmp, ".mdHumanViewer", "s", "w.md"), "w\n")
            out = run(tmp)
            paths = {f["file_path"] for f in out["files"]}
            self.assertEqual(paths, {"good.md"})

    def test_custom_exclude_replaces_defaults(self):
        """--exclude overrides defaults entirely: noise dirs are no longer skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "kept.md"), "# kept\n")
            write(os.path.join(tmp, "dropme", "x.md"), "x\n")
            write(os.path.join(tmp, ".git", "leak.md"), "leak\n")  # normally skipped
            out = run(tmp, "--exclude", "dropme")
            paths = {f["file_path"] for f in out["files"]}
            self.assertIn("kept.md", paths)
            self.assertNotIn("dropme/x.md", paths)
            self.assertIn(".git/leak.md", paths)


class HeadingTests(unittest.TestCase):
    def test_anchor_dedup_within_file(self):
        """Duplicate headings within one file: first bare, then -1, -2."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "doc.md"),
                  "# Top\n## Overview\nbody\n## Overview\nmore\n## Overview\nlast\n")
            out = run(tmp)
            anchors = [h["anchor"] for h in out["files"][0]["headings"]]
            self.assertEqual(
                anchors, ["top", "overview", "overview-1", "overview-2"])

    def test_headings_skip_fenced_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "doc.md"),
                  "# Real\n```\n# not a heading\n```\n## After\n")
            out = run(tmp)
            anchors = [h["anchor"] for h in out["files"][0]["headings"]]
            self.assertEqual(anchors, ["real", "after"])

    def test_heading_levels_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "doc.md"), "# H1\n### H3 here\n")
            out = run(tmp)
            heads = out["files"][0]["headings"]
            self.assertEqual(heads[0], {"level": 1, "text": "H1", "anchor": "h1"})
            self.assertEqual(heads[1]["level"], 3)
            self.assertEqual(heads[1]["anchor"], "h3-here")


class LinkTests(unittest.TestCase):
    def test_link_classification_and_root_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skills", "auth", "SKILL.md"),
                  "# Auth\n"
                  "[refs](../../refs/tokens.md)\n"
                  "[ext](https://oauth.net)\n"
                  "[code](src/auth.go)\n"
                  "[img](./logo.png)\n")
            write(os.path.join(tmp, "refs", "tokens.md"), "# Tokens\n")
            out = run(tmp)
            auth = by_slug(out)["skills-auth-skill"]
            by_type = {}
            for ln in auth["links"]:
                by_type.setdefault(ln["type"], []).append(ln)
            # relative_md normalized to ROOT.
            self.assertEqual(len(by_type["relative_md"]), 1)
            self.assertEqual(by_type["relative_md"][0]["target"], "refs/tokens.md")
            self.assertEqual(len(by_type["external_url"]), 1)
            self.assertEqual(by_type["external_url"][0]["target"], "https://oauth.net")
            self.assertTrue(any(l["target"] == "src/auth.go" for l in by_type.get("code_ref", [])))
            # png is neither md nor a clean code path of interest -> "other".
            self.assertTrue(any(l["type"] == "other" for l in auth["links"]))


class FrontmatterTests(unittest.TestCase):
    def test_frontmatter_parsed_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skill.md"),
                  "---\nname: auth\ndescription: Handles auth\n---\n# Auth\nbody\n")
            out = run(tmp)
            f = out["files"][0]
            self.assertIn("frontmatter", f)
            self.assertEqual(f["frontmatter"]["name"], "auth")
            self.assertEqual(f["frontmatter"]["description"], "Handles auth")
            # The title comes from the H1 below the front matter.
            self.assertEqual(f["title"], "Auth")

    def test_frontmatter_omitted_when_absent(self):
        """Absent front matter -> the field is omitted entirely (never an empty {})."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "plain.md"), "# Plain\nbody\n")
            out = run(tmp)
            self.assertNotIn("frontmatter", out["files"][0])


class GraphTests(unittest.TestCase):
    def test_nodes_one_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# A\n")
            write(os.path.join(tmp, "b.md"), "# B\n")
            out = run(tmp)
            nodes = out["graph"]["nodes"]
            self.assertEqual(len(nodes), 2)
            for n in nodes:
                self.assertIn("slug", n)
                self.assertIn("title", n)
                self.assertIn("file_path", n)

    def test_strong_edge_and_resolved_slug(self):
        """A relative_md link whose ROOT-normalized target matches another
        file's path -> strong edge AND that link's resolved_slug filled."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skills", "auth.md"),
                  "# Auth\n[tokens](../refs/tokens.md)\n")
            write(os.path.join(tmp, "refs", "tokens.md"), "# Tokens\n")
            out = run(tmp)
            files = by_slug(out)
            auth = files["skills-auth"]
            rel = [l for l in auth["links"] if l["type"] == "relative_md"][0]
            self.assertEqual(rel["target"], "refs/tokens.md")
            self.assertEqual(rel["resolved_slug"], "refs-tokens")
            strong = [e for e in out["graph"]["edges"] if e["strength"] == "strong"]
            self.assertEqual(len(strong), 1)
            self.assertEqual(strong[0]["from"], "skills-auth")
            self.assertEqual(strong[0]["to"], "refs-tokens")

    def test_resolved_slug_omitted_when_target_outside_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# A\n[missing](./gone.md)\n")
            out = run(tmp)
            a = by_slug(out)["a"]
            rel = [l for l in a["links"] if l["type"] == "relative_md"][0]
            self.assertNotIn("resolved_slug", rel)
            # No strong edge to a non-existent file.
            self.assertEqual(
                [e for e in out["graph"]["edges"] if e["strength"] == "strong"], [])

    def test_weak_edge_by_name_match_marked_tentative(self):
        """A relative_md link target whose bare name matches another file (but
        whose path does not resolve) -> a WEAK edge marked tentative."""
        with tempfile.TemporaryDirectory() as tmp:
            # Link points at ./session.md (doesn't exist at that path), but a
            # file named session.md exists elsewhere -> weak name match.
            write(os.path.join(tmp, "skills", "auth.md"),
                  "# Auth\n[sess](./session.md)\n")
            write(os.path.join(tmp, "other", "session.md"), "# Session\n")
            out = run(tmp)
            edges = out["graph"]["edges"]
            strong = [e for e in edges if e["strength"] == "strong"]
            weak = [e for e in edges if e["strength"] == "weak"]
            self.assertEqual(strong, [])
            self.assertEqual(len(weak), 1)
            self.assertEqual(weak[0]["from"], "skills-auth")
            self.assertEqual(weak[0]["to"], "other-session")
            self.assertIn("tentative", weak[0]["reason"])

    def test_strong_and_weak_coexist(self):
        """Strong (resolved) + weak (name-only) edges both present in one run."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"),
                  "# A\n[real](./b.md)\n[ghost](./c.md)\n")
            write(os.path.join(tmp, "b.md"), "# B\n")
            write(os.path.join(tmp, "deep", "c.md"), "# C\n")
            out = run(tmp)
            edges = out["graph"]["edges"]
            strong = [e for e in edges if e["strength"] == "strong"]
            weak = [e for e in edges if e["strength"] == "weak"]
            self.assertEqual([(e["from"], e["to"]) for e in strong], [("a", "b")])
            self.assertEqual([(e["from"], e["to"]) for e in weak], [("a", "deep-c")])


class InlineMdRefEdgeTests(unittest.TestCase):
    """Docs cross-reference each other by backtick filename (`SKILL.md`,
    `refs/tokens.md`) far more than by [text](link); those inline references
    are real graph edges (md_ref), not dropped on the floor."""

    def test_inline_md_ref_captured_as_md_ref_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# A\nSee `SKILL.md` for the flow.\n")
            write(os.path.join(tmp, "SKILL.md"), "# Skill\n")
            a = by_slug(run(tmp))["a"]
            md_refs = [l for l in a["links"] if l["type"] == "md_ref"]
            self.assertEqual(len(md_refs), 1)
            self.assertEqual(md_refs[0]["target"], "SKILL.md")

    def test_inline_md_ref_exact_path_is_strong_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"),
                  "# A\nThe contract lives in `refs/tokens.md`.\n")
            write(os.path.join(tmp, "refs", "tokens.md"), "# Tokens\n")
            edges = run(tmp)["graph"]["edges"]
            strong = [e for e in edges if e["strength"] == "strong"]
            self.assertEqual(len(strong), 1)
            self.assertEqual((strong[0]["from"], strong[0]["to"]),
                             ("a", "refs-tokens"))
            self.assertEqual(strong[0]["reason"], "inline reference")

    def test_inline_md_ref_bare_name_is_weak_edge(self):
        # A bare `tokens.md` (no dir) cannot resolve by exact path -> a weak,
        # tentative name match, like the relative-link name match.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# A\nsee `tokens.md`\n")
            write(os.path.join(tmp, "refs", "tokens.md"), "# Tokens\n")
            edges = run(tmp)["graph"]["edges"]
            strong = [e for e in edges if e["strength"] == "strong"]
            weak = [e for e in edges if e["strength"] == "weak"]
            self.assertEqual(strong, [])
            self.assertEqual(len(weak), 1)
            self.assertEqual((weak[0]["from"], weak[0]["to"]), ("a", "refs-tokens"))
            self.assertIn("tentative", weak[0]["reason"])

    def test_inline_non_md_code_ref_makes_no_edge(self):
        # A backtick .py path is a code_ref to a non-node file -> NOT an edge.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# A\nimplemented in `scripts/x.py`\n")
            write(os.path.join(tmp, "b.md"), "# B\n")
            out = run(tmp)
            a = by_slug(out)["a"]
            self.assertTrue(any(l["type"] == "code_ref" for l in a["links"]))
            self.assertFalse(any(l["type"] == "md_ref" for l in a["links"]))
            self.assertEqual(out["graph"]["edges"], [])

    def test_inline_md_ref_self_reference_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# A\nthis very file `a.md` is special\n")
            write(os.path.join(tmp, "b.md"), "# B\n")
            edges = run(tmp)["graph"]["edges"]
            self.assertEqual(edges, [])

    def test_inline_md_ref_does_not_duplicate_a_relative_link_edge(self):
        # Same pair referenced BOTH as a [link](b.md) and inline `b.md`: one
        # strong edge, not two.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"),
                  "# A\n[b](./b.md) and also `b.md`\n")
            write(os.path.join(tmp, "b.md"), "# B\n")
            edges = [e for e in run(tmp)["graph"]["edges"]
                     if (e["from"], e["to"]) == ("a", "b")]
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0]["strength"], "strong")


class RootRelativeInlineRefTests(unittest.TestCase):
    """A backtick (or [](link)) ref written from a *subdirectory* doc is
    frequently meant relative to ROOT, not to the source dir: `references/schemas.md`
    cited from `agents/mdhv-renderer.md` means the repo-root `references/schemas.md`
    (there is no `agents/references/`). build_graph now recovers that ROOT-relative
    reading via `_root_relative_target` as a fallback when the source-dir-relative
    target matches no node, yielding a STRONG edge with a filled `resolved_slug`
    instead of a demoted weak/bare-name match (Finding 3)."""

    def test_subdir_inline_ref_resolves_root_relative_to_strong_edge(self):
        # The exact Finding-3 scenario: an agents/ doc backtick-cites the
        # repo-root references/schemas.md. Source-dir-relative would be
        # `agents/references/schemas.md` (no node); ROOT-relative resolves it.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"),
                  "# Renderer\nThe class vocabulary lives in `references/schemas.md`.\n")
            write(os.path.join(tmp, "references", "schemas.md"), "# Schemas\n")
            out = run(tmp)
            renderer = by_slug(out)["agents-mdhv-renderer"]
            md_refs = [l for l in renderer["links"] if l["type"] == "md_ref"]
            self.assertEqual(len(md_refs), 1)
            # The link's resolved_slug is filled by the ROOT-relative join.
            self.assertEqual(md_refs[0]["resolved_slug"], "references-schemas")
            strong = [e for e in out["graph"]["edges"] if e["strength"] == "strong"]
            self.assertEqual(len(strong), 1)
            self.assertEqual((strong[0]["from"], strong[0]["to"]),
                             ("agents-mdhv-renderer", "references-schemas"))
            self.assertEqual(strong[0]["reason"], "inline reference")
            # And NOT demoted to a weak/bare-name match.
            self.assertEqual(
                [e for e in out["graph"]["edges"] if e["strength"] == "weak"], [])

    def test_multiple_agent_docs_each_get_strong_edge_to_schemas(self):
        # The proven case: both agents/mdhv-renderer.md and agents/mdhv-verifier.md
        # gain a strong edge to references-schemas via the ROOT-relative recovery.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"),
                  "# Renderer\nemit against `references/schemas.md`\n")
            write(os.path.join(tmp, "agents", "mdhv-verifier.md"),
                  "# Verifier\ncheck contracts per `references/schemas.md`\n")
            write(os.path.join(tmp, "references", "schemas.md"), "# Schemas\n")
            out = run(tmp)
            strong_pairs = {
                (e["from"], e["to"])
                for e in out["graph"]["edges"] if e["strength"] == "strong"
            }
            self.assertIn(("agents-mdhv-renderer", "references-schemas"), strong_pairs)
            self.assertIn(("agents-mdhv-verifier", "references-schemas"), strong_pairs)
            for slug in ("agents-mdhv-renderer", "agents-mdhv-verifier"):
                md_refs = [l for l in by_slug(out)[slug]["links"]
                           if l["type"] == "md_ref"]
                self.assertEqual([l["resolved_slug"] for l in md_refs],
                                 ["references-schemas"])

    def test_subdir_relative_md_link_resolves_root_relative_to_strong_edge(self):
        # The same ROOT-relative recovery applies to a [text](file.md) relative_md
        # link from a subdir doc, not only to inline backtick refs.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"),
                  "# Renderer\nSee [schemas](references/schemas.md).\n")
            write(os.path.join(tmp, "references", "schemas.md"), "# Schemas\n")
            out = run(tmp)
            renderer = by_slug(out)["agents-mdhv-renderer"]
            rel = next(l for l in renderer["links"] if l["type"] == "relative_md")
            self.assertEqual(rel["resolved_slug"], "references-schemas")
            strong = [e for e in out["graph"]["edges"] if e["strength"] == "strong"]
            self.assertEqual(len(strong), 1)
            self.assertEqual(strong[0]["reason"], "direct relative link")
            self.assertEqual((strong[0]["from"], strong[0]["to"]),
                             ("agents-mdhv-renderer", "references-schemas"))

    def test_source_relative_match_still_wins_no_root_fallback_needed(self):
        # No regression: when the source-dir-relative reading already matches a
        # node, that match is used directly (the ROOT-relative fallback is only a
        # fallback). agents/a.md citing `agents/b.md` resolves source-relative.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "a.md"),
                  "# A\nsee sibling `agents/b.md`\n")
            write(os.path.join(tmp, "agents", "b.md"), "# B\n")
            out = run(tmp)
            a = by_slug(out)["agents-a"]
            md_refs = [l for l in a["links"] if l["type"] == "md_ref"]
            self.assertEqual(md_refs[0]["resolved_slug"], "agents-b")
            strong = [e for e in out["graph"]["edges"] if e["strength"] == "strong"]
            self.assertEqual(len(strong), 1)
            self.assertEqual((strong[0]["from"], strong[0]["to"]),
                             ("agents-a", "agents-b"))

    def test_source_relative_wins_when_both_source_and_root_targets_exist(self):
        # The ROOT-relative reading is ONLY a fallback. When BOTH a
        # source-dir-relative twin (agents/references/schemas.md) AND a repo-root
        # references/schemas.md exist, a `references/schemas.md` ref from agents/
        # must resolve to the source-dir-relative file, never to the root one.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "a.md"),
                  "# A\nthe vocabulary lives in `references/schemas.md`\n")
            write(os.path.join(tmp, "agents", "references", "schemas.md"),
                  "# Agents schemas\n")
            write(os.path.join(tmp, "references", "schemas.md"),
                  "# Root schemas\n")
            out = run(tmp)
            a = by_slug(out)["agents-a"]
            md_refs = [l for l in a["links"] if l["type"] == "md_ref"]
            self.assertEqual(len(md_refs), 1)
            # Source-dir-relative reading (agents/references/schemas.md) wins,
            # so the resolved slug is the agents-scoped one, NOT the root twin.
            self.assertEqual(md_refs[0]["resolved_slug"], "agents-references-schemas")
            strong = [e for e in out["graph"]["edges"] if e["strength"] == "strong"]
            self.assertEqual(len(strong), 1)
            self.assertEqual((strong[0]["from"], strong[0]["to"]),
                             ("agents-a", "agents-references-schemas"))
            # Crucially, the root-relative fallback did NOT also fire to the
            # repo-root references/schemas.md.
            self.assertNotIn(
                ("agents-a", "references-schemas"),
                {(e["from"], e["to"]) for e in strong})

    def test_root_relative_md_ref_yields_single_edge_no_weak_duplicate(self):
        # A resolved ROOT-relative md_ref must produce EXACTLY ONE edge for the
        # pair (the strong one) — the bare-name twin (references/schemas.md and
        # the node share the name 'schemas') must NOT also spawn a weak
        # 'inline name match' duplicate once the link is strongly resolved.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"),
                  "# Renderer\nemit against `references/schemas.md`\n")
            write(os.path.join(tmp, "references", "schemas.md"), "# Schemas\n")
            out = run(tmp)
            pair_edges = [
                e for e in out["graph"]["edges"]
                if (e["from"], e["to"]) == ("agents-mdhv-renderer", "references-schemas")
            ]
            self.assertEqual(len(pair_edges), 1)
            self.assertEqual(pair_edges[0]["strength"], "strong")
            self.assertEqual(pair_edges[0]["reason"], "inline reference")
            # No weak edge at all for this run (the strong resolution consumed it).
            self.assertEqual(
                [e for e in out["graph"]["edges"] if e["strength"] == "weak"], [])

    def test_root_relative_candidate_matching_no_node_makes_no_strong_edge(self):
        # A subdir backtick ref whose ROOT-relative reading still matches no node
        # (and no bare-name twin exists) yields NO edge at all — the fallback only
        # fires on a real match, it never invents one.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "a.md"),
                  "# A\nmissing `references/nope.md`\n")
            write(os.path.join(tmp, "references", "schemas.md"), "# Schemas\n")
            out = run(tmp)
            a = by_slug(out)["agents-a"]
            md_refs = [l for l in a["links"] if l["type"] == "md_ref"]
            self.assertEqual(len(md_refs), 1)
            self.assertNotIn("resolved_slug", md_refs[0])
            self.assertEqual(out["graph"]["edges"], [])

    def test_bare_filename_ref_is_not_promoted_to_strong_edge(self):
        # A BARE filename (no directory component) from a subdir doc must NOT get
        # the ROOT-relative fallback: it stays a weak/tentative same-name match to
        # a root file, never a false strong "direct link". Guards the over-reach
        # the adversarial review caught.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "pkg", "module.md"),
                  "# Module\nsee `README.md` for setup\n")
            write(os.path.join(tmp, "README.md"), "# Readme\n")
            out = run(tmp)
            m = by_slug(out)["pkg-module"]
            md_refs = [l for l in m["links"] if l["type"] == "md_ref"]
            self.assertEqual(len(md_refs), 1)
            # Bare name is NOT strong-resolved by the fallback...
            self.assertNotIn("resolved_slug", md_refs[0])
            edges = {(e["from"], e["to"]): e["strength"] for e in out["graph"]["edges"]}
            # ...and any edge to the root README stays WEAK, never strong.
            self.assertNotEqual(edges.get(("pkg-module", "readme")), "strong")
            if ("pkg-module", "readme") in edges:
                self.assertEqual(edges[("pkg-module", "readme")], "weak")

    def test_relative_md_link_with_title_resolves_root_relative_strong(self):
        # A markdown link carrying a title — [s](references/schemas.md "Doc") —
        # from a subdir must still resolve ROOT-relative to a STRONG edge: the
        # title must be stripped from the destination before matching. Guards the
        # title-attribute leak the review flagged.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "agents", "a.md"),
                  '# A\nsee [schemas](references/schemas.md "Schema doc")\n')
            write(os.path.join(tmp, "references", "schemas.md"), "# Schemas\n")
            out = run(tmp)
            a = by_slug(out)["agents-a"]
            rel = [l for l in a["links"] if l["type"] == "relative_md"]
            self.assertEqual(len(rel), 1)
            self.assertEqual(rel[0].get("resolved_slug"), "references-schemas")
            strong = {(e["from"], e["to"]) for e in out["graph"]["edges"]
                      if e["strength"] == "strong"}
            self.assertIn(("agents-a", "references-schemas"), strong)

    # ---- direct unit tests of the recovery helper -------------------------- #

    def test_helper_recovers_backtick_token_root_relative(self):
        link = {"raw": "`references/schemas.md`", "target": "agents/references/schemas.md",
                "type": "md_ref"}
        self.assertEqual(parse_structure._root_relative_target(link),
                         "references/schemas.md")

    def test_helper_recovers_markdown_link_token_root_relative(self):
        link = {"raw": "[schemas](references/schemas.md)",
                "target": "agents/references/schemas.md", "type": "relative_md"}
        self.assertEqual(parse_structure._root_relative_target(link),
                         "references/schemas.md")

    def test_helper_strips_fragment_and_normalizes_dot_segments(self):
        link = {"raw": "`./references/../references/schemas.md#x`", "type": "md_ref"}
        self.assertEqual(parse_structure._root_relative_target(link),
                         "references/schemas.md")

    def test_helper_rejects_token_escaping_root(self):
        # A token that climbs above ROOT cannot be a ROOT-relative node key.
        link = {"raw": "`../outside.md`", "type": "md_ref"}
        self.assertIsNone(parse_structure._root_relative_target(link))

    def test_helper_returns_none_when_no_usable_token(self):
        # Bare prose with no backtick path / [](link) yields no candidate.
        self.assertIsNone(parse_structure._root_relative_target({"raw": "see the docs"}))
        self.assertIsNone(parse_structure._root_relative_target({"raw": ""}))
        self.assertIsNone(parse_structure._root_relative_target({}))


class BareNameEdgeTests(unittest.TestCase):
    """Bare kebab-case inline-code tokens (`mdhv-renderer`) that name another
    corpus file by STEM (not a path, no extension) yield a LOWEST-priority weak
    'bare-name match' edge. The hyphen requirement is the core FP guard: a bare
    generic word (`config`, `skill`) that merely collides with a file stem must
    NOT fabricate an edge."""

    def test_bare_token_with_matching_file_yields_weak_tentative_edge(self):
        # skill.md backtick-cites `mdhv-renderer`; agents/mdhv-renderer.md exists
        # -> a weak edge skill -> agents-mdhv-renderer, tentative, exact reason.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skill.md"),
                  "# Skill\nDispatch N `mdhv-renderer` agents in parallel.\n")
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"), "# Renderer\n")
            out = run(tmp)
            edges = out["graph"]["edges"]
            weak = [e for e in edges if e["strength"] == "weak"]
            self.assertEqual(len(weak), 1)
            self.assertEqual((weak[0]["from"], weak[0]["to"]),
                             ("skill", "agents-mdhv-renderer"))
            self.assertEqual(weak[0]["reason"],
                             "bare-name match: 'mdhv-renderer' (tentative)")

    def test_bare_generic_word_no_hyphen_makes_no_edge(self):
        # CORE FP GUARD: a bare `config` (no hyphen) colliding with config.md, and
        # a bare `skill` colliding with skill.md, must produce NO edge.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "config.md"), "# Config\n")
            write(os.path.join(tmp, "skill.md"), "# Skill\n")
            write(os.path.join(tmp, "doc.md"),
                  "# Doc\nSet `config` then run the `skill`.\n")
            out = run(tmp)
            self.assertEqual(out["graph"]["edges"], [])

    def test_hyphenated_token_matching_no_file_stem_makes_no_edge(self):
        # A hyphenated token (`not-a-file`) that matches no file stem -> no edge.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "doc.md"),
                  "# Doc\nThere is no `not-a-file` here.\n")
            write(os.path.join(tmp, "other.md"), "# Other\n")
            out = run(tmp)
            self.assertEqual(out["graph"]["edges"], [])

    def test_hyphenated_identifier_in_prose_not_backticks_makes_no_edge(self):
        # The SAME hyphenated identifier appearing in PROSE (no backticks) is
        # NOT inline code -> no token extracted -> no edge. Inline-code only.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skill.md"),
                  "# Skill\nDispatch the mdhv-renderer agents in parallel.\n")
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"), "# Renderer\n")
            out = run(tmp)
            self.assertEqual(out["graph"]["edges"], [])

    def test_multiword_backtick_token_makes_no_edge(self):
        # A multi-word backtick token (`hello world`) contains whitespace, so it
        # fails the pure-stem STEM_RE boundary -> no match, no edge.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "doc.md"), "# Doc\nsay `hello world` now\n")
            write(os.path.join(tmp, "hello-world.md"), "# HW\n")
            out = run(tmp)
            self.assertEqual(out["graph"]["edges"], [])

    def test_leading_underscore_token_makes_no_edge(self):
        # A leading-underscore token (`_internal-x`) fails STEM_RE (must begin
        # with an alphanumeric) -> no match, no edge.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "doc.md"), "# Doc\nthe `_internal-x` flag\n")
            write(os.path.join(tmp, "_internal-x.md"), "# Internal\n")
            out = run(tmp)
            self.assertEqual(out["graph"]["edges"], [])

    def test_self_reference_bare_token_makes_no_self_loop(self):
        # A file whose own hyphenated stem appears in its own backticks must not
        # produce a self-loop edge.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "mdhv-renderer.md"),
                  "# Renderer\nThis is the `mdhv-renderer` agent itself.\n")
            write(os.path.join(tmp, "other.md"), "# Other\n")
            out = run(tmp)
            self.assertEqual(out["graph"]["edges"], [])

    def test_strong_edge_preempts_duplicate_bare_name_edge(self):
        # A pair connected by a STRONG path edge (a real `agents/mdhv-renderer.md`
        # backtick ref) AND also by a bare `mdhv-renderer` token gets EXACTLY ONE
        # edge — the strong one — never a duplicate weak bare-name edge.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skill.md"),
                  "# Skill\nDispatch `agents/mdhv-renderer.md`; the "
                  "`mdhv-renderer` does the work.\n")
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"), "# Renderer\n")
            out = run(tmp)
            pair_edges = [
                e for e in out["graph"]["edges"]
                if (e["from"], e["to"]) == ("skill", "agents-mdhv-renderer")
            ]
            self.assertEqual(len(pair_edges), 1)
            self.assertEqual(pair_edges[0]["strength"], "strong")
            self.assertEqual(pair_edges[0]["reason"], "inline reference")
            # No weak edge at all for this run.
            self.assertEqual(
                [e for e in out["graph"]["edges"] if e["strength"] == "weak"], [])

    def test_ambiguous_stem_yields_weak_edges_to_both_files(self):
        # Two files share the same hyphenated stem in different dirs; a third doc
        # bare-refs it -> a weak (tentative) edge to BOTH.
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "doc.md"),
                  "# Doc\nThe `mdhv-renderer` agent runs here.\n")
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"), "# A\n")
            write(os.path.join(tmp, "legacy", "mdhv-renderer.md"), "# B\n")
            out = run(tmp)
            weak = [e for e in out["graph"]["edges"] if e["strength"] == "weak"]
            weak_pairs = {(e["from"], e["to"]) for e in weak}
            self.assertEqual(
                weak_pairs,
                {("doc", "agents-mdhv-renderer"), ("doc", "legacy-mdhv-renderer")})
            for e in weak:
                self.assertEqual(
                    e["reason"], "bare-name match: 'mdhv-renderer' (tentative)")

    def test_weak_link_name_match_preempts_bare_name_edge(self):
        # A pair joined by a WEAK link-derived edge (an unresolved `mdhv-renderer.md`
        # inline ref -> "inline name match") AND also by a bare `mdhv-renderer`
        # token must get EXACTLY ONE weak edge that keeps the LINK reason — the
        # bare-name pass runs last/lowest-priority and must neither duplicate nor
        # override it (guards the `pair in weak_seen` check + pass ordering).
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skill.md"),
                  "# Skill\nThe `mdhv-renderer.md` sidecar; the `mdhv-renderer` "
                  "does the work.\n")
            write(os.path.join(tmp, "agents", "mdhv-renderer.md"), "# Renderer\n")
            out = run(tmp)
            pair_edges = [
                e for e in out["graph"]["edges"]
                if (e["from"], e["to"]) == ("skill", "agents-mdhv-renderer")
            ]
            self.assertEqual(len(pair_edges), 1)
            self.assertEqual(pair_edges[0]["strength"], "weak")
            self.assertEqual(
                pair_edges[0]["reason"],
                "inline name match: 'mdhv-renderer' (tentative)")

    def test_build_graph_back_compat_without_name_tokens_arg(self):
        # Calling build_graph(files) with NO name_tokens arg behaves exactly as
        # before: no crash, no bare-name edges (only strong/weak from links).
        files = [
            {"slug": "skill", "file_path": "skill.md", "title": "Skill",
             "token_estimate": 5,
             "links": [{"raw": "`mdhv-renderer`", "target": "mdhv-renderer",
                        "type": "code_ref"}]},
            {"slug": "agents-mdhv-renderer",
             "file_path": "agents/mdhv-renderer.md", "title": "Renderer",
             "token_estimate": 5, "links": []},
        ]
        graph = build_graph(files)
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(graph["edges"], [])

    def test_build_graph_explicit_none_and_absent_slug_noop(self):
        # Explicit name_tokens=None and a stem map keyed by a slug NOT in files[]
        # both produce no bare-name edges and never raise (the `name_tokens or {}`
        # guard + iterating over files, not the map).
        files = [
            {"slug": "skill", "file_path": "skill.md", "title": "Skill",
             "token_estimate": 5, "links": []},
            {"slug": "agents-mdhv-renderer",
             "file_path": "agents/mdhv-renderer.md", "title": "Renderer",
             "token_estimate": 5, "links": []},
        ]
        self.assertEqual(build_graph(files, None)["edges"], [])
        self.assertEqual(
            build_graph(files, {"ghost-slug": {"mdhv-renderer"}})["edges"], [])

    # ---- direct unit tests of name_tokens_in ------------------------------- #

    def test_name_tokens_in_returns_hyphenated_stem(self):
        self.assertEqual(
            name_tokens_in("emit N `mdhv-renderer` agents"), {"mdhv-renderer"})

    def test_name_tokens_in_excludes_non_hyphenated_words(self):
        # `config`/`skill` have no hyphen -> excluded.
        self.assertEqual(name_tokens_in("set `config` then `skill`"), set())

    def test_name_tokens_in_excludes_dotted_path_token(self):
        # `a.md` contains a '.' (not a pure stem) -> excluded by STEM_RE.
        self.assertEqual(name_tokens_in("see `a.md`"), set())

    def test_name_tokens_in_excludes_multiword_token(self):
        # `hello world` contains whitespace -> excluded.
        self.assertEqual(name_tokens_in("say `hello world`"), set())

    def test_name_tokens_in_excludes_leading_underscore_token(self):
        # `_x-y` begins with '_' -> fails STEM_RE (must start alphanumeric).
        self.assertEqual(name_tokens_in("the `_x-y` flag"), set())

    def test_name_tokens_in_excludes_too_short_token(self):
        # `ab` is < 3 chars (and has no hyphen) -> excluded.
        self.assertEqual(name_tokens_in("use `ab` here"), set())

    def test_name_tokens_in_length_gate_isolated(self):
        # The >=3 length gate fires INDEPENDENTLY of the hyphen gate: a 2-char
        # hyphenated token (`a-`, matches STEM_RE, has a hyphen) is still excluded
        # by length, while the 3-char `a-b` is included. Pins the boundary so a
        # regression loosening the bound to >=2 cannot slip through.
        self.assertEqual(name_tokens_in("use `a-` here"), set())
        self.assertEqual(name_tokens_in("use `a-b` here"), {"a-b"})


class GroupTests(unittest.TestCase):
    def test_groups_present_top_level(self):
        """A top-level groups[] array is emitted; meta/files/graph untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "a.md"), "# A\n")
            out = run(tmp)
            self.assertIn("groups", out)
            self.assertIsInstance(out["groups"], list)
            # Existing top-level keys remain.
            self.assertIn("meta", out)
            self.assertIn("files", out)
            self.assertIn("graph", out)

    def test_grouping_by_directory(self):
        """group = dirname(file_path); root-level files land under '.'."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "root.md"), "# Root\n")
            write(os.path.join(tmp, "skills", "auth.md"), "# Auth\n")
            write(os.path.join(tmp, "skills", "session.md"), "# Session\n")
            write(os.path.join(tmp, "refs", "tokens.md"), "# Tokens\n")
            out = run(tmp)
            by_group = {g["group"]: g for g in out["groups"]}
            self.assertEqual(set(by_group), {".", "skills", "refs"})
            self.assertEqual(
                {f["file_path"] for f in by_group["."]["files"]}, {"root.md"})
            self.assertEqual(
                {f["file_path"] for f in by_group["skills"]["files"]},
                {"skills/auth.md", "skills/session.md"})
            self.assertEqual(
                {f["file_path"] for f in by_group["refs"]["files"]},
                {"refs/tokens.md"})

    def test_group_file_record_shape(self):
        """Each group file carries only slug/file_path/title/token_estimate."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skills", "auth.md"),
                  "# Auth\n[x](https://x.test)\n")
            out = run(tmp)
            g = out["groups"][0]
            self.assertEqual(g["group"], "skills")
            rec = g["files"][0]
            self.assertEqual(set(rec), {"slug", "file_path", "title", "token_estimate"})
            self.assertEqual(rec["slug"], "skills-auth")
            self.assertEqual(rec["file_path"], "skills/auth.md")
            self.assertEqual(rec["title"], "Auth")
            self.assertGreater(rec["token_estimate"], 0)
            # The trimmed record must NOT leak headings/links.
            self.assertNotIn("headings", rec)
            self.assertNotIn("links", rec)

    def test_group_file_count_and_summed_token_estimate(self):
        """file_count counts members; token_estimate sums member estimates."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "skills", "a.md"), "# A\n" + "x" * 100)
            write(os.path.join(tmp, "skills", "b.md"), "# B\n" + "y" * 40)
            out = run(tmp)
            g = {grp["group"]: grp for grp in out["groups"]}["skills"]
            self.assertEqual(g["file_count"], 2)
            self.assertEqual(len(g["files"]), 2)
            expected = sum(f["token_estimate"] for f in g["files"])
            self.assertEqual(g["token_estimate"], expected)
            # Cross-check against the full files[] records for the same slugs.
            full = by_slug(out)
            self.assertEqual(
                g["token_estimate"],
                full["skills-a"]["token_estimate"] + full["skills-b"]["token_estimate"])

    def test_groups_sorted_by_group_name_and_files_by_path(self):
        """groups[] sorted by group name; files within a group by file_path."""
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "zed", "z.md"), "# Z\n")
            write(os.path.join(tmp, "alpha", "b.md"), "# B\n")
            write(os.path.join(tmp, "alpha", "a.md"), "# A\n")
            write(os.path.join(tmp, "mid.md"), "# Mid\n")
            out = run(tmp)
            group_names = [g["group"] for g in out["groups"]]
            self.assertEqual(group_names, sorted(group_names))
            self.assertEqual(group_names, [".", "alpha", "zed"])
            alpha = {g["group"]: g for g in out["groups"]}["alpha"]
            paths = [f["file_path"] for f in alpha["files"]]
            self.assertEqual(paths, ["alpha/a.md", "alpha/b.md"])


class UtfAndRootTests(unittest.TestCase):
    def test_skip_non_utf8_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "ok.md"), "# ok\n")
            bad = os.path.join(tmp, "bad.md")
            with open(bad, "wb") as f:
                f.write(b"\xff\xfe not utf-8 \xfa\xfb")
            proc = subprocess.run(
                [sys.executable, SCRIPT, tmp],
                capture_output=True, text=True, check=True,
            )
            out = json.loads(proc.stdout)
            paths = {f["file_path"] for f in out["files"]}
            self.assertEqual(paths, {"ok.md"})
            self.assertIn("Warning", proc.stderr)
            self.assertIn("bad.md", proc.stderr)

    def test_missing_root_exits_nonzero(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "/definitely/does/not/exist/here-xyz"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Error", proc.stderr)

    def test_utf8_json_output_preserves_non_ascii(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "ru.md"), "# Привет мир\nтекст\n")
            proc = subprocess.run(
                [sys.executable, SCRIPT, tmp],
                capture_output=True, text=True, check=True,
            )
            # ensure_ascii=False keeps the cyrillic literal in the raw stdout.
            self.assertIn("Привет", proc.stdout)


if __name__ == "__main__":
    unittest.main()
