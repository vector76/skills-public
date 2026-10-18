#!/usr/bin/env python
"""Move a systematic code review's file:line references to a new commit.

    python reanchor.py <review dir> <new commit> --out <dir>            # report only
    python reanchor.py <review dir> <new commit> --out <dir> --write    # and rewrite

Reads the review's render.json for its one repository and its pin, and finds
every reference the renderer would link in findings/*.md, SUMMARY.md and
README.md, resolving each against the pinned commit exactly as the renderer
does. Each line number is then carried through
`git diff -U0 <pin>:<path> <new>:<path>`:

    same     the file did not change
    exact    the file changed, but not on these lines: the number shifts
    approx   a changed hunk covers these lines; mapped to the hunk that
             replaced them, which is a guess for an evaluator to correct
    deleted  the file was deleted between the two commits; left as written
    unresolved  the renderer could not resolve the path at the pin either

Renamed files are followed. --out receives summary.json and one
anchors/<doc>.tsv per document (ID, class, old ref, new ref). --write
rewrites same/exact/approx references in place, then moves render.json's pin,
last, so a run cut short leaves the pin unmoved. It refuses to write anything
unless every file it would change is tracked by git and unmodified, and
render.json names the old pin exactly once.

Read-only against the code repository: only `git diff`, `git ls-tree`,
`git show`, `git rev-parse` and `git merge-base`, none of which may prompt.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(
    os.path.join(HERE, "..", "..", "systematic-code-review", "scripts")))
import render_review_html as rr  # noqa: E402  the renderer's reference parsing

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def git(root, *args, check=True):
    out = subprocess.run(("git", "-c", "core.quotepath=off") + args, cwd=root,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=rr.GIT_ENV)
    if check and out.returncode not in (0, 1):
        sys.exit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out


def changed_paths(root, old, new, rel_review):
    """(renamed {old: new}, deleted set, modified set) between two commits."""
    spec = ["--", "."] + ([f":(exclude){rel_review}"] if rel_review else [])
    out = git(root, "diff", "-M", "--name-status", "-z", old, new, *spec).stdout
    parts = [p for p in out.split("\0")]
    renamed, deleted, modified = {}, set(), set()
    i = 0
    while i < len(parts) and parts[i]:
        st = parts[i]
        if st[0] in "RC":
            src, dst = parts[i + 1], parts[i + 2]
            if st[0] == "R":
                renamed[src] = dst
            i += 3
            continue
        path = parts[i + 1]
        if st[0] == "D":
            deleted.add(path)
        elif st[0] == "M" or st[0] == "T":
            modified.add(path)
        i += 2
    return renamed, deleted, modified


HUNKS = {}


def parse_hunks(diff_text):
    """Sorted (old start, old count, new start, new count) from `git diff -U0`."""
    hs = []
    for m in HUNK_RE.finditer(diff_text):
        a, b, c, d = m.groups()
        hs.append((int(a), 1 if b is None else int(b),
                   int(c), 1 if d is None else int(d)))
    return sorted(hs)


def hunks(root, old, new, old_path, new_path):
    key = (old_path, new_path)
    if key not in HUNKS:
        out = git(root, "diff", "-U0", "--no-color", "--no-ext-diff",
                  f"{old}:{old_path}", f"{new}:{new_path}").stdout
        HUNKS[key] = parse_hunks(out)
    return HUNKS[key]


def map_line(hs, n):
    """(new line, exact) for old line n."""
    shift = 0
    for a, b, c, d in hs:
        if b == 0:                  # pure insertion after old line a
            if a < n:
                shift += d
                continue
            break
        if a + b - 1 < n:           # hunk wholly before n
            shift += d - b
            continue
        if a <= n:                  # n is inside a changed hunk
            return (c + min(n - a, d - 1) if d else max(c, 1)), False
        break
    return n + shift, True


def map_range(hs, s, e):
    ns, ok1 = map_line(hs, s)
    ne, ok2 = map_line(hs, e)
    exact = ok1 and ok2 and (ne - ns) == (e - s)
    return ns, max(ne, ns), exact


def display_path(raw, old_path, new_path):
    """Write a renamed file the way the doc wrote it, if that still fits."""
    raw_n = raw.replace("\\", "/")
    while raw_n.startswith("./"):
        raw_n = raw_n[2:]
    if old_path.endswith(raw_n):
        prefix = old_path[: len(old_path) - len(raw_n)]
        if new_path.startswith(prefix):
            return new_path[len(prefix):]
    return new_path


def remap_numbers(text, hs):
    """Map every line number and range in a ref's tail; (text, exact)."""
    exact = True

    def one(m):
        nonlocal exact
        s = int(m.group(1))
        e = int(m.group(3)) if m.group(3) else None
        if e is None:
            n, ok = map_line(hs, s)
            exact = exact and ok
            return str(n)
        ns, ne, ok = map_range(hs, s, e)
        exact = exact and ok
        return f"{ns}{m.group(2)}{ne}"

    return re.sub(r"(\d+)(?:(\s*-\s*)(\d+))?", one, text), exact


