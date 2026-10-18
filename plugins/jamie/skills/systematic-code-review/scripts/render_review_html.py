#!/usr/bin/env python
"""Render a systematic code review's markdown to a browseable static HTML site.

Every `file.cpp:123` style reference is turned into a deep link into Azure DevOps
or GitHub at the right line, pinned to the commit the review was written
against. Code with no hosted copy is marked unlinked and its references stay
plain text. Usage:

    python render_review_html.py <review dir>                 # everything
    python render_review_html.py <review dir> SUMMARY.md findings/U01-foo.md
    python render_review_html.py <review dir> index           # just the index
    python render_review_html.py <review dir> --quiet         # during a run
    python render_review_html.py <review dir> --unresolved    # and list misses

--quiet prints only WARNING lines and one closing line, for an orchestrator
re-rendering after every unit without filling its context. While a run is
going, units backlog.md marks pending or in-progress get a placeholder card
instead of a page, since their docs may be half-written.

--unresolved adds one UNRESOLVED line per distinct reference that looks like a
source reference but didn't become a link, with the document it is in. It
uses the same regex and resolution as the links, so it covers every extension
the site links. References into a repo marked "links": false are expected to
stay plain and are not listed.

Everything specific to one review — its repos, their pinned commits, where
they are hosted, the review's path shorthand — comes from <review dir>/render.json,
so this file carries no project's names and can live anywhere. Output goes to
<review dir>/html/ mirroring the source layout. Nothing outside <review dir>/html/
is written, so the reviewed code can be read-only.
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import html as htmllib

import markdown

import review_format as rf

# File extensions a `file.ext:123` reference may carry. render.json's
# "extensions" adds to these.
LINK_EXT = (
    "c", "h", "cpp", "hpp", "cc", "cxx", "hh", "asm", "s", "map", "cfg", "cmd",
    "icf", "bat", "ps1", "sh", "py", "go", "rs", "java", "kt", "cs", "js", "jsx",
    "mjs", "cjs", "ts", "tsx", "css", "scss", "html", "sql", "json", "yaml", "yml",
    "xml", "toml", "proto",
)

# --- per-review configuration (render.json) -------------------------------------
#
# {
#   "title":       "Foo Deep-Dive Code Review",
#   "nav_label":   "Foo Review",            sidebar heading
#   "page_prefix": "Foo Review",            browser-tab titles
#   "ado_base":    "https://dev.azure.com/<org>/<project>/_git",   for Azure DevOps repos
#   "home_repo":   "Foo",
#   "repos": {
#     "Foo": {
#       "root":    "C:/src/Foo",            local working copy, read only
#       "bases":   ["src/", ""],            prefixes an unqualified ref may sit under
#       "version": "GC<40-char sha>",       GC<sha> commit, GB<name> branch, GT<name> tag
#       "host":    "ado",                   optional: "ado" (the default) or "github"
#       "ado_base": "...",                  ado: optional, when this repo is in another project
#       "github_base": "https://github.com/<owner>/<repo>",   github: required
#       "links": false                      optional: no hosted copy of this code (not
#                                           git, or on another host); its references
#                                           render as plain text and "version" is shown
#                                           only as a label, e.g. "tree sha256:1a2b3c4"
#     }
#   },
#   "subprojects": ["src/"],
#   "dir_roots":   ["src/", "inc/"],
#   "prefix_map":  [["Other/", "OtherRepo", ""]],
#   "extensions":  ["vhd"]                  optional: reference extensions to add
#                                           to LINK_EXT
# }
#
# Links are pinned to the exact commit each repo was reviewed at, NOT to a
# branch: the review's line numbers are only meaningful against that tree, and
# fixes landing afterwards would silently slide every reference off its target.
#
# The review-shorthand knobs:
#   home_repo   the repo an unqualified reference belongs to, and the winner when
#               a bare basename exists in more than one repo
#   subprojects candidate path prefixes inside home_repo. The one that dominates
#               a given document's **File:** lines wins for that document, so one
#               review's bare `src/foo.cpp` can land in one project in one unit
#               and another project in the next. First entry is the fallback
#               when a document gives no signal; on a tie the longest wins.
#
# A bare basename, or a partial path such as `orders/server.ts`, that matches
# several files resolves first to the file the finding's own **File:** line
# cites, then to a file in that file's directory. Partial paths also match as a
# path suffix when no base completes them.
#   dir_roots   directory names that begin a home-repo-relative reference
#   prefix_map  fully-qualified path prefixes: [doc prefix, repo, repo prefix]

REQUIRED = ("title", "home_repo", "repos")

# Where linked code can live, and the name messages and the index use for it.
HOSTS = {"ado": "Azure DevOps", "github": "GitHub"}

REVIEW = {}
REPOS = {}
FILE_INDEX = {}
AT_PIN = set()  # repos whose file index was read from the pinned commit
SITE_ROOT = ""  # the review directory's parent; only for printing paths
CURRENT_DOC = ""  # the document being rendered, for UNRESOLVED
UNRESOLVED = []   # (document, reference as written) that resolved to nothing

# Renders run unattended, often on every batch. A git call that wants
# credentials, such as `ls-remote` against a private remote, must fail rather
# than wait on a prompt or a Git Credential Manager window nobody will answer.
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")


def ref_re(exts):
    """file.ext:LINE[-LINE], plus trailing ",LINE" / "/LINE-LINE" continuations.

    Directory names may contain ( ) [ ], as in Next.js route folders such as
    `app/(app)/layout.tsx` or `projects/[id]/page.tsx`. A bracket that opens a
    reference without closing in it, as in `(src/foo.c:12)`, is peeled off in
    linkify_text.
    """
    alts = "|".join(re.escape(e) for e in sorted(set(exts), key=lambda e: (-len(e), e)))
    return re.compile(
        r"(?<![\w/.\\-])"
        r"((?:[A-Za-z0-9_.+\-()\[\]]+[/\\])*[A-Za-z0-9_.+\-]+"
        rf"\.(?:{alts}))"
        r":(\d+)(?:\s*-\s*(\d+))?"
        r"((?:\s*[,/]\s*\d+(?:\s*-\s*\d+)?)*)"
    )


def load_config(review_dir):
    path = os.path.join(review_dir, "render.json")
    if not os.path.isfile(path):
        sys.exit(f"no render.json in {review_dir} — see the header of this script")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        sys.exit(f"{path}: missing {', '.join(missing)}")
    if cfg["home_repo"] not in cfg["repos"]:
        sys.exit(f"{path}: home_repo {cfg['home_repo']!r} is not in repos")

    repos = {}
    for name, r in cfg["repos"].items():
        links = r.get("links", True)
        host = r.get("host", "ado")
        if host not in HOSTS:
            sys.exit(f"{path}: repo {name!r} has unknown host {host!r} "
                     f"(use {' or '.join(repr(h) for h in HOSTS)})")
        ado_base = r.get("ado_base", cfg.get("ado_base", ""))
        github_base = r.get("github_base", "")
        if links and not r.get("version"):
            sys.exit(f"{path}: repo {name!r} needs a version, or \"links\": false")
        if links and host == "ado" and not ado_base:
            sys.exit(f"{path}: repo {name!r} needs ado_base, or \"links\": false")
        if links and host == "github" and not github_base:
            sys.exit(f"{path}: repo {name!r} needs github_base "
                     f"(https://github.com/<owner>/<repo>), or \"links\": false")
        repos[name] = {
            "root": r["root"],
            "bases": tuple(r.get("bases", [""])),
            "version": r.get("version") or "",
            "host": host,
            "ado_base": ado_base.rstrip("/"),
            "github_base": github_base.rstrip("/"),
            "links": links,
        }
    review = {
        "title": cfg["title"],
        "nav_label": cfg.get("nav_label", cfg["title"]),
        "page_prefix": cfg.get("page_prefix", cfg["title"]),
        "home_repo": cfg["home_repo"],
        "subprojects": tuple(cfg.get("subprojects", [""])),
        "dir_roots": tuple(cfg.get("dir_roots", [])),
        "prefix_map": [(d, (repo, rp)) for d, repo, rp in cfg.get("prefix_map", [])],
    }
    for _, (repo, _) in review["prefix_map"]:
        if repo not in repos:
            sys.exit(f"{path}: prefix_map names unknown repo {repo!r}")
    exts = tuple(LINK_EXT) + tuple(e.lstrip(".").lower() for e in cfg.get("extensions", []))
    global REF_RE
    REF_RE = ref_re(exts)
    REVIEW.update(review)
    REPOS.update(repos)
    FILE_INDEX.update(build_index(review_dir, exts))


def build_index(review_dir, exts):
    """basename -> list of (repo, repo-relative posix path).

    Read from the pinned commit where it can be, because the review's
    references describe that tree. Fixes landing afterwards add, rename and
    delete files, and a working-tree index would resolve against those. The
    review directory itself is left out.
    """
    suffixes = tuple("." + e for e in exts)
    index = {}
    AT_PIN.clear()
    for repo, cfg in REPOS.items():
        root = cfg["root"]
        if not os.path.isdir(root):
            print(f"WARNING: {repo} working copy not found at {root} — "
                  f"bare filename references into it will not be linked")
            continue
        skip = review_rel(root, review_dir)
        paths = tree_at_pin(root, cfg["version"])
        if paths is None:
            paths = walk_tree(root)
        else:
            AT_PIN.add(repo)
        for rel in paths:
            if skip and (rel == skip or rel.startswith(skip + "/")):
                continue
            fn = rel.rsplit("/", 1)[-1]
            if fn.lower().endswith(suffixes):
                index.setdefault(fn, []).append((repo, rel))
    return index


def tree_at_pin(root, version):
    """Every path in the pinned commit, or None when it can't be read."""
    if not version.startswith(("GC", "GB", "GT")):
        return None
    out = git(root, "ls-tree", "-r", "--name-only", "-z", version[2:])
    if out is None:
        return None
    return [p for p in out.split("\0") if p]


