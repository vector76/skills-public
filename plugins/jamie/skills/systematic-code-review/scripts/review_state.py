#!/usr/bin/env python
"""Bookkeeping and checks for a systematic code review, so the orchestrator never
edits backlog.md, state files or findings docs with ad-hoc shell one-liners.

    python review_state.py status <file.md> <ID> [<ID> ...] --set <status> [--lock <RUN.lock>]
    python review_state.py log <file.md> <text> [--lock <RUN.lock>]
    python review_state.py check-unit <review dir> <findings doc>
    python review_state.py check-verify <review dir> <findings doc> [<doc> ...] [--ids ID,ID]
    python review_state.py drift <review dir> [<findings doc> ...]
    python review_state.py extract <review dir> [--json <out.json>]

status   sets the last column of the table rows whose first cell is one of the
         IDs (backlog.md units, the verification queue, a rebase's state.md
         jobs). Statuses are pending | in-progress | done.
log      adds "- <date time>: <text>" as the last entry of the ## Log section,
         wherever that section sits (backlog.md, a rebase's state.md), or at
         the end of a file that has none.
check-unit   a returned reviewer's doc: done only if it has a ## Coverage notes
         section. Prints its findings by severity and any format drift. Exit 1
         when the doc isn't done.
check-verify every Critical, High and Medium finding in the docs (by its
         severity before any verifier changed it) has exactly one Verification
         bullet. --ids limits the check to those findings. Exit 1 when not.
drift    format drift the renderer doesn't flag: a level followed by ":"
         instead of " — ", a missing bullet, an unknown level. Exit 1 on drift.
extract  step 6's programmatic extraction: every finding's fields, the
         cross-checks, and the tables SUMMARY.md is built from.

Every write keeps each file's line endings exactly as they were, line by line,
because a review is read as a diff and a CRLF rewrite turns it into noise.
--lock refreshes the heartbeat line of a RUN.lock file after the write.

Findings are read with review_format.py, the same vocabulary the renderer
uses, so the two never disagree about a level, an ID or a correction marker.
"""

import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

from review_format import (CONFIDENCES, FIELD_RE, FINDING_RE, FIX_IMPACTS, RGRS,
                           SEVERITIES, STATUSES, VERDICTS, current_level, ids_in,
                           level, original_level, split_markers)

REQUIRED = ("File", "Severity", "Confidence", "Category", "Description",
            "Suggested fix", "Fix impact", "RGR", "Status")
JOB_STATUSES = ("pending", "in-progress", "done")


# --- files that keep their line endings ------------------------------------------

def read_lines(path):
    """Lines without their endings, and each line's ending (b"\\r\\n", b"\\n" or b"")."""
    data = open(path, "rb").read()
    parts = data.split(b"\n")
    lines, endings = [], []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if last and part == b"":
            break
        cr = part.endswith(b"\r")
        lines.append((part[:-1] if cr else part).decode("utf-8"))
        endings.append(b"" if last else (b"\r\n" if cr else b"\n"))
    return lines, endings


def write_lines(path, lines, endings):
    with open(path, "wb") as fh:
        for line, end in zip(lines, endings):
            fh.write(line.encode("utf-8") + end)


def dominant_ending(endings):
    counts = collections.Counter(e for e in endings if e)
    return counts.most_common(1)[0][0] if counts else b"\n"


def touch_lock(lock):
    if not lock or not os.path.exists(lock):
        return
    lines, endings = read_lines(lock)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [l for l in lines if not l.startswith("heartbeat:")] + [f"heartbeat: {now}"]
    end = dominant_ending(endings)
    write_lines(lock, lines, [end] * len(lines))


def cmd_status(args):
    if args.set not in JOB_STATUSES:
        sys.exit(f"--set must be one of {', '.join(JOB_STATUSES)}")
    lines, endings = read_lines(args.file)
    found = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\| *([^|]+?) *\|", line)
        if m and m.group(1) in args.ids:
            new = re.sub(r"\| *(?:pending|in-progress|done) *\| *$", f"| {args.set} |", line)
            if new == line:
                sys.exit(f"{args.file}: row {m.group(1)} has no status column to set")
            lines[i] = new
            found.add(m.group(1))
    missing = [i for i in args.ids if i not in found]
    if missing:
        sys.exit(f"{args.file}: no row for {', '.join(missing)}")
    write_lines(args.file, lines, endings)
    touch_lock(args.lock)
    print(f"{', '.join(args.ids)} -> {args.set}")