def process_text(text, ctx, root, old, new, changes, where):
    """Rewrite one document's references; returns (new text, [ref records])."""
    renamed, deleted, modified = changes
    records = []

    own = rr.finding_files(text)

    def sub(m):
        raw_path = m.group(1)
        whole = m.group(0)
        tail = whole[len(raw_path):]          # ":12-14, 20"
        fid = where(m.start())
        rec = {"id": fid, "old": whole.strip()}
        # Resolve exactly as the renderer does for this finding: its own file
        # first, the cited line, and a leading bracket peeled off.
        fctx = dict(ctx, files=([own[fid]] if fid in own else []) + list(ctx.get("files", [])))
        lead, raw_path, target = rr.resolve_ref(raw_path, m.group(2), m.group(3), fctx)
        if not target or target[0] != rr.REVIEW["home_repo"]:
            rec.update({"class": "unresolved", "new": rec["old"]})
            records.append(rec)
            return whole
        _, path = target
        if path in deleted:
            rec.update({"class": "deleted", "new": rec["old"]})
            records.append(rec)
            return whole
        new_path = renamed.get(path, path)
        if path not in renamed and path not in modified:
            rec.update({"class": "same", "new": rec["old"]})
            records.append(rec)
            return whole
        hs = hunks(root, old, new, path, new_path)
        new_tail, exact = remap_numbers(tail, hs)
        shown = display_path(raw_path, path, new_path) if path in renamed else raw_path
        out = lead + shown + new_tail
        rec.update({"class": "exact" if exact else "approx", "new": out.strip()})
        records.append(rec)
        return out

    return rr.REF_RE.sub(sub, text), records


def finding_locator(text):
    """Position -> the finding ID it falls in, '(prose)' outside any finding."""
    marks = []                                   # (offset, label)
    pos = 0
    for line in text.splitlines(keepends=True):
        m = rr.FINDING_RE.match(line.rstrip("\r\n"))
        if m:
            marks.append((pos, m.group(1)))
        elif line.startswith("## "):
            marks.append((pos, "(prose)"))
        pos += len(line)

    def where(offset):
        label = "(prose)"
        for start, lab in marks:
            if start > offset:
                break
            label = lab
        return label

    return where


