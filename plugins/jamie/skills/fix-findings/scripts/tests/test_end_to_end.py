"""End-to-end test of fix_run.py in a scratch Go repository with a synthetic review.

It drives a run the way the playbook does, without subagents: the session's
own steps stand in for the fixer, checker and reviewer. It covers an in-place
run in one-commit mode, a red check refusing a test that doesn't build,
check-fix, check-round (including a round that edits a test line older than
the task), squash and done, and prompt rendering for every role. It needs git
and Go on the path, and is skipped without them.

    python -m unittest discover -s scripts/tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
T = CODE = REVIEW = None
FAILS = []


def sh(*args, cwd=None, ok=True):
    p = subprocess.run(list(args), cwd=cwd or CODE, capture_output=True, text=True, encoding="utf-8")
    if ok and p.returncode:
        raise SystemExit(f"{args} failed: {p.stdout}{p.stderr}")
    return p


def fix(*args, script=None):
    script = script or os.path.join(CODE, ".fix-findings", "state", "bin", "fix_run.py")
    p = sh(sys.executable, script, "--root", CODE, *args, ok=False)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"raw": p.stdout + p.stderr}


def expect(name, cond, detail=""):
    if not cond:
        print(f"FAIL {name}: {detail}")
        FAILS.append(name)


def write(path, text):
    full = os.path.join(CODE, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w", encoding="utf-8", newline="\n").write(text)


FINDING = """# U01: calc

### U01-1: Add subtracts instead of adding
- **File:** calc.go:3
- **Severity:** High
- **Confidence:** High
- **Category:** arithmetic
- **Description:** `Add(2, 3)` returns -1.
- **Suggested fix:** Return `a + b`.
- **Fix impact:** Trivial — one operator.
- **RGR:** Unit — call `Add` and compare.
- **Status:** Open

## Coverage notes

Done.
"""
XFIND = """# Cross-cutting

