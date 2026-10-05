"""
Checks an analyst answer against the tool results it was written from.
Prompting gpt-oss "never state a number that isn't in a tool result" was not
enough: in testing it invented dates and values for a table, computed an
"employment rate" as 100 minus unemployment, and cited real-sounding papers
that no search had returned. So the code checks:

  numbers    every number in the answer must appear in a tool result, or be
             a difference / ratio / % change of two numbers that BOTH appear in
             the answer and in a tool result (i.e. the arithmetic is shown) —
             rounded to the answer's precision. Allowing any pair of tool numbers
             accepted 100% of random 1-decimal numbers in [13, 100] against a
             county profile (~150 numbers -> ~90K pairs); see tests/.
  citations  every author-year citation ("Chetty et al. 2016") and every
             "<file>.pdf" must appear in the passages search_papers returned

It's a guard, not a proof: a number can match by coincidence, and wording
(e.g. "doubled") isn't checked.
"""

import re

import numpy as np

_RANGE = re.compile(r"\b(?:19|20)\d{2}\s*[-–‑]\s*\d{2,4}\b")      # 1978-83, 2004-2013
_DATE = re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b")        # 2026-09-01, 2026-09
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# Author-year citations: "Chetty et al. (2016", "Chetty & Hendren 2018", "Lundberg (2017)".
# A bare "Name 2020" only counts in parentheses, and never for months or
# ALL-CAPS series ids ("Feb 2020", "EMRATIO, 2020" are dates, not citations).
_CITATION = re.compile(
    r"\b([A-Z][a-z][a-zA-Z'’-]*)(?:\s+et\s+al\.?,?\s+\(?|\s+(?:and|&)\s+[A-Z][a-zA-Z'’-]+,?\s+\(?|\s+\()"
    r"((?:19|20)\d{2})")
_MONTHS = {m.lower() for m in "January February March April May June July August September October "
           "November December Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec".split()}
_PDF = re.compile(r"[\w.-]+\.pdf\b")


def _numbers(text: str) -> list[tuple[str, float, int]]:
    """(as written, value, decimals) for each number, skipping dates, years,
    and small integers (list numbering, months, counts)."""
    text = _DATE.sub(" ", _RANGE.sub(" ", text))
    out = []
    for m in _NUM.finditer(text):
        raw = m.group()
        value = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if decimals == 0 and (value <= 12 or 1900 <= value <= 2100):
            continue
        out.append((raw, value, decimals))
    return out


def _matches(value: float, decimals: int, candidates: np.ndarray) -> bool:
    return bool(len(candidates)) and bool(np.any(np.abs(candidates - value) <= 0.5 * 10 ** -decimals + 1e-9))


def unsupported_numbers(answer: str, structured: str, passages: str) -> list[str]:
    source = np.array(sorted({v for _, v, _ in _numbers(structured + " " + passages)}))
    fractions = source[source <= 1] * 100  # 0.38 in a tool result, "38%" in the answer
    found = _numbers(answer)
    # numbers the answer took straight from a tool: the only allowed operands
    operands = np.array(sorted({v for _, v, d in found if _matches(v, d, source)}))
    derived = np.array([])
    if len(operands) > 1:
        a, b = np.meshgrid(operands, operands)
        with np.errstate(divide="ignore", invalid="ignore"):
            derived = np.concatenate([np.abs(a - b).ravel(), (a / b).ravel(),
                                      (100 * a / b).ravel(), np.abs(100 * (a - b) / b).ravel()])
        derived = derived[np.isfinite(derived)]
    candidates = np.concatenate([source, fractions, derived])
    missing = [raw for raw, value, decimals in found if not _matches(value, decimals, candidates)]
    return list(dict.fromkeys(missing))


def unsupported_citations(answer: str, passages: str, sources: set[str]) -> list[str]:
    missing = []
    for m in _CITATION.finditer(answer):
        name, year = m.groups()
        if name.lower() in _MONTHS:
            continue
        if not re.search(rf"{re.escape(name)}[^\n]{{0,120}}{year}", passages):
            missing.append(m.group().strip())
    missing += [f for f in _PDF.findall(answer) if f not in sources]
    return list(dict.fromkeys(missing))
