#!/usr/bin/env python3
"""The `related` command for a Go module: runs the tests that a set of changed
files can affect.

    python "<state>/bin/go_related.py" {files}

Run from the module's root, as `exec` does. Go tests run by package, so each
changed file names its package's folder (a file under a `testdata` folder
names the package above it), and the tests run are those of every package in
the module that is one of them or imports one of them, through its own code or
its tests. A changed file in no package, such as an embedded document, a
script or go.mod, could reach anything, so it runs every package. No changed
files, or only deleted folders, selects nothing and exits 0.

Standard library only.
"""

import os
import subprocess
import sys

ROOT = os.getcwd()


def rel(f):
    return os.path.relpath(os.path.abspath(f), ROOT).replace(os.sep, "/")


def package_dir(f):
    """The folder of the package a file belongs to, or None for none."""
    parts = rel(f).split("/")
    if "testdata" in parts:
        return "/".join(parts[:parts.index("testdata")]) or "."
    if parts[-1].endswith(".go"):
        return "/".join(parts[:-1]) or "."
    return None


def run(args):
    print("$ " + " ".join(args), flush=True)
    return subprocess.call(args, cwd=ROOT)


def go_list(fmt, *pkgs):
    p = subprocess.run(["go", "list", "-f", fmt, *pkgs], cwd=ROOT, capture_output=True, text=True)
    if p.returncode:
        sys.stderr.write(p.stderr)
        sys.exit(p.returncode)
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def main(files):
    dirs = set()
    for f in files:
        d = package_dir(f)
        if d is None:
            print(f"{rel(f)} is in no package: running every package", flush=True)
            return run(["go", "test", "-count=1", "./..."])
        dirs.add(d)
    changed = set()
    for d in sorted(dirs):
        if os.path.isdir(os.path.join(ROOT, d)):
            changed.update(go_list("{{.ImportPath}}", "." if d == "." else "./" + d))
    if not changed:
        print("no package to test for these files", flush=True)
        return 0
    fmt = '{{.ImportPath}}|{{join .Deps ","}}|{{join .TestImports ","}}|{{join .XTestImports ","}}'
    selected = []
    for line in go_list(fmt, "./..."):
        path, *lists = line.split("|")
        uses = {x for part in lists for x in part.split(",") if x}
        if path in changed or uses & changed:
            selected.append(path)
    return run(["go", "test", "-count=1", *selected])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
