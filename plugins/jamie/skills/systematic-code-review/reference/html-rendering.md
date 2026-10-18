# Rendering the review to browseable HTML

**This is how the findings actually get read.** Markdown is the storage format;
the HTML site is the review surface — a sidebar of units, severity and
verification badges per finding, and every `file.cpp:123` reference turned into a
deep link that lands on that exact line in Azure DevOps or on GitHub.

Rendering is decoupled from the review loop: it can be skipped, or run much later
against a finished review, and it never changes the markdown.

## The generator

`scripts/render_review_html.py`, in this skill's folder, serves every review.
Run it where it is, and do not copy it into the review or the code. Requires
Python 3.9+ and `python -m pip install markdown`. Output has no external assets
and works from `file://`.

```powershell
python <skill dir>\scripts\render_review_html.py <review dir>             # everything
python <skill dir>\scripts\render_review_html.py <review dir> SUMMARY.md findings/U01-foo.md
python <skill dir>\scripts\render_review_html.py <review dir> --quiet     # during the run
python <skill dir>\scripts\render_review_html.py <review dir> --unresolved  # check b below
```

`index` is its own target — pass it explicitly when re-rendering only the index,
since it recounts severities by re-reading every findings doc.

Output goes to `<review dir>/html/`, regenerated wholesale. **Never hand-edit it**
— the next render discards the edit. That directory is the only thing the
generator writes, so an external-mode review never touches the code root.

### `render.json`

Everything specific to one review lives in `<review dir>/render.json`, created
in step 2 of the playbook:

```json
{
  "title": "Foo Deep-Dive Code Review",
  "nav_label": "Foo Review",
  "page_prefix": "Foo Review",
  "ado_base": "https://dev.azure.com/<org>/<project>/_git",
  "home_repo": "Foo",
  "repos": {
    "Foo": {
      "root": "C:/src/Foo",
      "bases": ["src/", ""],
      "version": "GC<40-char sha>"
    }
  },
  "subprojects": ["src/"],
  "dir_roots": ["src/", "inc/"],
  "prefix_map": [["Other/", "OtherRepo", ""]],
  "extensions": ["vhd"]
}
```

- `repos` — one entry per repository the findings cite. A new review
  covers one repository, so it has one entry. Reviews from before that rule
  may cite several. `root` is the local
  working copy (read, never written), `bases` the path prefixes an unqualified
  reference may sit under (add `""` when files at the repo root are cited), and
  `version` the pin. A repository in a different DevOps project takes its own
  `ado_base`.
- `"host"` on a repo — `"ado"` (the default) or `"github"`. A GitHub repo
  needs `"github_base": "https://github.com/<owner>/<repo>"` (a GitHub
  Enterprise base works the same way) and no `ado_base`:

  ```json
  "example-app": {
    "root": "C:/src/example-app",
    "bases": ["", "apps/web/src/"],
    "version": "GC<40-char sha>",
    "host": "github",
    "github_base": "https://github.com/<owner>/example-app"
  }
  ```

  One review can mix hosts: each repo links to its own.
- `"links": false` on a repo — for code with no hosted copy: a remote on
  another host, or no git at all (see [tree-identity.md](tree-identity.md)). Its
  references render as plain text, it needs no `ado_base`, and its `version` is
  only a label, e.g. `"v2.3.1 tree sha256:1a2b3c4"`. The index page says which
  tree the plain references refer to. Top-level `ado_base` is needed only when
  some repo links.
- `home_repo`, `subprojects`, `dir_roots`, `prefix_map` — the review's own
  path shorthand; see *Reference resolution* below.
- `extensions` — optional. File extensions a `file.ext:123` reference may
  carry, beyond the script's `LINK_EXT` list. That list covers C and C++,
  assembly and linker files, and the common languages: Go, Rust, Java, C#,
  Python, JavaScript and TypeScript, SQL, CSS, HTML, and config formats. Add
  an extension when the findings cite a file type the list lacks. The sweep
  below can't show those: it only sees references with an extension the
  renderer already knows.

The header of the script documents every field. To start, fill in `repos` and
`home_repo`, then run the unresolved-reference sweep below and add shorthand
until only vendor code is left.

### Rendering during the run

The playbook re-renders after each sweep batch, after each verify job, and
after the summary, with `--quiet` and no targets:

- **Everything renders each time**, so every page's sidebar lists every
  finished unit. It takes seconds.