def walk_tree(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            out.append(rel.replace("\\", "/"))
    return out


def review_rel(root, review_dir):
    """The review directory relative to `root`, or "" when it is outside it."""
    # realpath first: Windows paths can name the same directory two ways
    # (8.3 short names, case), and relpath compares them as text. Different
    # drives raise ValueError, and then the review cannot be inside the repo.
    try:
        rel = os.path.relpath(os.path.realpath(review_dir), os.path.realpath(root))
    except ValueError:
        return ""
    if rel.startswith("..") or os.path.isabs(rel):
        return ""
    return rel.replace(os.sep, "/")


def git(root, *args):
    try:
        # UTF-8 explicitly: the locale's code page (cp1252 on Windows) fails on
        # source files and paths that aren't ASCII.
        out = subprocess.run(("git",) + args, cwd=root, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=30,
                             env=GIT_ENV)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def check_pins(review_dir):
    """Verify each pinned commit is usable, and say so on stdout.

    Two independent failure modes:

    1. The working copy has moved off the pin. Bare filenames resolve against
       the pinned commit when it can be read, and then this is expected once
       fixes land. Otherwise they resolve against the working *tree*, so a
       moved or dirty checkout can resolve a path that did not exist at the
       pinned commit.
    2. The pin was never pushed. The host (Azure DevOps or GitHub) cannot show
       a commit it has never received, so every link into that repo 404s even
       though the sha is perfectly correct locally. This one is invisible from
       the generated HTML — it once cost a review thousands of dead links.

    Every git call here is read-only, so it is safe against a code root the
    review may not write to. A review stored inside the repo it reviews is
    excluded from both checks: its own files, and commits of them, say
    nothing about whether the code moved.
    """
    for repo, cfg in REPOS.items():
        root, version = cfg["root"], cfg["version"]
        if not cfg["links"]:
            print(f"NOTE: {repo} is not linked — its references render as "
                  f"plain text")
            continue
        if not os.path.isdir(root) or not version.startswith("GC"):
            continue
        pinned = version[2:]
        spec = code_pathspec(root, review_dir)

        head = git(root, "rev-parse", "HEAD")
        if head and head != pinned:
            same = git(root, "diff", "--quiet", pinned, "HEAD", *spec)
            if same is None and repo in AT_PIN:
                print(f"NOTE: {repo} is at {head[:7]}, past the pin {pinned[:7]}, "
                      f"and the code differs — expected once fixes land; "
                      f"filenames resolve against the pinned commit")
            elif same is None:
                print(f"WARNING: {repo} is at {head[:7]} but links are pinned "
                      f"to {pinned[:7]} and the code differs — file resolution "
                      f"uses the working tree, so verify new/renamed files "
                      f"before trusting those links")
        if repo not in AT_PIN:
            dirty = git(root, "--no-optional-locks", "status", "--porcelain", *spec)
            if dirty:
                print(f"WARNING: {repo} working tree is dirty "
                      f"({len(dirty.splitlines())} paths) — same caveat as above")

        state, detail = pin_on_remote(root, pinned)
        if state is True:
            print(f"pin OK: {repo} {pinned[:7]} is on {detail}")
        elif state is False:
            print(f"WARNING: {repo} {pinned[:7]} is NOT on the remote — every "
                  f"link into this repo will 404 on {HOSTS[cfg['host']]} until that commit is "
                  f"pushed; if it cannot be pushed, render with \"links\": false "
                  f"rather than re-pinning")
        else:
            print(f"NOTE: could not confirm {repo} {pinned[:7]} is on the "
                  f"remote ({detail}) — spot-check one link before publishing")


def code_pathspec(root, review_dir):
    """git pathspec for the code in `root`, leaving out the review itself."""
    rel = review_rel(root, review_dir)
    return ["--", ".", f":(exclude){rel}"] if rel else ["--", "."]


def pin_on_remote(root, pinned):
    """(True|False|None, detail): is `pinned` reachable from a remote branch?"""
    contains = git(root, "branch", "-r", "--contains", pinned)
    if contains:
        branches = [ln.strip().split(" ")[0] for ln in contains.splitlines()]
        named = [b for b in branches if not b.endswith("/HEAD")]
        # Name the remote's default branch when it holds the pin, not whichever
        # branch sorts first (a dependabot branch, say).
        default = git(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
        if default and default in named:
            return True, default
        return True, (named or branches)[0]

    # Not in the local remote-tracking refs — but those may simply be stale, so
    # ask the remote itself before crying wolf. Only when every remote branch tip
    # matches what we hold locally is "not contained" a real answer.
    heads = git(root, "ls-remote", "--heads", "origin")
    if not heads:
        return None, "remote unreachable"
    for ln in heads.splitlines():
        sha, ref = ln.split()
        name = ref[len("refs/heads/"):]
        if git(root, "rev-parse", "--verify", "-q", f"origin/{name}") != sha:
            return None, (f"origin/{name} is stale locally; a git fetch "
                          f"would settle it, if you may write to this repo")
    return False, "origin"


def source_url(repo, path, line, line_end=None):
    """Deep link to `path` at `line` (through `line_end`) in the repo's host."""
    if REPOS[repo]["host"] == "github":
        return github_url(repo, path, line, line_end)
    return devops_url(repo, path, line, line_end)


def github_url(repo, path, line, line_end=None):
    # GitHub highlights #L<a>-L<b> inclusive. It renders Markdown and other
    # markup files by default, where line anchors don't exist, so those get the
    # plain view.
    cfg = REPOS[repo]
    plain = "?plain=1" if path.lower().endswith(MARKUP_EXT) else ""
    anchor = f"#L{line}"
    if line_end and str(line_end) != str(line):
        anchor += f"-L{line_end}"
    return f"{cfg['github_base']}/blob/{cfg['version'][2:]}/{path}{plain}{anchor}"


MARKUP_EXT = (".md", ".markdown", ".rst", ".adoc", ".org", ".ipynb")


def commit_url(repo, sha):
    cfg = REPOS[repo]
    if cfg["host"] == "github":
        return f"{cfg['github_base']}/commit/{sha}"
    return f"{cfg['ado_base']}/{repo}/commit/{sha}"


def devops_url(repo, path, line, line_end=None):
    # DevOps selects [line, lineEnd) at column 1, so lineEnd must be the line
    # *after* the last line we want highlighted.
    line_end = int(line_end or line) + 1
    return (
        f"{REPOS[repo]['ado_base']}/{repo}?path=/{path}"
        f"&version={REPOS[repo]['version']}"
        f"&line={line}&lineEnd={line_end}&lineStartColumn=1&lineEndColumn=1"
        f"&lineStyle=plain&_a=contents"
    )


def resolve(ref, ctx):
    """Map a doc file reference to (repo, repo-relative path) or None.

    `ctx` is {"review": REVIEW, "subproject": <prefix for this doc>}.
    """
    review = ctx["review"]
    home = review["home_repo"]
    bases = candidate_bases(home, ctx)

    # Strip a leading "./" only — a character-class strip would eat the dot of
    # genuine paths like ".rel/Main.map".
    ref = ref.replace("\\", "/")
    while ref.startswith("./"):
        ref = ref[2:]

    # 1. Fully-qualified prefixes.
    for doc_pfx, (repo, repo_pfx) in review["prefix_map"]:
        if ref.startswith(doc_pfx):
            return repo, repo_pfx + ref[len(doc_pfx):]

    # 2. A path rooted at a known directory of the home repo.
    if ref.startswith(review["dir_roots"]):
        for base in bases:
            # `Source/Driver/x.c` already carries its own base — don't double it.
            candidate = ref if ref.startswith(base) else base + ref
            if lookup_exact(home, candidate):
                return home, candidate
        return None

    # 3. A bare basename: the file index decides, home repo winning ties.
    if "/" not in ref:
        matches = FILE_INDEX.get(ref, [])
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        matches = long_enough(matches, ctx)
        if len(matches) == 1:
            return matches[0]
        cited = prefer_cited(matches, ctx)
        if cited:
            return cited
        for base in bases:
            for repo, path in matches:
                if repo == home and path.startswith(base):
                    return repo, path
        home_matches = [m for m in matches if m[0] == home]
        if len(home_matches) == 1:
            return home_matches[0]
        return None

    # 4. Some other relative path — including a cross-repo one, cited without
    # the other repo's prefix.
    for repo in [home] + [r for r in REPOS if r != home]:
        for base in candidate_bases(repo, ctx):
            if lookup_exact(repo, base + ref):
                return repo, base + ref

    # 5. A partial path that no base completes, e.g. `orders/server.ts` for
    # `src/app/orders/server.ts`: match it as a path suffix.
    tails = [(r, p) for r, p in FILE_INDEX.get(ref.rsplit("/", 1)[-1], [])
             if p.endswith("/" + ref)]
    tails = long_enough(tails, ctx)
    if len(tails) == 1:
        return tails[0]
    return prefer_cited(tails, ctx) if tails else None


def long_enough(matches, ctx):
    """Drop candidates shorter than the cited line, unless that drops them all.

    A reference to line 337 can't mean a 168-line file of the same name.
    """
    line = ctx.get("line")
    if not line or len(matches) < 2:
        return matches
    kept = [(r, p) for r, p in matches if line_count(r, p) >= line]
    return kept or matches


LINE_COUNTS = {}  # (repo, path) -> lines at the pin (or in the working tree), 0 if unreadable


def line_count(repo, path):
    # Bytes, not text: source files are often UTF-8 while git's output would be
    # decoded in the console's code page.
    if (repo, path) not in LINE_COUNTS:
        cfg = REPOS[repo]
        data = None
        if repo in AT_PIN:
            try:
                out = subprocess.run(("git", "show", f"{cfg['version'][2:]}:{path}"),
                                     cwd=cfg["root"], capture_output=True, timeout=30,
                                     env=GIT_ENV)
                data = out.stdout if out.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                data = None
        if data is None:
            try:
                with open(os.path.join(cfg["root"], path), "rb") as fh:
                    data = fh.read()
            except OSError:
                data = b""
        LINE_COUNTS[repo, path] = len(data.splitlines())
    return LINE_COUNTS[repo, path]


def prefer_cited(matches, ctx):
    """Among several matches, the file the finding cites, or one beside it.

    `ctx["files"]` lists the finding's own **File:** path first, then every
    **File:** path in the document. Reviewers often cite a sibling as a bare
    basename (`handlers.ts:475`) in a repo that has many files of that name.
    """
    home = ctx["review"]["home_repo"]
    files = ctx.get("files", [])
    for f in files:
        for repo, path in matches:
            if repo == home and path == f:
                return repo, path
    for f in files:
        d = f.rsplit("/", 1)[0] + "/" if "/" in f else ""
        beside = [(r, p) for r, p in matches
                  if r == home and d and p.startswith(d) and "/" not in p[len(d):]]
        if len(beside) == 1:
            return beside[0]
    return None


def candidate_bases(repo, ctx):
    """Path prefixes to try inside `repo`, this document's own subproject first."""
    review = ctx["review"]
    ordered = []
    if repo == review["home_repo"]:
        ordered.append(ctx["subproject"])
        ordered += list(review["subprojects"])
    ordered += list(REPOS[repo]["bases"])
    seen, out = set(), []
    for b in ordered:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def lookup_exact(repo, path):
    base = path.rsplit("/", 1)[-1]
    return (repo, path) in FILE_INDEX.get(base, [])


# --- reference linkification --------------------------------------------------

REF_RE = ref_re(LINK_EXT)  # load_config adds render.json's "extensions"

CONT_RE = re.compile(r"(\s*[,/]\s*)(\d+)(?:(\s*-\s*)(\d+))?")


def resolve_ref(raw_path, line, line_end, ctx):
    """(lead, path as written, target) for one REF_RE match.

    The one place a reference is resolved, shared with rebase-findings'
    reanchor.py so the rebase moves exactly the references the site links.
    `lead` is a bracket that opens before the reference without closing in it,
    as in "(src/foo.c:12)": it belongs to the prose, not the path. The cited
    line goes into the context so that files too short to hold it are passed
    over.
    """
    lead = ""
    while raw_path[:1] in ("(", "[") and raw_path.count(raw_path[0]) > \
            raw_path.count(")" if raw_path[0] == "(" else "]"):
        lead, raw_path = lead + raw_path[0], raw_path[1:]
    return lead, raw_path, resolve(raw_path, dict(ctx, line=int(line_end or line)))


def finding_files(text):
    """{finding ID: the path its **File:** line cites}, for per-finding contexts."""
    _, findings, _ = split_findings(text.replace("\r\n", "\n"))
    out = {}
    for f in findings:
        own = (field(f["body"], "File") or "").split()
        if own:
            out[f["id"]] = own[0].rsplit(":", 1)[0]
    return out


def linkify_text(text, ctx):
    """Linkify refs in a plain-text (already HTML-escaped) fragment."""

    def sub(m):
        raw_path, line, line_end, cont = m.group(1), m.group(2), m.group(3), m.group(4)
        lead, raw_path, target = resolve_ref(raw_path, line, line_end, ctx)
        label = f"{raw_path}:{line}" + (f"-{line_end}" if line_end else "")
        if not target:
            UNRESOLVED.append((CURRENT_DOC, htmllib.unescape(label)))
            return m.group(0)
        if not REPOS[target[0]]["links"]:
            return m.group(0)
        repo, path = target
        url = source_url(repo, path, line, line_end)
        out = lead + f'<a class="srcref" href="{url}" target="_blank" rel="noopener">{label}</a>'
        if cont:
            def sub_cont(c):
                sep, a, b = c.group(1), c.group(2), c.group(4)
                u = source_url(repo, path, a, b)
                lbl = a + (f"-{b}" if b else "")
                return (f'{sep}<a class="srcref" href="{u}" target="_blank"'
                        f' rel="noopener">{lbl}</a>')
            out += CONT_RE.sub(sub_cont, cont)
        return out

    return REF_RE.sub(sub, text)


TAG_RE = re.compile(r"<[^>]+>")


def linkify_html(html_text, ctx):
    """Walk rendered HTML, linkifying text nodes only and never inside <a>."""
    out = []
    pos = 0
    depth_a = 0
    for m in TAG_RE.finditer(html_text):
        chunk = html_text[pos:m.start()]
        out.append(chunk if depth_a else linkify_text(chunk, ctx))
        tag = m.group(0)
        low = tag.lower()
        if low.startswith("<a "):
            depth_a += 1
        elif low.startswith("</a"):
            depth_a = max(0, depth_a - 1)
        out.append(tag)
        pos = m.end()
    tail = html_text[pos:]
    out.append(tail if depth_a else linkify_text(tail, ctx))
    return "".join(out)


# --- markdown -> html ---------------------------------------------------------

MD_LINK_RE = re.compile(r"\]\((?!https?:|#)([^)\s]+?)\.md(#[^)\s]*)?\)")


def rewrite_internal_links(src):
    return MD_LINK_RE.sub(lambda m: f"]({m.group(1)}.html{m.group(2) or ''})", src)


def context_for(src, review):
    """Which home-repo subproject this document's unqualified refs point into."""
    subprojects = review["subprojects"]
    counts = {}
    for m in re.finditer(r"\*\*File:\*\*\s*(\S+)", src):
        for pref in subprojects:
            if m.group(1).startswith(pref):
                counts[pref] = counts.get(pref, 0) + 1
    if counts:
        best = max(counts.values())
        # "" matches every path, so on a tie the most specific prefix wins.
        chosen = max((p for p, c in counts.items() if c == best), key=len)
    else:
        chosen = subprojects[0]
    files = [m.group(1).rsplit(":", 1)[0]
             for m in re.finditer(r"\*\*File:\*\*\s*(\S+)", src)]
    return {"review": review, "subproject": chosen, "files": files}


def md(src):
    return markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"permalink": False}},
    ).convert(src)


