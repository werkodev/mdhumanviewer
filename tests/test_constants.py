"""Unit tests for scripts/constants.py — the single-source vocabulary + the
gate-3 ``normalize`` function.

Run from the plugin root:
    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.constants import (  # noqa: E402
    ALLOWED_CHROME_ANCHORS,
    REQUIRED_CLASS_HOOKS,
    REQUIRED_MOUNT_MARKERS,
    normalize,
)


class ConstantsShapeTests(unittest.TestCase):
    def test_vocabularies_are_unique_nonempty(self):
        for name, seq in (
            ("REQUIRED_CLASS_HOOKS", REQUIRED_CLASS_HOOKS),
            ("REQUIRED_MOUNT_MARKERS", REQUIRED_MOUNT_MARKERS),
            ("ALLOWED_CHROME_ANCHORS", ALLOWED_CHROME_ANCHORS),
        ):
            self.assertTrue(seq, f"{name} is empty")
            self.assertEqual(len(seq), len(set(seq)), f"{name} has duplicates")

    def test_mount_markers_are_paired_start_end(self):
        starts = [m for m in REQUIRED_MOUNT_MARKERS if "START" in m]
        ends = [m for m in REQUIRED_MOUNT_MARKERS if "END" in m]
        self.assertEqual(len(starts), len(ends))

    def test_chrome_anchors_are_hash_prefixed(self):
        for a in ALLOWED_CHROME_ANCHORS:
            self.assertTrue(a.startswith("#"))


class NormalizeTests(unittest.TestCase):
    def test_reduces_to_content_word_stream(self):
        # tag strip -> entity unescape -> non-word runs -> single space -> lower.
        # &amp; (->'&') and the escaped <x> are punctuation/symbols -> dropped;
        # the word 'x' survives; output is lowercased.
        self.assertEqual(
            normalize("  do   <code>NOT</code>\n  use &amp; &lt;x&gt; "),
            "do not use x",
        )

    def test_case_insensitive(self):
        # A contract that differs from the source only in letter case must match
        # (the run's 'casing' contract_violations). Case is presentation.
        self.assertEqual(normalize("Quote Tweet"), normalize("quote tweet"))
        self.assertIn(normalize("Quote отдельный head"),
                      normalize("a quote — ОТДЕЛЬНЫЙ Head in the module"))

    def test_tags_removed_but_their_text_kept(self):
        # A real tag is removed wholesale (its name does not leak as a word),
        # while the entity-escaped angle brackets are dropped as punctuation.
        self.assertEqual(normalize("a <code>b</code> c"), "a b c")
        self.assertEqual(normalize("a &lt;b&gt; c"), "a b c")

    def test_idempotent(self):
        once = normalize("the <code>exp</code> claim")
        self.assertEqual(normalize(once), once)

    def test_markdown_backtick_source_converges_with_code_render(self):
        """A markdown source carrying inline code (`exp`) must compare equal to a
        faithful render that emits <code>exp</code>, so a legitimate contract
        referencing a field name does not false-fail gate 3."""
        md_source = "The `exp` claim is a Unix timestamp."
        rendered = "The <code>exp</code> claim is a Unix timestamp."
        self.assertEqual(normalize(md_source), normalize(rendered))
        self.assertEqual(normalize(md_source), "the exp claim is a unix timestamp")

    def test_markdown_emphasis_markers_neutralized(self):
        # *strong*/~~del~~ markers converge with their rendered tag forms.
        self.assertEqual(normalize("a **must** rule"), normalize("a <strong>must</strong> rule"))
        self.assertEqual(normalize("drop ~~this~~"), normalize("drop <del>this</del>"))

    def test_snake_case_identifier_splits_to_words(self):
        # Underscores are SEPARATORS (like - / . :), so a snake_case identifier
        # splits into its words and the surrounding backticks drop. This is what
        # lets a source identifier and its faithful human-readable render
        # converge (see test_snake_case_path_cell_matches_humanized_render).
        self.assertEqual(normalize("set `max_retries` now"), "set max retries now")

    def test_snake_case_path_cell_matches_humanized_render(self):
        # Regression (real run, hold_start.md §2.4 anti-patterns table): a markdown
        # "file" cell `home-mixer/filters/muted_keyword_filter.rs:46-50` and a
        # faithful render that humanizes the snake_case identifier to
        # "muted keyword filter rs 46 50" MUST normalize to the same word stream,
        # so gate 3 / reconcile.py do not false-fail. Before the fix `_` survived,
        # so the source token `muted_keyword_filter` could not contain the render's
        # split words as a subsequence and the contract gate failed.
        source_cell = "`home-mixer/filters/muted_keyword_filter.rs:46-50`"
        render = "muted keyword filter rs 46 50"
        self.assertEqual(
            normalize(source_cell),
            "home mixer filters muted keyword filter rs 46 50",
        )
        self.assertIn(normalize(render), normalize(source_cell))

    def test_markdown_table_row_contract_matches_source(self):
        """The real failure this fix targets: the renderer turns a markdown
        TABLE ROW into prose like 'col1: col2', but the source row is
        '| col1 | col2 |' with pipes. Comparing exact punctuation false-failed
        gate 3; the content-word stream makes the contract a clean substring of
        the source row (pipes, colons, dashes all collapse to spaces)."""
        src_row = "| 1 | a header must precede its body | reject the file |"
        contract = "a header must precede its body: reject the file"
        self.assertIn(normalize(contract), normalize(src_row))

    def test_distorted_word_still_breaks_match(self):
        # The word stream still catches real distortions: a changed/dropped word
        # is NOT a substring of the source.
        src = "Tokens MUST expire after 3600 seconds"
        self.assertIn(normalize("MUST expire after 3600 seconds"), normalize(src))
        self.assertNotIn(normalize("MUST expire after 60 seconds"), normalize(src))   # changed number
        self.assertNotIn(normalize("tokens SHOULD expire"), normalize(src))           # weakened modal

    def test_heredoc_literal_angle_not_treated_as_tag(self):
        # REGRESSION (the real bug, _TAG_RE = <[^>]*> -> <[^<>]*>): the literal
        # text of SKILL.md line 58 contains a heredoc ``python3 <<EOF``. That
        # ``<`` is NOT a tag opener. In a long source, a REAL tag (here a code
        # span ``<code>…</code>``) appears downstream. The OLD ``<[^>]*>`` let the
        # match that starts at the heredoc ``<`` run all the way to the real tag's
        # closing ``>``, DELETING the words in between ("eof", "heredocs", …) --
        # but only on this long source buffer, never on the short contract span,
        # so the two sides of the gate-3 compare diverged ("applied symmetrically"
        # invariant broken). The new body ``[^<>]*`` halts at the real tag's ``<``,
        # so the runaway span never forms and the content words survive.
        src = ("run `python3 -c …` or `python3 <<EOF` heredocs "
               "(or inline <code>python</code> here)")
        n = normalize(src)
        self.assertIn("eof", n)        # OLD regex: deleted (spanned to real tag's '>')
        self.assertIn("heredocs", n)   # OLD regex: deleted (spanned to real tag's '>')
        # The contract span is now a clean word-substring of the longer buffer --
        # the symmetry the old regex broke (short side kept words, long side lost
        # them) is restored.
        self.assertIn(normalize("`python3 <<EOF` heredocs"), n)

    def test_placeholder_angle_pair_is_symmetric(self):
        # SYMMETRY of the ``<slug>--<anchor>`` placeholder literal. A *complete*
        # ``<slug>`` strips cleanly under both regexes; the asymmetry bit when a
        # bare/literal ``<`` (a stray ``<`` in prose, or a placeholder typed
        # without its ``>``) sat upstream of a REAL tag and the old match ate
        # everything up to that tag's ``>``. So compare the contract span against
        # the SAME text embedded in a larger document whose later content includes
        # a genuine tag. Under the old ``<[^>]*>`` the embedded ``<order`` here
        # spanned into the real ``<em>`` tag and dropped words; the two sides
        # diverged. Now the contract normalizes to a clean substring of the doc.
        contract = "ids must keep `<slug>--<anchor>` order"
        document = (contract +
                    " — see the <em>rendered</em> page for the resolved hrefs")
        self.assertIn(normalize(contract), normalize(document))
        # A *complete* ``<slug>``/``<anchor>`` is a well-formed tag and strips on
        # BOTH sides identically (to empty), which is itself symmetric -- the only
        # words that can diverge are the prose AROUND a runaway match, which the
        # new body now protects (asserted above).
        self.assertEqual(normalize("`<slug>--<anchor>`"), "")

    def test_real_tags_and_comments_still_strip(self):
        # PARITY (must still pass): a real, well-formed tag still strips wholesale
        # and an HTML comment strips to nothing -- the fix only narrows the tag
        # BODY (no inner '<'), it does not stop stripping genuine tags.
        self.assertEqual(normalize("a <code>b</code> c"), "a b c")
        self.assertEqual(normalize("x <!-- a comment --> y"), "x y")
        # comment-only input is symmetric to empty
        self.assertEqual(normalize("<!-- only -->"), normalize(""))

    def test_lone_open_angle_keeps_trailing_words(self):
        # EDGE: a lone ``<`` with no following ``>`` (e.g. a "less-than" in prose)
        # must NOT swallow the rest of the line -- neither regex matches without a
        # closing ``>``, so the trailing words stay.
        self.assertEqual(normalize("a < b heredocs"), "a b heredocs")
        # And when a real tag follows, the new body's ``<``-exclusion stops the
        # stray ``<`` from reaching into that tag, so "heredocs" is kept.
        self.assertIn("heredocs", normalize("a < b heredocs <code>c</code>"))


if __name__ == "__main__":
    unittest.main()
