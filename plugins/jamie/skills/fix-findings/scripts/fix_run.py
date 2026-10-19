#!/usr/bin/env python3
"""State for a fix-findings run: one session fixing findings one at a time.

The session changes the run's state only through this script, so the task
table, the queue, the log and the lock stay consistent, and a fresh session
can continue from them alone. The state lives in
<code root>/.fix-findings/state/, which .git/info/exclude keeps out of git.
The run's lock and, in private mode, its queue live in
<review dir>/fixes/<run>/. Run with -h for the commands.

Standard library only. Every command prints one JSON object on stdout.
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

# Output is JSON with ensure_ascii=False, and tool output can be any Unicode;
# a Windows console or pipe defaults to cp1252, which can't encode it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
# The review skill's scripts, beside this skill in the same plugin. init copies
# what the run needs from there into the run's state/bin.
REVIEW_SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "systematic-code-review", "scripts"))
for _p in (HERE, REVIEW_SCRIPTS):
    if os.path.isfile(os.path.join(_p, "review_format.py")) and _p not in sys.path:
        sys.path.insert(0, _p)
from review_format import (FINDING_ID_RE, FINDING_RE, FIX_IMPACTS, RGRS,  # noqa: E402
                           SEVERITIES, STATUSES, VERDICTS, current_level, ids_in)

FIELD_LINE = re.compile(r"^-\s+\*\*([A-Za-z' ]+):\*\*\s*(.*)$")
ROUNDS = {"default": 1, "uncertain": 2, "harness": 3, "notest": 3}
OUTCOMES = ("fixed", "fixed-partly", "fixed-without-test", "harness",
            "gone", "not-a-defect", "present")
DISPOSITIONS = ("gone", "not-a-defect", "present")

# Task statuses. done releases dependents; bounced and blocked never do.
PENDING, ACTIVE, DONE, BOUNCED, BLOCKED = "pending", "in-progress", "done", "bounced", "blocked"


# --- small helpers -------------------------------------------------------------

def stamp():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def out(obj, code=0):
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    sys.exit(code)


def die(msg, **extra):
    out({"error": msg, **extra}, 1)


def git(cwd, *args, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        die(f"git {' '.join(args)} failed", stderr=p.stderr.strip(), cwd=cwd)
    return p.stdout.strip() if check else p


def retrying(fn):
    """On Windows a file a virus scanner or an editor has open can't be
    replaced for a moment. Retry briefly."""
    for attempt in range(40):
        try:
            return fn()
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default

    def rd():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return retrying(rd)


def write_json(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    retrying(lambda: os.replace(tmp, path))


def id_sort_key(fid):
    head, _, tail = fid.rpartition("-")
    return (head, int(tail)) if tail.isdigit() else (fid, 0)


def cell(text):
    return (text or "").replace("|", "\\|").replace("\n", " ")


# --- where things are -----------------------------------------------------------

def code_root(start):
    top = git(start, "rev-parse", "--show-toplevel")
    common = git(start, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if os.path.normcase(os.path.normpath(os.path.dirname(common.rstrip("/\\")))) != \
            os.path.normcase(os.path.normpath(top)):
        die("this is a worktree, not the repository's own folder; start the run in the main folder",
            toplevel=top, git_common_dir=common)
    return os.path.normpath(top)


class Run:
    def __init__(self, root):
        self.root = root
        self.base_dir = os.path.join(root, ".fix-findings")
        self.state = os.path.join(self.base_dir, "state")
        self.run_json = os.path.join(self.state, "run.json")
        self.tasks_json = os.path.join(self.state, "tasks.json")
        self.log_md = os.path.join(self.state, "log.md")
        self.records = os.path.join(self.state, "records")

    def exists(self):
        return os.path.isfile(self.run_json)

    def cfg(self):
        c = read_json(self.run_json)
        if c is None:
            die("no fix-findings run here", root=self.root)
        return c

    def save_cfg(self, c):
        write_json(self.run_json, c)

    def tasks(self):
        return read_json(self.tasks_json)

    def save_tasks(self, t):
        write_json(self.tasks_json, t)

    def log(self, msg):
        with open(self.log_md, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"- {stamp()} {msg}\n")
        self.touch_lock()

    def run_dir(self, cfg=None):
        cfg = cfg or self.cfg()
        return os.path.join(cfg["review_dir"], "fixes", cfg["run"])

    def lock_path(self, cfg=None):
        return os.path.join(self.run_dir(cfg), "RUN.lock")

    def touch_lock(self):
        cfg = read_json(self.run_json)
        if not cfg:
            return
        p = self.lock_path(cfg)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if not ln.startswith("heartbeat:")]
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines + [f"heartbeat: {stamp()}"]) + "\n")

    def active(self, data=None):
        data = data or self.tasks()
        return next((t for t in data["tasks"].values() if t["status"] == ACTIVE), None)


# --- parsing the review ----------------------------------------------------------

def fields_of(lines):
    f = {}
    for line in lines:
        m = FIELD_LINE.match(line)
        if m and m.group(1) not in f:
            f[m.group(1)] = m.group(2).strip()
    return f


def parse_findings(review_dir):
    findings = {}
    fdir = os.path.join(review_dir, "findings")
    if not os.path.isdir(fdir):
        die("the review has no findings/ directory", review=review_dir)
    for fn in sorted(os.listdir(fdir)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(fdir, fn), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        cur = None
        for line in lines:
            m = FINDING_RE.match(line.rstrip("\r"))
            if m:
                cur = {"id": m.group(1), "title": m.group(2).strip(), "doc": fn,
                       "lines": [line.rstrip("\r")]}
                findings[cur["id"]] = cur
                continue
            if line.startswith("## ") or line.startswith("# "):
                cur = None
                continue
            if cur is not None:
                cur["lines"].append(line.rstrip("\r"))
    for f in findings.values():
        lines = f.pop("lines")
        f["text"] = "\n".join(lines).rstrip()
        b = fields_of(lines)
        f["severity"] = current_level(b.get("Severity", ""), SEVERITIES) or "Low"
        f["fix_impact"] = current_level(b.get("Fix impact", ""), FIX_IMPACTS)
        f["rgr_text"] = b.get("RGR", "")
        f["rgr"] = current_level(f["rgr_text"], RGRS)
        f["partly"] = f["rgr"] == "No" and "partly testable" in f["rgr_text"].lower()
        f["deps"] = [d for d in dict.fromkeys(ids_in(b.get("Depends on", ""))) if d != f["id"]]
        f["verdict"] = current_level(b.get("Verification", ""), VERDICTS)
        f["status"] = current_level(b["Status"], STATUSES) or "?" if "Status" in b else "Open"
        f["file"] = b.get("File", "")
        f["category"] = b.get("Category", "").lower()
        f["unit"] = f["id"].rpartition("-")[0]
    return findings


def kind_of(f):
    if f["id"].startswith("X-"):
        return "harness"
    return {"Unit": "fix", "Seam (local)": "seam", "Seam (harness)": "fix", "Seam": "fix",
            "No": "partly" if f["partly"] else "notest"}.get(f["rgr"], "")


def rgr_slug(f):
    if f["rgr"] == "No":
        return "partly" if f["partly"] else "no"
    if f["rgr"] == "Seam (local)":
        return "seam-local"
    if f["rgr"].startswith("Seam"):
        return "seam-harness"
    return f["rgr"].lower()


def matches(f, expr):
    key, _, vals = expr.partition("=")
    key, wanted = key.strip().lower(), [v.strip().lower() for v in vals.split(",") if v.strip()]
    have = {
        "id": f["id"].lower(), "unit": f["unit"].lower(), "rgr": rgr_slug(f),
        "kind": kind_of(f), "severity": f["severity"].lower(),
        "fix": (f["fix_impact"] or "").lower(), "category": f["category"],
        "verdict": (f["verdict"] or "none").lower(),
    }.get(key)
    if have is None:
        die(f"unknown filter key '{key}'", known="id unit rgr kind severity fix category verdict")
    if key == "rgr" and "seam" in wanted and have.startswith("seam"):
        return True
    if key == "rgr" and "no" in wanted and have == "partly":
        return True
    return have in wanted


def queue_rows(path):
    cols = None
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.lstrip().startswith("|"):
                cols = None
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cols is None:
                cols = [c.lower() for c in cells]
                continue
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c) or "id" not in cols:
                continue
            yield dict(zip(cols, cells))


def prior_run_fixed(review_dir, root, base, fid):
    """Private mode: some earlier fix run fixed it, and its test or probe is in
    the base. Status stays Open until reconcile, so this is the only way to see
    it."""
    fixes = os.path.join(review_dir, "fixes")
    if not os.path.isdir(fixes):
        return False
    for run in os.listdir(fixes):
        q = os.path.join(fixes, run, "queue.md")
        if not os.path.isfile(q):
            continue
        for row in queue_rows(q):
            if row.get("id") != fid or not row.get("status", "").lower().startswith("fixed"):
                continue
            for col in ("test", "probe"):
                ref = row.get(col, "").strip("` ")
                if "::" not in ref:
                    continue
                path, _, needle = ref.partition("::")
                for spec in ([path.strip()], []):
                    if git(root, "grep", "-F", "-q", "-e", needle.strip(), base, "--", *spec,
                           check=False).returncode == 0:
                        return True
    return False


def find_cycles(nodes, deps):
    """Tarjan: every strongly connected component larger than one node, or a
    node that depends on itself."""
    index, low, on, stack, found = {}, {}, set(), [], []
    counter = [0]
    sys.setrecursionlimit(max(10000, len(nodes) * 4))

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in deps.get(v, []):
            if w not in nodes:
                continue
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in deps.get(v, []):
                found.append(comp)

    for v in sorted(nodes):
        if v not in index:
            visit(v)
    return found


def dependents_of(tasks):
    """Every task's dependents in the run, direct or not."""
    rev = {}
    for t in tasks.values():
        for d in t["deps"]:
            rev.setdefault(d, []).append(t["id"])
    every = {}
    for tid in tasks:
        seen, todo = set(), list(rev.get(tid, []))
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            todo.extend(rev.get(x, []))
        every[tid] = seen
    return every


