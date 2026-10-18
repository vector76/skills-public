"""reanchor.py's line arithmetic: where a line number at the old pin lands at
the new one, and whether that landing is exact.

    python -m unittest discover -s scripts/tests     (from the skill folder)

The hand-written cases give the hunks directly. The randomized case edits a
file at random, takes the hunks from `git diff --no-index -U0` of two
temporary files, and checks that every line map_line calls exact holds the
same text at the new number. No repository is touched; without git, that case
is skipped. Needs the systematic-code-review skill beside this one and the
`markdown` package, since reanchor.py imports the renderer.
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import reanchor  # noqa: E402


class MapLine(unittest.TestCase):
    # Hunks are (old start, old count, new start, new count), as `git diff -U0`
    # writes them; a count of 0 is a pure insertion or deletion.

    def test_no_hunks(self):
        self.assertEqual(reanchor.map_line([], 7), (7, True))

    def test_insertion_shifts_later_lines_only(self):
        hs = [(3, 0, 4, 2)]          # two lines inserted after old line 3
        self.assertEqual(reanchor.map_line(hs, 3), (3, True))
        self.assertEqual(reanchor.map_line(hs, 4), (6, True))

    def test_deletion_shifts_back_and_deleted_lines_are_approx(self):
        hs = [(2, 3, 1, 0)]          # old lines 2-4 deleted
        self.assertEqual(reanchor.map_line(hs, 5), (2, True))
        self.assertEqual(reanchor.map_line(hs, 3), (1, False))

    def test_line_inside_a_replaced_hunk_is_approx(self):
        hs = [(10, 3, 10, 5)]        # old 10-12 became new 10-14
        self.assertEqual(reanchor.map_line(hs, 11), (11, False))
        self.assertEqual(reanchor.map_line(hs, 14), (16, True))

    def test_range_that_gains_lines_is_approx(self):
        hs = [(5, 0, 6, 2)]          # two lines inserted after old line 5
        self.assertEqual(reanchor.map_range(hs, 4, 8), (4, 10, False))
        self.assertEqual(reanchor.map_range(hs, 6, 8), (8, 10, True))

    def test_numbers_in_a_reference_tail(self):
        hs = [(1, 0, 2, 3)]          # three lines inserted after old line 1
        self.assertEqual(reanchor.remap_numbers(":12-14, 20", hs), (":15-17, 23", True))

    def test_parse_hunks(self):
        diff = "@@ -3,0 +4,2 @@ ctx\n+a\n+b\n@@ -9 +11 @@\n-x\n+y\n"
        self.assertEqual(reanchor.parse_hunks(diff), [(3, 0, 4, 2), (9, 1, 11, 1)])


@unittest.skipIf(shutil.which("git") is None, "needs git")
class AgainstGitDiff(unittest.TestCase):
    def test_exact_lines_hold_the_same_text(self):
        rnd = random.Random(1)
        d = tempfile.mkdtemp()
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        try:
            for t in range(200):
                old = [f"line {i}" for i in range(rnd.randint(1, 40))]
                new = list(old)
                for _ in range(rnd.randint(1, 6)):
                    k = rnd.randint(0, len(new))
                    op = rnd.choice("idr")
                    if op == "i":
                        new[k:k] = [f"new {t}.{k}.{j}" for j in range(rnd.randint(1, 4))]
                    elif new and op == "d":
                        del new[k:k + rnd.randint(1, 3)]
                    elif new:
                        new[min(k, len(new) - 1)] = f"changed {t}.{k}"
                for path, lines in ((a, old), (b, new)):
                    with open(path, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write("".join(ln + "\n" for ln in lines))
                out = subprocess.run(["git", "diff", "--no-index", "--no-ext-diff",
                                      "--no-color", "-U0", a, b],
                                     capture_output=True, text=True).stdout
                hs = reanchor.parse_hunks(out)
                for n, line in enumerate(old, 1):
                    got, exact = reanchor.map_line(hs, n)
                    if exact:
                        self.assertTrue(1 <= got <= len(new) and new[got - 1] == line,
                                        f"case {t}: old line {n} -> {got}, hunks {hs}")
                    else:
                        self.assertTrue(1 <= got <= max(len(new), 1),
                                        f"case {t}: old line {n} -> {got}, hunks {hs}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
