"""Smoke tests for scripts/init_session.py.

Run ONLY this module from the plugin root:
    python3 -m unittest tests.test_init_session -v
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "init_session.py")


def run(root, selection_path, *extra_args, check=True):
    proc = subprocess.run(
        [sys.executable, SCRIPT,
         "--selection", selection_path,
         "--root", root,
         *extra_args],
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"init_session failed: {proc.stderr!r}")
    return proc


def run_raw(*args, check=True):
    """Invoke the script with an explicit, raw argv (no implicit --selection)."""
    proc = subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"init_session failed: {proc.stderr!r}")
    return proc


def write_selection(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f)


class InitSessionTests(unittest.TestCase):
    def test_happy_path_creates_session_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [
                {"path": "a/a.md", "slug": "a-a", "token_estimate": 10},
                {"path": "b/b.md", "slug": "b-b", "token_estimate": 20},
            ])
            proc = run(tmp, sel)
            # The last stdout line is the ABSOLUTE, CWD-resolvable session dir.
            session_dir = proc.stdout.strip().splitlines()[-1]
            self.assertTrue(session_dir)
            self.assertTrue(os.path.isabs(session_dir))
            self.assertTrue(os.path.isdir(session_dir))
            # BOTH per-file artifact subdirectories exist.
            self.assertTrue(os.path.isdir(os.path.join(session_dir, "analysis")))
            self.assertTrue(os.path.isdir(os.path.join(session_dir, "fragments")))

            with open(os.path.join(session_dir, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)

            # Folder name shape: yyyy-mm-dd_HH-MM[_n]
            self.assertRegex(manifest["session"], r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(_\d+)?$")
            # created_at is UTC ISO with Z suffix.
            self.assertRegex(manifest["created_at"], r"Z$")
            self.assertEqual(manifest["output_language"], "source")
            self.assertEqual(manifest["root"], tmp)
            self.assertEqual(len(manifest["selected_files"]), 2)
            self.assertEqual(manifest["selected_files"][0],
                             {"path": "a/a.md", "slug": "a-a", "token_estimate": 10})

            # EXACTLY the 7 step keys, all pending.
            expected = {"preflight", "parse", "render", "verify",
                        "crossfile", "assemble", "report"}
            self.assertEqual(set(manifest["steps"].keys()), expected)
            for k in expected:
                self.assertEqual(manifest["steps"][k], {"status": "pending"})

    def test_accepts_discover_shaped_object(self):
        """If the selection file is the full discover/parse output (a dict with
        a `files` key), init_session.py should accept it and use `files`."""
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            with open(sel, "w", encoding="utf-8") as f:
                json.dump({
                    "root": ".",
                    "files": [{"path": "x.md", "slug": "x", "token_estimate": 1}],
                    "groups": [],
                }, f)
            proc = run(tmp, sel)  # just verify it doesn't raise
            session_dir = proc.stdout.strip().splitlines()[-1]
            with open(os.path.join(session_dir, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(len(manifest["selected_files"]), 1)
            self.assertEqual(manifest["selected_files"][0]["slug"], "x")

    def test_accepts_file_path_alias_from_structure_json(self):
        """structure.json's files[] use `file_path`, not `path`. A subset of
        parse_structure.py output must feed straight through without renaming;
        the manifest normalizes it to the canonical `path` key."""
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [
                {"file_path": "a/a.md", "slug": "a-a", "token_estimate": 7},
            ])
            proc = run(tmp, sel)
            session_dir = proc.stdout.strip().splitlines()[-1]
            with open(os.path.join(session_dir, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            entry = manifest["selected_files"][0]
            self.assertEqual(entry["path"], "a/a.md")
            self.assertEqual(entry["slug"], "a-a")
            self.assertEqual(entry["token_estimate"], 7)

    def test_language_tag_carried_into_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"path": "x.md", "slug": "x"}])
            proc = run(tmp, sel, "--language", "ru")
            session_dir = proc.stdout.strip().splitlines()[-1]
            with open(os.path.join(session_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["output_language"], "ru")
            # token_estimate omitted (not null) when the selection did not supply it.
            self.assertNotIn("token_estimate", manifest["selected_files"][0])

    def test_collision_suffix_on_same_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"path": "x.md", "slug": "x"}])
            first = run(tmp, sel).stdout.strip().splitlines()[-1]
            second = run(tmp, sel).stdout.strip().splitlines()[-1]
            self.assertNotEqual(first, second)
            # second must look like <first>_<n>, n >= 2.
            self.assertRegex(second, re.escape(first) + r"_\d+$")

    def test_empty_selection_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [])
            proc = run(tmp, sel, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("non-empty", proc.stderr)

    def test_missing_required_field_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"path": "only-path.md"}])  # no slug
            proc = run(tmp, sel, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("slug", proc.stderr)


def _full_structure():
    """A whole-ROOT structure.json with 4 files; only 2 will be selected."""
    return {
        "meta": {"root": ".", "file_count": 4, "output_language": "source"},
        "files": [
            {"slug": "a", "file_path": "a.md", "title": "A", "headings": [],
             "links": [{"raw": "[b](b.md)", "target": "b.md", "type": "relative_md",
                        "resolved_slug": "b"},
                       {"raw": "[c](c.md)", "target": "c.md", "type": "relative_md",
                        "resolved_slug": "c"}]},
            {"slug": "b", "file_path": "b.md", "title": "B", "headings": [], "links": []},
            {"slug": "c", "file_path": "c.md", "title": "C", "headings": [], "links": []},
            {"slug": "d", "file_path": "d.md", "title": "D", "headings": [], "links": []},
        ],
        "graph": {
            "nodes": [{"slug": s, "title": s.upper(), "file_path": s + ".md"}
                      for s in ("a", "b", "c", "d")],
            "edges": [
                {"from": "a", "to": "b", "strength": "strong", "reason": "link"},
                {"from": "a", "to": "c", "strength": "strong", "reason": "link"},
                {"from": "c", "to": "d", "strength": "weak", "reason": "name"},
            ],
        },
    }


class ScopeStructureTests(unittest.TestCase):
    """init_session.py --structure must write a session structure.json scoped to
    the selection: this is the deterministic step that stops assemble.py's
    coverage gate from failing on unselected (un-rendered) files."""

    def _run_scoped(self, tmp, selected):
        sel = os.path.join(tmp, "selection.json")
        write_selection(sel, selected)
        full = os.path.join(tmp, "structure.full.json")
        with open(full, "w", encoding="utf-8") as f:
            json.dump(_full_structure(), f)
        proc = run(tmp, sel, "--structure", full)
        session_dir = proc.stdout.strip().splitlines()[-1]
        with open(os.path.join(session_dir, "structure.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_scopes_files_and_graph_to_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            scoped = self._run_scoped(tmp, [
                {"path": "a.md", "slug": "a"}, {"path": "b.md", "slug": "b"}])
            self.assertEqual({f["slug"] for f in scoped["files"]}, {"a", "b"})
            self.assertEqual({n["slug"] for n in scoped["graph"]["nodes"]}, {"a", "b"})
            # Only the a->b edge survives (a->c and c->d touch unselected nodes).
            self.assertEqual(scoped["graph"]["edges"],
                             [{"from": "a", "to": "b", "strength": "strong", "reason": "link"}])
            self.assertEqual(scoped["meta"]["file_count"], 2)

    def test_drops_resolved_slug_for_unselected_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            scoped = self._run_scoped(tmp, [
                {"path": "a.md", "slug": "a"}, {"path": "b.md", "slug": "b"}])
            a = next(f for f in scoped["files"] if f["slug"] == "a")
            by_target = {lk["target"]: lk for lk in a["links"]}
            # a->b is selected: resolved_slug kept. a->c is NOT: resolved_slug dropped.
            self.assertEqual(by_target["b.md"].get("resolved_slug"), "b")
            self.assertNotIn("resolved_slug", by_target["c.md"])

    def test_no_structure_flag_writes_no_scoped_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"path": "a.md", "slug": "a"}])
            session_dir = run(tmp, sel).stdout.strip().splitlines()[-1]
            self.assertFalse(os.path.exists(os.path.join(session_dir, "structure.json")))

    def test_scoped_structure_has_no_groups_key(self):
        """groups[] is whole-corpus only; the scoped structure.json must drop it
        so stale whole-corpus groups never leak (review #5)."""
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"path": "a.md", "slug": "a"}])
            full = os.path.join(tmp, "structure.full.json")
            data = _full_structure()
            data["groups"] = [{"group": ".", "files": [], "file_count": 4,
                               "token_estimate": 99}]
            with open(full, "w", encoding="utf-8") as f:
                json.dump(data, f)
            session_dir = run(tmp, sel, "--structure", full).stdout.strip().splitlines()[-1]
            with open(os.path.join(session_dir, "structure.json"), encoding="utf-8") as f:
                scoped = json.load(f)
            self.assertNotIn("groups", scoped)