def log_position(lines):
    """Where a new log entry goes: after the last non-blank line of the
    `## Log` section, so a section added below it, such as the verification
    queue, never collects log entries. The end of the file when there is no
    `## Log`."""
    start = next((i for i, l in enumerate(lines) if l.strip() == "## Log"), None)
    if start is None:
        return len(lines)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
               len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def cmd_log(args):
    lines, endings = read_lines(args.file)
    end = dominant_ending(endings)
    at = log_position(lines)
    if at == len(lines) and endings and not endings[-1]:
        endings[-1] = end
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- {now}: {args.text}"
    lines.insert(at, entry)
    endings.insert(at, end)
    write_lines(args.file, lines, endings)
    touch_lock(args.lock)
    print(entry)


# --- findings --------------------------------------------------------------------


def parse_doc(path):
    lines, _ = read_lines(path)
    findings, cur, key = [], None, None
    for n, line in enumerate(lines, 1):
        if line.startswith("## ") and cur is not None:
            findings.append(cur)
            cur, key = None, None
            continue
        m = FINDING_RE.match(line)
        if m:
            if cur is not None:
                findings.append(cur)
            cur = {"id": m.group(1), "title": m.group(2), "doc": os.path.basename(path),
                   "line": n, "fields": {}, "raw": {}}
            key = None
            continue
        if cur is None:
            continue
        fm = FIELD_RE.match(line)
        if fm:
            key = fm.group(1).strip()
            cur["fields"][key] = fm.group(2).strip()
            cur["raw"].setdefault(key, []).append(n)
        elif key and line.startswith("  ") and line.strip():
            cur["fields"][key] += " " + line.strip()
    if cur is not None:
        findings.append(cur)
    for f in findings:
        enrich(f)
    return findings, lines


def enrich(f):
    # A corrected field reads "<level> — <reason> (verifier: was <old>)", the
    # newest marker first; the "_orig" values are the level before any of them.
    fl = f["fields"]
    sev = fl.get("Severity", "")
    f["sev"] = current_level(sev, SEVERITIES)
    f["sev_orig"] = original_level(sev, SEVERITIES)
    f["conf"] = level(fl.get("Confidence", ""), CONFIDENCES)
    f["category"] = (fl.get("Category", "").split() or [""])[0]
    fix = fl.get("Fix impact", "")
    f["fix"] = current_level(fix, FIX_IMPACTS)
    f["fix_orig"] = original_level(fix, FIX_IMPACTS)
    rgr, rgr_was = split_markers(fl.get("RGR", ""))
    f["rgr"] = level(rgr, RGRS)
    if f["rgr"] == "No" and "partly testable" in rgr:
        f["rgr"] = "No (partly)"
    f["rgr_orig"] = (level(rgr_was[-1], RGRS) if rgr_was else f["rgr"]) or f["rgr"]
    f["verdict"] = level(fl.get("Verification", ""), VERDICTS)
    f["status"] = current_level(fl.get("Status", ""), STATUSES)
    f["deps"] = ids_in(fl.get("Depends on", ""))
    f["harness"] = ""
    if f["rgr"] == "Seam (harness)":
        reason = rgr.split("—", 1)[1] if "—" in rgr else rgr
        f["harness"] = re.split(r"[;:.,(]", reason.strip())[0].strip()


