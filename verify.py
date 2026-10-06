"""
Checks an analyst answer against the tool results it was written from.
Prompting gpt-oss "never state a number that isn't in a tool result" was not
enough: in testing it invented dates and values for a table, computed an
"employment rate" as 100 minus unemployment, and cited real-sounding papers
that no search had returned. So the code checks:

  numbers    every number in the answer must appear in a tool result, or be
             a difference / ratio / % change of two numbers that BOTH appear in
             the answer and in a tool result (i.e. the arithmetic is shown) —
             rounded to the answer's precision (derived ones within 0.1%, since
             answers round as they go), or a span between two years the answer
             names. "237.5 million" matches 237,527,782; numbers inside names
             (COVID-19, G20, series ids) are ignored. Allowing any pair of tool
             numbers accepted 100% of random 1-decimal numbers in [13, 100]
             against a county profile (~150 numbers -> ~90K pairs); see tests/.
  citations  every author-year citation ("Chetty et al. 2016") and every
             "<file>.pdf" must appear in the passages search_papers returned;
             data source tags "(World Bank: X, 2024)" and place names with a
             year ("Nigeria (2024)") aren't citations

It's a guard, not a proof: a number can match by coincidence, and wording
(e.g. "doubled") isn't checked.
"""

import json
import re
from pathlib import Path

import numpy as np

_RANGE = re.compile(r"\b(?:19|20)\d{2}\s*[-–‑]\s*\d{2,4}\b")      # 1978-83, 2004-2013
_DATE = re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b")        # 2026-09-01, 2026-09
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# Numbers inside names, not quantities: COVID-19, G20, CO2, SP.DYN.LE00.IN (a
# word with a capital letter, glued to the digits or joined by a hyphen).
_NAME = re.compile(r"\b[A-Za-z]*[A-Z][A-Za-z.]*-?\d+(?:\.\d+)*")
# Non-breaking / Unicode hyphens (gpt-oss writes "COVID‑19", "2026‑09‑01")
_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-"})
# "237.5 million" = 237,527,782 to the stated precision; also "$2.7B"
_SCALE = re.compile(r"\s*((?i:thousand|million|billion|trillion))\b|([KMBT])\b")
_SCALES = {"thousand": 3, "million": 6, "billion": 9, "trillion": 12, "k": 3, "m": 6, "b": 9, "t": 12}
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# Derived numbers may be off by this much (relative): "2,362.861 ÷ 102.824 ≈ 22.99"
# (exactly 22.980) is an answer rounding as it goes, not an invented figure.
DERIVED_REL_TOL = 1e-3
# Author-year citations: "Chetty et al. (2016", "Chetty & Hendren 2018", "Lundberg (2017)".
# A bare "Name 2020" only counts in parentheses, and never for months or
# ALL-CAPS series ids ("Feb 2020", "EMRATIO, 2020" are dates, not citations).
_CITATION = re.compile(
    r"\b([A-Z][a-z][a-zA-Z'’-]*)(?:\s+et\s+al\.?,?\s+\(?|\s+(?:and|&)\s+[A-Z][a-zA-Z'’-]+,?\s+\(?|\s+\()"
    r"((?:19|20)\d{2})")
_MONTHS = {m.lower() for m in "January February March April May June July August September October "
           "November December Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec".split()}
_PDF = re.compile(r"[\w.-]+\.pdf\b")


def _clean(text: str) -> str:
    text = text.translate(_HYPHENS)
    # dates before ranges: "2026-09-15" read as the range "2026-09" left a stray 15
    return _RANGE.sub(" ", _DATE.sub(" ", _NAME.sub(" ", text)))


def _numbers(text: str) -> list[tuple[str, float, int, float | None]]:
    """(as written, value, decimals, unscaled value) for each number, skipping
    dates, years, names (COVID-19), and small integers (list numbering, months,
    counts). A scale word multiplies: "237.5 million" -> 237,500,000 with
    decimals -5 (i.e. +-50,000), unscaled 237.5 (None if no scale word)."""
    text = _clean(text)
    out = []
    for m in _NUM.finditer(text):
        raw = m.group()
        value = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        scale = _SCALE.match(text, m.end())
        if scale and (word := (scale.group(1) or scale.group(2) or "").lower()) in _SCALES:
            exp = _SCALES[word]
            out.append((raw + " " + scale.group().strip(), value * 10 ** exp, decimals - exp, value))
            continue
        if decimals == 0 and (value <= 12 or 1900 <= value <= 2100):
            continue
        out.append((raw, value, decimals, None))
    return out