- **Only units `backlog.md` marks `done` get a page.** A `pending` or
  `in-progress` unit is a greyed placeholder card on the index, with no link,
  so a doc still being written, or cut short by a crash, never appears. An
  explicit target for such a doc is skipped, with a line saying why. Any other
  status, or a doc no backlog row names, renders as before. A finished or
  older review is unaffected.
- **Until `SUMMARY.md` exists**, the index and sidebar say it isn't written
  yet rather than linking to a missing page.
- **`--quiet` prints the `WARNING` lines and one closing line**: pages
  written, units done, and the path to `index.html`. `NOTE` and `pin OK` lines
  are dropped. The first render and the final one run without `--quiet`, and
  those are the ones whose output is read in full.

**After the first unit is done**, render once without `--quiet` and set up the
links. Read the pin lines (check a below), run the unresolved-reference sweep
(check b), and add shorthand to `render.json` until only vendor code is left.
One unit shows most mapping gaps, and fixing them then is one edit to
`render.json`, not a surprise at the end. Then give the user the path to
`html/index.html`.

A verifier may be editing a doc while a render reads it. The worst case is one
page rendered from a half-edited doc, and the next render replaces it.

## Pin links to the reviewed commit, never to master

A review's line numbers only mean anything against the tree it was written
from. Every fix that lands afterwards slides the references off their targets, so
each repo carries its own pinned `version`.

`version` names it by prefix, the way Azure DevOps encodes it: `GC<40-char-sha>`
for a commit, `GB<name>` for a branch, `GT<name>` for a tag. GitHub links use
the same field, minus the prefix: `…/blob/<sha>/<path>`.

### Establishing the commit

The playbook records the commit in step 2: `render.json` holds the pins, and the
`## Reviewed tree` section of `README.md` holds the same shas. Use those, and
still spot-check one finding per repo (as in *Prove it* below) in case the tree moved during
the sweep.

A review that predates that step does not record it, so recover the candidate
and then **prove** it. **Commit dates only work when the review lives in the same
repository as the code, or was committed the same day.** An external review's
commit history says nothing about the code's history.

1. **Candidate** — the last commit in each repo at or before the date the review
   docs were committed. If findings carry a per-unit `Reviewed:` date, prefer
   that and note its *range*: a review spanning days may straddle a commit.
2. **Prove it by spot-check.** Pick three or four findings citing something
   distinctive and confirm the cited line holds exactly that code at the
   candidate commit:

   ```powershell
   git show <sha>:src/queue.cpp | Select-Object -Index 477   # a finding citing :478
   ```

   **Pick files the candidate commits actually differ in, or the check proves
   nothing.** In one review with two candidate commits, only one cited file
   discriminated between them: it holds the cited code at one candidate and is
   not even that long at the other. Every other spot-check passed at both.

   If lines are off by a few, the review straddled a commit — walk back one and
   re-check rather than guessing.
3. Do this for **every** repo the findings reference. Reviews cite across repos,
   and the pins are usually different commits.

### The pin must be pushed

**A correct sha that exists only locally breaks every link into that repo, and
the failure is invisible.** Neither Azure DevOps nor GitHub can show a commit
it never received: the URLs look right, the render reports success, every link
404s.

This is not hypothetical — one review shipped **thousands of dead links** because
the pinned commit had never been pushed, while `git rev-parse HEAD` matched the
pin perfectly. Pushing it fixed every link **with no re-render**, since the URLs
already named the right sha.

`check_pins()` tests this on every run. It asks `git branch -r --contains` first
and, when that comes up empty, queries `git ls-remote` before warning — so a
stale remote-tracking branch reports "could not confirm" rather than a false
alarm. Every git call it makes is read-only (it never fetches, and `status`
runs with `--no-optional-locks`), so it is safe against a code root the review
may not write to. When the review directory sits inside the repo, it is left
out of both checks: a committed review moves `HEAD` without moving the code,
and only a change to the code itself is reported.

**When the commit can't be pushed** — third-party code, a repository you only
read — step 1 has already asked the user to either stop and correct it, or
continue without links. If the check fails at render time anyway, **don't
re-pin**. The findings' line numbers belong to the reviewed commit, and moving
the pin would silently point every reference at different code. Tell the user:
push the commit, or render with `"links": false`. Moving a review to a newer
commit on purpose is the `rebase-findings` skill, which rewrites every
reference along with the pin.

## The URL details to preserve

**Azure DevOps:**

```
…&version=GC<reviewed-sha>&line=<start>&lineEnd=<end+1>&lineStartColumn=1&lineEndColumn=1&lineStyle=plain&_a=contents
```

