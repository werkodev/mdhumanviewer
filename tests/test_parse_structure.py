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