def _years(text: str) -> list[int]:
    return [int(y) for y in _YEAR.findall(_RANGE.sub(" ", _DATE.sub(" ", text.translate(_HYPHENS))))]


def _matches(value: float, decimals: int, candidates: np.ndarray, rel_tol: float = 0.0) -> bool:
    if not len(candidates):
        return False
    tol = np.maximum(0.5 * 10.0 ** -decimals, rel_tol * np.abs(candidates)) + 1e-9
    return bool(np.any(np.abs(candidates - value) <= tol))


def unsupported_numbers(answer: str, structured: str, passages: str) -> list[str]:
    source_numbers = _numbers(structured + " " + passages)
    source = np.array(sorted({v for _, v, _, _ in source_numbers} | {u for *_, u in source_numbers if u}))
    fractions = source[source <= 1] * 100  # 0.38 in a tool result, "38%" in the answer
    direct = np.concatenate([source, fractions])
    found = _numbers(answer)
    # numbers the answer took straight from a tool: the only allowed operands
    operands = set()
    for _, value, decimals, unscaled in found:
        if _matches(value, decimals, direct):
            operands.add(value)
        elif unscaled is not None and _matches(unscaled, decimals, direct):
            operands.add(unscaled)
    operands = np.array(sorted(operands))
    derived = np.array([])
    if len(operands) > 1:
        a, b = np.meshgrid(operands, operands)
        with np.errstate(divide="ignore", invalid="ignore"):
            derived = np.concatenate([np.abs(a - b).ravel(), (a / b).ravel(),
                                      (100 * a / b).ravel(), np.abs(100 * (a - b) / b).ravel()])
        derived = derived[np.isfinite(derived)]
    # spans between two years the answer names ("2025 − 1960 = 65 years")
    years = sorted(set(_years(answer)))
    spans = np.array([b - a for i, a in enumerate(years) for b in years[i + 1:]], dtype=float)
    missing = [raw for raw, value, decimals, unscaled in found
               if not (_matches(value, decimals, direct)
                       or (unscaled is not None and _matches(unscaled, decimals, direct))
                       or _matches(value, decimals, derived, DERIVED_REL_TOL)
                       or _matches(value, decimals, spans))]
    return list(dict.fromkeys(missing))


# Data source tags the analyst is told to write: "(World Bank: SP.DYN.LE00.IN, 2024)"
_SOURCE_TAG = re.compile(r"\((?:World Bank|FRED|Opportunity Atlas|Atlas|paper)\b[^)]*\)")
_US_STATES = ("Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia "
              "Hawaii Idaho Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts "
              "Michigan Minnesota Mississippi Missouri Montana Nebraska Nevada Hampshire Jersey Mexico York "
              "Carolina Dakota Ohio Oklahoma Oregon Pennsylvania Rhode Island Tennessee Texas Utah Vermont "
              "Virginia Washington Wisconsin Wyoming")
_PLACE_EXTRA = "County Parish State States Region Eastern Western Northern Southern Central World Bank Census"
_places: set[str] | None = None


def _place_words() -> set[str]:
    """Capitalized words of economy, region, and US state names: "Nigeria
    (2024)" is a figure's year, not a paper by Nigeria."""
    global _places
    if _places is None:
        _places = set(f"{_US_STATES} {_PLACE_EXTRA}".split())
        try:
            for c in json.loads((Path(__file__).parent / "data/worldbank/countries.json").read_text()):
                if not c["aggregate"] or c["name"].strip() == c["region"]:
                    _places |= set(re.findall(r"[A-Z][a-z]+", c["name"]))
                _places |= set(re.findall(r"[A-Z][a-z]+", c["region"]))
        except (OSError, ValueError, KeyError):
            pass  # catalog not built yet: US states only
    return _places


def unsupported_citations(answer: str, passages: str, sources: set[str]) -> list[str]:
    missing = []
    places = _place_words()
    for m in _CITATION.finditer(_SOURCE_TAG.sub(" ", answer.translate(_HYPHENS))):
        name, year = m.groups()
        if name.lower() in _MONTHS or set(re.findall(r"[A-Z][a-z]+", m.group())) & places:
            continue
        if not re.search(rf"{re.escape(name)}[^\n]{{0,120}}{year}", passages):
            missing.append(m.group().strip())
    missing += [f for f in _PDF.findall(answer) if f not in sources]
    return list(dict.fromkeys(missing))
