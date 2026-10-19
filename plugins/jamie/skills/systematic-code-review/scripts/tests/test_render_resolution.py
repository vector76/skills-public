"""Tests for the renderer's link building and reference resolution.

    python -m unittest discover -s scripts/tests     (from the skill folder)

They set the renderer's module state by hand, so no repository or review
directory is needed; only the `markdown` package the renderer imports.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import render_review_html as rr  # noqa: E402

SHA = "a" * 40


def setup(host="github", files=(), lengths=None):
    rr.REVIEW.clear()
    rr.REVIEW.update({"title": "t", "nav_label": "t", "page_prefix": "t", "home_repo": "r",
                      "subprojects": ("", "apps/web/src/"), "dir_roots": ("apps/", "packages/"),
                      "prefix_map": []})
    rr.REPOS.clear()
    rr.REPOS["r"] = {"root": ".", "bases": ("", "apps/web/src/"), "version": "GC" + SHA,
                     "host": host, "links": True,
                     "ado_base": "https://dev.azure.com/org/proj/_git",
                     "github_base": "https://github.com/owner/r"}
    rr.FILE_INDEX.clear()
    for path in files:
        rr.FILE_INDEX.setdefault(path.rsplit("/", 1)[-1], []).append(("r", path))
    rr.AT_PIN.clear()
    rr.LINE_COUNTS.clear()
    for path, n in (lengths or {}).items():
        rr.LINE_COUNTS["r", path] = n
    del rr.UNRESOLVED[:]


def ctx(files=()):
    return {"review": rr.REVIEW, "subproject": "apps/web/src/", "files": list(files)}


class Urls(unittest.TestCase):
    def test_github_single_line_and_range(self):
        setup()
        self.assertEqual(rr.source_url("r", "apps/a.ts", 10),
                         f"https://github.com/owner/r/blob/{SHA}/apps/a.ts#L10")
        self.assertEqual(rr.source_url("r", "apps/a.ts", 10, 12),
                         f"https://github.com/owner/r/blob/{SHA}/apps/a.ts#L10-L12")

    def test_github_markdown_gets_plain_view(self):
        setup()
        self.assertTrue(rr.source_url("r", "docs/x.md", 3).endswith("/docs/x.md?plain=1#L3"))

    def test_devops_line_end_is_exclusive(self):
        setup(host="ado")
        url = rr.source_url("r", "src/x.c", 412, 473)
        self.assertIn(f"version=GC{SHA}", url)
        self.assertIn("&line=412&lineEnd=474&", url)
        self.assertIn("&line=478&lineEnd=479&", rr.source_url("r", "src/x.c", 478))

    def test_continuation_numbers_link_to_the_same_file(self):
        setup(files=("apps/web/src/a.ts",))
        html = rr.linkify_text("apps/web/src/a.ts:12, 14-16", ctx())
        self.assertEqual(html.count('class="srcref"'), 2, html)
        self.assertIn(f"/blob/{SHA}/apps/web/src/a.ts#L12\"", html)
        self.assertIn(f"/blob/{SHA}/apps/web/src/a.ts#L14-L16\"", html)
        self.assertIn(">14-16</a>", html)

    def test_commit_url_per_host(self):
        setup()
        self.assertEqual(rr.commit_url("r", "c" * 40), "https://github.com/owner/r/commit/" + "c" * 40)
        setup(host="ado")
        self.assertEqual(rr.commit_url("r", "c" * 40),
                         "https://dev.azure.com/org/proj/_git/r/commit/" + "c" * 40)


class Resolution(unittest.TestCase):
    ORDERS = "apps/web/src/features/orders/actions.ts"
    INVOICES = "apps/web/src/features/invoices/actions.ts"

    def test_ambiguous_basename_prefers_the_cited_files_directory(self):
        setup(files=(self.INVOICES, self.ORDERS))
        got = rr.resolve("actions.ts", ctx(["apps/web/src/features/orders/server.ts"]))
        self.assertEqual(got, ("r", self.ORDERS))

    def test_ambiguous_basename_prefers_the_cited_file_itself(self):
        setup(files=(self.INVOICES, self.ORDERS))
        self.assertEqual(rr.resolve("actions.ts", ctx([self.ORDERS])), ("r", self.ORDERS))

    def test_files_too_short_for_the_cited_line_are_passed_over(self):
        setup(files=(self.INVOICES, self.ORDERS),
              lengths={self.INVOICES: 120, self.ORDERS: 400})
        got = rr.resolve("actions.ts", dict(ctx(["apps/web/src/components/sidebar.tsx"]), line=300))
        self.assertEqual(got, ("r", self.ORDERS))

    def test_partial_path_matches_as_a_suffix(self):
        setup(files=("apps/web/src/features/invoices/server.ts", "apps/web/src/features/reports/server.ts"))
        self.assertEqual(rr.resolve("invoices/server.ts", ctx()),
                         ("r", "apps/web/src/features/invoices/server.ts"))

    def test_route_folders_with_brackets_match(self):
        setup()
        m = rr.REF_RE.search("see apps/web/src/app/(app)/layout.tsx:48 and orders/[id]/page.tsx:3")
        self.assertEqual(m.group(1), "apps/web/src/app/(app)/layout.tsx")

    def test_unbalanced_opening_bracket_is_peeled_off(self):
        setup(files=("apps/web/src/a.ts",))
        lead, path, target = rr.resolve_ref("(apps/web/src/a.ts", "3", None, ctx())
        self.assertEqual((lead, path, target), ("(", "apps/web/src/a.ts", ("r", "apps/web/src/a.ts")))

    def test_link_keeps_the_bracket_outside(self):
        setup(files=("apps/web/src/a.ts",))
        html = rr.linkify_text("(apps/web/src/a.ts:3)", ctx())
        self.assertTrue(html.startswith('(<a class="srcref"'), html)
        self.assertTrue(html.endswith("</a>)"), html)

    def test_balanced_brackets_stay_in_the_path(self):
        setup(files=("apps/web/src/app/(app)/layout.tsx",))
        lead, path, _ = rr.resolve_ref("apps/web/src/app/(app)/layout.tsx", "48", None, ctx())
        self.assertEqual((lead, path), ("", "apps/web/src/app/(app)/layout.tsx"))


class Unresolved(unittest.TestCase):
    def test_a_reference_that_links_nowhere_is_recorded(self):
        setup(files=("apps/web/src/a.ts",))
        rr.CURRENT_DOC = "findings/U01-x.md"
        html = rr.linkify_text("see apps/web/src/a.ts:3 and gone.ts:9-10 and x.yaml:4", ctx())
        self.assertIn('class="srcref"', html)
        self.assertEqual(rr.UNRESOLVED, [("findings/U01-x.md", "gone.ts:9-10"),
                                         ("findings/U01-x.md", "x.yaml:4")])

    def test_an_unlinked_repo_is_not_a_miss(self):
        setup(files=("apps/web/src/a.ts",))
        rr.REPOS["r"]["links"] = False
        rr.linkify_text("apps/web/src/a.ts:3", ctx())
        self.assertEqual(rr.UNRESOLVED, [])


class Context(unittest.TestCase):
    def test_subproject_tie_goes_to_the_longest_prefix(self):
        setup()
        c = rr.context_for("- **File:** apps/web/src/x.ts:1\n", rr.REVIEW)
        self.assertEqual(c["subproject"], "apps/web/src/")
        self.assertEqual(c["files"], ["apps/web/src/x.ts"])

    def test_empty_subprojects_reads_as_none(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "render.json"), "w", encoding="utf-8") as fh:
                json.dump({"title": "t", "home_repo": "r", "subprojects": [],
                           "repos": {"r": {"root": ".", "links": False}}}, fh)
            rr.load_config(d)
        self.assertEqual(rr.REVIEW["subprojects"], ("",))
        self.assertEqual(rr.context_for("no file lines\n", rr.REVIEW)["subproject"], "")

    def test_finding_files_maps_each_finding_to_its_own_file(self):
        text = ("### U01-1: a\n- **File:** apps/one.ts:5\n- **Severity:** Low\n"
                "### U01-2: b\n- **File:** apps/two.ts:9\n")
        self.assertEqual(rr.finding_files(text), {"U01-1": "apps/one.ts", "U01-2": "apps/two.ts"})


if __name__ == "__main__":
    unittest.main()
