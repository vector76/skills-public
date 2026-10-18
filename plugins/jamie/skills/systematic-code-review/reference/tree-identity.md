# Identifying the reviewed tree

A review's line numbers only mean anything against the exact tree it was
written from. Step 1 records that tree, and a resume checks it again. This file
says how, for each kind of code root. Record the result in the `## Reviewed
tree` section of `README.md`, and as the pins in `render.json`.

Two rules apply everywhere:

- **Leave the review out of the comparison.** In in-tree mode the review
  directory sits inside the code. Its own files, and any commit of them, make
  the tree look dirty or moved when no code has changed. Every command below
  takes the review directory as an exclusion. In external mode there is
  nothing to exclude.
- **External mode is read-only, including `.git`.** `git fetch` writes refs and
  objects, and a plain `git status` can rewrite the index. In external mode use
  only the read-only forms below. If a question can't be settled without
  writing, report it as unconfirmed and ask the user.

## One repository only

A review covers one repository. Before recording anything, check that nothing
in scope is a second one:

- `git submodule status` prints nothing, and there is no `.gitmodules` file.
- No directory in scope other than the root holds a `.git`, whether a folder
  (a nested repository) or a file (a nested worktree). This prints nothing:
  `Get-ChildItem -LiteralPath <code root> -Force | Where-Object Name -ne '.git' | Get-ChildItem -Recurse -Force -Filter .git`
  It skips the root's own `.git` by name rather than by path, because Windows
  can spell the same path two ways (8.3 short names) and a path comparison
  then flags the root itself. An in-tree review that is its own repository
  (step 2 of the playbook) shows up here as `<review dir>\.git`; that one is
  expected and is not code in scope.

If either finds something in scope, stop and tell the user: several
repositories and submodules are out of scope for this skill. They can exclude
those paths from scope, or review each repository separately.

## A git repository

In the commands, `<rel review>` is the review directory relative to the repo
root (for example `docs/code-review`). Drop the `:(exclude)` term in external
mode.

| What | Command |
|---|---|
| Commit | `git rev-parse HEAD` |
| Branch | `git rev-parse --abbrev-ref HEAD` |
| Remote | `git remote get-url origin` |
| Clean? | `git --no-optional-locks status --porcelain -- . ":(exclude)<rel review>"` — must print nothing, untracked files included. A git review is of committed code, so a dirty tree stops step 1 |
| On the remote? | in-tree: `git fetch`, then `git branch -r --contains HEAD`. External: `git branch -r --contains HEAD` against the refs already there, then `git ls-remote origin` to see whether `HEAD` is a remote branch tip. If neither shows it, it is **unconfirmed**; ask the user rather than fetching |
| Still the reviewed code? (resume) | **Both** of these: `git diff --quiet <pinned sha> HEAD -- . ":(exclude)<rel review>"` exits 0, meaning the committed code is identical even if `HEAD` has moved; **and** the clean check above prints nothing. The first alone misses uncommitted edits, which are what reviewers would actually read |

**Can it be linked?** Links need a hosted copy of the pinned commit, in Azure
DevOps or on GitHub. If `origin` is a GitHub URL (`github.com/<owner>/<repo>`),
the repo links with `"host": "github"` and `"github_base":
"https://github.com/<owner>/<repo>"` in `render.json`; a GitHub Enterprise
host works the same way with its own base URL. If `origin` is neither that nor
an Azure DevOps URL (`dev.azure.com/…` or `*.visualstudio.com/…`),
there is nothing to link to. That is the usual case for code on other hosts,
such as GitLab or Bitbucket. Record the sha anyway, and mark the repo
`"links": false` in `render.json`, so its references render as plain text.
Tell the user at the step 1 gate.

## Code that is not a git repository

Examples are a vendor source drop, a tarball, or an extracted archive. There is
no sha. Record what identifies it instead:

- **The vendor's own version or release label**, if the drop has one.
- **A content hash of the tree**, computed as below. It changes if any file in
  scope changes, and it is how a resume proves the tree is the same.

```powershell
$root    = 'C:\path\to\code'
$exclude = ''    # the review directory, if it sits inside $root
$lines = Get-ChildItem -LiteralPath $root -Recurse -File |
  Where-Object { -not $exclude -or -not $_.FullName.StartsWith($exclude, [StringComparison]::OrdinalIgnoreCase) } |
  ForEach-Object {
    $rel = $_.FullName.Substring($root.Length).TrimStart('\').Replace('\', '/')
    '{0} {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $rel
  } |
  Sort-Object { $_.Substring(65) }
$bytes = [Text.Encoding]::UTF8.GetBytes($lines -join "`n")
$hash  = -join ([Security.Cryptography.SHA256]::Create().ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })
"tree sha256:$hash ($($lines.Count) files)"
```

In `render.json`, give such a tree `"links": false` and use the label as its
`version`, e.g. `"version": "v2.3.1 tree sha256:1a2b3c4"`. Its references
render as plain text, and the index page says which tree they refer to.