def md_inline(src):
    """Render a one-line fragment without the wrapping paragraph."""
    out = md(src).strip()
    if out.startswith("<p>") and out.endswith("</p>"):
        out = out[3:-4]
    return out


# The scales themselves, and how a field's level and correction markers are
# read, live in review_format.py, shared with review_state.py and reanchor.py.
SEV_ORDER = {s: i for i, s in enumerate(rf.SEVERITIES)}
FIX_LEVELS = rf.FIX_IMPACTS
# Whether a failing test can prove the defect, then its fix. A "No" that a test
# reaches part of says "partly testable" and gets its own filter value.
# A Seam says which kind it is, "Seam (local)" or "Seam (harness)"; a review
# written before that distinction says plain "Seam" and renders as before.
RGR_LEVELS = ("Unit", "Seam", "No")
RGR_FILTER = (("unit", "Unit"), ("seam-local", "Seam (local)"),
              ("seam-harness", "Seam (harness)"), ("seam", "Seam"),
              ("partly", "No, partly testable"), ("no", "No"))
SEAM_KIND_RE = re.compile(r"Seam\s*\((local|harness)\)", re.I)


def rgr_label(f):
    """The filter label for the finding's rating: its level, a Seam's kind,
    or "No, partly testable"."""
    return dict(RGR_FILTER)[rgr_slug(f)]