def _multidir_structure():
    """A whole-ROOT structure.json across several directories, with headings,
    links and token estimates — exercises menu grouping and --select forms."""
    return {
        "meta": {"root": ".", "file_count": 5, "output_language": "source"},
        "files": [
            {"slug": "readme", "file_path": "README.md", "title": "Readme",
             "language": "en", "token_estimate": 100,
             "headings": [{"text": "Intro", "anchor": "intro"}],
             "links": [{"raw": "[g](skills/guide.md)", "target": "skills/guide.md",
                        "type": "relative_md", "resolved_slug": "skills-guide"}]},
            {"slug": "skills-guide", "file_path": "skills/guide.md", "title": "Guide",
             "language": "en", "token_estimate": 200,
             "headings": [{"text": "How", "anchor": "how"}], "links": []},
            {"slug": "skills-tips", "file_path": "skills/tips.md", "title": "Tips",
             "language": "en", "token_estimate": 50,
             "headings": [], "links": []},
            {"slug": "docs-arch", "file_path": "docs/arch.md", "title": "Arch",
             "language": "en", "token_estimate": 300,
             "headings": [], "links": []},
            {"slug": "docs-deep-x", "file_path": "docs/deep/x.md", "title": "Deep X",
             "language": "en", "token_estimate": 30,
             "headings": [], "links": []},
        ],
        "graph": {
            "nodes": [{"slug": "readme", "title": "Readme", "file_path": "README.md"},
                      {"slug": "skills-guide", "title": "Guide",
                       "file_path": "skills/guide.md"},
                      {"slug": "skills-tips", "title": "Tips",
                       "file_path": "skills/tips.md"},
                      {"slug": "docs-arch", "title": "Arch", "file_path": "docs/arch.md"},
                      {"slug": "docs-deep-x", "title": "Deep X",
                       "file_path": "docs/deep/x.md"}],
            "edges": [{"from": "readme", "to": "skills-guide",
                       "strength": "strong", "reason": "link"}],
        },
        "groups": [{"group": ".", "files": [], "file_count": 5, "token_estimate": 680}],
    }


