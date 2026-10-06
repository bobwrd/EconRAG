"""Tokenizer: source text -> list of logical Lines of Tokens."""

from dataclasses import dataclass
from typing import Any

from .errors import AskError


@dataclass
class Token:
    type: str          # STR | NUM | WORD | OP | PUNCT
    value: Any
    line: int
    col: int


@dataclass
class Line:
    lineno: int
    indent: int
    tokens: list
    raw: str = ""


TWO_CHAR_OPS = (">=", "<=")
ONE_CHAR_OPS = set("=><+-*/")
PUNCT = set("[]{}(),:|")


def tokenize(src):
    """Turn source text into logical Lines (comments/blank lines dropped)."""
    lines = []
    for lineno, raw in enumerate(src.split("\n"), start=1):
        indent = len(raw) - len(raw.lstrip(" \t"))
        toks = _tokenize_line(raw, lineno)
        if toks:
            lines.append(Line(lineno, indent, toks, raw=raw))
    return lines


def _tokenize_line(raw, lineno):
    toks = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c in " \t":
            i += 1
            continue
        if c == "#":
            break  # comment to end of line
        col = i + 1
        if c == '"':
            j = i + 1
            buf = []
            while j < n and raw[j] != '"':
                buf.append(raw[j])
                j += 1
            if j >= n:
                raise AskError(
                    what='A quoted string is missing its closing " mark.',
                    where=f'line {lineno}',
                    fix='Add a closing double-quote, e.g. "West"',
                    line=lineno,
                )
            toks.append(Token("STR", "".join(buf), lineno, col))
            i = j + 1
            continue
        if c == "`":
            # `column.name` - an unambiguous COLUMN REFERENCE for names that
            # aren't valid bare words (dots, spaces, punctuation - common in
            # raw exports like ILOStat/Eurostat's "ref_area.label" style
            # columns). Distinct from "..." which is always a value literal,
            # so `where `sex.label` is "Total"` can't be confused with
            # `where "sex.label" is "Total"` (a constant string compared to
            # another constant string, which is never what's meant). Brackets
            # were the obvious first choice but are already `in [...]`'s list
            # syntax, so backticks (SQL/dbt's own convention for this) it is.
            j = i + 1
            buf = []
            while j < n and raw[j] != "`":
                buf.append(raw[j])
                j += 1
            if j >= n:
                raise AskError(
                    what="A column reference is missing its closing ` mark.",
                    where=f"line {lineno}",
                    fix="Add a closing backtick, e.g. `sex.label`",
                    line=lineno,
                )
            toks.append(Token("COLREF", "".join(buf), lineno, col))
            i = j + 1
            continue
        if c.isdigit() or (c == "." and i + 1 < n and raw[i + 1].isdigit()):
            j = i
            dot = False
            while j < n and (raw[j].isdigit() or (raw[j] == "." and not dot)):
                if raw[j] == ".":
                    dot = True
                j += 1
            num = raw[i:j]
            toks.append(Token("NUM", float(num) if "." in num else int(num), lineno, col))
            i = j
            continue
        if raw[i:i + 2] in TWO_CHAR_OPS:
            toks.append(Token("OP", raw[i:i + 2], lineno, col))
            i += 2
            continue
        if c in ONE_CHAR_OPS:
            toks.append(Token("OP", c, lineno, col))
            i += 1
            continue
        if c in PUNCT:
            toks.append(Token("PUNCT", c, lineno, col))
            i += 1
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (raw[j].isalnum() or raw[j] in "_"):
                j += 1
            toks.append(Token("WORD", raw[i:j], lineno, col))
            i = j
            continue
        raise AskError(
            what=f"I don't understand the character {c!r}.",
            where=f"line {lineno}, column {col}",
            fix="Remove it, or put text in double quotes.",
            line=lineno,
        )
    return toks