def rounds_for(t, table):
    n = table["default"]
    if t["fix_impact"] == "Uncertain":
        n = max(n, table["uncertain"])
    if t["kind"] == "harness":
        n = max(n, table["harness"])
    if t["kind"] in ("notest", "partly"):
        n = max(n, table["notest"])
    return n


# --- queue rendering ---------------------------------------------------------------

def status_text(t):
    s = t["status"]
    if s == DONE:
        return {"fixed": "fixed", "fixed-partly": "fixed, residual without a test",
                "fixed-without-test": "fixed without a test", "harness": "fixed (harness)",
                "gone": "gone", "not-a-defect": "not a defect",
                "present": "present"}.get(t["outcome"], t["outcome"] or "done")
    if s == BOUNCED:
        return f"bounced: {t['category']}"
    if s == BLOCKED:
        return f"blocked: {t['blocked_by']}"
    if s == ACTIVE:
        return f"in-progress: {t.get('step') or 'started'}"
    return s


def ordered(tasks):
    return sorted(tasks.values(), key=lambda t: (t["order"], id_sort_key(t["id"])))


def render_queue(run):
    cfg, data = run.cfg(), run.tasks()
    tasks = ordered(data["tasks"])
    counts = {}
    for t in tasks:
        k = status_text(t).split(":")[0]
        counts[k] = counts.get(k, 0) + 1
    lines = [f"# Fix-findings queue — {cfg['run']}", "",
             f"Run branch `{cfg['branch']}`, base `{cfg['base'][:12]}`, tracking mode {cfg['mode']}.",
             f"Written {stamp()}. " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ".",
             "",
             "| ID | Kind | Severity | Fix impact | RGR | Title | Status | Test | Probe | Proof | Rounds | Commits | Landed | Note |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in tasks:
        note = (t.get("evidence") if t["status"] == DONE else t.get("reason")) or ""
        lines.append("| " + " | ".join([
            t["id"], t["kind"], t["severity"], t["fix_impact"] or "—", t["rgr"] or "—",
            cell(t["title"]), status_text(t), cell(t.get("test")), cell(t.get("probe")),
            cell(t.get("proof")), "" if t.get("rounds_run") is None else str(t["rounds_run"]),
            " ".join(t.get("commits") or []), "", cell(note)]) + " |")
    excl = data.get("excluded", {})
    if excl:
        lines += ["", "## Not in this run", "", "| ID | Reason |", "|---|---|"]
        for fid in sorted(excl, key=id_sort_key):
            lines.append(f"| {fid} | {cell(excl[fid])} |")
    path = cfg["queue_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


# --- the task table -----------------------------------------------------------------

def recompute_blocked(tasks):
    """Derive pending or blocked for every task not in progress, done or
    bounced: blocked if any prerequisite is bounced or blocked. Returns the
    tasks that became blocked."""
    before = {k: t["status"] for k, t in tasks.items()}
    for t in tasks.values():
        if t["status"] == BLOCKED:
            t["status"], t["blocked_by"] = PENDING, None
    changed = True
    while changed:
        changed = False
        for t in tasks.values():
            if t["status"] != PENDING:
                continue
            bad = next((d for d in t["deps"]
                        if d in tasks and tasks[d]["status"] in (BOUNCED, BLOCKED)), None)
            if bad:
                t["status"], t["blocked_by"] = BLOCKED, bad
                changed = True
    return sorted((k for k, t in tasks.items() if t["status"] == BLOCKED and before[k] != BLOCKED),
                  key=id_sort_key)


def pickable(tasks):
    ready = [t for t in tasks.values() if t["status"] == PENDING
             and all(tasks[d]["status"] == DONE for d in t["deps"] if d in tasks)]
    return sorted(ready, key=lambda t: (t["order"], id_sort_key(t["id"])))


def planned_order(tasks):
    """The order `next` offers the tasks in, if every one is done."""
    sim = {k: {"status": PENDING, "deps": t["deps"], "order": t["order"], "id": k}
           for k, t in tasks.items()}
    seq = []
    while True:
        ready = pickable(sim)
        if not ready:
            return seq
        ready[0]["status"] = DONE
        seq.append(ready[0]["id"])


def deps_info(run, data, t):
    """What a task's subagents need to know about its prerequisites."""
    info = []
    for d in t["deps"]:
        p = data["tasks"].get(d)
        if not p:
            continue
        proofs = [f for f in os.listdir(run.records) if f.startswith(f"{d}.proof.")] \
            if p["kind"] == "harness" else []
        info.append({"id": d, "title": p["title"], "kind": p["kind"], "outcome": p["outcome"],
                     "test": p.get("test"), "probe": p.get("probe"), "proof": p.get("proof"),
                     "commits": p.get("commits"),
                     "proof_copy": os.path.join(run.records, proofs[0]) if proofs else None})
    return info


def archive_records(run, tid):
    """A task starting again gets a clean record: earlier attempts' records
    and patches move to records/attempts/<ID>-<n>/."""
    mine = [f for f in os.listdir(run.records) if f.startswith(f"{tid}.")
            and not f.startswith(f"{tid}.proof.")]
    if not mine:
        return None
    n = 1
    while os.path.exists(os.path.join(run.records, "attempts", f"{tid}-{n}")):
        n += 1
    dest = os.path.join(run.records, "attempts", f"{tid}-{n}")
    os.makedirs(dest)
    for f in mine:
        shutil.move(os.path.join(run.records, f), os.path.join(dest, f))
    return dest


def change_task(run, tid, mutate, log_msg, need_active=True):
    data = run.tasks()
    t = data["tasks"].get(tid)
    if t is None:
        die("unknown task", task=tid)
    if need_active and t["status"] != ACTIVE:
        die(f"{tid} isn't the task in progress", status=t["status"])
    extra = mutate(data["tasks"], t) or {}
    run.save_tasks(data)
    run.log(log_msg(t))
    render_queue(run)
    return t, extra


def end_task(t):
    t.update({"step": None, "start": None, "untracked": None})


# --- the working tree -------------------------------------------------------------

def untracked(root):
    """Untracked files that git doesn't ignore, as paths relative to the root."""
    raw = git(root, "status", "--porcelain", "--untracked-files=all", "-z")
    return sorted(e[3:] for e in raw.split("\0") if e.startswith("?? "))


def tracked_changes(root):
    return git(root, "status", "--porcelain", "--untracked-files=no")


def on_run_branch(root, cfg):
    p = git(root, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    return p.returncode == 0 and p.stdout.strip() == cfg["branch"]


def review_snapshot(cfg):
    """What the review directory holds, apart from this run's own folder and
    the rendered site: path, size and modification time of every file. A
    subagent must never write there."""
    review = cfg["review_dir"]
    skip = {os.path.normcase(os.path.join(review, "fixes", cfg["run"])),
            os.path.normcase(os.path.join(review, "html")),
            os.path.normcase(os.path.join(review, ".git"))}
    snap = []
    for dirpath, dirnames, filenames in os.walk(review):
        dirnames[:] = [d for d in dirnames
                       if os.path.normcase(os.path.join(dirpath, d)) not in skip]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap.append(f"{os.path.relpath(p, review)}\t{st.st_size}\t{st.st_mtime_ns}")
    return sorted(snap)


def save_review_snapshot(run, cfg):
    write_json(os.path.join(run.state, "review-snapshot.json"), review_snapshot(cfg))


def name_filter(cfg, name):
    """A test name, safe to put in the target template: escaped when the
    runner's filter is a regex, and free of the characters cmd.exe quoting
    can't carry."""
    if not name:
        return ""
    if cfg.get("name_is_regex", True):
        name = re.escape(name).replace('"', ".").replace("%", ".")
        return name.replace("\\ ", " ")
    return name.replace('"', "").replace("%", "")


def quote_path(f):
    """Always quoted, so cmd.exe can't read &, |, ( or spaces in a path as
    syntax."""
    return '"' + f.replace('"', '') + '"'


def tree_of(root, rev):
    return git(root, "rev-parse", f"{rev}^{{tree}}", check=False).stdout.strip()


def leak_hits(run, since):
    """Private mode: nothing the run adds may point at the review. Scans the
    added lines of the diff from `since` to the working tree, the untracked
    files the task added, and the messages of the commits since then."""
    cfg = run.cfg()
    root = run.root
    units = {"X"}
    index = os.path.join(run.state, "index.md")
    if os.path.isfile(index):
        with open(index, encoding="utf-8") as fh:
            units |= {m.group(1) for m in re.finditer(r"^\| ([A-Z][A-Z0-9]*)-\d+ \|", fh.read(), re.M)}
    units = sorted(units, key=len, reverse=True)
    review = os.path.normpath(cfg["review_dir"])
    pats = [re.compile(r"\b(?:" + "|".join(map(re.escape, units)) + r")-\d+\b"),
            re.compile(re.escape(review).replace(r"\\", r"[\\/]"), re.I),
            re.compile(r"\bcode[ -]review|\breview finding|\baudit finding", re.I)]
    # The review's own folder name, unless it is a bare date or the project's
    # name, which ordinary code contains.
    rname = os.path.basename(review)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rname) and \
            rname.lower() != os.path.basename(root).lower():
        pats.insert(1, re.compile(r"(?<![A-Za-z0-9])" + re.escape(rname) + r"(?![A-Za-z0-9])", re.I))
    hits = []
    diff = git(root, "diff", since, "--unified=0", "--no-color")
    t = run.active()
    known = set((t or {}).get("untracked") or [])
    for f in untracked(root):
        if f in known:
            continue
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                diff += f"\n+++ b/{f}\n" + "".join("+" + ln for ln in fh)
        except OSError as e:
            hits.append({"where": f, "match": "", "line": f"couldn't read it to scan: {e}"})
    cur = None
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else ln[4:]
            continue
        if ln.startswith("+"):
            for p in pats:
                m = p.search(ln)
                if m:
                    hits.append({"where": cur, "match": m.group(0), "line": ln[1:200]})
                    break
    for msg in git(root, "log", "--format=%B%x00", f"{since}..HEAD").split("\0"):
        for p in pats:
            m = p.search(msg)
            if m:
                hits.append({"where": "commit message", "match": m.group(0), "line": msg.strip()[:200]})
                break
    return hits


def probe_problem(root, probe, start):
    """A probe must be in HEAD and not in start, or it can't find the fix again."""
    _, _, needle = probe.partition("::")
    now_has = git(root, "grep", "-F", "-q", "-e", needle, "HEAD", check=False).returncode == 0
    had = git(root, "grep", "-F", "-q", "-e", needle, start, check=False).returncode == 0
    if not now_has or had:
        return {"result": "bad-probe", "probe": probe, "in_head": now_has, "in_start": had}
    return None


# --- commands -------------------------------------------------------------------------

def write_throttle(bin_dir, gate):
    if gate == "slack":
        src = os.path.join(REVIEW_SCRIPTS, "slack-check.bat")
        if not os.path.isfile(src):
            die("the review skill's slack-check.bat isn't beside this skill", expected=src)
        shutil.copy2(src, os.path.join(bin_dir, "slack-check.bat"))
        call = 'call "%~dp0slack-check.bat" %*\nexit /b %ERRORLEVEL%\n'
    elif gate == "none":
        call = "exit /b 0\n"
    else:
        if not os.path.isfile(gate):
            die("the usage gate script doesn't exist", gate=gate)
        call = f'call "{os.path.abspath(gate)}" %*\nexit /b %ERRORLEVEL%\n'
    with open(os.path.join(bin_dir, "throttle.bat"), "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("@echo off\n"
                 "REM fix-findings throttle. Contract: exit 0 = go on, non-zero = pause.\n"
                 "REM 1. Manual pause: PAUSE.flag in the state directory.\n"
                 "REM 2. The usage gate.\n"
                 "if exist \"%~dp0..\\PAUSE.flag\" (\n"
                 "    echo PAUSED ^(manual PAUSE.flag^)\n"
                 "    exit /b 1\n"
                 ")\n" + call)


def cmd_init(a, run):
    if run.exists() and not run.cfg().get("finished"):
        die("a run is already live here; one run per repository at a time", run=run.cfg()["run"])
    review = os.path.abspath(a.review)
    root = run.root
    base = git(root, "rev-parse", a.base)
    findings = parse_findings(review)
    wanted = [x.strip() for x in (a.ids or "").replace(" ", ",").split(",") if x.strip()]
    unknown = [x for x in wanted if x not in findings]
    if unknown:
        die("these IDs aren't findings in the review", ids=unknown)

    excluded, cand, prior_fixed, pulled = {}, {}, set(), []

    def why_not(f):
        if f["status"] != "Open":
            return f"Status is {f['status']}"
        if f["verdict"] == "REFUTED":
            return "REFUTED at verification"
        if not kind_of(f):
            return "no RGR rating"
        if a.mode == "private" and prior_run_fixed(review, root, base, f["id"]):
            prior_fixed.add(f["id"])
            return "fixed by an earlier fix run, and the fix is in the base"
        return None

    for fid, f in findings.items():
        if wanted and fid not in wanted:
            continue
        if f["status"] != "Open" and not wanted:
            continue
        reason = why_not(f)
        if reason:
            excluded[fid] = reason
            continue
        if a.include and not any(matches(f, e) for e in a.include):
            excluded[fid] = "filtered out: not in " + "; ".join(a.include)
            continue
        hit = next((e for e in (a.exclude or []) if matches(f, e)), None)
        if hit:
            excluded[fid] = f"filtered out: {hit}"
            continue
        cand[fid] = f

    if a.with_prereqs:
        todo = list(cand)
        while todo:
            fid = todo.pop()
            for dep in findings[fid]["deps"]:
                g = findings.get(dep)
                if dep in cand or g is None or g["status"] != "Open":
                    continue
                if why_not(g) is None:
                    cand[dep] = g
                    excluded.pop(dep, None)
                    pulled.append(dep)
                    todo.append(dep)

    def satisfied_outside(dep):
        g = findings.get(dep)
        if g is None:
            return False, "unknown finding"
        if g["status"] in ("Fixed", "Not a defect"):
            return True, ""
        if dep in prior_fixed or (a.mode == "private" and prior_run_fixed(review, root, base, dep)):
            return True, ""
        return False, f"{g['status']}, not in this run"

    changed = True
    while changed:
        changed = False
        for fid in sorted(cand, key=id_sort_key):
            for dep in cand[fid]["deps"]:
                if dep in cand:
                    continue
                ok, why = satisfied_outside(dep)
                if not ok:
                    excluded[fid] = f"waits on {dep}: {excluded.get(dep) or why}"
                    del cand[fid]
                    changed = True
                    break
    for comp in find_cycles(set(cand), {k: v["deps"] for k, v in cand.items()}):
        for fid in comp:
            excluded[fid] = "cycle: " + ", ".join(sorted(comp, key=id_sort_key))
            cand.pop(fid, None)
    changed = True
    while changed:
        changed = False
        for fid in sorted(cand, key=id_sort_key):
            bad = next((d for d in cand[fid]["deps"] if d not in cand and d in excluded
                        and d not in prior_fixed), None)
            if bad:
                excluded[fid] = f"waits on {bad}: {excluded[bad]}"
                del cand[fid]
                changed = True

    tasks = {}
    for fid, f in cand.items():
        tasks[fid] = {
            "id": fid, "title": f["title"], "unit": f["unit"], "doc": f["doc"],
            "file": f["file"], "severity": f["severity"], "fix_impact": f["fix_impact"],
            "rgr": rgr_slug(f), "verdict": f["verdict"], "kind": kind_of(f),
            "deps": [d for d in f["deps"] if d in cand], "status": PENDING,
            "step": None, "start": None, "untracked": None, "rekinded": False,
            "outcome": None, "category": None, "reason": None, "blocked_by": None,
            "commits": [], "test": None, "probe": None, "proof": None, "evidence": None,
            "rounds_run": None, "note": None,
        }
    for spec in a.note or []:
        fid, _, text = spec.partition("=")
        if fid.strip() not in tasks or not text.strip():
            die("a --note is ID=text, for a finding in the run", note=spec)
        tasks[fid.strip()]["note"] = text.strip()
    every = dependents_of(tasks)
    for t in tasks.values():
        t["dependents_total"] = len(every[t["id"]])
        t["rounds"] = rounds_for(t, ROUNDS)
    # The order tasks are offered in, among those whose prerequisites are done:
    # the user's order when they listed IDs (a prerequisite pulled in goes with
    # the earliest finding waiting on it), then choke points, then severity.
    pos = {fid: i for i, fid in enumerate(wanted)}
    for t in tasks.values():
        p = min([pos[x] for x in every[t["id"]] | {t["id"]} if x in pos], default=len(wanted))
        t["order"] = [p, -t["dependents_total"], SEVERITIES.index(t["severity"])]

    by_kind = {}
    for t in tasks.values():
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
    reasons = {}
    for r in excluded.values():
        k = ("waits on a finding not in this run" if r.startswith("waits on ")
             else "filtered out" if r.startswith("filtered out")
             else "cycle" if r.startswith("cycle")
             else "not Open" if r.startswith("Status is") else r)
        reasons[k] = reasons.get(k, 0) + 1
    missing = sorted({e.split(":")[0][len("waits on "):] for e in excluded.values()
                      if e.startswith("waits on ") and ", not in this run" in e
                      and e.split(":")[1].strip().startswith("Open")}, key=id_sort_key)
    top = sorted(tasks.values(), key=lambda t: -t["dependents_total"])[:10]
    summary = {
        "tasks": len(tasks), "by_kind": by_kind, "excluded": len(excluded),
        "excluded_by_reason": reasons,
        "order": planned_order(tasks),
        "pulled_in_as_prerequisites": sorted(pulled, key=id_sort_key),
        "open_prerequisites_not_in_run": missing,
        "choke_points": [f"{t['id']} ({t['dependents_total']} waiting)" for t in top
                         if t["dependents_total"]],
        "no_test": sorted((t["id"] for t in tasks.values() if t["kind"] == "notest"), key=id_sort_key),
        "partly_testable": sorted((t["id"] for t in tasks.values() if t["kind"] == "partly"),
                                  key=id_sort_key),
        "harnesses": sorted((t["id"] for t in tasks.values() if t["kind"] == "harness"),
                            key=id_sort_key),
    }
    run_dir = os.path.join(review, "fixes", a.run)
    queue_path = os.path.join(run_dir, "queue.md") if a.mode == "private" \
        else os.path.join(run.state, "queue.md")
    if os.path.exists(run_dir):
        die("the review already has a fix run of this name; pick another run name", run_dir=run_dir)
    if a.dry_run:
        out({"dry_run": True, **summary,
             "excluded_list": {k: excluded[k] for k in sorted(excluded, key=id_sort_key)}})
    if not tasks:
        die("no finding is eligible, so there is nothing to run", **summary)

    add_excludes(root, ["/.fix-findings/"])
    if run.exists():
        old = run.cfg()
        archive = os.path.join(run.base_dir, "finished-" + old["run"])
        if os.path.exists(archive):
            die("an archive of the previous run is in the way", path=archive)
        os.rename(run.state, archive)
    for d in (run.state, os.path.join(run.state, "briefs"), run.records,
              os.path.join(run.state, "logs"), os.path.join(run.state, "bin"), run_dir):
        os.makedirs(d, exist_ok=True)

    # The run uses its own copies, so a plugin update mid-run changes nothing.
    bin_dir = os.path.join(run.state, "bin")
    shutil.copy2(os.path.abspath(__file__), os.path.join(bin_dir, "fix_run.py"))
    import review_format
    shutil.copy2(review_format.__file__, os.path.join(bin_dir, "review_format.py"))
    write_throttle(bin_dir, a.gate)

    cfg = {
        "run": a.run, "created": stamp(), "review_dir": review, "code_root": root,
        "base": base, "pin": a.pin or "", "branch": a.branch, "mode": a.mode,
        "ids": wanted, "with_prereqs": a.with_prereqs,
        "filters": {"include": a.include or [], "exclude": a.exclude or []},
        "gate": a.gate, "queue_path": queue_path, "rounds": ROUNDS,
        "commands": {"target": "", "related": "", "suite": "", "check": "", "install": ""},
        "workers": None, "test_globs": [], "commit_style": "", "name_is_regex": True,
        "env_streak": 0, "suite_tree": None, "suite_exit": None, "baseline_done": False,
        "finished": False,
    }
    run.save_cfg(cfg)
    run.save_tasks({"tasks": tasks, "excluded": excluded})

    for fid, f in cand.items():
        parts = [f["text"]]
        if tasks[fid]["kind"] == "harness":
            waiting = [g for g in findings.values() if fid in g["deps"]
                       and g["verdict"] != "REFUTED" and g["status"] == "Open"]
            proof = next(iter(FINDING_ID_RE.findall(f["rgr_text"])), None)
            parts.append("\n## Findings that wait on it\n")
            for g in sorted(waiting, key=lambda g: id_sort_key(g["id"])):
                parts.append(f"### {g['id']}: {g['title']}\n- **RGR:** {g['rgr_text']}\n")
            if proof and proof in findings:
                parts.append("\n## The one that proves it\n")
                parts.append(findings[proof]["text"])
        with open(os.path.join(run.state, "briefs", f"{fid}.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("\n".join(parts) + "\n")
    with open(os.path.join(run.state, "index.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Every finding in the review\n\n| ID | Status | File | Title |\n|---|---|---|---|\n")
        for fid in sorted(findings, key=id_sort_key):
            g = findings[fid]
            fh.write(f"| {fid} | {g['status']} | {cell(g['file'])} | {cell(g['title'])} |\n")
    run.log(f"run {a.run} created: {len(tasks)} tasks, {len(excluded)} not in this run")
    out({"created": True, "state": run.state, "run_dir": run_dir, "queue": render_queue(run),
         **summary})


def cmd_config(a, run):
    cfg = run.cfg()
    for kv in a.set or []:
        key, _, val = kv.partition("=")
        try:
            v = json.loads(val)
        except json.JSONDecodeError:
            v = val
        node = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    if a.set:
        run.save_cfg(cfg)
    out(cfg)


def cmd_status(a, run):
    cfg, data = run.cfg(), run.tasks()
    tasks = data["tasks"]
    counts, bounces = {}, {}
    for t in tasks.values():
        k = status_text(t).split(":")[0]
        counts[k] = counts.get(k, 0) + 1
        if t["status"] == BOUNCED:
            bounces.setdefault(t["category"], []).append(t["id"])
    tail = []
    if os.path.isfile(run.log_md):
        with open(run.log_md, encoding="utf-8") as fh:
            tail = fh.read().splitlines()[-a.log:]
    t = run.active(data)
    lock = None
    if os.path.isfile(run.lock_path(cfg)):
        with open(run.lock_path(cfg), encoding="utf-8") as fh:
            lock = fh.read().strip().splitlines()
    out({"run": cfg["run"], "branch": cfg["branch"], "mode": cfg["mode"],
         "finished": cfg.get("finished"), "baseline_done": cfg.get("baseline_done"),
         "paused": os.path.exists(os.path.join(run.state, "PAUSE.flag")), "lock": lock,
         "counts": counts, "in_progress": t and {"id": t["id"], "kind": t["kind"],
                                                 "step": t["step"], "start": t["start"]},
         "pickable_now": [x["id"] for x in pickable(tasks)][:10],
         "bounced": {k: sorted(v, key=id_sort_key) for k, v in bounces.items()},
         "env_streak": cfg.get("env_streak", 0),
         "suite_on_tip": cfg.get("suite_tree") == tree_of(run.root, "HEAD"),
         "recent_log": tail})


def cmd_show(a, run):
    data = run.tasks()
    t = data["tasks"].get(a.task)
    if t is None:
        die("unknown task", task=a.task)
    out({"task": t, "deps": deps_info(run, data, t)})


def cmd_lock(a, run):
    cfg = run.cfg()
    p = run.lock_path(cfg)
    if a.release:
        if os.path.isfile(p):
            os.remove(p)
        out({"result": "released"})
    held = None
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            held = fh.read().strip().splitlines()
    if not a.take:
        out({"result": "held" if held else "free", "lock": held})
    if held and not a.force:
        out({"result": "held", "lock": held,
             "why": "another session may be running this run: ask the user to confirm it has "
                    "stopped, then take the lock with --force"}, 3)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"run: {cfg['run']}\nstate: {run.state}\nstarted: {stamp()}\nheartbeat: {stamp()}\n")
    run.log("lock taken" + (" over an earlier session's" if held else ""))
    out({"result": "taken", "lock": p})


def add_excludes(root, patterns):
    """Add patterns to .git/info/exclude, the repository's local ignore list,
    which is never committed."""
    gitdir = git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    path = os.path.join(gitdir, "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    have = b""
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            have = fh.read()
    lines = have.decode("utf-8", errors="replace").splitlines()
    added = [p for p in patterns if p not in lines]
    if added:
        with open(path, "ab") as fh:
            if have and not have.endswith(b"\n"):
                fh.write(b"\n")
            fh.write(("\n".join(added) + "\n").encode("utf-8"))
    return added, path


def cmd_exclude(a, run):
    added, path = add_excludes(run.root, a.patterns)
    out({"result": "ok", "added": added, "file": path})


def cmd_next(a, run):
    cfg = run.cfg()
    if cfg.get("finished"):
        out({"result": "finished"})
    if not cfg.get("baseline_done"):
        die("the baseline isn't recorded yet: run `baseline --done` after the baseline step")
    data = run.tasks()
    tasks = data["tasks"]
    cur = run.active(data)
    if cur:
        out({"result": "in-progress", "task": cur,
             "why": "a task was left in progress: `discard --restart` it, then `next` again"}, 4)
    if not on_run_branch(run.root, cfg):
        die("the run branch isn't checked out", branch=cfg["branch"])
    if tracked_changes(run.root):
        die("tracked files are changed before the task starts",
            status=tracked_changes(run.root).splitlines()[:20])
    ready = pickable(tasks)
    if not ready:
        pending = [t["id"] for t in tasks.values() if t["status"] == PENDING]
        if pending:
            run.log(f"stalled: {len(pending)} pending, none pickable")
            out({"result": "stalled", "pending": sorted(pending, key=id_sort_key)})
        out({"result": "done"})
    t = ready[0]
    archived = archive_records(run, t["id"])
    t.update({"status": ACTIVE, "step": "started", "start": git(run.root, "rev-parse", "HEAD"),
              "untracked": untracked(run.root), "started_at": stamp()})
    run.save_tasks(data)
    save_review_snapshot(run, cfg)
    run.log(f"started {t['id']} ({t['kind']}, {t['dependents_total']} waiting on it)")
    render_queue(run)
    here = os.path.dirname(os.path.abspath(__file__))
    out({"result": "picked", "task": t, "deps": deps_info(run, data, t),
         "earlier_attempt": archived,
         "remaining": sum(1 for x in tasks.values() if x["status"] == PENDING),
         "brief": os.path.join(run.state, "briefs", f"{t['id']}.md"),
         "record": os.path.join(run.records, f"{t['id']}.md"),
         "follow": "the skill's reference/work.md for this task's kind; never from memory alone",
         "fix_run": os.path.join(here, "fix_run.py")})


def cmd_beat(a, run):
    data = run.tasks()
    t = run.active(data)
    if t and a.step:
        t["step"] = a.step
        run.save_tasks(data)
    run.touch_lock()
    out({"result": "ok", "task": t and t["id"], "step": t and t["step"]})


def cmd_exec(a, run):
    cfg = run.cfg()
    which, fallback = a.which, None
    if which == "related" and (not cfg["commands"].get("related") or not a.files):
        which, fallback = "suite", ("no related command" if not cfg["commands"].get("related")
                                    else "no changed files given")
    tpl = cfg["commands"].get(which) or ""
    if not tpl:
        out({"result": "no-command", "which": which})
    workers = cfg.get("workers") if isinstance(cfg.get("workers"), int) and cfg["workers"] > 0 \
        else (os.cpu_count() or 2)
    cmdline = (tpl.replace("{file}", a.file or "")
               .replace("{name}", name_filter(cfg, a.test_name))
               .replace("{files}", " ".join(quote_path(f) for f in (a.files or [])))
               .replace("{workers}", str(workers)))
    head = git(run.root, "rev-parse", "HEAD")
    logdir = os.path.join(run.state, "logs")
    os.makedirs(logdir, exist_ok=True)
    log = os.path.join(logdir, (a.log or f"{which}-{int(time.time())}") + ".log")
    run.touch_lock()
    env = dict(os.environ)
    # A Python test runner writing to a redirected log would otherwise use
    # cp1252 on Windows and crash on any non-ASCII output.
    env.setdefault("PYTHONUTF8", "1")
    start, started = time.time(), stamp()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(f"$ {cmdline}\n(cwd {run.root})\n\n")
        fh.flush()
        p = subprocess.run(cmdline, cwd=run.root, shell=True, env=env,
                           stdout=fh, stderr=subprocess.STDOUT)
    seconds = round(time.time() - start)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n[fix-findings] {which}: exit {p.returncode} after {seconds} s, "
                 f"started {started}\n")
    with open(log, encoding="utf-8", errors="replace") as fh:
        tail = fh.read().splitlines()[-a.tail:]
    res = {"which": which, "exit": p.returncode, "seconds": seconds, "log": log, "tail": tail}
    if fallback:
        res["fallback"] = f"ran the full suite: {fallback}"
    # `done` checks that the full suite ran on the tree being recorded: the
    # committed tree, with nothing uncommitted beyond the task's snapshot.
    if which == "suite" and not tracked_changes(run.root):
        t = run.active()
        known = set((t or {}).get("untracked") or [])
        if set(untracked(run.root)) <= known or t is None:
            cfg = run.cfg()
            cfg.update({"suite_tree": tree_of(run.root, head), "suite_exit": p.returncode})
            run.save_cfg(cfg)
    run.touch_lock()
    out(res)


def cmd_baseline(a, run):
    cfg = run.cfg()
    cfg["baseline_done"] = stamp()
    run.save_cfg(cfg)
    save_review_snapshot(run, cfg)
    run.log("baseline recorded")
    out({"result": "ok"})


def cmd_guard(a, run):
    """After every subagent: HEAD hasn't moved, the run branch is still checked
    out, and nothing in the review directory changed."""
    cfg = run.cfg()
    problems = []
    if not on_run_branch(run.root, cfg):
        problems.append(f"the run branch {cfg['branch']} isn't checked out any more")
    head = git(run.root, "rev-parse", "HEAD")
    if a.head and not head.startswith(a.head) and not a.head.startswith(head):
        problems.append(f"HEAD moved: expected {a.head[:12]}, found {head[:12]}")
    was = read_json(os.path.join(run.state, "review-snapshot.json"), [])
    now_ = review_snapshot(cfg)
    diff = sorted(set(was) ^ set(now_))
    if diff:
        problems.append("the review directory changed: " +
                        ", ".join(sorted({d.split(chr(9))[0] for d in diff}))[:500])
    out({"result": "escaped" if problems else "ok", "problems": problems}, 4 if problems else 0)


MANIFESTS = re.compile(
    r"(^|/)(package(-lock)?\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|"
    r"requirements[^/]*\.txt|pyproject\.toml|poetry\.lock|Pipfile(\.lock)?|setup\.(py|cfg)|"
    r"uv\.lock|go\.(mod|sum)|Cargo\.(toml|lock)|[^/]*\.csproj|[^/]*\.fsproj|packages\.config|"
    r"Directory\.(Packages|Build)\.props|pom\.xml|build\.gradle(\.kts)?|Gemfile(\.lock)?|"
    r"composer\.(json|lock)|[^/]*\.gemspec|mix\.(exs|lock)|Podfile(\.lock)?)$", re.I)
BUILD = re.compile(
    r"(^|/)(\.github/|\.gitlab-ci\.yml$|azure-pipelines[^/]*\.ya?ml$|\.circleci/|Jenkinsfile$|"
    r"Makefile$|CMakeLists\.txt$|[^/]*\.cmake$|Dockerfile[^/]*$|docker-compose[^/]*\.ya?ml$|"
    r"(jest|vitest|vite|webpack|rollup|babel|karma|playwright|cypress)\.config\.[^/]+$|"
    r"tsconfig[^/]*\.json$|\.eslintrc[^/]*$|eslint\.config\.[^/]+$|\.babelrc$|"
    r"pytest\.ini$|tox\.ini$|noxfile\.py$|\.editorconfig$|[^/]*\.sln$)", re.I)


def is_test(path, globs):
    import fnmatch
    for g in globs or []:
        if fnmatch.fnmatchcase(path, g):
            return True
        if g.startswith("**/") and fnmatch.fnmatchcase(path, g[3:]):
            return True
    return False


def cmd_changes(a, run):
    """What changed since a commit, sorted for the checks: test and production
    files, the findings doc, new files, size, line-ending churn, and what the
    checker should be told about."""
    cfg = run.cfg()
    root = run.root
    t = run.active()
    ref = a.since or (t and t["start"]) or "HEAD"
    globs = cfg.get("test_globs") or []
    review_rel = os.path.relpath(cfg["review_dir"], root).replace(os.sep, "/")
    in_review = (lambda p: not review_rel.startswith("..") and
                 (p == review_rel or p.startswith(review_rel + "/")))
    status = {}
    for ln in git(root, "diff", "--name-status", "--no-renames", ref).splitlines():
        st, _, path = ln.partition("\t")
        status[path] = st[:1]
    known = set((t or {}).get("untracked") or [])
    for f in untracked(root):
        if f not in known:
            status[f] = "?"
    numstat = {}
    for ln in git(root, "diff", "--numstat", "--no-renames", ref).splitlines():
        add, dele, path = ln.split("\t", 2)
        numstat[path] = (0 if add == "-" else int(add), 0 if dele == "-" else int(dele))
    loose = {}
    for ln in git(root, "diff", "--numstat", "--no-renames", "--ignore-cr-at-eol", ref).splitlines():
        add, dele, path = ln.split("\t", 2)
        loose[path] = (0 if add == "-" else int(add), 0 if dele == "-" else int(dele))
    for f, st in status.items():
        if st == "?":
            try:
                with open(os.path.join(root, f), "rb") as fh:
                    numstat[f] = (fh.read().count(b"\n") or 1, 0)
            except OSError:
                numstat[f] = (0, 0)
    res = {"since": ref, "test": [], "production": [], "review": [], "new": [], "deleted": [],
           "line_ending_churn": [], "flags": []}
    size = {"prod": [0, 0], "test": [0, 0]}
    for f in sorted(status):
        st = status[f]
        if in_review(f):
            res["review"].append(f)
            continue
        kind = "test" if is_test(f, globs) else "production"
        res[kind].append(f)
        if st in ("A", "?"):
            res["new"].append(f)
        if st == "D":
            res["deleted"].append(f)
        k = "test" if kind == "test" else "prod"
        size[k][0] += 1
        size[k][1] += sum(numstat.get(f, (0, 0)))
        if st == "M" and f in numstat and loose.get(f, (0, 0)) != numstat[f]:
            res["line_ending_churn"].append(f)
        if MANIFESTS.search(f):
            res["flags"].append(f"dependency manifest or lockfile changed: {f}")
        elif BUILD.search(f):
            res["flags"].append(f"build, test-runner or CI configuration changed: {f}")
        elif kind == "production" and st in ("A", "?"):
            res["flags"].append(f"new production file: {f}")
        if kind == "test" and st in ("M", "D") and numstat.get(f, (0, 0))[1] > 0:
            res["flags"].append(f"existing test file has removed or changed lines (check its "
                                f"assertions): {f}")
    res["size"] = (f"prod {size['prod'][0]}f/{size['prod'][1]}l, "
                   f"test {size['test'][0]}f/{size['test'][1]}l")
    out(res)


def cmd_scan(a, run):
    hits = leak_hits(run, a.since)
    out({"result": "leak" if hits else "clean", "hits": hits[:20]})


def cmd_set_status(a, run):
    """Committed mode: rewrite the finding's Status bullet in place, in its
    findings doc in the code root, keeping every line's ending."""
    cfg, data = run.cfg(), run.tasks()
    t = data["tasks"].get(a.task)
    if t is None:
        die("unknown task", task=a.task)
    if cfg["mode"] != "committed":
        die("Status is written only in committed mode")
    path = os.path.join(cfg["review_dir"], "findings", t["doc"])
    raw = open(path, "rb").read()
    parts = raw.split(b"\n")
    in_finding, done_ = False, False
    for i, ln in enumerate(parts):
        text = ln.decode("utf-8").rstrip("\r")
        m = FINDING_RE.match(text)
        if m:
            in_finding = m.group(1) == a.task
            continue
        if text.startswith("# ") or text.startswith("## "):
            in_finding = False
            continue
        if in_finding and re.match(r"^-\s+\*\*Status:\*\*", text):
            ending = b"\r" if ln.endswith(b"\r") else b""
            parts[i] = f"- **Status:** {a.text}".encode("utf-8") + ending
            done_ = True
            break
    if not done_:
        die("no Status bullet for the finding", task=a.task, doc=path)
    with open(path, "wb") as fh:
        fh.write(b"\n".join(parts))
    save_review_snapshot(run, cfg)
    run.log(f"{a.task} Status set: {a.text[:120]}")
    out({"result": "ok", "doc": os.path.relpath(path, run.root).replace(os.sep, "/")})


def cmd_discard(a, run):
    """Put the tree back at the task's start, or with --to-head at HEAD: drop
    the task's commits and changes, and delete the untracked files the task
    added, one by one. Untracked files that were there when the task started,
    and ignored files, stay."""
    cfg, data = run.cfg(), run.tasks()
    t = run.active(data)
    if t is None:
        die("no task is in progress")
    root = run.root
    gitdir = git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    if os.path.isdir(os.path.join(gitdir, "rebase-merge")) or \
            os.path.isdir(os.path.join(gitdir, "rebase-apply")):
        git(root, "rebase", "--abort")
    if os.path.isfile(os.path.join(gitdir, "CHERRY_PICK_HEAD")):
        git(root, "cherry-pick", "--abort")
    if not on_run_branch(root, cfg):
        die("the run branch isn't checked out; whatever is, isn't the run's to discard",
            branch=cfg["branch"])
    before = git(root, "rev-parse", "HEAD")
    target = before if a.to_head else t["start"]
    if git(root, "merge-base", "--is-ancestor", target, before, check=False).returncode != 0:
        die("HEAD doesn't descend from the task's start; show the user the branch log",
            head=before, start=target)
    git(root, "reset", "--soft", target)
    git(root, "restore", f"--source={target}", "--staged", "--worktree", "--", ":/")
    keep = set(t["untracked"] or [])
    removed = []
    for f in untracked(root):
        if f in keep:
            continue
        p = os.path.join(root, f)
        if os.path.isfile(p) or os.path.islink(p):
            os.remove(p)
            removed.append(f)
    left = tracked_changes(root)
    extra = sorted(set(untracked(root)) - keep)
    head = git(root, "rev-parse", "HEAD")
    if left or extra or head != target:
        die("the tree isn't back at the task's start after discarding",
            status=left.splitlines()[:20], untracked=extra[:20], head=head, expected=target)
    save_review_snapshot(run, cfg)
    res = {"result": "clean", "dropped_from": before, "head": head, "removed": removed}
    if a.restart:
        end_task(t)
        t.update({"status": PENDING})
        run.save_tasks(data)
        run.log(f"{t['id']} restarted: discarded back to its start")
        render_queue(run)
        res["restarted"] = t["id"]
    out(res)


def require_at_start(run, t):
    if git(run.root, "rev-parse", "HEAD") != t["start"] or tracked_changes(run.root):
        die("the tree isn't at the task's start: `discard` first", start=t["start"])


def cmd_bounce(a, run):
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    require_at_start(run, t)

    def m(tasks, t):
        t.update({"status": BOUNCED, "category": a.category, "reason": a.reason})
        end_task(t)
        return {"blocked": recompute_blocked(tasks)}
    _, extra = change_task(run, a.task, m, lambda t: f"{t['id']} bounced {a.category}: {a.reason}")
    cfg = run.cfg()
    cfg["env_streak"] = cfg.get("env_streak", 0) + 1 if a.category == "env" else 0
    run.save_cfg(cfg)
    out({"result": "bounced", "task": a.task, "blocked": extra["blocked"],
         "env_streak": cfg["env_streak"]})


def cmd_rekind(a, run):
    """A Unit finding whose test needs a seam: try once more as Seam (local)."""
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    require_at_start(run, t)

    def m(tasks, t):
        end_task(t)
        if t["rekinded"] or t["kind"] != "fix" or t["rgr"] != "unit":
            t.update({"status": BOUNCED, "category": "not-unit", "reason": a.reason})
            return {"bounced": True, "blocked": recompute_blocked(tasks)}
        t.update({"status": PENDING, "kind": "seam", "rekinded": True,
                  "reason": f"re-rated Seam (local): {a.reason}"})
        return {"bounced": False}
    t, extra = change_task(run, a.task, m, lambda t: f"{t['id']} " + (
        "bounced not-unit" if t["status"] == BOUNCED else "requeued as Seam (local)") + f": {a.reason}")
    out({"result": "bounced" if extra["bounced"] else "requeued", "task": a.task, **extra})


def cmd_block_on(a, run):
    """The fixer found an unrecorded prerequisite. If it is a task in this run
    that isn't done, wait for it; otherwise bounce."""
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    require_at_start(run, t)

    def m(tasks, t):
        end_task(t)
        dep = tasks.get(a.dep)
        reason = None
        if dep is None:
            reason = f"waits on {a.dep}, which isn't in this run"
        elif dep["status"] == DONE:
            reason = f"waits on {a.dep}, which is already done"
        elif dep["status"] in (BOUNCED, BLOCKED):
            reason = f"waits on {a.dep}, which is {dep['status']}"
        else:
            seen, todo = set(), [a.dep]
            while todo:
                x = todo.pop()
                if x == t["id"]:
                    reason = f"waits on {a.dep}, which waits on this one"
                    break
                if x in seen:
                    continue
                seen.add(x)
                todo.extend(tasks[x]["deps"] if x in tasks else [])
        if reason:
            t.update({"status": BOUNCED, "category": "blocked", "reason": f"{reason}: {a.reason}"})
            return {"bounced": True, "blocked": recompute_blocked(tasks)}
        t["deps"].append(a.dep)
        t["status"] = PENDING
        t["reason"] = f"waits on {a.dep}: {a.reason}"
        # It now goes after its new prerequisite, which takes its place.
        dep["order"] = min(dep["order"], t["order"])
        every = dependents_of(tasks)
        for x in tasks.values():
            x["dependents_total"] = len(every[x["id"]])
        return {"bounced": False}
    t, extra = change_task(run, a.task, m, lambda t: f"{t['id']} " + (
        "bounced blocked" if t["status"] == BOUNCED else "now waits on " + a.dep) + f": {a.reason}")
    out({"result": "bounced" if extra["bounced"] else "requeued", "task": a.task, **extra})


def cmd_dispose(a, run):
    """Private mode: a disposition needs no commit, so it is done at once."""
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    require_at_start(run, t)

    def m(tasks, t):
        end_task(t)
        t.update({"status": DONE, "outcome": a.outcome, "evidence": a.evidence})
    change_task(run, a.task, m, lambda t: f"{t['id']} dispositioned {a.outcome}: {a.evidence}")
    cfg = run.cfg()
    cfg["env_streak"] = 0
    run.save_cfg(cfg)
    out({"result": "done", "task": a.task, "outcome": a.outcome})


def cmd_done(a, run):
    """Every check and review has passed and the task's commits are made.
    Re-check what the record relies on, then record the task as done."""
    cfg = run.cfg()
    root = run.root
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    start = t["start"]
    if tracked_changes(root):
        die("tracked files have uncommitted changes; commit or discard them first")
    extra = sorted(set(untracked(root)) - set(t["untracked"] or []))
    if extra:
        die("the task left untracked files; commit or discard them first", untracked=extra[:20])
    if not on_run_branch(root, cfg):
        die("the run branch isn't checked out")
    if git(root, "merge-base", "--is-ancestor", start, "HEAD", check=False).returncode != 0:
        die("HEAD doesn't descend from the task's start", start=start)
    commits = git(root, "log", "--reverse", "--format=%h", f"{start}..HEAD").split()
    if not commits:
        die("nothing committed since the task started")
    if git(root, "rev-list", "--merges", f"{start}..HEAD"):
        die("the task's commits include a merge commit; history must stay linear")
    if a.outcome not in DISPOSITIONS:
        if cfg.get("suite_tree") != tree_of(root, "HEAD"):
            out({"result": "no-full-suite", "why": "the full suite hasn't run on this tree: run "
                 "the Full suite step, then `done` again"}, 4)
    bad = []
    for sha in git(root, "rev-list", "--reverse", f"{start}..HEAD").split():
        msg = git(root, "log", "-1", "--format=%B", sha)
        subject, _, body = msg.partition("\n")
        if not body.strip():
            bad.append({"commit": sha[:12], "problem": "no body", "subject": subject})
        for line in msg.splitlines():
            if re.match(r"\s*co-authored-by:", line, re.I) or \
                    re.search(r"generated with \[?claude", line, re.I):
                bad.append({"commit": sha[:12], "problem": "attribution line", "line": line.strip()})
    if bad:
        out({"result": "bad-message", "why": "every commit needs a body and no attribution "
             "lines: reword them, as work.md's Done section says, then `done` again",
             "commits": bad}, 4)
    if a.probe:
        bp = probe_problem(root, a.probe, start)
        if bp:
            out(bp, 4)
    if cfg["mode"] == "private":
        hits = leak_hits(run, start)
        if hits:
            out({"result": "leak", "hits": hits[:20]}, 4)
    with open(os.path.join(run.records, f"{a.task}.final.patch"), "w", encoding="utf-8",
              newline="\n", errors="replace") as fh:
        fh.write(git(root, "diff", "--binary", start, "HEAD") + "\n")

    def m(tasks, t):
        end_task(t)
        t.update({"status": DONE, "outcome": a.outcome, "commits": commits, "test": a.test,
                  "probe": a.probe, "proof": a.proof, "evidence": a.evidence,
                  "rounds_run": a.rounds_run, "reason": None})
    change_task(run, a.task, m,
                lambda t: f"{t['id']} done: {a.outcome}, {len(commits)} commits ending {commits[-1]}")
    cfg = run.cfg()
    cfg["env_streak"] = 0
    run.save_cfg(cfg)
    out({"result": "done", "task": a.task, "outcome": a.outcome, "commits": commits})


def cmd_requeue(a, run):
    """Between runs, on the user's word: a bounced or blocked task goes back to
    pending, with the user's decision as a note its next attempt gets."""
    def m(tasks, t):
        if t["status"] not in (BOUNCED, BLOCKED):
            die("only a bounced or blocked task can be requeued", status=t["status"])
        t.update({"status": PENDING, "category": None, "blocked_by": None, "reason": None,
                  "note": a.note or t.get("note")})
        return {"blocked": recompute_blocked(tasks)}
    if run.cfg().get("finished"):
        die("the run is finished: start a new run for these findings, with --note")
    t, extra = change_task(run, a.task, m, lambda t: f"{t['id']} requeued"
                           + (f" with a note: {a.note}" if a.note else ""), need_active=False)
    out({"result": "requeued", "task": a.task, "status_now": t["status"],
         "blocked_by": t.get("blocked_by"), "blocked": extra["blocked"]})


def cmd_flaky(a, run):
    with open(os.path.join(run.state, "flaky.md"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {stamp()} {a.test} (seen in {a.task})\n")
    run.log(f"flaky test: {a.test} (seen in {a.task})")
    out({"result": "ok"})


def cmd_log(a, run):
    run.log(a.message)
    out({"result": "ok"})


def cmd_queue(a, run):
    out({"result": "ok", "queue": render_queue(run)})


def cmd_stop(a, run):
    """The run stops early: log why and release the lock. A task in progress
    stays in progress, for a resume to discard and redo."""
    run.log(f"run stopped: {a.reason}")
    render_queue(run)
    p = run.lock_path()
    if os.path.isfile(p):
        os.remove(p)
    out({"result": "stopped", "reason": a.reason})


def cmd_finish(a, run):
    cfg, data = run.cfg(), run.tasks()
    live = [t["id"] for t in data["tasks"].values() if t["status"] in (PENDING, ACTIVE)]
    if live and not a.force:
        die("tasks are still pending or in progress", tasks=sorted(live, key=id_sort_key)[:30])
    cfg["finished"] = stamp()
    run.save_cfg(cfg)
    run.log("run finished")
    q = render_queue(run)
    # The record goes with the review: the queue, every task's record, and the
    # final patches reconcile compares landed fixes with.
    run_dir = run.run_dir(cfg)
    rec_out = os.path.join(run_dir, "records")
    os.makedirs(rec_out, exist_ok=True)
    copied = []
    if os.path.normcase(q) != os.path.normcase(os.path.join(run_dir, "queue.md")):
        shutil.copy2(q, os.path.join(run_dir, "queue.md"))
    for f in sorted(os.listdir(run.records)):
        if f.endswith(".md") or f.endswith(".final.patch") or ".proof." in f:
            shutil.copy2(os.path.join(run.records, f), os.path.join(rec_out, f))
            copied.append(f)
    shutil.copy2(run.log_md, os.path.join(run_dir, "log.md"))
    p = run.lock_path(cfg)
    if os.path.isfile(p):
        os.remove(p)
    out({"result": "finished", "run_dir": run_dir, "queue": os.path.join(run_dir, "queue.md"),
         "records_copied": len(copied)})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="the code root (default: found from the current directory)")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("init", help="create a run from a review")
    s.add_argument("--review", required=True)
    s.add_argument("--run", required=True)
    s.add_argument("--branch", required=True)
    s.add_argument("--base", default="HEAD")
    s.add_argument("--mode", choices=("committed", "private"), required=True)
    s.add_argument("--pin", help="the review's pinned commit, from its README")
    s.add_argument("--ids", help="only these findings, in this order: comma- or space-separated")
    s.add_argument("--with-prereqs", action="store_true",
                   help="also take every Open prerequisite of the chosen findings")
    s.add_argument("--include", action="append", help="key=v1,v2; repeatable")
    s.add_argument("--exclude", action="append", help="key=v1,v2; repeatable")
    s.add_argument("--gate", default="slack", help="slack, none, or the path of a usage-check script")
    s.add_argument("--note", action="append",
                   help="ID=text: the user's decision about a finding, for its subagents; repeatable")
    s.add_argument("--dry-run", action="store_true")

    s = sp.add_parser("config", help="show run.json, or --set key=value")
    s.add_argument("--set", action="append")
    s = sp.add_parser("status")
    s.add_argument("--log", type=int, default=15)
    s = sp.add_parser("show", help="one task, with what its subagents need about its prerequisites")
    s.add_argument("task")
    s = sp.add_parser("lock", help="show the run's lock, --take it, or --release it")
    s.add_argument("--take", action="store_true")
    s.add_argument("--force", action="store_true")
    s.add_argument("--release", action="store_true")
    s = sp.add_parser("exclude", help="add patterns to .git/info/exclude")
    s.add_argument("patterns", nargs="+")
    sp.add_parser("baseline", help="record that the baseline step is done")
    sp.add_parser("next", help="start the next task")
    s = sp.add_parser("beat", help="record the task's step, and refresh the lock")
    s.add_argument("--step")

    s = sp.add_parser("exec", help="run a recorded command in the code root")
    s.add_argument("which", choices=("target", "related", "suite", "check", "install"))
    s.add_argument("--files", nargs="*", help="changed files, for {files} in `related`")
    s.add_argument("--file")
    s.add_argument("--test-name")
    s.add_argument("--log")
    s.add_argument("--tail", type=int, default=15)

    s = sp.add_parser("guard", help="after a subagent: nothing escaped")
    s.add_argument("--head", help="the HEAD the tree should still be at")
    s = sp.add_parser("changes", help="what changed since a commit, sorted for the checks")
    s.add_argument("--since", help="default: the task's start")
    s = sp.add_parser("scan", help="private mode: leak scan of added lines and messages")
    s.add_argument("--since", required=True)
    s = sp.add_parser("set-status", help="committed mode: rewrite a finding's Status bullet")
    s.add_argument("task")
    s.add_argument("text")
    s = sp.add_parser("discard", help="put the tree back at the task's start")
    s.add_argument("--to-head", action="store_true", help="keep the commits; drop only uncommitted changes")
    s.add_argument("--restart", action="store_true", help="and set the task pending, to start again")

    for name in ("bounce", "rekind"):
        s = sp.add_parser(name)
        s.add_argument("task")
        if name == "bounce":
            s.add_argument("category")
        s.add_argument("reason")
    s = sp.add_parser("block-on")
    s.add_argument("task")
    s.add_argument("dep")
    s.add_argument("reason")
    s = sp.add_parser("dispose", help="private mode: record a disposition with no commit")
    s.add_argument("task")
    s.add_argument("outcome", choices=DISPOSITIONS)
    s.add_argument("evidence")
    s = sp.add_parser("done", help="re-check a finished task, and record it done")
    s.add_argument("task")
    s.add_argument("--outcome", required=True, choices=OUTCOMES)
    s.add_argument("--test")
    s.add_argument("--probe")
    s.add_argument("--proof")
    s.add_argument("--evidence")
    s.add_argument("--rounds-run", type=int)

    s = sp.add_parser("requeue", help="on the user's word: a bounced or blocked task, again")
    s.add_argument("task")
    s.add_argument("--note", help="what the user decided, for the next attempt's subagents")
    s = sp.add_parser("flaky")
    s.add_argument("task")
    s.add_argument("test")
    s = sp.add_parser("log")
    s.add_argument("message")
    sp.add_parser("queue")
    s = sp.add_parser("stop", help="stop the run early, releasing the lock")
    s.add_argument("reason")
    s = sp.add_parser("finish")
    s.add_argument("--force", action="store_true")

    a = p.parse_args()
    root = os.path.abspath(a.root) if a.root else code_root(os.getcwd())
    run = Run(root)
    handler = globals()["cmd_" + a.cmd.replace("-", "_")]
    if a.cmd not in ("init", "exclude"):
        run.cfg()
    handler(a, run)


if __name__ == "__main__":
    main()
