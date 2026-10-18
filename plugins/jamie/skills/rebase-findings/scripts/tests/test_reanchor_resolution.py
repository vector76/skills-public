"""reanchor.py must resolve every reference exactly as the renderer links it, or
the rebase moves different references from the ones the site shows.

    python -m unittest discover -s scripts/tests     (from the skill folder)

Needs the systematic-code-review skill beside this one (reanchor imports its
renderer) and the `markdown` package. No repository is touched: every case
uses unchanged, renamed-away or deleted paths, which need no git diff.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import reanchor  # noqa: E402
from reanchor import rr  # noqa: E402  the renderer, as reanchor imports it

ORDERS = "apps/web/src/features/orders/actions.ts"
INVOICES = "apps/web/src/features/invoices/actions.ts"


def setup(files, lengths=None):
    rr.REVIEW.clear()
    rr.REVIEW.update({"title": "t", "nav_label": "t", "page_prefix": "t", "home_repo": "r",
                      "subprojects": ("", "apps/web/src/"), "dir_roots": ("apps/", "packages/"),
                      "prefix_map": []})
    rr.REPOS.clear()
    rr.REPOS["r"] = {"root": ".", "bases": ("", "apps/web/src/"), "version": "GC" + "a" * 40,
                     "host": "github", "links": True, "ado_base": "",
                     "github_base": "https://github.com/owner/r"}
    rr.FILE_INDEX.clear()
    for path in files:
        rr.FILE_INDEX.setdefault(path.rsplit("/", 1)[-1], []).append(("r", path))
    rr.LINE_COUNTS.clear()
    for path, n in (lengths or {}).items():
        rr.LINE_COUNTS["r", path] = n


def run(text, deleted=()):
    ctx = rr.context_for(text, rr.REVIEW)
    changes = ({}, set(deleted), set())
    return reanchor.process_text(text, ctx, ".", "old", "new", changes,
                                 reanchor.finding_locator(text))


class SameAsRenderer(unittest.TestCase):
    def test_reference_inside_parentheses_is_resolved(self):
        setup(["apps/web/src/a.ts"])
        out, recs = run("### U01-1: t\n- **File:** apps/web/src/a.ts:1\n"
                        "- **Description:** see (apps/web/src/a.ts:3).\n")
        self.assertEqual([r["class"] for r in recs], ["same", "same"])
        self.assertIn("(apps/web/src/a.ts:3)", out)

    def test_bare_name_resolves_to_the_findings_own_directory(self):
        # Deleting the orders file makes its references "deleted"; a reference
        # resolved to the invoices file instead would stay "same".
        setup([INVOICES, ORDERS])
        text = ("### U01-1: t\n- **File:** apps/web/src/features/orders/server.ts:1\n"
                "- **Description:** `actions.ts:12` writes it.\n")
        _, recs = run(text, deleted=[ORDERS])
        self.assertEqual(recs[-1]["class"], "deleted")

    def test_bare_name_skips_files_too_short_for_the_line(self):
        setup([INVOICES, ORDERS], lengths={INVOICES: 120, ORDERS: 400})
        text = ("### U21-1: t\n- **File:** apps/web/src/components/sidebar.tsx:1\n"
                "- **Description:** calls `actions.ts:300`.\n")
        _, recs = run(text, deleted=[ORDERS])
        self.assertEqual(recs[-1]["class"], "deleted")

    def test_matches_what_the_renderer_links(self):
        setup([INVOICES, ORDERS])
        text = ("### U01-1: t\n- **File:** apps/web/src/features/orders/server.ts:1\n"
                "- **Description:** `actions.ts:12` and (actions.ts:14).\n")
        ctx = dict(rr.context_for(text, rr.REVIEW), files=[
            "apps/web/src/features/orders/server.ts"])
        html = rr.linkify_text(text, ctx)
        self.assertEqual(html.count("features/orders/actions.ts#L"), 2)
        _, recs = run(text, deleted=[ORDERS])
        self.assertEqual([r["class"] for r in recs[1:]], ["deleted", "deleted"])


if __name__ == "__main__":
    unittest.main()
