"""The finding format's vocabulary, shared by every script that reads findings.

render_review_html.py, review_state.py, add_status_bullets.py and
rebase-findings' reanchor.py all read the same `### <ID>: <title>` headings
and `- **Field:** <level> — <reason>` bullets. Each once kept its own regexes,
and they drifted apart: a correction marker holding parentheses,
`(verifier: was Seam (local))`, was cut short by one and not another. Levels,
IDs and markers are read here, once.

Standard library only, so review_state.py runs without the `markdown` package.
"""

import re

SEVERITIES = ("Critical", "High", "Medium", "Low")
CONFIDENCES = ("High", "Medium", "Low")
FIX_IMPACTS = ("Trivial", "Low", "Uncertain")
RGRS = ("Unit", "Seam (local)", "Seam (harness)", "Seam", "No")
VERDICTS = ("CONFIRMED", "REFUTED", "UNCERTAIN")
STATUSES = ("Open", "Fixed", "Planned", "Won't fix", "Not a defect")

FINDING_RE = re.compile(r"^###\s+(\S+?):\s*(.*)$")          # heading: ID, title
FINDING_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")       # an ID in running text
FIELD_RE = re.compile(r"^-\s+\*\*([A-Za-z' ]+):\*\*\s*(.*)$")  # bullet: name, value

MARKER = "(verifier: was "


def split_markers(value):
    """(current text, [earlier values, newest first]) for one field's value.

    A correction keeps the value it replaced as `(verifier: was <old>)`, and a
    later correction goes in front of any marker already there, so the last
    marker holds the original value. Parentheses inside a marker are
    balanced, as in `(verifier: was Seam (local))`.
    """
    i = value.find(MARKER)
    if i < 0:
        return value.strip(), []
    current, olds = value[:i].strip(), []
    while i >= 0:
        start = j = i + len(MARKER)
        depth = 1
        while j < len(value) and depth:
            depth += {"(": 1, ")": -1}.get(value[j], 0)
            j += 1
        olds.append(value[start:j - 1 if depth == 0 else j].strip())
        i = value.find(MARKER, j)
    return current, olds


def level(value, choices):
    """The level `value` starts with, spelled as in `choices`, or "".

    Case and a typographic apostrophe don't matter. The longest level wins,
    so `Seam (local)` is never read as plain `Seam`.
    """
    v = value.replace("’", "'").lower()
    for c in sorted(choices, key=len, reverse=True):
        if v.startswith(c.lower()):
            return c
    return ""


def current_level(value, choices):
    """The level the field holds now, ignoring its correction markers."""
    return level(split_markers(value)[0], choices)


def original_level(value, choices):
    """The level before any correction: the oldest marker's, else the current."""
    current, olds = split_markers(value)
    return level(olds[-1] if olds else current, choices)


def ids_in(value):
    """The finding IDs a field names now, leaving out those its markers record."""
    return FINDING_ID_RE.findall(split_markers(value)[0])
