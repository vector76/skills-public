"""Tests for the shared finding-format reading, and review_state.py's log.

    python -m unittest discover -s scripts/tests     (from the skill folder)

Standard library only; the log test writes a temporary file.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import review_format as rf  # noqa: E402
import review_state as rs  # noqa: E402


class Markers(unittest.TestCase):
    def test_no_marker(self):
        self.assertEqual(rf.split_markers("High — reason"), ("High — reason", []))

    def test_marker_holding_parentheses(self):
        cur, olds = rf.split_markers("Seam (harness) — needs a fixture (verifier: was Seam (local))")
        self.assertEqual(cur, "Seam (harness) — needs a fixture")
        self.assertEqual(olds, ["Seam (local)"])

    def test_stacked_markers_newest_first(self):
        value = "Medium (verifier: was High) (verifier: was Critical)"
        self.assertEqual(rf.split_markers(value)[1], ["High", "Critical"])
        self.assertEqual(rf.current_level(value, rf.SEVERITIES), "Medium")
        self.assertEqual(rf.original_level(value, rf.SEVERITIES), "Critical")

    def test_ids_ignore_the_marker(self):
        self.assertEqual(rf.ids_in("U12-3 (verifier: was U12-3, U12-5)"), ["U12-3"])
        self.assertEqual(rf.ids_in("none (verifier: was U12-5)"), [])


class Levels(unittest.TestCase):
    def test_longest_level_wins(self):
        self.assertEqual(rf.level("Seam (local) — export it", rf.RGRS), "Seam (local)")
        self.assertEqual(rf.level("Seam — older review", rf.RGRS), "Seam")

    def test_typographic_apostrophe_and_case(self):
        self.assertEqual(rf.level("Won’t fix — accepted", rf.STATUSES), "Won't fix")
        self.assertEqual(rf.level("confirmed — real", rf.VERDICTS), "CONFIRMED")

    def test_unknown_level(self):
        self.assertEqual(rf.level("Severe", rf.SEVERITIES), "")


class Enrich(unittest.TestCase):
    """review_state.py and the renderer read a finding the same way."""

    def finding(self, **fields):
        f = {"fields": fields}
        rs.enrich(f)
        return f

    def test_rgr_correction_keeps_the_seam_kind(self):
        f = self.finding(RGR="Seam (harness) — needs a fixture (verifier: was Seam (local))")
        self.assertEqual((f["rgr_orig"], f["rgr"]), ("Seam (local)", "Seam (harness)"))
        self.assertEqual(f["harness"], "needs a fixture")

    def test_original_severity_is_the_oldest_marker(self):
        f = self.finding(Severity="Medium (verifier: was High) (verifier: was Critical)")
        self.assertEqual((f["sev"], f["sev_orig"]), ("Medium", "Critical"))

    def test_partly_testable_reads_the_current_value_only(self):
        f = self.finding(RGR="Unit — call parse() (verifier: was No — partly testable: x)")
        self.assertEqual(f["rgr"], "Unit")
        f = self.finding(RGR="No — partly testable: test: a; residual: b")
        self.assertEqual(f["rgr"], "No (partly)")

    def test_renderer_agrees(self):
        try:
            import render_review_html as rr
        except ImportError:
            self.skipTest("the renderer needs the markdown package")
        text = ("### U01-1: t\n- **Severity:** Medium (verifier: was High) (verifier: was Critical)\n"
                "- **RGR:** Seam (harness) — fixture (verifier: was Seam (local))\n"
                "- **Depends on:** U01-2 (verifier: was none)\n- **Status:** Won’t fix — ok\n")
        f = rr.split_findings(text)[1][0]
        self.assertEqual((f["severity"], f["sev_revised"]), ("Medium", True))
        self.assertEqual((f["rgr"], f["seam_kind"], f["rgr_revised"]), ("Seam", "harness", True))
        self.assertEqual(f["depends"], ["U01-2"])
        self.assertEqual(f["status"], "Won't fix")


class Log(unittest.TestCase):
    def log(self, text, newline="\n"):
        path = os.path.join(tempfile.mkdtemp(), "backlog.md")
        with open(path, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
        args = type("A", (), {"file": path, "text": "U02 done", "lock": None})()
        with contextlib.redirect_stdout(io.StringIO()):
            rs.cmd_log(args)
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()

    def test_entry_goes_inside_the_log_section(self):
        out = self.log("## Log\n- a\n\n## Verification queue\n| Job | Status |\n| V01 | done |\n")
        lines = out.split("\n")
        self.assertTrue(lines[2].endswith(": U02 done"), out)
        self.assertEqual(lines[-2], "| V01 | done |")

    def test_log_at_the_end_of_the_file(self):
        out = self.log("| ID | Status |\n\n## Log\n- a\n")
        self.assertTrue(out.endswith(": U02 done\n"), out)

    def test_empty_log_section_and_no_trailing_newline(self):
        out = self.log("## Log\n\n## Next")
        self.assertRegex(out, r"^## Log\n- .*: U02 done\n\n## Next$")

    def test_crlf_file_stays_crlf(self):
        out = self.log("## Log\n- a\n", newline="\r\n")
        self.assertNotIn("\n", out.replace("\r\n", ""))

    def test_no_log_section_appends(self):
        out = self.log("# state\n")
        self.assertTrue(out.endswith(": U02 done\n"), out)


if __name__ == "__main__":
    unittest.main()