def rgr_slug(f):
    if f["rgr"] == "No" and f["rgr_partly"]:
        return "partly"
    if f["rgr"] == "Seam" and f["seam_kind"]:
        return "seam-" + f["seam_kind"]
    return f["rgr"].lower()
# Every status but Open is a disposition: the finding is handled, whether or
# not it is fixed. Planned means another system, such as a ticket, owns it now.
STATUS_LEVELS = rf.STATUSES
STATUS_CLASS = {"Open": "open", "Fixed": "fixed", "Planned": "planned",
                "Won't fix": "wontfix", "Not a defect": "notdefect"}
UNKNOWN_STATUS = "?"  # a Status bullet whose level isn't recognised


def is_open(f):
    """Open, or with no recognisable status. Either way, nobody has decided."""
    return f["status"] not in STATUS_LEVELS[1:]


def status_level(raw):
    return rf.current_level(raw, STATUS_LEVELS) or UNKNOWN_STATUS


FINDING_RE = rf.FINDING_RE
FINDING_ID_RE = rf.FINDING_ID_RE
DEPENDS_LINE_RE = re.compile(r"^-\s+\*\*Depends on:\*\*.*$", re.M)
# Set by build_site: where each finding is rendered, and the reverse of every
# Depends on, so a prerequisite can list what is waiting on it.
FINDINGS_AT = {}   # finding ID -> its doc's stem
NEEDED_BY = {}     # finding ID -> [IDs that depend on it]


def id_sort_key(fid):
    """U29-10 sorts after U29-6, not before it."""
    head, _, tail = fid.rpartition("-")
    return (head, int(tail)) if tail.isdigit() else (fid, 0)


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s or "section"


def split_findings(src):
    """Split a findings doc into (preamble, [finding dicts], trailer)."""
    lines = src.split("\n")
    pre, findings, trailer = [], [], []
    cur = None
    for line in lines:
        m = FINDING_RE.match(line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "lines": []}
            findings.append(cur)
            continue
        if line.startswith("## ") and cur is not None:
            cur = None
            trailer.append(line)
            continue
        if cur is not None:
            cur["lines"].append(line)
        elif findings:
            trailer.append(line)
        else:
            pre.append(line)
    for f in findings:
        body = "\n".join(f["lines"])
        f["body"] = body
        # A verifier's correction reads "High (verifier: was Critical)" or
        # "High (verifier: was Medium)". The current level is the first word.
        sv = field(body, "Severity") or ""
        f["severity"] = rf.current_level(sv, rf.SEVERITIES) or "—"
        f["sev_revised"] = bool(rf.split_markers(sv)[1])
        f["confidence"] = field(body, "Confidence") or "—"
        f["category"] = field(body, "Category") or ""
        f["file"] = field(body, "File") or ""
        # Reviews written before the fix impact scale have no such bullet;
        # they render exactly as before, with no fix badge, column or filter.
        fi = field(body, "Fix impact") or ""
        f["fix"] = rf.current_level(fi, FIX_LEVELS)
        f["fix_revised"] = bool(rf.split_markers(fi)[1])
        # Likewise RGR: a review without it renders as before.
        rg_all = field(body, "RGR") or ""
        rg, rg_was = rf.split_markers(rg_all)
        f["rgr"] = rf.level(rg, RGR_LEVELS)
        f["rgr_revised"] = bool(rg_was)
        f["rgr_partly"] = "partly testable" in rg.lower()
        sk = SEAM_KIND_RE.match(rg) if f["rgr"] == "Seam" else None
        f["seam_kind"] = sk.group(1).lower() if sk else ""
        # Prerequisites, by finding ID: "Depends on: X-1, U12-4".
        # A verifier's correction keeps the old list in "(verifier: was …)";
        # only the IDs before it are current.
        f["depends"] = rf.ids_in(field(body, "Depends on") or "")
        f["verdict"] = rf.level(field(body, "Verification") or "", rf.VERDICTS)
        # Reviews written before the status scale have no such bullet; every
        # finding then counts as open and renders as before.
        st = field(body, "Status")
        f["status"] = status_level(st) if st is not None else ""
    return "\n".join(pre), findings, "\n".join(trailer)


def field(body, name):
    m = re.search(rf"^-\s+\*\*{name}:\*\*\s*(.*)$", body, re.M)
    if not m:
        return None
    return m.group(1).strip().rstrip(".")


# --- page templates -----------------------------------------------------------

CSS = """
:root {
  --bg: #ffffff; --fg: #1c1e21; --muted: #5c6470; --line: #e3e6ea;
  --panel: #f7f8fa; --accent: #0b5fa5; --code-bg: #f2f4f7;
  --crit: #b3261e; --high: #b25e00; --med: #7a6a00; --low: #4a5568;
  --ok: #1c7a4a; --bad: #8a1f1f;
  --blk-crit: #d32f2f; --blk-high: #f57c00; --blk-med: #f2c200; --blk-low: #3b82f6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --fg: #e6e8eb; --muted: #98a1ad; --line: #2b3138;
    --panel: #1b1f24; --accent: #6bb5ff; --code-bg: #22272d;
    --crit: #ff6b60; --high: #ffab52; --med: #d9c65a; --low: #9aa5b1;
    --ok: #5ed69a; --bad: #ff8a80;
    --blk-crit: #ef5350; --blk-high: #ff9800; --blk-med: #fdd835; --blk-low: #60a5fa;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--accent); }
.wrap { display: flex; align-items: flex-start; }
nav.side {
  position: sticky; top: 0; width: 250px; flex: 0 0 250px; height: 100vh;
  overflow-y: auto; border-right: 1px solid var(--line); background: var(--panel);
  padding: 18px 14px; font-size: 13px;
}
nav.side h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); margin: 18px 0 6px; }
nav.side a { display: block; padding: 3px 0; text-decoration: none; }
nav.side a:hover { text-decoration: underline; }
nav.side a.here { font-weight: 600; }
nav.side .unwritten { display: block; padding: 3px 0; color: var(--muted); }
main { flex: 1 1 auto; min-width: 0; padding: 28px 36px 120px; max-width: 1100px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 20px; margin-top: 2em; padding-bottom: 4px; border-bottom: 1px solid var(--line); }
h3 { font-size: 16px; margin-top: 1.6em; }
code, pre { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 13px; }
code { background: var(--code-bg); padding: 1px 4px; border-radius: 3px; }
pre { background: var(--code-bg); padding: 12px 14px; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { margin: 1em 0; padding: 8px 16px; border-left: 3px solid var(--accent);
  background: var(--panel); }
blockquote p { margin: .4em 0; }
table { border-collapse: collapse; margin: 1em 0; display: block; overflow-x: auto;
  max-width: 100%; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left;
  vertical-align: top; }
th { background: var(--panel); }
hr { border: 0; border-top: 1px solid var(--line); margin: 2em 0; }
a.srcref { text-decoration: none; border-bottom: 1px dotted var(--accent);
  white-space: nowrap; }
a.srcref:hover { background: var(--code-bg); }
.crumb { font-size: 13px; color: var(--muted); margin-bottom: 14px; }
.crumb a { text-decoration: none; }
p.pin { color: var(--muted); font-size: 11px; line-height: 1.7; margin: 4px 0 0; }
p.pin code { font-size: 11px; }

/* finding cards */
.finding { border: 1px solid var(--line); border-left: 4px solid var(--low);
  border-radius: 6px; padding: 4px 18px 10px; margin: 22px 0; background: var(--panel); }
.finding.sev-critical { border-left-color: var(--crit); }
.finding.sev-high { border-left-color: var(--high); }
.finding.sev-medium { border-left-color: var(--med); }
.finding h3 { margin-top: 14px; }
.finding h3 .fid { color: var(--muted); font-weight: 600; margin-right: 6px; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 4px; }
.badge { font-size: 11px; letter-spacing: .04em; text-transform: uppercase;
  border: 1px solid var(--line); border-radius: 10px; padding: 1px 8px; color: var(--muted);
  background: var(--bg); }
.badge.sev-critical { color: var(--crit); border-color: var(--crit); }
.badge.sev-high { color: var(--high); border-color: var(--high); }
.badge.sev-medium { color: var(--med); border-color: var(--med); }
.badge.v-confirmed { color: var(--ok); border-color: var(--ok); }
.badge.v-refuted { color: var(--bad); border-color: var(--bad); }
.badge.fix-trivial { color: var(--ok); border-color: var(--ok); }
.badge.fix-uncertain { color: var(--high); border-color: var(--high); }
.badge.rgr-unit, .badge.rgr-seam-local { color: var(--ok); border-color: var(--ok); }
.badge.rgr-no, .badge.rgr-partly { color: var(--high); border-color: var(--high); }
.filters .group { display: inline-flex; gap: 10px; align-items: center; }
.filters .group > span { color: var(--muted); }
.finding ul { padding-left: 20px; }
.finding > ul > li { margin: .45em 0; }
.finding > ul > li.verif, .finding > ul > li.status { background: var(--bg);
  border-left: 3px solid var(--muted); border-radius: 0 4px 4px 0; padding: 6px 10px;
  margin-left: -20px; list-style: none; }
.finding > ul > li.status { border-left-color: var(--ok); }
.finding.is-disp h3 { color: var(--muted); }
.badge.st-open { color: var(--fg); border-color: var(--fg); }
.badge.st-fixed { color: #fff; background: var(--ok); border-color: var(--ok); }
.badge.st-planned, .badge.st-wontfix, .badge.st-notdefect { color: var(--ok);
  border-color: var(--ok); }
tr.is-disp td { color: var(--muted); }
table td:first-child { white-space: nowrap; }

/* filter bar */
.filters { position: sticky; top: 0; z-index: 5; background: var(--bg);
  border-bottom: 1px solid var(--line); padding: 10px 0; margin-bottom: 6px;
  display: flex; flex-wrap: wrap; gap: 14px; align-items: center; font-size: 13px; }
.filters label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.filters input[type=search] { flex: 1 1 200px; min-width: 160px; padding: 5px 8px;
  border: 1px solid var(--line); border-radius: 4px; background: var(--panel);
  color: var(--fg); font: inherit; }
.count { color: var(--muted); }

/* index cards */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px; margin: 18px 0; }
.card { border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px;
  background: var(--panel); }
.card a { text-decoration: none; font-weight: 600; }
.card .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
.sev { display: flex; flex-direction: column; gap: 2px; margin: 6px 0 4px; }
.sev .row { display: flex; flex-wrap: wrap; gap: 2px; min-height: 9px; }
.sev .g { display: contents; }
.sev i { display: block; width: 9px; height: 9px; border-radius: 2px;
  background: var(--muted); }
.sev .crit i { background: var(--blk-crit); }
.sev .high i { background: var(--blk-high); }
.sev .med i { background: var(--blk-med); }
.sev .low i { background: var(--blk-low); }
.sev .g.disp i { background: transparent; box-shadow: inset 0 0 0 1.5px var(--muted); }
.sev .crit.disp i { box-shadow: inset 0 0 0 1.5px var(--blk-crit); }
.sev .high.disp i { box-shadow: inset 0 0 0 1.5px var(--blk-high); }
.sev .med.disp i { box-shadow: inset 0 0 0 1.5px var(--blk-med); }
.sev .low.disp i { box-shadow: inset 0 0 0 1.5px var(--blk-low); }
.card .open { color: var(--fg); font-weight: 600; }
.card.unfinished { background: transparent; border-style: dashed; color: var(--muted); }
.card.unfinished > span { font-weight: 600; }
@media (max-width: 800px) {
  .wrap { display: block; }
  nav.side { position: static; width: auto; height: auto; border-right: 0;
    border-bottom: 1px solid var(--line); }
  main { padding: 20px 16px 80px; }
}
"""