**`lineEnd` is always the line *after* the last line to highlight.** DevOps
selects the half-open range `line:1` → `lineEnd:1`, so `:478` renders as
`line=478&lineEnd=479`, and `:412-473` as `line=412&lineEnd=474`. Using the last
line itself leaves it unhighlighted. Preserve this across any edit to the URL
builder; a highlight sitting one line off means it has regressed.

**GitHub:**

```
https://github.com/<owner>/<repo>/blob/<reviewed-sha>/<path>#L<start>-L<end>
```

GitHub's range is inclusive, so `:412-473` is `#L412-L473` and `:478` is just
`#L478`. GitHub renders Markdown and other markup files instead of showing
their lines, so those get `?plain=1` before the anchor, or the anchor lands
nowhere.

## Verify after rendering — three checks

**a. Read the pin lines.** Every run prints one per repo:

```
pin OK: Foo f00cb68 is on origin/master
```

`WARNING: … is NOT on the remote` → every link into that repo will 404; push
it, or render with `"links": false` if the user agrees. `NOTE: could not confirm …` → offline or stale
remote-tracking branches; a `git fetch` settles it if you may write to that
repo, and otherwise treat it as unverified and spot-check by hand.
`NOTE: … is not linked` → expected for a `"links": false` repo.
`NOTE: … past the pin …` → expected once fixes land; see *Reference
resolution* below. Read the `WARNING` lines about statuses too: each names
findings that are counted as open because their Status drifted. `WARNING: fix
commit … is not on any remote-tracking branch` is the pin's failure mode for a
fix: that commit's link 404s until it is pushed.

**b. Sweep for unresolved references** — anything that looks like a source
reference but did not become a link. Render with `--unresolved`, alone or
with `--quiet`. It prints one `UNRESOLVED <doc>: <reference>` line per
distinct miss, found by the same regex and resolution that build the links,
so it covers every extension the site links, `render.json`'s `extensions`
included. References into a `"links": false` repo are left out, since they
are meant to stay plain.

Everything left should be **vendor** source absent from the working copies, or
not a real path (a wildcard, a format template, an example in prose). **Any real
first-party filename is a mapping bug.** The shapes to expect:

| Symptom | Cause |
|---|---|
| A shorthand prefix for another repo unlinked | needs a `prefix_map` entry |
| `.rel/Foo.map` unlinked | `lstrip("./")` strips *characters*, eating the leading dot |
| Files at the repo root unlinked | that repo's `bases` needs `""` alongside the source subdir |
| A cross-repo reference unlinked | must be tried under the *other* repo's bases too |

**c. Spot-check one link per repo.** Follow a reference from `index.html` and
confirm the host lands on the cited line *with that line highlighted*.

## Reference resolution, in priority order