def statuses(text):
    _, findings, _ = rr.split_findings(text.replace("\r\n", "\n"))
    return {f["id"]: (f["status"] or "Open") for f in findings}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("review_dir")
    ap.add_argument("new")
    ap.add_argument("--out", required=True, help="directory for the report")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    review_dir = os.path.normpath(os.path.abspath(args.review_dir))
    rr.load_config(review_dir)
    if len(rr.REPOS) != 1:
        sys.exit("render.json names more than one repository; rebase one review "
                 "of one repository")
    home = rr.REVIEW["home_repo"]
    repo = rr.REPOS[home]
    root, version = repo["root"], repo["version"]
    if not version.startswith("GC"):
        sys.exit(f"the pin {version!r} is not a commit (GC<sha>)")
    old = version[2:]
    if home not in rr.AT_PIN:
        sys.exit(f"could not read the pinned commit {old[:7]} in {root}")
    r = git(root, "rev-parse", "--verify", "-q", f"{args.new}^{{commit}}")
    new = r.stdout.strip()
    if r.returncode != 0 or not new:
        sys.exit(f"{args.new!r} is not a commit in {root}")
    if new == old:
        sys.exit(f"the review is already pinned to {new[:7]}")
    if git(root, "merge-base", "--is-ancestor", old, new).returncode != 0:
        sys.exit(f"the pin {old[:7]} is not an ancestor of {new[:7]}")

    rel_review = rr.review_rel(root, review_dir)
    changes = changed_paths(root, old, new, rel_review)

    docs = sorted(glob.glob(os.path.join(review_dir, "findings", "*.md")))
    # Outside findings/, references are prose: no finding owns them, and no
    # finding is flagged by them. The renderer links them all the same.
    prose = {os.path.join(review_dir, n): f"({n[:-3].lower()})"
             for n in ("SUMMARY.md", "README.md")
             if os.path.isfile(os.path.join(review_dir, n))}
    docs += list(prose)

    cfg_path = os.path.join(review_dir, "render.json")
    if args.write:
        # Every check comes before the first write, so a refusal leaves
        # nothing to restore.
        with open(cfg_path, encoding="utf-8", newline="") as fh:
            cfg = fh.read()
        if cfg.count(f"GC{old}") != 1:
            sys.exit(f"render.json: expected GC{old} exactly once; nothing written")
        targets = [os.path.relpath(p, review_dir) for p in docs] + ["render.json"]
        tracked = git(review_dir, "ls-files", "-z", "--", *targets).stdout
        tracked = {os.path.normcase(os.path.normpath(p))
                   for p in tracked.split("\0") if p}
        untracked = [t for t in targets
                     if os.path.normcase(os.path.normpath(t)) not in tracked]
        dirty = git(review_dir, "status", "--porcelain", "--", *targets).stdout.strip()
        if untracked or dirty:
            sys.exit("refusing to write: these must be tracked and unmodified: "
                     + ", ".join(untracked or [ln[3:] for ln in dirty.splitlines()]))

    os.makedirs(os.path.join(args.out, "anchors"), exist_ok=True)
    counts, direct, per_doc, new_texts = {}, {}, {}, {}
    for path in docs:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        ctx = rr.context_for(text, rr.REVIEW)
        label = prose.get(path)
        where = (lambda _o: label) if label else finding_locator(text)
        new_text, recs = process_text(text, ctx, root, old, new, changes, where)
        stem = os.path.splitext(os.path.basename(path))[0]
        per_doc[stem] = recs
        new_texts[path] = new_text
        status = {} if label else statuses(text)
        for rec in recs:
            counts[rec["class"]] = counts.get(rec["class"], 0) + 1
            fid = rec["id"]
            if rec["class"] in ("approx", "deleted") and status.get(fid) == "Open":
                worst = direct.get(fid)
                if worst != "deleted":
                    direct[fid] = rec["class"]
        with open(os.path.join(args.out, "anchors", stem + ".tsv"), "w",
                  encoding="utf-8", newline="\n") as fh:
            for rec in recs:
                fh.write(f"{rec['id']}\t{rec['class']}\t{rec['old']}\t{rec['new']}\n")

    renamed, deleted, modified = changes
    report = {
        "old": old, "new": new,
        "changed_files": {"modified": len(modified), "renamed": len(renamed),
                          "deleted": len(deleted)},
        "refs": counts,
        "direct_hits": dict(sorted(direct.items(), key=lambda kv: rr.id_sort_key(kv[0]))),
        "written": False,
    }

    if args.write:
        for path, text in new_texts.items():
            with open(path, encoding="utf-8", newline="") as fh:
                if fh.read() == text:
                    continue
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        with open(cfg_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(cfg.replace(f"GC{old}", f"GC{new}"))
        report["written"] = True

    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    total = sum(counts.values())
    print(f"{old[:7]} -> {new[:7]}: {len(modified)} modified, {len(renamed)} renamed, "
          f"{len(deleted)} deleted files; {total} refs: "
          + ", ".join(f"{counts.get(k, 0)} {k}"
                      for k in ("same", "exact", "approx", "deleted", "unresolved"))
          + f"; {len(direct)} open findings hit directly"
          + ("; written, pin moved" if args.write else ""))


if __name__ == "__main__":
    main()