FILTER_JS = """
(function () {
  var bar = document.querySelector('.filters');
  if (!bar) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll('.finding'));
  var counter = bar.querySelector('.count');
  function apply() {
    function checked(group) {
      return Array.prototype.slice
        .call(bar.querySelectorAll('input[data-group=' + group + ']:checked'))
        .map(function (c) { return c.value; });
    }
    var sevs = checked('sev'), fixes = checked('fix'), sts = checked('status');
    var rgrs = checked('rgr');
    var hasFix = !!bar.querySelector('input[data-group=fix]');
    var hasRgr = !!bar.querySelector('input[data-group=rgr]');
    var hasSt = !!bar.querySelector('input[data-group=status]');
    var q = (bar.querySelector('input[type=search]').value || '').toLowerCase();
    var shown = 0;
    cards.forEach(function (c) {
      var fix = c.dataset.fix || '', rgr = c.dataset.rgr || '';
      var ok = sevs.indexOf(c.dataset.severity) !== -1 &&
               (!hasFix || !fix || fixes.indexOf(fix) !== -1) &&
               (!hasRgr || !rgr || rgrs.indexOf(rgr) !== -1) &&
               (!hasSt || sts.indexOf(c.dataset.status) !== -1) &&
               (!q || c.textContent.toLowerCase().indexOf(q) !== -1);
      c.hidden = !ok;
      if (ok) shown++;
    });
    if (counter) counter.textContent = shown + ' of ' + cards.length + ' shown';
  }
  bar.addEventListener('input', apply);
  apply();
})();
"""


def page(title, body, depth, nav_html, extra_js=""):
    up = "../" * depth
    js = f"<script>{extra_js}</script>" if extra_js else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{htmllib.escape(title)}</title>
