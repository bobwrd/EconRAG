"""AskError — an error that teaches: what went wrong, where, and a paste-able fix."""

import re


class AskError(Exception):
    """`apply` is an optional structured, one-click correction the UI can offer:
        {"mode": "replace", "line": N, "text": "..."}   -> replace that line
        {"mode": "insert",  "line": N, "text": "..."}   -> insert before line N
    """

    def __init__(self, what, where="", fix="", line=None, apply=None):
        self.what = what
        self.where = where
        self.fix = fix
        self.line = line
        self.apply = apply
        super().__init__(what)

    def render(self):
        parts = [self.what]
        if self.where:
            parts.append(self.where)
        if self.fix:
            parts.append("Try: " + self.fix)
        return "\n".join(parts)


def replace_first_word(line, old, new):
    """Replace the leading command word in a raw line, whole-word anchored."""
    return re.sub(r"\b" + re.escape(old) + r"\b", new, line.raw, count=1)


def replace_word_in_line(raw, old, new):
    """Replace the first whole-word occurrence of `old` with `new`."""
    return re.sub(r"\b" + re.escape(old) + r"\b", new, raw, count=1)