def drift_in(path):
    """Format problems in one doc, as (line, message)."""
    findings, lines = parse_doc(path)
    out = []
    for f in findings:
        for name in REQUIRED:
            if name not in f["fields"]:
                out.append((f["line"], f"{f['id']}: no **{name}:** bullet"))
        for name, choices, key in (("Severity", SEVERITIES, "sev"), ("Confidence", CONFIDENCES, "conf"),
                                   ("Fix impact", FIX_IMPACTS, "fix"), ("Status", STATUSES, "status")):
            if name in f["fields"] and not f[key]:
                out.append((f["raw"][name][0], f"{f['id']}: {name} level not on the scale: {f['fields'][name][:40]!r}"))
        if "RGR" in f["fields"] and not f["rgr"]:
            out.append((f["raw"]["RGR"][0], f"{f['id']}: RGR level not on the scale"))
        for name in ("Fix impact", "RGR", "Verification", "Status"):
            v = f["fields"].get(name, "")
            lv = {"Fix impact": f["fix"], "RGR": f["rgr"].replace(" (partly)", ""),
                  "Verification": f["verdict"], "Status": f["status"]}[name]
            if lv and v[len(lv):len(lv) + 1] == ":":
                out.append((f["raw"][name][0], f"{f['id']}: '{name}: {lv}:' — use '{lv} — <reason>'"))
        for name, ns in f["raw"].items():
            if len(ns) > 1:
                out.append((ns[1], f"{f['id']}: {len(ns)} **{name}:** bullets"))
    if lines and not any(l.startswith("## Coverage notes") for l in lines):
        out.append((len(lines), "no ## Coverage notes section"))
    return out


def docs_of(review_dir, names):
    if names:
        return [n if os.path.isabs(n) else os.path.join(review_dir, "findings", os.path.basename(n))
                for n in names]
    return sorted(glob.glob(os.path.join(review_dir, "findings", "*.md")))


def cmd_check_unit(args):
    path = docs_of(args.review_dir, [args.doc])[0]
    if not os.path.exists(path):
        print(f"{os.path.basename(path)}: NOT DONE (no doc)")
        sys.exit(1)
    findings, lines = parse_doc(path)
    done = any(l.startswith("## Coverage notes") for l in lines)
    counts = collections.Counter(f["sev"] for f in findings)
    print(f"{os.path.basename(path)}: {'done' if done else 'NOT DONE (no Coverage notes)'}; "
          + ", ".join(f"{counts[s]} {s}" for s in SEVERITIES))
    for n, msg in drift_in(path):
        print(f"  drift line {n}: {msg}")
    sys.exit(0 if done else 1)


def cmd_check_verify(args):
    only = set(args.ids.split(",")) if args.ids else None
    bad = 0
    tally = collections.Counter()
    for path in docs_of(args.review_dir, args.docs):
        for f in parse_doc(path)[0]:
            if only is not None and f["id"] not in only:
                continue
            if f["sev_orig"] not in ("Critical", "High", "Medium"):
                continue
            n = len(f["raw"].get("Verification", []))
            if n != 1:
                bad += 1
                print(f"{f['id']}: {n} Verification bullets")
            else:
                tally[f["verdict"] or "?"] += 1
    print("verdicts:", dict(tally), "| problems:", bad)
    sys.exit(1 if bad else 0)


def cmd_drift(args):
    total = 0
    for path in docs_of(args.review_dir, args.docs):
        for n, msg in drift_in(path):
            total += 1
            print(f"{os.path.basename(path)}:{n}: {msg}")
    print(f"{total} drift problem(s)")
    sys.exit(1 if total else 0)