<link rel="stylesheet" href="{up}style.css">
</head>
<body>
<div class="wrap">
{nav_html}
<main>
{body}
</main>
</div>
{js}
</body>
</html>
"""


def nav(site, active, depth):
    up = "../" * depth
    out = ['<nav class="side">']
    out.append(f'<a href="{up}index.html"><strong>'
               f'{site["review"]["nav_label"]}</strong></a>')
    out.append('<h2>Overview</h2>')
    for href, label in (("SUMMARY.html", "Summary"), ("README.html", "Method / README"),
                        ("backlog.html", "Backlog &amp; log")):
        if href == "SUMMARY.html" and not site["has_summary"]:
            out.append(f'<span class="unwritten">{label} (not written yet)</span>')
            continue
        cls = ' class="here"' if href == active else ""
        out.append(f'<a href="{up}{href}"{cls}>{label}</a>')
    out.append('<h2>Findings by unit</h2>')
    for stem, label in site["units"]:
        href = f"findings/{stem}.html"
        cls = ' class="here"' if href == active else ""
        out.append(f'<a href="{up}{href}"{cls}>{label}</a>')
    out.append('<h2>Source links</h2><p class="pin">')
    out.append("<br>".join(
        f'{repo} <code>{short_version(cfg["version"])}</code>'
        for repo, cfg in site["pin_repos"]))
    out.append("</p></nav>")
    return "\n".join(out)


def short_version(version):
    if not version:
        return "unlinked"
    if version.startswith("GC"):
        return version[2:9]
    if version.startswith("GB"):
        return version[2:] + " (branch)"
    return version


UNFINISHED = ("pending", "in-progress")


def backlog_units(review_dir):
    """[(ID, unit name, status)] from backlog.md's unit table, or [] without one.

    The unit table is the first one with ID and Status columns; the
    verification queue has no ID column, so it never matches.
    """
    path = os.path.join(review_dir, "backlog.md")
    if not os.path.isfile(path):
        return []
    rows, cols = [], None
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.lstrip().startswith("|"):
                if rows:
                    break
                cols = None
                continue
            cells = [c.strip().strip("*`").strip() for c in ln.strip().strip("|").split("|")]
            if cols is None:
                low = [c.lower() for c in cells]
                if "id" in low and "status" in low:
                    cols = (low.index("id"),
                            low.index("unit name") if "unit name" in low else low.index("id"),
                            low.index("status"))
                continue
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                continue
            if len(cells) > max(cols):
                words = cells[cols[2]].lower().split()
                rows.append((cells[cols[0]], cells[cols[1]], words[0] if words else ""))
    return rows


def unit_list(review_dir):
    """(units to render, unfinished units).

    A doc whose unit backlog.md marks pending or in-progress is left out:
    a reviewer may still be writing it, or a crash cut it short. Its unit is
    returned as unfinished instead, to draw as a placeholder. Any other
    status, a doc no backlog row names, or no unit table at all renders as
    before.
    """
    backlog = backlog_units(review_dir)
    ids = sorted((r[0] for r in backlog if r[0]), key=len, reverse=True)
    unfinished = [(i, n, s) for i, n, s in backlog if s in UNFINISHED]
    hold = {i for i, _, _ in unfinished}
    fdir = os.path.join(review_dir, "findings")
    units = []
    for fn in sorted(os.listdir(fdir)):
        if not fn.endswith(".md"):
            continue
        stem = fn[:-3]
        unit = next((i for i in ids if stem == i or stem.startswith(i + "-")), None)
        if unit in hold:
            continue
        with open(os.path.join(fdir, fn), encoding="utf-8") as fh:
            first = fh.readline().strip().lstrip("# ")
        units.append((stem, htmllib.escape(first)))
    return units, unfinished


def render_plain(site, src_path, out_path, title, active, depth):
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    ctx = context_for(src, site["review"])
    body = linkify_html(md(rewrite_internal_links(src)), ctx)
    write(out_path, page(title, body, depth, nav(site, active, depth)))


def render_findings(site, src_path, out_path, active, depth):
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    ctx = context_for(src, site["review"])
    pre, findings, trailer = split_findings(src)
    title = pre.strip().split("\n")[0].lstrip("# ").strip()
    # Some docs head their finding list with "## Findings"; toc_html emits its
    # own heading with the severity counts, so drop the bare one.
    pre = re.sub(r"^##\s+Findings\s*$", "", pre, flags=re.M)

    parts = [linkify_html(md(rewrite_internal_links(pre)), ctx)]

    if findings:
        has_status = site["has_status"]
        parts.append(toc_html(findings, has_status))
        parts.append(filter_bar(findings, has_status))
        for f in findings:
            parts.append(finding_html(f, ctx, has_status))
    if trailer.strip():
        parts.append(linkify_html(md(rewrite_internal_links(trailer)), ctx))

    write(out_path, page(title, "\n".join(parts), depth,
                         nav(site, active, depth), FILTER_JS))


SEVS = ("Critical", "High", "Medium", "Low")


def counted(items, has_status):
    """`open (total)` once statuses are recorded, so "3 (10)"; else just "10"."""
    if not has_status:
        return str(len(items))
    return f"{sum(1 for f in items if is_open(f))} ({len(items)})"


def status_slug(f):
    """The filter value: anything not dispositioned counts as open."""
    return "open" if is_open(f) else STATUS_CLASS[f["status"]]


def toc_html(findings, has_status):
    has_fix = any(f["fix"] for f in findings)
    has_rgr = any(f["rgr"] for f in findings)
    rows = []
    for f in sorted(findings, key=lambda x: (SEV_ORDER.get(x["severity"], 9),
                                             id_sort_key(x["id"]))):
        sev = f["severity"]
        st = {"": "—", UNKNOWN_STATUS: "unrecognised"}.get(f["status"], f["status"])
        rows.append(
            ('<tr class="is-disp">' if not is_open(f) else "<tr>") +
            f'<td><a href="#{slugify(f["id"])}">{htmllib.escape(f["id"])}</a></td>'
            f'<td class="sev-{sev.lower()}"><strong>{htmllib.escape(sev)}</strong></td>'
            f'<td>{htmllib.escape(f["confidence"])}</td>'
            + (f'<td>{htmllib.escape(f["fix"] or "—")}</td>' if has_fix else "")
            + (f'<td>{htmllib.escape(rgr_label(f) or "—")}</td>' if has_rgr else "") +
            f'<td>{htmllib.escape(f["verdict"] or "—")}</td>'
            + (f'<td>{htmllib.escape(st)}</td>' if has_status else "") +
            f'<td>{md_inline(f["title"])}</td></tr>'
        )
    summary = ", ".join(
        f"{counted([f for f in findings if f['severity'] == s], has_status)} {s}"
        for s in SEVS if any(f["severity"] == s for f in findings)
    )
    if has_status:
        summary = f"— open (total): {counted(findings, True)} · {summary}"
    else:
        summary = f"({summary})"
    return (
        f'<h2 id="findings">Findings <span class="count">{summary}</span></h2>\n'
        '<table><thead><tr><th>ID</th><th>Severity</th><th>Conf.</th>'
        + ('<th>Fix</th>' if has_fix else '')
        + ('<th>RGR</th>' if has_rgr else '') +
        '<th>Verified</th>'
        + ('<th>Status</th>' if has_status else '') +
        '<th>Title</th></tr></thead><tbody>\n'
        + "\n".join(rows) + "\n</tbody></table>"
    )


def filter_bar(findings, has_status):
    present = [s for s in SEVS if any(f["severity"] == s for f in findings)]
    boxes = '<span class="group">' + "".join(
        f'<label><input type="checkbox" data-group="sev" value="{s}" checked> {s}</label>'
        for s in present
    ) + '</span>'
    fixes = [lv for lv in FIX_LEVELS if any(f["fix"] == lv for f in findings)]
    if fixes:
        boxes += '<span class="group"><span>Fix:</span>' + "".join(
            f'<label><input type="checkbox" data-group="fix" value="{lv}" checked> {lv}</label>'
            for lv in fixes
        ) + '</span>'
    rgrs = {rgr_slug(f) for f in findings if f["rgr"]}
    if rgrs:
        boxes += '<span class="group"><span>RGR:</span>' + "".join(
            f'<label><input type="checkbox" data-group="rgr" value="{v}" checked> {lv}</label>'
            for v, lv in RGR_FILTER if v in rgrs
        ) + '</span>'
    if has_status:
        slugs = {status_slug(f) for f in findings}
        boxes += '<span class="group"><span>Status:</span>' + "".join(
            f'<label><input type="checkbox" data-group="status" '
            f'value="{STATUS_CLASS[lv]}" checked> {htmllib.escape(lv)}</label>'
            for lv in STATUS_LEVELS if STATUS_CLASS[lv] in slugs
        ) + '</span>'
    return (
        '<div class="filters">' + boxes +
        '<input type="search" placeholder="filter text (file, category, keyword)…">'
        '<span class="count"></span></div>'
    )


REDUNDANT_BULLET_RE = re.compile(
    r"^-\s+\*\*(?:Severity|Confidence|Category):\*\*.*(?:\n(?!-|\s*$).*)*\n?", re.M)
# A bare "Open" says nothing the badge doesn't; a disposition's evidence does.
OPEN_STATUS_RE = re.compile(r"^-\s+\*\*Status:\*\*[ \t]*Open\.?[ \t]*(?:\n|$)", re.M)
STATUS_LINE_RE = re.compile(r"^-\s+\*\*Status:\*\*.*$", re.M)
BARE_SHA_RE = re.compile(r"(?<!\[)`([0-9a-f]{7,40})`(?!\])")
COMMITS = {}  # (repo, sha as written) -> full sha, or None when it doesn't resolve


def link_status_commits(body_md, review):
    """Turn a bare `sha` on the Status line into a link to that commit.

    Only a sha the home repo actually holds becomes a link, expanded to its
    full form. Anything else stays as written, so a typo shows up as plain
    text rather than as a dead link.
    """
    repo = review["home_repo"]
    cfg = REPOS[repo]
    if not cfg["links"] or not os.path.isdir(cfg["root"]):
        return body_md

    def sha_link(m):
        sha = m.group(1)
        if (repo, sha) not in COMMITS:
            full = git(cfg["root"], "rev-parse", "--verify", "-q", sha + "^{commit}")
            COMMITS[repo, sha] = full
            # The pin's failure mode again: the host 404s a commit it never
            # received, and nothing in the HTML shows it.
            if full and not git(cfg["root"], "branch", "-r", "--contains", full):
                print(f"WARNING: fix commit {sha} is not on any remote-tracking "
                      f"branch of {repo} — its link will 404 until it is pushed "
                      f"(or a git fetch shows it already is)")
        full = COMMITS[repo, sha]
        if not full:
            return m.group(0)
        return f"[`{sha}`]({commit_url(repo, full)})"

    return STATUS_LINE_RE.sub(lambda m: BARE_SHA_RE.sub(sha_link, m.group(0)), body_md)


def finding_link(fid):
    """A markdown link to a finding on its own page; plain text if unknown."""
    stem = FINDINGS_AT.get(fid)
    return f"[{fid}]({stem}.html#{slugify(fid)})" if stem else fid


def link_dependencies(f, body_md):
    """Link each ID on the Depends on line, and add a rendered-only Needed by
    line listing the findings that depend on this one."""
    body_md = DEPENDS_LINE_RE.sub(
        lambda m: FINDING_ID_RE.sub(lambda i: finding_link(i.group(0)), m.group(0)),
        body_md)
    users = NEEDED_BY.get(f["id"])
    if users:
        body_md = (body_md.rstrip("\n") + "\n- **Needed by:** "
                   + ", ".join(finding_link(u) for u in sorted(users, key=id_sort_key))
                   + "\n")
    return body_md


def finding_html(f, ctx, has_status):
    # The finding's own file comes first when resolving its bare references.
    own = (field(f["body"], "File") or "").split()
    if own:
        ctx = dict(ctx, files=[own[0].rsplit(":", 1)[0]] + list(ctx.get("files", [])))
    sev = f["severity"]
    # Severity/Confidence/Category are shown as badges; don't repeat them as bullets.
    body_md = REDUNDANT_BULLET_RE.sub("", f["body"])
    body_md = link_status_commits(OPEN_STATUS_RE.sub("", body_md), ctx["review"])
    body_md = link_dependencies(f, body_md)
    body = linkify_html(md(body_md), ctx)
    body = body.replace("<li><strong>Verification:</strong>",
                        '<li class="verif"><strong>Verification:</strong>')
    disp = not is_open(f)
    if disp:
        body = body.replace("<li><strong>Status:</strong>",
                            '<li class="status"><strong>Status:</strong>')
    sev_label = sev + (" (revised)" if f["sev_revised"] else "")
    badges = []
    if has_status:
        label = {"": "No status", UNKNOWN_STATUS: "Status?"}.get(f["status"], f["status"])
        badges.append(f'<span class="badge st-{status_slug(f)}">{htmllib.escape(label)}</span>')
    badges += [f'<span class="badge sev-{sev.lower()}">{htmllib.escape(sev_label)}</span>',
               f'<span class="badge">Confidence: {htmllib.escape(f["confidence"])}</span>']
    if f["fix"]:
        label = f'Fix: {f["fix"]}' + (" (revised)" if f["fix_revised"] else "")
        badges.append(f'<span class="badge fix-{f["fix"].lower()}">{label}</span>')
    if f["rgr"]:
        label = f"RGR: {rgr_label(f)}" + (" (revised)" if f["rgr_revised"] else "")
        badges.append(f'<span class="badge rgr-{rgr_slug(f)}">{htmllib.escape(label)}</span>')
    if f["category"]:
        badges.append(f'<span class="badge">{htmllib.escape(f["category"])}</span>')
    if f["verdict"]:
        badges.append(
            f'<span class="badge v-{f["verdict"].lower()}">{f["verdict"]}</span>')
    return (
        f'<section class="finding sev-{sev.lower()}{" is-disp" if disp else ""}" '
        f'id="{slugify(f["id"])}" data-severity="{htmllib.escape(sev)}" '
        f'data-fix="{f["fix"]}" data-rgr="{rgr_slug(f) if f["rgr"] else ""}" '
        f'data-status="{status_slug(f)}">\n'
        f'<h3><span class="fid">{htmllib.escape(f["id"])}</span>'
        f'{md_inline(f["title"])}</h3>\n'
        f'<div class="badges">{"".join(badges)}</div>\n{body}\n</section>'
    )


SEV_BLOCK_CLASS = {"Critical": "crit", "High": "high", "Medium": "med", "Low": "low"}


def severity_blocks(findings):
    """One colored square per finding, ordered by severity. With fix impact
    recorded, Uncertain fixes go on the top row and Trivial/Low on the bottom;
    a finding missing the field counts as Uncertain. An unrecognised severity
    gets a grey square, so format drift stays visible.

    Open findings are solid and dispositioned ones hollow, after the open ones
    of the same severity, so the solid squares are what still needs deciding.
    The aria-label keeps the per-severity totals, for the SUMMARY.md
    cross-check, and says how many of each are dispositioned."""
    if not findings:
        return ""
    if any(f["fix"] for f in findings):
        rows = [("Uncertain fix",
                 [f for f in findings if f["fix"] not in ("Trivial", "Low")]),
                ("Trivial or Low fix",
                 [f for f in findings if f["fix"] in ("Trivial", "Low")])]
    else:
        rows = [("", findings)]
    open_word = " open" if any(f["status"] for f in findings) else ""
    html_rows, aria = [], []
    for name, items in rows:
        sevs = sorted({f["severity"] for f in items},
                      key=lambda s: SEV_ORDER.get(s, len(SEV_ORDER)))
        groups, text = [], []
        for s in sevs:
            cls = SEV_BLOCK_CLASS.get(s, "unk")
            label = htmllib.escape(s or "no severity")
            these = [f for f in items if f["severity"] == s]
            n_open = sum(1 for f in these if is_open(f))
            done = [f["status"] for f in these if not is_open(f)]
            if n_open:
                groups.append(f'<span class="g {cls}" title="{n_open} {label}{open_word}">'
                              + "<i></i>" * n_open + "</span>")
            if done:
                how = ", ".join(f"{done.count(lv)} {lv}" for lv in STATUS_LEVELS
                                if lv in done)
                groups.append(f'<span class="g {cls} disp" title="{len(done)} {label} '
                              f'dispositioned: {htmllib.escape(how)}">'
                              + "<i></i>" * len(done) + "</span>")
            text.append(f"{len(these)} {s or 'no severity'}"
                        + (f" ({len(done)} dispositioned)" if done else ""))
        html_rows.append(f'<div class="row">{"".join(groups)}</div>')
        text = ", ".join(text) or "none"
        aria.append(f"{name}: {text}" if name else text)
    return (f'<div class="sev" role="img" aria-label="{htmllib.escape("; ".join(aria))}">'
            + "".join(html_rows) + "</div>")


def status_tally(findings):
    """"86 Fixed · 2 Not a defect": the dispositions, in scale order."""
    done = [f["status"] for f in findings if not is_open(f)]
    return " · ".join(f"{done.count(lv)} {lv}" for lv in STATUS_LEVELS if lv in done)


def render_index(site):
    units, review, has_st = site["units"], site["review"], site["has_status"]
    cards, everything = [], []
    for stem, label in units:
        src = os.path.join(site["dir"], "findings", stem + ".md")
        with open(src, encoding="utf-8") as fh:
            _, findings, _ = split_findings(fh.read())
        everything += findings
        lines = []
        if has_st and findings:
            lines.append(f'<span class="open">open (total): {counted(findings, True)}</span>')
            if status_tally(findings):
                lines.append(status_tally(findings))
        fixes = [(lv, [f for f in findings if f["fix"] == lv]) for lv in FIX_LEVELS]
        if any(g for _, g in fixes):
            lines.append("fix: " + " · ".join(
                f"{counted(g, has_st)} {lv}" for lv, g in fixes if g))
        rgrs = [(lv, [f for f in findings if f["rgr"] and rgr_slug(f) == v])
                for v, lv in RGR_FILTER]
        if any(g for _, g in rgrs):
            lines.append("RGR: " + " · ".join(
                f"{counted(g, has_st)} {'No (partly)' if lv.startswith('No,') else lv}"
                for lv, g in rgrs if g))
        sub = severity_blocks(findings) + "<br>".join(lines)
        cards.append(
            f'<div class="card"><a href="findings/{stem}.html">{label}</a>'
            f'<div class="sub">{sub or "&nbsp;"}</div></div>'
        )
    unfinished = site["unfinished"]
    for uid, name, status in unfinished:
        title = htmllib.escape(f"{uid}: {name}" if name and name != uid else uid)
        cards.append(f'<div class="card unfinished"><span>{title}</span>'
                     f'<div class="sub">{status.replace("-", " ")}</div></div>')
    progress = ""
    if unfinished:
        progress = (f"<p><strong>Review in progress:</strong> {len(units)} units "
                    f"reviewed, {len(unfinished)} still to come, shown greyed. "
                    f"This page is re-rendered as each unit finishes, and "
                    f"verification may still revise any finding.</p>")
    linked =[(r, c) for r, c in site["pin_repos"] if c["links"]]
    unlinked = [(r, c) for r, c in site["pin_repos"] if not c["links"]]
    pins = ", ".join(f'{repo} <code>{short_version(cfg["version"])}</code>'
                     for repo, cfg in linked)
    if linked:
        hosts = " and ".join(sorted({HOSTS[c["host"]] for _, c in linked}))
        links_html = f"""<p>Every <code>file:line</code> reference links to that
