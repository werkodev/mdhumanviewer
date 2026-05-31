"""Smoke tests for scripts/mark_step.py.

Run ONLY this module from the plugin root:
    python3 -m unittest tests.test_mark_step -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "mark_step.py")
INIT_SCRIPT = os.path.join(REPO_ROOT, "scripts", "init_session.py")


def run_mark(session, step, status, *extra_args, check=True):
    proc = subprocess.run(
        [sys.executable, SCRIPT, session, step, status, *extra_args],
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"mark_step failed: {proc.stderr!r}")
    return proc


def make_session(tmp):
    """Build a session dir with a manifest skeleton via init_session.py so the
    step shape matches production exactly. Returns the absolute session dir."""
    sel = os.path.join(tmp, "selection.json")
    with open(sel, "w", encoding="utf-8") as f:
        json.dump([{"path": "x.md", "slug": "x"}], f)
    proc = subprocess.run(
        [sys.executable, INIT_SCRIPT, "--selection", sel, "--root", tmp],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"init_session failed: {proc.stderr!r}")
    return proc.stdout.strip().splitlines()[-1]


def load_manifest(session_dir):
    with open(os.path.join(session_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


class MarkStepTests(unittest.TestCase):
    def test_marks_step_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            proc = run_mark(session, "render", "done")
            self.assertEqual(proc.returncode, 0)
            # Nothing on stdout on success.
            self.assertEqual(proc.stdout.strip(), "")
            manifest = load_manifest(session)
            self.assertEqual(manifest["steps"]["render"], {"status": "done"})
            # Untouched steps stay pending.
            self.assertEqual(manifest["steps"]["parse"], {"status": "pending"})

    def test_meta_key_value_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            run_mark(session, "verify", "failed",
                     "--meta", "reason=missing-heading",
                     "--meta", "slug=stat")
            manifest = load_manifest(session)
            self.assertEqual(
                manifest["steps"]["verify"],
                {"status": "failed", "reason": "missing-heading", "slug": "stat"},
            )

    def test_meta_value_may_contain_equals(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            run_mark(session, "assemble", "done", "--meta", "note=a=b=c")
            manifest = load_manifest(session)
            self.assertEqual(manifest["steps"]["assemble"]["note"], "a=b=c")

    def test_replaces_prior_step_object(self):
        """A second mark replaces the object wholesale (stale meta does not linger)."""
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            run_mark(session, "report", "failed", "--meta", "reason=boom")
            run_mark(session, "report", "done")
            manifest = load_manifest(session)
            self.assertEqual(manifest["steps"]["report"], {"status": "done"})

    def test_other_manifest_fields_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            before = load_manifest(session)
            run_mark(session, "preflight", "running")
            after = load_manifest(session)
            for key in ("session", "session_dir", "created_at", "root",
                        "output_language", "selected_files"):
                self.assertEqual(after[key], before[key])

    def test_unknown_step_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            proc = run_mark(session, "bogus", "done", check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("bogus", proc.stderr)
            # The manifest is left untouched on a rejected step.
            manifest = load_manifest(session)
            self.assertNotIn("bogus", manifest["steps"])

    def test_unknown_status_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            proc = run_mark(session, "render", "in-progress", check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("in-progress", proc.stderr)
            # render stays pending — the bad status was not applied.
            manifest = load_manifest(session)
            self.assertEqual(manifest["steps"]["render"], {"status": "pending"})

    def test_missing_manifest_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "no-session")
            os.makedirs(empty)
            proc = run_mark(empty, "render", "done", check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("manifest", proc.stderr.lower())

    def test_malformed_meta_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(tmp)
            proc = run_mark(session, "render", "done",
                            "--meta", "no-equals-here", check=False)
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