| Step | Reference form | Resolves to |
|---|---|---|
| 1 | A fully-qualified repo prefix, plus the review's own shorthand | that repo, prefix rewritten |
| 2 | A path rooted at one of the review's `dir_roots` | home repo, under the document's own base |
| 3 | A bare basename (`queue.cpp:1581`) | the file index; ties go to the file the finding cites (its own **File:** first, then the document's), then a file in that file's directory, then the home repo and the document's base |
| 4 | Any other relative path, including cross-repo | tried under every base of every repo |
| 5 | A partial path no base completes (`orders/server.ts:262`) | the indexed path ending in it; ties as in step 3 |
| — | anything else | left as plain text |

`subprojects` deserves note: the base that **dominates a given document's
`**File:**` lines wins for that document**, which is how one review's bare
`src/shared.cpp` lands in one project in one unit and in its sibling
project in another. On a tie the longest prefix wins, since `""` matches every
path.

**Prefer what the finding cites.** Reviewers often write a sibling as a bare
basename, `handlers.ts:475` beside a `**File:**` line naming
`src/orders/handlers.ts`. In a repo with many files of that name, the base
rules alone pick one arbitrarily, which in one review sent over a third of such
links to the wrong file. So an ambiguous name resolves to the file the finding itself
cites first.

Bare basenames resolve against the **pinned commit**: the file index is read
with `git ls-tree` at the pin, leaving out the review directory. Fixes landing
afterwards add, rename and delete files, and an index of the working tree
would resolve against those and produce links that 404 at the pin. So once
fixes land, the working copy moving past the pin is expected, and
`check_pins()` prints it as a `NOTE`. Only where the pin can't be read (no git,
or a `version` that isn't a commit, branch or tag) does the index fall back to
the working tree. Then a moved or dirty working copy is a `WARNING`: not fatal,
but investigate before trusting new links.

## Format drift shows up here first

The renderer parses the finding format the playbook specifies, so a drifted doc
**loses features silently rather than failing loudly**. It reads:

- `### <ID>-<n>: <title>` starts a finding; a following `## ` heading ends the
  list and becomes trailing prose.
- `**Severity:** / **Confidence:** / **Category:** / **Fix impact:** /
  **Verification:**` become badges. The first three are dropped from the card
  body to avoid saying it twice. Fix impact and Verification stay in the body,
  because their reasons are the useful part.
- Verdicts are recognised by prefix: `CONFIRMED`, `REFUTED`, `UNCERTAIN`.
  Severity and fix impact levels are too; the level is the first word. A
  trailing `(verifier: was …)` marks the badge as
  revised, and counts and filters use the current level.
- Fix impact also gets a column in each unit's table, a filter group, and
  per-unit counts on the index. A review written before the field existed has
  none of them and renders as before. A new review where some findings lack the
  field has drifted: those findings stay visible under every fix filter, so the
  gap is easy to miss. The summary's fix-impact count is what catches it.
- `**RGR:**` is handled the same way as fix impact: a badge, a column, a
  filter group, and per-unit counts, with the same drift risk, which the
  summary's RGR count catches. A `No` whose reason contains `partly testable`
  is shown as *No, partly testable* (*No (partly)* on the index) and has its
  own filter box, so that slice can be selected on its own. A Seam's kind,
  `Seam (local)` or `Seam (harness)`, gets its own label and filter box. A
  plain `Seam`, from a review before the kinds existed, renders as before.
- `**Depends on:**` IDs link to their findings, on whatever page they are.
  Every finding something depends on gets a rendered-only **Needed by** line
  listing its dependents, so a cross-cutting harness finding shows what waits
  on it. Only the IDs before a `(verifier: was …)` marker count. Dependency
  drift is reported in a `WARNING`: a Depends on that names no finding or a
  refuted one, a cycle, or, once any `X-` finding exists, a `Seam (harness)`
  finding with no prerequisite.
- `findings/X-cross-cutting.md` has no backlog row, so it renders as soon as
  it exists, as its own card on the index.
- `**Status:**` becomes the first badge, a Status column, and a filter group.
  Its level is recognised by prefix, case aside. A bare `Status: Open` bullet is
  dropped from the body. A disposition's bullet stays, because its evidence is
  the useful part, and a bare `` `sha` `` in it is linked to that commit, if
  the home repo has it. Once any finding has a Status, counts read
  `open (total)`. Unlike the other fields, status drift is reported: a
  finding with no Status bullet, or a level not on the scale, is counted as
  open and named in a `WARNING`. A review written before the field has no
  Status bullets anywhere, renders as before, and gets no warning.

Each unit card on the index draws **one square per finding**, colored by
severity (red Critical, orange High, yellow Medium, blue Low), evenly spaced
and ordered by severity. With fix impact recorded there are two rows:
Uncertain on top, Trivial/Low below; a finding missing the field lands on the
top row. Without the field, one row. An unrecognised severity draws a grey
square, which is drift. **Open findings are solid and dispositioned ones
hollow**, after the open ones of the same severity, so the solid squares are
what still needs a decision. The `open (total)`, status and `fix: …` counts stay
as text under the squares.

**Cross-check the index's per-severity totals against the review's own
`SUMMARY.md` table.** A mismatch means a finding header or `**Severity:**` line
has drifted out of format. The totals aren't printed as text: hover a run of
squares for its count, or read each card's `aria-label`
(`grep -o 'aria-label="[^"]*"' html/index.html`). The label counts every
finding, dispositioned or not, so it matches the summary however many
dispositions have been recorded since. The cross-cutting `X-` card is the
summary's Testability section, counted apart from the unit totals.

## Committing

The output is generated, and the review's `.gitignore` (step 2) leaves `html/`
out, so by default it is never tracked. Committing it makes the review readable
without a Python install. If the user wants that, take the `html/` line out of
the review's `.gitignore` first, or the `git add` fails on an ignored path.
It goes in whichever repository holds the review — the code's repository
in-tree, the reviews repository in external mode:

```powershell
git -C <review dir> add .gitignore html
```

The generator itself is not committed with the review; it ships with this skill.

**Leave it untracked for the user to review and commit.** Per the playbook's
delivery gate, do not stage, commit, or push it yourself unless the user asks.
Re-render whenever the markdown changes.