line in {hosts}. Links are <strong>pinned to the commits the review was
written against</strong> ({pins}) rather than to a branch, so a reference keeps
pointing at the code it describes even after the defect is fixed. To see the
current state of a file, switch the branch selector once {hosts} has opened
it.</p>"""
    else:
        links_html = ""
    if unlinked:
        names = ", ".join(
            f'{r} <code>{htmllib.escape(c["version"] or "unlinked")}</code>'
            for r, c in unlinked)
        links_html += f"""<p>References into {names} are plain text: that code
has no hosted copy to link to. The README's <em>Reviewed tree</em> section
identifies exactly which tree the line numbers refer to.</p>"""
    legend = ""
    if has_st:
        n_open = sum(1 for f in everything if is_open(f))
        tally = status_tally(everything)
        rows = (" Uncertain fixes are on the top row, Trivial and Low below."
                if any(f["fix"] for f in everything) else "")
        legend = f"""<p>Each square is a finding, colored by severity.
<strong>Solid squares are open</strong>: nobody has decided what happens to them
yet. Hollow squares are dispositioned: fixed, planned in another system, won't
fix, or not a defect.{rows} Counts read <strong>open (total)</strong>.</p>
<p><strong>{n_open} of {len(everything)}</strong> findings are open.
{"Dispositioned: " + tally + "." if tally else ""} These counts are read from
the findings docs on every render. The summary records the review as
delivered.</p>"""
    summary = ('<a href="SUMMARY.html"><strong>Summary</strong></a> —\ntotals, '
               'cross-cutting themes, tiers, method caveats.'
               if site["has_summary"] else
               '<strong>Summary</strong>: not written yet. It comes once every '
               'unit is reviewed and verified.')
    body = f"""<h1>{review["title"]}</h1>
