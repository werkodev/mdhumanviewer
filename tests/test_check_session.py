"""Tests for scripts/check_session.py.

Builds a temp session directory by hand (no dependency on init_session.py) and
exercises check_session.py end-to-end via subprocess.

Run ONLY this module from the plugin root:
    python3 -m unittest tests.test_check_session -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "check_session.py")


def run(session_dir):
    return subprocess.run(
        [sys.executable, SCRIPT, session_dir],
        capture_output=True, text=True,
    )


def build_session(base, slugs, *, findings=None, write_findings=False):
    """Create a session dir under `base` with manifest + analysis/ + fragments/.

    Returns the session dir path. By default every slug gets a non-empty
    fragment and a parseable analysis JSON. `findings` (when write_findings) is
    json.dumped verbatim into findings.json (so malformed shapes can be tested);
    if `findings` is a str it is written raw (for malformed-JSON cases).
    """
    session_dir = os.path.join(base, "session")
    os.makedirs(os.path.join(session_dir, "analysis"))
    os.makedirs(os.path.join(session_dir, "fragments"))

    manifest = {
        "session": "session",
        "session_dir": session_dir,
        "selected_files": [{"path": f"{s}.md", "slug": s} for s in slugs],
        "steps": {},
    }
    with open(os.path.join(session_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    for s in slugs:
        with open(os.path.join(session_dir, "fragments", s + ".html"),
                  "w", encoding="utf-8") as f:
            f.write(f"<div class='mdhv-section' id='{s}--x'></div>")
        with open(os.path.join(session_dir, "analysis", s + ".json"),
                  "w", encoding="utf-8") as f:
            json.dump({"slug": s, "tldr": "x", "sections": [], "contracts": []}, f)

    if write_findings:
        fpath = os.path.join(session_dir, "findings.json")
        with open(fpath, "w", encoding="utf-8") as f:
            if isinstance(findings, str):
                f.write(findings)
            else:
                json.dump(findings, f)

    return session_dir


class CheckSessionTests(unittest.TestCase):
    def test_ok_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a", "b-b"])
            proc = run(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertTrue(report["ok"])
            self.assertEqual(report["missing_fragments"], [])
            self.assertEqual(report["unparsable_analysis"], [])
            self.assertFalse(report["bad_findings"])

    def test_ok_with_valid_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"],
                               write_findings=True,
                               findings={"cross_file_findings": []})
            proc = run(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertTrue(report["ok"])
            self.assertFalse(report["bad_findings"])

    def test_absent_findings_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"])
            self.assertFalse(os.path.exists(os.path.join(sd, "findings.json")))
            proc = run(sd)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertTrue(report["ok"])
            self.assertFalse(report["bad_findings"])

    def test_missing_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a", "b-b"])
            os.remove(os.path.join(sd, "fragments", "b-b.html"))
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertIn("b-b", report["missing_fragments"])
            self.assertNotIn("a-a", report["missing_fragments"])

    def test_empty_fragment_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"])
            # Truncate the fragment to zero bytes.
            open(os.path.join(sd, "fragments", "a-a.html"), "w").close()
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertIn("a-a", report["missing_fragments"])

    def test_unparsable_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a", "b-b"])
            with open(os.path.join(sd, "analysis", "b-b.json"),
                      "w", encoding="utf-8") as f:
                f.write("{ this is not valid json ]")
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertIn("b-b", report["unparsable_analysis"])
            self.assertNotIn("a-a", report["unparsable_analysis"])

    def test_missing_analysis_file_counts_as_unparsable(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"])
            os.remove(os.path.join(sd, "analysis", "a-a.json"))
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertIn("a-a", report["unparsable_analysis"])

    def test_malformed_findings_bare_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"],
                               write_findings=True, findings=[])
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertTrue(report["bad_findings"])
            # The per-file artifacts are fine; only findings is bad.
            self.assertEqual(report["missing_fragments"], [])
            self.assertEqual(report["unparsable_analysis"], [])

    def test_malformed_findings_missing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"],
                               write_findings=True, findings={"other": 1})
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertTrue(report["bad_findings"])

    def test_malformed_findings_key_not_a_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"],
                               write_findings=True,
                               findings={"cross_file_findings": "nope"})
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertTrue(report["bad_findings"])

    def test_malformed_findings_unparsable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = build_session(tmp, ["a-a"],
                               write_findings=True, findings="{ broken json ]")
            proc = run(sd)
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertFalse(report["ok"])
            self.assertTrue(report["bad_findings"])

    def test_missing_manifest_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            # An empty dir with no manifest.json.
            proc = run(tmp)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("manifest.json", proc.stderr)


if __name__ == "__main__":
    unittest.main()
