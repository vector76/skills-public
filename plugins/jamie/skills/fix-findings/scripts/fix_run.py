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
# What `init --like` takes from an earlier run of the same review.
LIKE_KEYS = ("commands", "test_globs", "commit_style", "name_is_regex", "workers", "commits")
LIKE_FILE = "review-context.md"

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
    the base. A run made before private mode wrote Status left the finding
    Open, so its queue is the only way to see it."""
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


def file_dir(f):
    """The folder of a test file, relative to the code root with forward
    slashes, "." for the root: for runners that take a package or folder
    rather than a file, such as `go test ./{dir}`."""
    d = os.path.dirname((f or "").replace("\\", "/").strip("/"))
    return d or "."


def fill_template(tpl, cfg, file, test_name, files):
    workers = cfg.get("workers") if isinstance(cfg.get("workers"), int) and cfg["workers"] > 0 \
        else (os.cpu_count() or 2)
    return (tpl.replace("{file}", file or "")
            .replace("{dir}", file_dir(file))
            .replace("{name}", name_filter(cfg, test_name))
            .replace("{files}", " ".join(quote_path(f) for f in (files or [])))
            .replace("{workers}", str(workers)))


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


def prior_run_state(run, name):
    """The state folder of an earlier, finished run in this repository: the
    current one if it is that run, or its archive."""
    if run.exists() and run.cfg().get("run") == name and run.cfg().get("finished"):
        return run.state
    p = os.path.join(run.base_dir, "finished-" + name)
    return p if os.path.isfile(os.path.join(p, "run.json")) else None


def cmd_init(a, run):
    a.commits_given = a.commits is not None
    a.commits = a.commits or "separate"
    if run.exists() and not run.cfg().get("finished"):
        die("a run is already live here; one run per repository at a time", run=run.cfg()["run"])
    review = os.path.abspath(a.review)
    root = run.root
    base = git(root, "rev-parse", a.base)
    if a.in_place:
        # The run works on the branch that is checked out, committing to it directly, as a
        # project that commits straight to its main branch asks.
        cur = git(root, "symbolic-ref", "-q", "--short", "HEAD", check=False)
        if cur.returncode != 0:
            die("--in-place needs a branch checked out, not a detached HEAD")
        here_branch = cur.stdout.strip()
        if a.branch and a.branch != here_branch:
            die("--in-place runs on the branch checked out; --branch names another",
                checked_out=here_branch, branch=a.branch)
        if base != git(root, "rev-parse", "HEAD"):
            die("--in-place starts at the branch's tip: --base must be HEAD")
        a.branch = here_branch
    elif not a.branch:
        die("--branch is required, or --in-place to run on the branch checked out")
    like = None
    if a.like:
        like = prior_run_state(run, a.like)
        if like is None:
            die("no finished run of that name in this repository to take settings from",
                run=a.like, looked_in=run.base_dir)
        if os.path.normcase(os.path.abspath(read_json(os.path.join(like, "run.json"), {})
                                            .get("review_dir", ""))) != os.path.normcase(review):
            die("the run to take settings from is of another review", run=a.like)
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
    summary["branch"] = a.branch + (" (in place: the branch checked out)" if a.in_place else "")
    summary["commits"] = a.commits
    if like:
        summary["settings_from"] = {"run": a.like, "copies": list(LIKE_KEYS) + [LIKE_FILE]}
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
        if like and os.path.normcase(like) == os.path.normcase(run.state):
            like = archive
    for d in (run.state, os.path.join(run.state, "briefs"), run.records,
              os.path.join(run.state, "logs"), os.path.join(run.state, "bin"), run_dir):
        os.makedirs(d, exist_ok=True)

    # The run uses its own copies, so a plugin update mid-run changes nothing.
    bin_dir = os.path.join(run.state, "bin")
    shutil.copy2(os.path.abspath(__file__), os.path.join(bin_dir, "fix_run.py"))
    import review_format
    shutil.copy2(review_format.__file__, os.path.join(bin_dir, "review_format.py"))
    shutil.copy2(os.path.join(HERE, "go_related.py"), os.path.join(bin_dir, "go_related.py"))
    prompts_dir = os.path.join(bin_dir, "prompts")
    os.makedirs(prompts_dir, exist_ok=True)
    for role in PROMPTS:
        shutil.copy2(os.path.join(HERE, "..", "reference", f"{role}-prompt.md"), prompts_dir)
    write_throttle(bin_dir, a.gate)

    cfg = {
        "run": a.run, "created": stamp(), "review_dir": review, "code_root": root,
        "base": base, "pin": a.pin or "", "branch": a.branch, "mode": a.mode,
        "ids": wanted, "with_prereqs": a.with_prereqs,
        "filters": {"include": a.include or [], "exclude": a.exclude or []},
        "gate": a.gate, "queue_path": queue_path, "rounds": ROUNDS,
        "in_place": bool(a.in_place), "commits": a.commits,
        "commands": {"target": "", "related": "", "suite": "", "check": "", "install": ""},
        "workers": None, "test_globs": [], "commit_style": "", "name_is_regex": True,
        "env_streak": 0, "suite_tree": None, "suite_exit": None, "baseline_done": False,
        "finished": False,
    }
    if like:
        # A follow-up run keeps the earlier run's commands and conventions,
        # which the user already approved, and its copy of the review context.
        prev = read_json(os.path.join(like, "run.json"), {})
        for k in LIKE_KEYS:
            if k in prev and not (k == "commits" and a.commits_given):
                cfg[k] = prev[k]
        cfg["settings_from"] = a.like
        src = os.path.join(like, LIKE_FILE)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(run.state, LIKE_FILE))
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
    out(run_exec(run, a.which, a.file, a.test_name, a.files, a.log, a.tail))


def run_exec(run, which, file=None, test_name=None, files=None, log_name=None, tail_lines=15):
    """Run a recorded command, write its output to a log, and return the result."""
    cfg = run.cfg()
    fallback = None
    if which == "related" and (not cfg["commands"].get("related") or not files):
        which, fallback = "suite", ("no related command" if not cfg["commands"].get("related")
                                    else "no changed files given")
    tpl = cfg["commands"].get(which) or ""
    if not tpl:
        return {"result": "no-command", "which": which, "exit": None}
    cmdline = fill_template(tpl, cfg, file, test_name, files)
    head = git(run.root, "rev-parse", "HEAD")
    logdir = os.path.join(run.state, "logs")
    os.makedirs(logdir, exist_ok=True)
    log = os.path.join(logdir, (log_name or f"{which}-{int(time.time())}") + ".log")
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
        tail = fh.read().splitlines()[-tail_lines:]
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
    return res


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
    out(compute_changes(run, a.since))


def compute_changes(run, since=None):
    """What changed since a commit, sorted for the checks: test and production
    files, the findings doc, new files, size, line-ending churn, and what the
    checker should be told about."""
    cfg = run.cfg()
    root = run.root
    t = run.active()
    ref = since or (t and t["start"]) or "HEAD"
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
    return res


def cmd_scan(a, run):
    hits = leak_hits(run, a.since)
    out({"result": "leak" if hits else "clean", "hits": hits[:20]})


def cmd_set_status(a, run):
    """Rewrite the finding's Status bullet in place, keeping every line's
    ending. Committed mode: in the findings doc in the code root, before the
    commit it goes in. Private mode: in the review, outside the project, once
    the task is done or disposed, so the review shows what the run did before
    it lands."""
    cfg, data = run.cfg(), run.tasks()
    t = data["tasks"].get(a.task)
    if t is None:
        die("unknown task", task=a.task)
    if cfg["mode"] != "committed" and t["status"] != DONE:
        die("in private mode, Status is written once the task is done or disposed",
            status=t["status"])
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
    inside = os.path.normcase(os.path.abspath(path)).startswith(
        os.path.normcase(os.path.abspath(run.root)) + os.sep)
    out({"result": "ok", "doc": os.path.relpath(path, run.root).replace(os.sep, "/") if inside
         else path})


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
    if cfg.get("commits") == "one" and len(commits) > 1:
        out({"result": "not-squashed", "why": "this run makes one commit per finding: `squash` "
             "the task's commits with its final message, then `done` again",
             "commits": commits}, 4)
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
        die("the run is finished: start a follow-up run for these findings, "
            f"init --like {run.cfg()['run']} --ids <IDs> --note \"<ID>=<text>\", on a new branch "
            "from this run's tip (SKILL.md, A follow-up run)")
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



# --- one commit per finding ---------------------------------------------------------

def cmd_squash(a, run):
    """One-commit mode: fold the task's commits into one, with its final message.
    The tree is unchanged, so every check that ran on it still holds."""
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    root, start = run.root, t["start"]
    if tracked_changes(root):
        die("tracked files have uncommitted changes; commit or discard them first")
    if not os.path.isfile(a.message):
        die("no message file", message=a.message)
    if not git(root, "rev-list", f"{start}..HEAD"):
        die("nothing committed since the task started")
    tree = tree_of(root, "HEAD")
    git(root, "reset", "-q", "--soft", start)
    p = git(root, "commit", "-q", "-F", a.message, check=False)
    if p.returncode != 0:
        out({"result": "commit-failed", "why": "the commit was refused, often by a commit hook; "
             "the task's changes are staged on its start: fix the message and `squash` again, "
             "or discard", "stderr": p.stderr.strip()}, 4)
    if tree_of(root, "HEAD") != tree:
        die("the squash changed the tree; discard the task")
    sha = git(root, "rev-parse", "--short", "HEAD")
    run.log(f"{t['id']} squashed into {sha}")
    out({"result": "squashed", "commit": sha,
         "subject": git(root, "log", "-1", "--format=%s")})


# --- the checks on a fix and on an improvement, run the same way every time --------

BUILD_FAILURE = re.compile(
    r"\[build failed\]|\[setup failed\]|build constraints exclude|cannot find package|"
    r"Test suite failed to run|error TS\d+|ModuleNotFoundError|ImportError while importing|"
    r"SyntaxError:|error\[E\d+\]|could not compile|BUILD FAILED|compilation failed", re.I)


def split_test(spec):
    path, sep, name = (spec or "").partition("::")
    if not sep or not path or not name:
        die("--test is <test file>::<test name>", test=spec)
    return path.strip(), name.strip()


def red_problem(res, name):
    """Why a run meant to show red doesn't, or None if it does: it ran, the
    named test appears in the log, and the build or import didn't fail."""
    if res.get("exit") is None:
        return "no target command"
    if res["exit"] == 0:
        return "the test passed without the fix"
    try:
        text = open(res["log"], encoding="utf-8", errors="replace").read()
    except OSError:
        return "no log"
    if BUILD_FAILURE.search(text):
        return "the test did not build or load without the fix: " + BUILD_FAILURE.search(text).group(0)
    if name not in text:
        return f"the log never names {name}: the test didn't run"
    return None