### X-1: The calc package has no fake clock
- **File:** calc
- **Severity:** Low
- **Confidence:** High
- **Category:** testability
- **Description:** No clock double. 0 findings wait on it.
- **Suggested fix:** A fake clock.
- **Fix impact:** Low — test-only.
- **RGR:** Unit — proven when U01-1's test fails for its finding's reason.
- **Status:** Open
"""


def setup():
    global T, CODE, REVIEW
    T = tempfile.mkdtemp(prefix="fix-findings-e2e-")
    CODE, REVIEW = os.path.join(T, "code"), os.path.join(T, "review")
    os.makedirs(CODE)
    sh("git", "init", "-q", "-b", "main")
    sh("git", "config", "user.email", "t@example.com")
    sh("git", "config", "user.name", "T")
    sh("git", "config", "core.autocrlf", "false")
    write("go.mod", "module calc\n\ngo 1.21\n")
    write("calc.go", "package calc\n\n// Add adds.\nfunc Add(a, b int) int { return a - b }\n")
    write("calc_test.go", 'package calc\n\nimport "testing"\n\nfunc TestZero(t *testing.T) {\n'
          '\tif Add(0, 0) != 0 {\n\t\tt.Fatal("zero")\n\t}\n}\n')
    sh("git", "add", "-A")
    sh("git", "commit", "-q", "-m", "start\n\nThe calc package.")
    os.makedirs(os.path.join(REVIEW, "findings"))
    open(os.path.join(REVIEW, "findings", "U01-calc.md"), "w", encoding="utf-8").write(FINDING)
    open(os.path.join(REVIEW, "findings", "X-cross-cutting.md"), "w", encoding="utf-8").write(XFIND)
    open(os.path.join(REVIEW, "README.md"), "w", encoding="utf-8").write("# review\n")


def run_flow():
    skill_fix = os.path.join(SKILL, "scripts", "fix_run.py")
    pin = sh("git", "rev-parse", "HEAD").stdout.strip()

    r = fix("init", "--review", REVIEW, "--run", "t1", "--branch", "other", "--in-place",
            "--mode", "private", "--pin", pin, "--gate", "none", "--ids", "U01-1,X-1",
            "--dry-run", script=skill_fix)
    expect("in-place refuses a different --branch", "error" in r, r)
    r = fix("init", "--review", REVIEW, "--run", "t1", "--in-place", "--commits", "one",
            "--mode", "private", "--pin", pin, "--gate", "none", "--ids", "U01-1,X-1",
            "--note", "U01-1=Keep Add's signature.", script=skill_fix)
    expect("init in place", r.get("created") is True, r)
    cfg = fix("config")
    expect("in place on main, one commit", cfg.get("branch") == "main" and cfg.get("in_place")
           and cfg.get("commits") == "one", {k: cfg.get(k) for k in ("branch", "in_place", "commits")})
    state = os.path.join(CODE, ".fix-findings", "state")
    rel = os.path.join(state, "bin", "go_related.py")
    for kv in (f'commands.target=go test -count=1 ./{{dir}} -run "{{name}}"',
               f'commands.related=python "{rel}" {{files}}',
               "commands.suite=go test -count=1 ./...", "commands.check=go vet ./...",
               'test_globs=["**/*_test.go", "*_test.go"]', "commit_style=Plain sentences."):
        fix("config", "--set", kv)
    fix("lock", "--take")
    expect("baseline suite", fix("exec", "suite").get("exit") == 0)
    fix("baseline")
    t = fix("next")
    expect("picks U01-1 on main", t.get("task", {}).get("id") == "U01-1", t)

    # prompts: one-commit, private, with a note
    p = fix("prompt", "U01-1", "fixer")
    text = open(p["prompt"], encoding="utf-8").read() if "prompt" in p else ""
    expect("fixer prompt rendered", bool(text), p)
    expect("fixer: no markers or session placeholders left",
           "`[" not in text and "<root>" not in text and "<state>" not in text
           and "<commit style>" not in text and "<the note>" not in text, text[:300])
    expect("fixer: one-commit body, not separate bodies",
           "**Commit body.**" in text and "`### test`" not in text, "")
    expect("fixer: private sentences kept", "nothing may point at the review" in text)
    expect("fixer: note included", "Keep Add's signature." in text)
    expect("fixer: no seam or harness paragraphs", "The seam is in place" not in text
           and "The harness is in place" not in text and "This finding is partly testable" not in text)
    expect("fixer: subagent placeholders kept", "<test file>" in text and "<one clause>" in text)
    expect("fixer: branch is main", "`main`" in text)

    # red that doesn't build: the test names a constant the fix adds
    write("calc_test.go", 'package calc\n\nimport "testing"\n\nfunc TestZero(t *testing.T) {\n'
          '\tif Add(0, 0) != 0 {\n\t\tt.Fatal("zero")\n\t}\n}\n\n'
          'func TestAddSumsTwoNumbers(t *testing.T) {\n\tif Add(2, 3) != five {\n'
          '\t\tt.Fatalf("Add(2, 3) = %d", Add(2, 3))\n\t}\n}\n')
    write("calc.go", "package calc\n\nconst five = 5\n\n// Add adds.\nfunc Add(a, b int) int { return a + b }\n")
    r = fix("check-fix", "U01-1", "--test", "calc_test.go::TestAddSumsTwoNumbers")
    expect("check-fix: a test that doesn't build is no-red",
           r.get("result") == "fail" and r.get("category") == "no-red" and "build" in r.get("why", ""), r)
    expect("check-fix: the fix is back after a failed red",
           "a + b" in open(os.path.join(CODE, "calc.go"), encoding="utf-8").read())

    # a proper red-green
    write("calc_test.go", open(os.path.join(CODE, "calc_test.go"), encoding="utf-8").read()
          .replace("!= five", "!= 5"))
    write("calc.go", "package calc\n\n// Add adds.\nfunc Add(a, b int) int { return a + b }\n")
    r = fix("check-fix", "U01-1", "--test", "calc_test.go::TestAddSumsTwoNumbers")
    expect("check-fix passes", r.get("result") == "ok", r)
    expect("check-fix: red lines name the test", any("TestAddSumsTwoNumbers" in ln
                                                     for ln in r.get("red_lines", [])), r.get("red_lines"))
    c = fix("prompt", "U01-1", "checker")
    ctext = open(c["prompt"], encoding="utf-8").read() if "prompt" in c else ""
    expect("checker prompt: size filled, red-green paragraph",
           "<prod and test files and lines>" not in ctext and "test.patch" in ctext
           and "There is no red test" not in ctext, c)

    sh("git", "add", "calc.go", "calc_test.go")
    sh("git", "commit", "-q", "-m", "Make Add add\n\nAdd subtracted.")
    rv = fix("prompt", "U01-1", "reviewer", "--round", "1")
    rtext = open(rv["prompt"], encoding="utf-8").read() if "prompt" in rv else ""
    expect("reviewer prompt: round and start filled, one-commit paragraph",
           "## Round 1" in rtext and "<start>" not in rtext and "squashes your" in rtext, rv)
    r = fix("prompt", "U01-1", "reviewer")
    expect("reviewer prompt without --round asks for it", "error" in r and "n" in r.get("missing", []), r)

    # an improvement round that touches a test line from before the task: rolled back
    write("calc_test.go", open(os.path.join(CODE, "calc_test.go"), encoding="utf-8").read()
          .replace('t.Fatal("zero")', 't.Fatal("Add(0, 0) is not zero")'))
    r = fix("check-round", "U01-1", "--test", "calc_test.go::TestAddSumsTwoNumbers")
    expect("check-round: an edited pre-existing test line is a rollback",
           r.get("category") == "rollback" and "predate" in r.get("why", ""), r)
    sh("git", "checkout", "--", "calc_test.go")
    # a good round: a clearer comment
    write("calc.go", "package calc\n\n// Add returns the sum of a and b.\nfunc Add(a, b int) int { return a + b }\n")
    r = fix("check-round", "U01-1", "--test", "calc_test.go::TestAddSumsTwoNumbers")
    expect("check-round passes, red still holds", r.get("result") == "ok"
           and any(s.get("step") == "red" for s in r.get("steps", [])), r)
    expect("check-round leaves the round in place",
           "returns the sum" in open(os.path.join(CODE, "calc.go"), encoding="utf-8").read())
    sh("git", "add", "calc.go")
    sh("git", "commit", "-q", "-m", "Say what Add returns\n\nA clearer comment.")

    fix("exec", "suite")
    r = fix("done", "U01-1", "--outcome", "fixed", "--test", "calc_test.go::TestAddSumsTwoNumbers",
            "--rounds-run", "1")
    expect("done refuses two commits in one-commit mode", r.get("result") == "not-squashed", r)
    msg = os.path.join(state, "records", "U01-1.final.msg")
    open(msg, "w", encoding="utf-8", newline="\n").write(
        "Make Add add\n\nAdd subtracted its arguments; it now returns their sum.\n")
    tree = sh("git", "rev-parse", "HEAD^{tree}").stdout.strip()
    r = fix("squash", "U01-1", "--message", msg)
    expect("squash", r.get("result") == "squashed", r)
    expect("squash keeps the tree", sh("git", "rev-parse", "HEAD^{tree}").stdout.strip() == tree)
    r = fix("done", "U01-1", "--outcome", "fixed", "--test", "calc_test.go::TestAddSumsTwoNumbers",
            "--rounds-run", "1")
    expect("done after squash records one commit", r.get("result") == "done"
           and len(r.get("commits", [])) == 1, r)
    expect("still on main, linear", sh("git", "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
           and sh("git", "rev-list", "--count", "HEAD").stdout.strip() == "2")

    # the builder and the verifier
    t = fix("next")
    expect("picks X-1", t.get("task", {}).get("id") == "X-1", t)
    b = fix("prompt", "X-1", "builder")
    btext = open(b["prompt"], encoding="utf-8").read() if "prompt" in b else ""
    expect("builder prompt: one-commit harness body", "### harness" in btext
           and "### production" not in btext and "<X-ID>" not in btext, b)
    v = fix("prompt", "X-1", "verifier", "--claim", "PRESENT: a fake clock exists in clock_test.go")
    vtext = open(v["prompt"], encoding="utf-8").read() if "prompt" in v else ""
    expect("verifier prompt: claim filled, <path> kept", "`PRESENT`: `a fake clock exists" in vtext
           and "<path>" in vtext, v)

    # the selection rules, in separate-commit and committed mode
    sys.path.insert(0, os.path.join(SKILL, "scripts"))
    import fix_run
    sample = ("Intro.\n\n`[SEPARATE COMMITS ONLY:]` **Bodies.** Draft two. "
              "`[PRIVATE MODE ONLY:]` Hide the review. Write them down.\n\n"
              "`[ONE COMMIT ONLY:]` **Body.** Draft one.\n\n"
              "- keep\n- `[PRIVATE MODE ONLY:]` private item\n  continued\n- last\n")
    ctx = {"mode": "committed", "note": None, "deps": [], "kind": "fix", "seam": None,
           "seam_double": False, "harness_deps": [], "proved": [], "flags": [], "reason": None,
           "commits": "separate"}
    got = fix_run.select_blocks(sample, ctx)
    expect("select: separate, committed", "Draft two." in got and "Hide the review" not in got
           and "Write them down." in got and "Draft one" not in got and "private item" not in got
           and "continued" not in got and "- last" in got and "- keep" in got, got)
    ctx.update(mode="private", commits="one")
    got = fix_run.select_blocks(sample, ctx)
    expect("select: one, private", "Draft two" not in got and "Draft one." in got
           and "private item" in got and "continued" in got, got)

    return FAILS


def _remove(path):
    def writable(fn, p, _):
        os.chmod(p, 0o666)
        fn(p)
    shutil.rmtree(path, onerror=writable)


@unittest.skipUnless(shutil.which("go") and shutil.which("git"), "needs go and git on the path")
class EndToEnd(unittest.TestCase):
    def test_run_on_a_scratch_go_repository(self):
        FAILS.clear()
        setup()
        try:
            failed = run_flow()
        finally:
            _remove(T)
        self.assertEqual(failed, [], "failed checks: " + ", ".join(failed))


if __name__ == "__main__":
    unittest.main()