<p>Browseable rendering of <code>{site["name"]}/</code>.</p>
{links_html}
<p>{summary}<br>
<a href="README.html">Method &amp; format</a> ·
<a href="backlog.html">Backlog and run log</a></p>
<h2>Findings by unit</h2>
{progress}
{legend}
<div class="grid">
{chr(10).join(cards)}
</div>"""
    write(os.path.join(site["out"], "index.html"),
          page(review["page_prefix"], body, 0, nav(site, "index.html", 0)))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote", os.path.relpath(path, SITE_ROOT).replace("\\", "/"))


def check_statuses(review_dir, units):
    """Whether this review records statuses, warning about any that drifted.

    A review from before the status scale has no Status bullets, renders
    every finding as open, and gets no warning. Once any finding has one,
    a finding without one, or with a level this doesn't know, is drift: it is
    counted as open, so say which.
    """
    missing, unknown, total = [], [], 0
    for stem, _ in units:
        with open(os.path.join(review_dir, "findings", stem + ".md"), encoding="utf-8") as fh:
            _, findings, _ = split_findings(fh.read())
        total += len(findings)
        missing += [f["id"] for f in findings if not f["status"]]
        unknown += [f["id"] for f in findings if f["status"] == UNKNOWN_STATUS]
    if len(missing) == total:
        return False
    if missing:
        print(f"WARNING: {len(missing)} findings have no Status bullet and are "
              f"counted as open: {', '.join(missing)}")
    if unknown:
        print(f"WARNING: {len(unknown)} findings have a status that isn't one of "
              f"{', '.join(STATUS_LEVELS)}, and are counted as open: {', '.join(unknown)}")
    return True


def check_dependencies(review_dir, units):
    """Fill FINDINGS_AT and NEEDED_BY, warning about drift: a Depends on that
    names no finding or a refuted one, a dependency cycle, or a Seam (harness)
    finding that doesn't depend on the cross-cutting finding for its harness."""
    FINDINGS_AT.clear()
    NEEDED_BY.clear()
    deps = {}
    harness_without = []
    refuted = set()
    for stem, _ in units:
        with open(os.path.join(review_dir, "findings", stem + ".md"), encoding="utf-8") as fh:
            _, findings, _ = split_findings(fh.read())
        for f in findings:
            FINDINGS_AT[f["id"]] = stem
            deps[f["id"]] = f["depends"]
            if f["status"] == "Not a defect":
                refuted.add(f["id"])
            # A harness seam waits on its harness finding, whatever else it
            # depends on. A refuted one keeps its rating but waits on nothing.
            if (f["seam_kind"] == "harness"
                    and not any(d.startswith("X-") for d in f["depends"])
                    and f["status"] != "Not a defect"):
                harness_without.append(f["id"])
    missing = sorted({d for ds in deps.values() for d in ds if d not in FINDINGS_AT},
                     key=id_sort_key)
    for fid, ds in deps.items():
        for d in ds:
            NEEDED_BY.setdefault(d, []).append(fid)
    if missing:
        print(f"WARNING: Depends on names {len(missing)} findings that don't exist: "
              f"{', '.join(missing)}")
    # What a refuted finding describes isn't there, so nothing can wait on it.
    on_refuted = sorted((f"{fid} on {d}" for fid, ds in deps.items() for d in ds
                         if d in refuted and fid not in refuted),
                        key=lambda s: id_sort_key(s.split()[0]))
    if on_refuted:
        print(f"WARNING: {len(on_refuted)} dependencies on a finding that is Not a "
              f"defect: {', '.join(on_refuted)}")
    cycles, state = [], {}

    def visit(fid, path):
        state[fid] = "active"
        for d in deps.get(fid, []):
            if state.get(d) == "active":
                cycles.append(" -> ".join(path[path.index(d):] + [d]))
            elif d in deps and d not in state:
                visit(d, path + [d])
        state[fid] = "done"

    for fid in sorted(deps, key=id_sort_key):
        if fid not in state:
            visit(fid, [fid])
    if cycles:
        print(f"WARNING: {len(cycles)} dependency cycles: {'; '.join(cycles)}")
    # Only once synthesis has written cross-cutting findings is a harness
    # seam with no prerequisite drift; before that, every one lacks it.
    if harness_without and any(fid.startswith("X-") for fid in FINDINGS_AT):
        print(f"WARNING: {len(harness_without)} Seam (harness) findings depend on no "
              f"cross-cutting X- finding: {', '.join(sorted(harness_without, key=id_sort_key))}")


def build_site(review_dir, targets):
    """Assemble the per-review state every renderer needs, then render."""
    global SITE_ROOT, CURRENT_DOC
    review = REVIEW
    name = os.path.basename(review_dir)
    SITE_ROOT = os.path.dirname(review_dir)
    units, unfinished = unit_list(review_dir)
    home = review["home_repo"]
    repos = [home] + [r for r in REPOS if r != home]
    site = {
        "name": name, "review": review, "dir": review_dir,
        "out": os.path.join(review_dir, "html"), "units": units,
        "pin_repos": [(r, REPOS[r]) for r in repos],
        "has_status": check_statuses(review_dir, units),
        "unfinished": unfinished,
        "has_summary": os.path.isfile(os.path.join(review_dir, "SUMMARY.md")),
    }

    check_dependencies(review_dir, units)
    docs = ["index", "SUMMARY.md", "README.md", "backlog.md"] + [
        f"findings/{s}.md" for s, _ in units]
    targets = targets or docs

    shown = {s for s, _ in units}
    write(os.path.join(site["out"], "style.css"), CSS)

    for t in targets:
        t = t.replace("\\", "/")
        if t == "index":
            render_index(site)
            continue
        src = os.path.join(review_dir, t)
        if not os.path.isfile(src):
            print(f"skipped {name}/{t} (not present)")
            continue
        if t.startswith("findings/") and t[len("findings/"):-3] not in shown:
            print(f"skipped {name}/{t} (its unit is not done in backlog.md)")
            continue
        CURRENT_DOC = t
        out = os.path.join(site["out"], t[:-3] + ".html")
        depth = t.count("/")
        label = review["page_prefix"]
        if t.startswith("findings/"):
            render_findings(site, src, out, t[:-3] + ".html", depth)
        else:
            titles = {"SUMMARY.md": f"{label} — Summary",
                      "README.md": f"{label} — Method",
                      "backlog.md": f"{label} — Backlog"}
            render_plain(site, src, out, titles.get(t, t), t[:-3] + ".html", depth)
    return site


def report_unresolved():
    """One UNRESOLVED line per distinct (document, reference), then a count."""
    seen = {}
    for key in UNRESOLVED:
        seen[key] = seen.get(key, 0) + 1
    for (doc, ref), n in seen.items():
        print(f"UNRESOLVED {doc}: {ref}" + (f" (x{n})" if n > 1 else ""))
    print(f"{len(seen)} unresolved reference(s)")


def main(argv):
    quiet = "--quiet" in argv
    unresolved = "--unresolved" in argv
    argv = [a for a in argv if a not in ("--quiet", "--unresolved")]
    if not argv or argv[0].startswith("-"):
        sys.exit("usage: render_review_html.py <review dir> [--quiet] [--unresolved] "
                 "[target.md ... | index]")
    review_dir, targets = os.path.normpath(os.path.abspath(argv[0])), argv[1:]
    if not os.path.isdir(os.path.join(review_dir, "findings")):
        sys.exit(f"{review_dir} has no findings/ directory — not a review directory")
    if not quiet:
        load_config(review_dir)
        check_pins(review_dir)
        build_site(review_dir, targets)
        if unresolved:
            report_unresolved()
        return
    # Everything still runs; only the WARNINGs and one closing line get out.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        load_config(review_dir)
        check_pins(review_dir)
        site = build_site(review_dir, targets)
    lines = buf.getvalue().splitlines()
    for ln in lines:
        if ln.startswith("WARNING"):
            print(ln)
    pages = sum(1 for ln in lines if ln.startswith("wrote ") and ln.endswith(".html"))
    left = len(site["unfinished"])
    done = len(site["units"])
    print(f"rendered {pages} pages: "
          + (f"{done} of {done + left} units done" if left else f"{done} units")
          + f" -> {os.path.join(site['out'], 'index.html')}")
    if unresolved:
        report_unresolved()


if __name__ == "__main__":
    main(sys.argv[1:])