def red_lines(res, name, limit=12):
    try:
        lines = open(res["log"], encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return []
    hit = [i for i, ln in enumerate(lines) if name in ln]
    keep = []
    for i in hit[:3]:
        keep.extend(lines[i:i + 4])
    return keep[:limit]


def regression_run(run, which, ident, files=None):
    """The regression rule: a failure gets one rerun. A pass on the rerun is a
    flaky test for the session to name; two failures are for it to compare with
    baseline.md and flaky.md."""
    r = run_exec(run, which, files=files, log_name=f"{ident}-{which}")
    if r.get("exit") in (0, None):
        return {"step": which, "ok": True, "log": r.get("log"), "skipped": r.get("exit") is None}
    r2 = run_exec(run, which, files=files, log_name=f"{ident}-{which}-rerun")
    if r2.get("exit") == 0:
        return {"step": which, "ok": True, "flaky_suspect": r["log"], "log": r2["log"]}
    return {"step": which, "ok": False, "log": r2["log"], "first_log": r["log"]}


def raw_diff(root, base, paths):
    if not paths:
        return b""
    return subprocess.run(["git", "diff", "--binary", base, "--", *paths], cwd=root,
                          capture_output=True).stdout


def at_ref(root, ref, path):
    return git(root, "cat-file", "-e", f"{ref}:{path}", check=False).returncode == 0


def checks_report(steps, result, category=None, why=None, **extra):
    rep = {"result": result, "steps": steps, **extra}
    if category:
        rep.update({"category": category, "why": why})
    out(rep, 0 if result == "ok" else 4)


def cmd_check_fix(a, run):
    """The checks on a fix (fix, partly, and the fix of a seam task), in order:
    the changes are allowed, red, green, the regression run and the check. The
    work is uncommitted; HEAD is the task's start, or the seam commit."""
    cfg, root = run.cfg(), run.root
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    test_file, test_name = split_test(a.test)
    steps = []
    ch = compute_changes(run, "HEAD")
    summary = {k: ch[k] for k in ("test", "production", "new", "deleted", "flags", "size")}
    if ch["review"]:
        checks_report(steps, "fail", "escaped", "files in the review directory changed: stop the run",
                      changes=summary)
    if not ch["test"] or not ch["production"]:
        checks_report(steps, "fail", "no-red", "a red-green fix changes at least one test file and "
                      "one production file", changes=summary)
    if ch["line_ending_churn"]:
        checks_report(steps, "fail", "env", f"line-ending churn: {ch['line_ending_churn']}",
                      changes=summary)
    steps.append({"step": "changes", "ok": True})
    if cfg["mode"] == "private":
        hits = leak_hits(run, t["start"])
        if hits:
            checks_report(steps, "fail", "leak", "the work points at the review", hits=hits[:20],
                          changes=summary)
        steps.append({"step": "scan", "ok": True})
    # Red: the production files back as they were, the test as written.
    prod = ch["production"]
    new_prod = [f for f in prod if f in ch["new"]]
    old_prod = [f for f in prod if f not in ch["new"]]
    patch = os.path.join(run.records, f"{a.task}.fix.patch")
    with open(patch, "wb") as fh:
        fh.write(raw_diff(root, "HEAD", old_prod))
    with open(os.path.join(run.records, f"{a.task}.test.patch"), "wb") as fh:
        fh.write(raw_diff(root, "HEAD", [f for f in ch["test"] if f not in ch["new"]]))
    aside = os.path.join(run.records, f"{a.task}.aside")
    if old_prod:
        git(root, "restore", "--source=HEAD", "--", *old_prod)
    for f in new_prod:
        dst = os.path.join(aside, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(os.path.join(root, f), dst)
    r = run_exec(run, "target", test_file, test_name, log_name=f"{a.task}-red")
    why = red_problem(r, test_name)
    lines = red_lines(r, test_name)
    for f in new_prod:
        shutil.move(os.path.join(aside, f), os.path.join(root, f))
    if os.path.getsize(patch) and subprocess.run(["git", "apply", patch], cwd=root).returncode:
        checks_report(steps, "fail", "env", "the fix did not apply back: the tree is in an unknown "
                      "state; discard, and bounce env", changes=summary)
    if why:
        checks_report(steps, "fail", "no-red", why, red_log=r.get("log"), red_lines=lines,
                      changes=summary)
    steps.append({"step": "red", "ok": True, "log": r["log"]})
    g = run_exec(run, "target", test_file, None, log_name=f"{a.task}-green")
    if g.get("exit") != 0:
        checks_report(steps, "fail", "no-green", "the test file doesn't pass with the fix",
                      green_log=g.get("log"), changes=summary)
    steps.append({"step": "green", "ok": True, "log": g["log"]})
    for which, files in (("related", ch["test"] + ch["production"]), ("check", None)):
        st = regression_run(run, which, a.task, files)
        steps.append(st)
        if not st["ok"]:
            checks_report(steps, "fail", "regression", f"{which} failed twice: compare its failures "
                          "with baseline.md and flaky.md before bouncing", changes=summary)
    run.log(f"{a.task} check-fix: passed")
    checks_report(steps, "ok", changes=summary, red_lines=lines,
                  confirm="read red_lines: the test must fail for the finding's reason, as the "
                          "record's Red line says")


def cmd_check_round(a, run):
    """The checks on an improvement round, with HEAD at the task's last commit.
    A failure means: roll this round back with `discard --to-head`."""
    cfg, root = run.cfg(), run.root
    t = run.active()
    if t is None or t["id"] != a.task:
        die(f"{a.task} isn't the task in progress")
    start = t["start"]
    before = a.before or start
    steps = []
    ch = compute_changes(run, "HEAD")
    if ch["review"]:
        checks_report(steps, "fail", "escaped", "files in the review directory changed: stop the run")
    if ch["line_ending_churn"]:
        checks_report(steps, "fail", "rollback", f"line-ending churn: {ch['line_ending_churn']}")
    if cfg["mode"] == "private":
        hits = leak_hits(run, start)
        if hits:
            checks_report(steps, "fail", "rollback", "the round points at the review", hits=hits[:20])
    # Test lines that predate the task may not change.
    old_lines = []
    for f in ch["test"]:
        if f in ch["new"] or not at_ref(root, start, f):
            continue
        was = set(git(root, "show", f"{start}:{f}").splitlines())
        diff = git(root, "diff", "-U0", "HEAD", "--", f).splitlines()
        old_lines += [f"{f}: {ln[1:].strip()}" for ln in diff
                      if ln.startswith("-") and not ln.startswith("---") and ln[1:] in was
                      and ln[1:].strip()]
    if old_lines:
        checks_report(steps, "fail", "rollback", "the round changes or removes test lines that "
                      "predate the task", lines=old_lines[:20])
    steps.append({"step": "changes", "ok": True, "size": ch["size"]})
    task_files = sorted(set(git(root, "diff", "--name-only", start, "HEAD").split())
                        | set(ch["test"]) | set(ch["production"]))
    globs = cfg.get("test_globs") or []
    prod = [f for f in task_files if not is_test(f, globs)]
    if a.test:
        test_file, test_name = split_test(a.test)
        patch = os.path.join(run.records, f"{a.task}.round.patch")
        with open(patch, "wb") as fh:
            fh.write(raw_diff(root, "HEAD", [f for f in prod if at_ref(root, "HEAD", f)]))
        aside = os.path.join(run.records, f"{a.task}.round-aside")
        moved, restored_extra = [], []
        for f in prod:
            if at_ref(root, before, f):
                if not at_ref(root, "HEAD", f):
                    restored_extra.append(f)
                git(root, "restore", f"--source={before}", "--", f)
            elif os.path.exists(os.path.join(root, f)):
                dst = os.path.join(aside, f)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(os.path.join(root, f), dst)
                moved.append(f)
        r = run_exec(run, "target", test_file, test_name, log_name=f"{a.task}-round-red")
        why = red_problem(r, test_name)
        back = [f for f in prod if at_ref(root, "HEAD", f)]
        if back:
            git(root, "restore", "--source=HEAD", "--", *back)
        for f in restored_extra:
            os.remove(os.path.join(root, f))
        for f in moved:
            shutil.move(os.path.join(aside, f), os.path.join(root, f))
        if os.path.getsize(patch) and subprocess.run(["git", "apply", patch], cwd=root).returncode:
            checks_report(steps, "fail", "rollback", "the round did not apply back after the red "
                          "check")
        if why:
            checks_report(steps, "fail", "rollback", f"red no longer holds: {why}", red_log=r.get("log"))
        steps.append({"step": "red", "ok": True, "log": r["log"]})
    if a.proof:
        proof_file, proof_name = split_test(a.proof)
        kept = os.path.join(run.records, f"{a.task}.proof.{os.path.basename(proof_file)}.txt")
        if not os.path.isfile(kept):
            die("no kept proof", kept=kept)
        shutil.copy2(kept, os.path.join(root, proof_file))
        r = run_exec(run, "target", proof_file, proof_name, log_name=f"{a.task}-round-proof")
        os.remove(os.path.join(root, proof_file))
        why = red_problem(r, proof_name)
        if why:
            checks_report(steps, "fail", "rollback", f"the proof no longer fails: {why}",
                          proof_log=r.get("log"))
        steps.append({"step": "proof", "ok": True, "log": r["log"]})
    if a.test:
        g = run_exec(run, "target", split_test(a.test)[0], None, log_name=f"{a.task}-round-green")
        if g.get("exit") != 0:
            checks_report(steps, "fail", "rollback", "the test file doesn't pass after the round",
                          green_log=g.get("log"))
        steps.append({"step": "green", "ok": True, "log": g["log"]})
    for which, files in (("related", task_files), ("check", None)):
        st = regression_run(run, which, f"{a.task}-round", files)
        steps.append(st)
        if not st["ok"]:
            checks_report(steps, "fail", "rollback", f"{which} failed twice: compare with "
                          "baseline.md and flaky.md; otherwise roll the round back")
    run.log(f"{a.task} check-round: passed")
    checks_report(steps, "ok")


# --- the subagent prompts, filled in --------------------------------------------------

PROMPTS = ("fixer", "seam", "notest-fixer", "builder", "checker", "harness-checker",
           "reviewer", "verifier")
MARKER = re.compile(r"`\[([^\]`]+?):?\]`\s?")


def marker_holds(name, ctx):
    n = name.rstrip(":").strip().upper()
    table = {
        "PRIVATE MODE ONLY": ctx["mode"] == "private",
        "USER NOTE ONLY": bool(ctx["note"]),
        "IF IT HAS PREREQUISITES ONLY": bool(ctx["deps"]),
        "PARTLY TESTABLE ONLY": ctx["kind"] == "partly",
        "NOTEST ONLY": ctx["kind"] == "notest",
        "HARNESS ONLY": ctx["kind"] == "harness",
        "RED-GREEN ONLY": ctx["kind"] in ("fix", "partly", "seam"),
        "SEAM COMMIT ONLY": bool(ctx["seam"]),
        "SEAM DOUBLE ONLY": bool(ctx["seam_double"]),
        "SEAM (HARNESS) ONLY": bool(ctx["harness_deps"]),
        "AND, IF THIS FINDING PROVED THAT HARNESS": bool(ctx["proved"]),
        "IF THE RUN FLAGGED ANYTHING": bool(ctx["flags"]),
        "RE-RATED ONLY": (ctx["reason"] or "").startswith("re-rated"),
        "ONE COMMIT ONLY": ctx["commits"] == "one",
        "SEPARATE COMMITS ONLY": ctx["commits"] != "one",
    }
    if n not in table:
        die(f"unknown prompt marker [{name}]")
    return table[n]


SENTENCE_END = re.compile(r"\.(?=\s|$)")
ITEM = re.compile(r"\s*[-*] ")


def select_blocks(text, ctx):
    """Keep what applies. A marker that starts a paragraph or a list item gates
    all of it; a marker inside a paragraph gates the rest of its sentence."""
    kept = []
    for block in re.split(r"\n[ \t]*\n", text):
        items, cur = [], []
        for ln in block.split("\n"):
            if ITEM.match(ln) and cur:
                items.append(cur)
                cur = []
            cur.append(ln)
        items.append(cur)
        lines = []
        for it in items:
            m = re.match(r"(\s*[-*] )" + MARKER.pattern, it[0])
            if m:
                if not marker_holds(m.group(2), ctx):
                    continue
                it = [m.group(1) + it[0][m.end():]] + it[1:]
            lines.extend(it)
        block = "\n".join(lines)
        m = MARKER.match(block)
        if m:
            if not marker_holds(m.group(1), ctx):
                continue
            block = block[m.end():]
        while True:
            m = MARKER.search(block)
            if not m:
                break
            if marker_holds(m.group(1), ctx):
                block = block[:m.start()] + block[m.end():]
                continue
            e = SENTENCE_END.search(block, m.end())
            stop = e.end() if e else len(block)
            block = block[:m.start()] + block[stop:].lstrip(" ")
        block = "\n".join(ln.rstrip() for ln in block.split("\n") if ln.strip())
        if block.strip():
            kept.append(block)
    return "\n\n".join(kept)


def cmd_prompt(a, run):
    """Fill in a subagent's prompt from the run's state, and write it to a file
    the subagent is told to read."""
    cfg, data = run.cfg(), run.tasks()
    t = data["tasks"].get(a.task)
    if t is None:
        die("unknown task", task=a.task)
    # The run's own copy, which init made; the skill's, for a run made before that.
    path = os.path.join(HERE, "prompts", f"{a.role}-prompt.md")
    if not os.path.isfile(path):
        path = os.path.join(HERE, "..", "reference", f"{a.role}-prompt.md")
    if not os.path.isfile(path):
        die("no such prompt", path=path)
    text = open(path, encoding="utf-8").read()
    text = text.split("\n---\n", 1)[1] if "\n---\n" in text else text
    deps = deps_info(run, data, t)
    harness_deps = [d for d in deps if d["kind"] == "harness"]
    proved = [d for d in harness_deps if (d.get("proof") or "").split(":")[0].strip() == t["id"]
              and d.get("proof_copy")]
    ch = compute_changes(run, "HEAD") if a.role in ("checker", "harness-checker") else None
    ctx = {"mode": cfg["mode"], "note": t.get("note"), "deps": deps, "kind": t["kind"],
           "seam": a.seam, "seam_double": a.seam_double, "harness_deps": harness_deps,
           "proved": proved, "flags": (ch or {}).get("flags"), "reason": t.get("reason"),
           "commits": cfg.get("commits", "separate")}
    text = select_blocks(text, ctx)
    # A placeholder the prose wrapped across lines reads as one.
    text = re.sub(r"<([^<>`]*?\n[^<>`]*?)>", lambda m: "<" + " ".join(m.group(1).split()) + ">",
                  text)
    state, root = run.state.replace("\\", "/"), run.root.replace("\\", "/")
    globs = cfg.get("test_globs") or []
    run_test = (f'python "{state}/bin/fix_run.py" --root "{root}" exec target --file <test file> '
                f'--test-name "<test name>"')
    values = {
        "root": root, "state": state, "branch": cfg["branch"], "pin": cfg.get("pin") or "",
        "review dir": cfg["review_dir"].replace("\\", "/"), "commit style": cfg.get("commit_style") or "",
        "the note": t.get("note") or "", "ID": t["id"], "X-ID": t["id"], "start": t.get("start") or "",
        "run a test": run_test, "the task's reason": t.get("reason") or "",
        "each ID and its title": "; ".join(f"{d['id']}: {d['title']}" for d in deps),
    }
    if harness_deps:
        h = harness_deps[0]
        values.update({"X-ID": h["id"], "its title": h["title"], "probe": h.get("probe") or ""})
        if proved:
            values["name"] = os.path.basename(proved[0]["proof_copy"]).split(".proof.", 1)[1][:-4]
    if t["kind"] == "harness":
        values["X-ID"] = t["id"]
        own = sorted(f for f in os.listdir(run.records) if f.startswith(f"{t['id']}.proof."))
        if own:
            values["name"] = own[0].split(".proof.", 1)[1][:-4]
    if a.seam:
        values["seam sha"] = a.seam
    if a.seam_line:
        values["the seam agent's line"] = a.seam_line
    if a.round:
        values["n"] = str(a.round)
    if ch is not None:
        values["prod and test files and lines"] = ch["size"]
    if a.proof_file:
        values["proof file"] = a.proof_file
    if a.claim:
        kind, _, line = a.claim.partition(":")
        values["GONE | REFUTED | PRESENT"] = kind.strip()
        values["the subagent's one line"] = line.strip()
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        values[k.strip()] = v
    if ctx["flags"]:
        text = re.sub(r"(\*\*The run flagged:\*\* )`<[^<>`]*>`", lambda m: m.group(1) +
                      "; ".join(ctx["flags"]), text, flags=re.S)
    if "test path globs" not in values:
        text = text.replace("`<test path globs>`", ", ".join(f"`{g}`" for g in globs))
        values["test path globs"] = ", ".join(globs)
    for k, v in values.items():
        text = text.replace(f"<{k}>", v)
    # What the session was meant to fill and didn't: a gap in the prompt.
    fillable = ("seam sha", "the seam agent's line", "n", "proof file", "GONE | REFUTED | PRESENT",
                "the subagent's one line", "prod and test files and lines")
    missing = sorted({k for k in fillable if f"<{k}>" in text})
    if missing:
        die("the prompt needs values the run doesn't have: pass them", missing=missing,
            options="--seam, --seam-line, --round, --proof-file, --claim, --set key=value")
    outdir = os.path.join(run.state, "prompts")
    os.makedirs(outdir, exist_ok=True)
    name = f"{t['id']}-{a.role}" + (f"-{a.round}" if a.round else "") + ".md"
    dest = os.path.join(outdir, name)
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.strip() + "\n")
    out({"result": "ok", "prompt": dest,
         "spawn": f"Your full instructions are in `{dest}`. Read that file in full first and "
                  "follow it exactly; it is your task, written by the session that started you. "
                  "Your final response must be only the one line it asks for."})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="the code root (default: found from the current directory)")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("init", help="create a run from a review")
    s.add_argument("--review", required=True)
    s.add_argument("--run", required=True)
    s.add_argument("--branch", help="the run branch, created at the base; omit with --in-place")
    s.add_argument("--in-place", action="store_true",
                   help="run on the branch checked out, committing to it directly")
    s.add_argument("--commits", choices=("separate", "one"), default=None,
                   help="separate: test, fix, seam, harness and round commits as made (default); "
                        "one: each finding squashed into a single commit before done")
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
    s.add_argument("--like", metavar="RUN",
                   help="a follow-up run: take the commands, test paths, commit style and review "
                        "context from this earlier, finished run of the same review")
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
    s.add_argument("--file", help="the test file, for {file} and {dir} in `target`")
    s.add_argument("--test-name")
    s.add_argument("--log")
    s.add_argument("--tail", type=int, default=15)

    s = sp.add_parser("guard", help="after a subagent: nothing escaped")
    s.add_argument("--head", help="the HEAD the tree should still be at")
    s = sp.add_parser("changes", help="what changed since a commit, sorted for the checks")
    s.add_argument("--since", help="default: the task's start")
    s = sp.add_parser("scan", help="private mode: leak scan of added lines and messages")
    s.add_argument("--since", required=True)
    s = sp.add_parser("set-status", help="rewrite a finding's Status bullet: committed mode before "
                                         "its commit, private mode once the task is done")
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

    s = sp.add_parser("squash", help="one-commit mode: fold the task's commits into one")
    s.add_argument("task")
    s.add_argument("--message", required=True, help="a file holding the final commit message")
    s = sp.add_parser("check-fix", help="the checks on a fix: changes, scan, red, green, "
                                         "regression run and check")
    s.add_argument("task")
    s.add_argument("--test", required=True, help="<test file>::<test name>")
    s = sp.add_parser("check-round", help="the checks on an improvement round")
    s.add_argument("task")
    s.add_argument("--test", help="<test file>::<test name>, for a task with a red-green test")
    s.add_argument("--before", help="the code before the fix (default: the task's start; for a "
                                     "seam task, the seam commit)")
    s.add_argument("--proof", help="a harness: <proof file>::<test name>, its kept proof")
    s = sp.add_parser("prompt", help="fill in a subagent's prompt and write it to a file")
    s.add_argument("task")
    s.add_argument("role", choices=PROMPTS)
    s.add_argument("--round", type=int, help="the review round, for the reviewer")
    s.add_argument("--seam", help="the seam commit, for the fixer and checker of a seam task")
    s.add_argument("--seam-double", action="store_true", help="the seam is a test double")
    s.add_argument("--seam-line", help="the seam agent's one line")
    s.add_argument("--proof-file", help="the proof file, for the harness checker")
    s.add_argument("--claim", help="the claim to verify, as GONE|REFUTED|PRESENT: <one line>")
    s.add_argument("--set", action="append", help="<placeholder>=<value>, for anything else")
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