def _write_full(tmp, structure):
    full = os.path.join(tmp, "structure.full.json")
    with open(full, "w", encoding="utf-8") as f:
        json.dump(structure, f)
    return full


class ModeValidationTests(unittest.TestCase):
    """Exactly ONE of --list / --select / --selection must be supplied."""

    def test_no_mode_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_raw("--root", tmp, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("exactly one", proc.stderr.lower())

    def test_two_modes_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = _write_full(tmp, _multidir_structure())
            proc = run_raw("--list", "--select", "all", "--structure", full,
                           "--root", tmp, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("mutually exclusive", proc.stderr.lower())

    def test_selection_plus_select_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"path": "a.md", "slug": "a"}])
            full = _write_full(tmp, _multidir_structure())
            proc = run_raw("--selection", sel, "--select", "all",
                           "--structure", full, "--root", tmp, check=False)
            self.assertNotEqual(proc.returncode, 0)

    def test_list_without_structure_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_raw("--list", "--root", tmp, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("structure", proc.stderr.lower())

    def test_select_without_structure_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_raw("--select", "all", "--root", tmp, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("structure", proc.stderr.lower())


class ListModeTests(unittest.TestCase):
    """--list prints a ready-to-show markdown menu grouped by directory."""

    def test_list_prints_groups_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = _write_full(tmp, _multidir_structure())
            proc = run_raw("--list", "--structure", full, "--root", tmp)
            out = proc.stdout
            # No session created.
            self.assertFalse(os.path.isdir(os.path.join(tmp, ".mdHumanViewer")))
            # Group headings present, sorted by dir name: '.', 'docs', 'docs/deep', 'skills'.
            heads = [ln for ln in out.splitlines() if ln.startswith("## ")]
            groups_in_order = [h.split(" — ")[0][3:] for h in heads]
            self.assertEqual(groups_in_order, sorted(groups_in_order))
            self.assertIn(".", groups_in_order)
            self.assertIn("skills", groups_in_order)
            self.assertIn("docs", groups_in_order)
            self.assertIn("docs/deep", groups_in_order)
            # skills group: 2 files, summed token estimate 200 + 50 = 250.
            skills_head = next(h for h in heads if h.startswith("## skills "))
            self.assertIn("2 files", skills_head)
            self.assertIn("250", skills_head)
            self.assertIn("estimate", skills_head)
            # per-file line: slug — title — ~tokens
            self.assertIn("- skills-guide — Guide — ~200 tokens", out)
            self.assertIn("- readme — Readme — ~100 tokens", out)
            # files within a group sorted by path: guide.md before tips.md.
            self.assertLess(out.index("skills-guide"), out.index("skills-tips"))


class SelectModeTests(unittest.TestCase):
    """--select resolves a spec against the full structure and creates the
    session itself, writing the scoped structure + per-file slices."""

    def _select(self, tmp, spec, check=True):
        full = _write_full(tmp, _multidir_structure())
        proc = run_raw("--select", spec, "--structure", full, "--root", tmp,
                       check=check)
        return proc

    def _selected_slugs(self, session_dir):
        with open(os.path.join(session_dir, "structure.json"), encoding="utf-8") as f:
            scoped = json.load(f)
        return {f["slug"] for f in scoped["files"]}

    def test_select_by_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._select(tmp, "readme").stdout.strip().splitlines()[-1]
            self.assertEqual(self._selected_slugs(sd), {"readme"})

    def test_select_by_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._select(tmp, "skills/guide.md").stdout.strip().splitlines()[-1]
            self.assertEqual(self._selected_slugs(sd), {"skills-guide"})

    def test_select_by_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._select(tmp, "skills").stdout.strip().splitlines()[-1]
            self.assertEqual(self._selected_slugs(sd), {"skills-guide", "skills-tips"})

    def test_select_by_directory_recurses(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 'docs' matches docs/arch.md AND docs/deep/x.md (under it).
            sd = self._select(tmp, "docs").stdout.strip().splitlines()[-1]
            self.assertEqual(self._selected_slugs(sd), {"docs-arch", "docs-deep-x"})

    def test_select_by_glob(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._select(tmp, "skills/*").stdout.strip().splitlines()[-1]
            self.assertEqual(self._selected_slugs(sd), {"skills-guide", "skills-tips"})

    def test_select_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._select(tmp, "all").stdout.strip().splitlines()[-1]
            self.assertEqual(
                self._selected_slugs(sd),
                {"readme", "skills-guide", "skills-tips", "docs-arch", "docs-deep-x"})

    def test_select_comma_mix(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._select(tmp, "readme,docs/arch.md").stdout.strip().splitlines()[-1]
            self.assertEqual(self._selected_slugs(sd), {"readme", "docs-arch"})

    def test_select_unknown_token_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._select(tmp, "nope-not-here", check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("nope-not-here", proc.stderr)

    def test_select_writes_manifest_selected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = self._select(tmp, "readme").stdout.strip().splitlines()[-1]
            with open(os.path.join(sd, "manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(len(manifest["selected_files"]), 1)
            entry = manifest["selected_files"][0]
            self.assertEqual(entry["path"], "README.md")
            self.assertEqual(entry["slug"], "readme")
            self.assertEqual(entry["token_estimate"], 100)
            # the 7 manifest step keys survive.
            self.assertEqual(set(manifest["steps"].keys()),
                             {"preflight", "parse", "render", "verify",
                              "crossfile", "assemble", "report"})


class SlicesTests(unittest.TestCase):
    """A slices/<slug>.json is written for every selected file when --structure
    is supplied; shape {slug,file_path,title,language,headings,links}; resolved_slug
    pruned for unselected targets; legacy --selection (no --structure) writes none."""

    def test_select_writes_one_slice_per_selected_file_with_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = _write_full(tmp, _multidir_structure())
            sd = run_raw("--select", "readme,skills/guide.md", "--structure", full,
                         "--root", tmp).stdout.strip().splitlines()[-1]
            slices_dir = os.path.join(sd, "slices")
            self.assertTrue(os.path.isdir(slices_dir))
            self.assertEqual(sorted(os.listdir(slices_dir)),
                             ["readme.json", "skills-guide.json"])
            with open(os.path.join(slices_dir, "readme.json"), encoding="utf-8") as f:
                sl = json.load(f)
            self.assertEqual(set(sl.keys()),
                             {"slug", "file_path", "title", "language",
                              "headings", "links"})
            self.assertEqual(sl["slug"], "readme")
            self.assertEqual(sl["file_path"], "README.md")
            self.assertEqual(sl["title"], "Readme")
            self.assertEqual(sl["language"], "en")
            self.assertEqual(sl["headings"], [{"text": "Intro", "anchor": "intro"}])

    def test_slice_resolved_slug_pruned_for_unselected_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = _write_full(tmp, _multidir_structure())
            # readme links to skills-guide; select readme ONLY -> link prunes
            # its resolved_slug (target un-rendered).
            sd = run_raw("--select", "readme", "--structure", full,
                         "--root", tmp).stdout.strip().splitlines()[-1]
            with open(os.path.join(sd, "slices", "readme.json"), encoding="utf-8") as f:
                sl = json.load(f)
            link = sl["links"][0]
            self.assertEqual(link["target"], "skills/guide.md")
            self.assertNotIn("resolved_slug", link)

    def test_slice_resolved_slug_kept_for_selected_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = _write_full(tmp, _multidir_structure())
            sd = run_raw("--select", "readme,skills-guide", "--structure", full,
                         "--root", tmp).stdout.strip().splitlines()[-1]
            with open(os.path.join(sd, "slices", "readme.json"), encoding="utf-8") as f:
                sl = json.load(f)
            self.assertEqual(sl["links"][0].get("resolved_slug"), "skills-guide")

    def test_selection_with_structure_writes_slices(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = _write_full(tmp, _multidir_structure())
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"file_path": "README.md", "slug": "readme"}])
            sd = run(tmp, sel, "--structure", full).stdout.strip().splitlines()[-1]
            self.assertTrue(os.path.isfile(os.path.join(sd, "slices", "readme.json")))

    def test_legacy_selection_without_structure_writes_no_slices(self):
        with tempfile.TemporaryDirectory() as tmp:
            sel = os.path.join(tmp, "selection.json")
            write_selection(sel, [{"path": "a.md", "slug": "a"}])
            sd = run(tmp, sel).stdout.strip().splitlines()[-1]
            self.assertFalse(os.path.exists(os.path.join(sd, "slices")))


if __name__ == "__main__":
    unittest.main()
