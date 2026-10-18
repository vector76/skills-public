#!/usr/bin/env python
"""Give every finding in a review written before the status scale a Status bullet.

    python add_status_bullets.py <review dir>          # dry run: counts only
    python add_status_bullets.py <review dir> --write

- `- **Fixed:** <evidence>` becomes `- **Status:** Fixed — <evidence>`, in place.
- A REFUTED finding with no Fixed bullet gets `Not a defect — refuted at verification`.
- Every other finding gets `- **Status:** Open`, after its last bullet.

Findings that already have a Status bullet are left alone. Every doc must come
out with exactly one Status bullet per finding, or nothing is written.
"""
import glob
import os
import re
import sys

from review_format import FINDING_RE

BULLET_RE = re.compile(r"^-\s+\*\*[^*]+:\*\*")
STATUS_RE = re.compile(r"^-\s+\*\*Status:\*\*", re.M)
FIXED_RE = re.compile(r"^-\s+\*\*Fixed:\*\*", re.M)


def migrate(text):
    lines = text.split("\n")
    # newline="" keeps a CRLF doc's \r on every line; match it on inserted ones
    cr = "\r" if text.count("\r\n") > text.count("\n") // 2 else ""
    out, counts = [], {"Fixed": 0, "Not a defect": 0, "Open": 0, "already": 0}
    i = 0
    while i < len(lines):
        if not FINDING_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not FINDING_RE.match(lines[j]) and not lines[j].startswith("## "):
            j += 1
        block = lines[i:j]
        body = "\n".join(block)
        if STATUS_RE.search(body):
            counts["already"] += 1
        elif FIXED_RE.search(body):
            if len(FIXED_RE.findall(body)) != 1:
                sys.exit(f"{block[0]}: more than one Fixed bullet; merge them by hand first")
            block = [re.sub(r"^-\s+\*\*Fixed:\*\*\s*", "- **Status:** Fixed — ", ln)
                     for ln in block]
            counts["Fixed"] += 1
        else:
            refuted = re.search(r"^-\s+\*\*Verification:\*\*\s*REFUTED", body, re.M)
            status = "Not a defect — refuted at verification" if refuted else "Open"
            counts["Not a defect" if refuted else "Open"] += 1
            bullets = [k for k, ln in enumerate(block) if BULLET_RE.match(ln)]
            if not bullets:
                sys.exit(f"{block[0]}: no bullets; this finding has drifted from the format")
            # after the last bullet and any lines that continue it
            end = bullets[-1] + 1
            while end < len(block) and block[end].strip() and not block[end].startswith("-"):
                end += 1
            block = block[:end] + [f"- **Status:** {status}{cr}"] + block[end:]
        out += block
        i = j
    return "\n".join(out), counts


def main(argv):
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__)
    review, write = argv[0], "--write" in argv[1:]
    docs = sorted(glob.glob(os.path.join(review, "findings", "*.md")))
    if not docs:
        sys.exit(f"no findings/*.md under {review}")
    results, total = [], {}
    for p in docs:
        with open(p, encoding="utf-8", newline="") as fh:
            text = fh.read()
        new, counts = migrate(text)
        n = sum(1 for ln in text.split("\n") if FINDING_RE.match(ln))
        got = len(STATUS_RE.findall(new))
        if n != got:
            sys.exit(f"{p}: {n} findings but {got} Status bullets; nothing written")
        print(os.path.basename(p), f"{n} findings", counts)
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
        results.append((p, text, new))
    print("total", total, "" if write else "(dry run: pass --write to apply)")
    if write:
        for p, text, new in results:
            if new != text:
                with open(p, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)


if __name__ == "__main__":
    main(sys.argv[1:])