def cmd_extract(args):
    allf = []
    for path in docs_of(args.review_dir, None):
        allf += parse_doc(path)[0]
    unit = [f for f in allf if f["id"].startswith("U")]
    xs = [f for f in allf if f["id"].startswith("X")]
    print(f"unit findings: {len(unit)}   cross-cutting: {len(xs)}")

    need = [f for f in unit if f["sev_orig"] in ("Critical", "High", "Medium")]
    print(f"verdicts: {sum(1 for f in need if f['verdict'])} of {len(need)} non-Low "
          f"(by severity before any verifier change)")
    low_verdicts = [f["id"] for f in unit if f["verdict"] and f["sev_orig"] == "Low"]
    if low_verdicts:
        print("  a verdict on a finding reported as Low:", low_verdicts)
    for name, key in (("fix impact", "fix"), ("RGR", "rgr"), ("status", "status")):
        miss = [f["id"] for f in unit if not f[key]]
        print(f"{name}: {len(unit) - len(miss)} of {len(unit)}" + (f"  MISSING {miss}" if miss else ""))
    off = [f["id"] for f in unit
           if not (f["status"] == "Open" or (f["verdict"] == "REFUTED" and f["status"] == "Not a defect"))]
    print("status other than Open / refuted-Not a defect:", off or "none")

    print("\nCurrent severity: reported / CONFIRMED / REFUTED / UNCERTAIN / unverified")
    for s in SEVERITIES:
        g = [f for f in unit if f["sev"] == s]
        c = collections.Counter(f["verdict"] or "-" for f in g)
        print(f"  {s:8} {len(g):4} {c['CONFIRMED']:4} {c['REFUTED']:4} {c['UNCERTAIN']:4} {c['-']:4}")
    moves = collections.Counter((f["sev_orig"], f["sev"]) for f in unit if f["sev_orig"] != f["sev"])
    verified = sum(1 for f in unit if f["verdict"])
    print(f"severity corrections: {sum(moves.values())} of {verified} verified "
          f"({100 * sum(moves.values()) / verified:.0f}%)" if verified else "severity corrections: n/a",
          dict(moves))
    print("raised:", [f["id"] for f in unit if SEVERITIES.index(f["sev"]) < SEVERITIES.index(f["sev_orig"])]
          if all(f["sev"] and f["sev_orig"] for f in unit) else "(drift: unknown severities)")
    print("fix impact changed:", dict(collections.Counter((f["fix_orig"], f["fix"]) for f in unit if f["fix_orig"] != f["fix"])))
    print("RGR changed:", dict(collections.Counter((f["rgr_orig"], f["rgr"]) for f in unit if f["rgr_orig"] != f["rgr"])))

    live = [f for f in unit if f["verdict"] != "REFUTED"]
    print("\nSeverity x fix impact (refuted out): Trivial Low Uncertain")
    for s in SEVERITIES:
        c = collections.Counter(f["fix"] for f in live if f["sev"] == s)
        print(f"  {s:8} {c['Trivial']:4} {c['Low']:4} {c['Uncertain']:4}")
    conf = [f for f in unit if f["verdict"] == "CONFIRMED"]
    cols = ("Unit", "Seam (local)", "Seam (harness)", "No (partly)", "No")
    print("\nFix readiness (CONFIRMED):", " | ".join(cols))
    for fx in FIX_IMPACTS:
        c = collections.Counter(f["rgr"] for f in conf if f["fix"] == fx)
        print(f"  {fx:9}", [c[r] for r in cols])

    print("\nSeam (harness) by harness name (merge names that mean the same):")
    groups = collections.defaultdict(list)
    for f in unit:
        if f["harness"] and f["verdict"] != "REFUTED":
            groups[f["harness"].lower()].append(f["id"])
    for h, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(ids):3}  {h}  {ids}")
    links = [(f["id"], d) for f in unit for d in f["deps"] if not d.startswith("X-")]
    cross = [(a, b) for a, b in links if a.split("-")[0] != b.split("-")[0]]
    print(f"\nDepends-on links (harness links apart): {len(links)}; cross-unit: {len(cross)}")
    waited = collections.Counter(b for _, b in links)
    print("  most waited on:", waited.most_common(5))
    if args.json:
        slim = [{k: v for k, v in f.items() if k not in ("raw",)} for f in allf]
        json.dump(slim, open(args.json, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"\nwrote {args.json}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status"); p.add_argument("file"); p.add_argument("ids", nargs="+")
    p.add_argument("--set", required=True); p.add_argument("--lock")
    p = sub.add_parser("log"); p.add_argument("file"); p.add_argument("text"); p.add_argument("--lock")
    p = sub.add_parser("check-unit"); p.add_argument("review_dir"); p.add_argument("doc")
    p = sub.add_parser("check-verify"); p.add_argument("review_dir"); p.add_argument("docs", nargs="+")
    p.add_argument("--ids")
    p = sub.add_parser("drift"); p.add_argument("review_dir"); p.add_argument("docs", nargs="*")
    p = sub.add_parser("extract"); p.add_argument("review_dir"); p.add_argument("--json")
    args = ap.parse_args(argv)
    {"status": cmd_status, "log": cmd_log, "check-unit": cmd_check_unit,
     "check-verify": cmd_check_verify, "drift": cmd_drift, "extract": cmd_extract}[args.cmd](args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:])
