"""
What changed between two runs of the same report (web.py adds it when a report is made again).

Compares what Python wrote, cell by cell: every table cell (section, row, column) and every
listed study or paper. The summary is left out on purpose: it is reworded every time, and a
change of wording is not a change of facts. Nothing here calls a model or fetches data.

Kinds of change, for one cell:
  newer data       the value's year (the "(2023)" in "1,234 (2023)") moved forward
  revised          same year (or no year), a different value: the source revised its figure
  now available    empty ("–") or absent before, a value now
  no longer shown  a value before, empty or absent now (e.g. a forecast row from an older edition)
"""

import re

HEADING = "Changes since the last report"
MAX_ROWS = 40  # cells listed; the rest are counted in a note
EMPTY = {"", "–", "-"}
_YEAR = re.compile(r"\((\d{4})\)\s*$")

ORDER = ["newer data", "revised", "now available", "no longer shown"]


def _cells(report: dict) -> dict[tuple[str, str, str], str]:
    """{(section heading, row label, column): cell text} for every table cell after the label column."""
    out = {}
    for sec in report.get("sections", []):
        if sec.get("heading") == HEADING or not sec.get("table"):
            continue
        columns = sec["table"]["columns"]
        for row in sec["table"]["rows"]:
            for col, cell in zip(columns[1:], row[1:]):
                out[(sec["heading"], str(row[0]), str(col))] = str(cell).strip()
    return out


def _items(report: dict) -> dict[str, set[str]]:
    return {sec["heading"]: {i["title"] for i in sec.get("items", [])}
            for sec in report.get("sections", []) if sec.get("heading") != HEADING and sec.get("items")}


def _kind(before: str, now: str) -> str | None:
    if before == now or (before in EMPTY and now in EMPTY):
        return None
    if before in EMPTY:
        return "now available"
    if now in EMPTY:
        return "no longer shown"
    old_year, new_year = _YEAR.search(before), _YEAR.search(now)
    if old_year and new_year and new_year.group(1) > old_year.group(1):
        return "newer data"
    return "revised"


def changes(old: dict, new: dict) -> list[dict]:
    """Every changed cell and listed item, in report order then ORDER."""
    before, now = _cells(old), _cells(new)
    found = []
    for key in list(now) + [k for k in before if k not in now]:
        b, n = before.get(key, ""), now.get(key, "")
        kind = _kind(b, n)
        if kind:
            found.append({"section": key[0], "what": f"{key[1]} — {key[2]}", "before": b or "–", "now": n or "–",
                          "change": kind})
    old_items, new_items = _items(old), _items(new)
    for heading in list(new_items) + [h for h in old_items if h not in new_items]:
        o, n = old_items.get(heading, set()), new_items.get(heading, set())
        found += [{"section": heading, "what": t, "before": "–", "now": "listed", "change": "now available"}
                  for t in sorted(n - o)]
        found += [{"section": heading, "what": t, "before": "listed", "now": "–", "change": "no longer shown"}
                  for t in sorted(o - n)]
    sections = list(dict.fromkeys([s["heading"] for s in new.get("sections", [])] +
                                  [s["heading"] for s in old.get("sections", [])]))
    return sorted(found, key=lambda c: (sections.index(c["section"]) if c["section"] in sections else len(sections),
                                        ORDER.index(c["change"])))


def section(old: dict, new: dict) -> dict:
    """The report section that lists the changes (a table, or a note that nothing changed)."""
    when = str(old.get("created", ""))[:16].replace("T", " ")
    found = changes(old, new)
    notes = [f"Compared with the same report made on {when}: every table cell and listed study, not the summary "
             "(its wording changes each time even when the facts don't)."]
    differs = [k for k in sorted(set(old.get("inputs", {})) | set(new.get("inputs", {})))
               if old.get("inputs", {}).get(k) != new.get("inputs", {}).get(k)]
    if differs:
        notes.append("The two reports were made with different settings (" + ", ".join(differs) + "), so some "
                     "changes come from that rather than from new data.")
    if not found:
        notes.append("Nothing changed: every value is the same as last time.")
        return {"heading": HEADING, "table": None, "notes": notes, "items": [], "charts": []}
    counts = {k: sum(c["change"] == k for c in found) for k in ORDER}
    notes.append("; ".join(f"{n} {k}" for k, n in counts.items() if n) + ". Newer data: a more recent year; "
                 "revised: the same year with a different value (sources revise past figures); now available / "
                 "no longer shown: a value appeared or went away (e.g. a forecast from a newer edition).")
    if len(found) > MAX_ROWS:
        notes.append(f"The first {MAX_ROWS} of {len(found)} changes are listed.")
    rows = [[c["section"], c["what"], c["before"], c["now"], c["change"]] for c in found[:MAX_ROWS]]
    return {"heading": HEADING, "table": {"columns": ["Section", "What", "Before", "Now", "Change"], "rows": rows},
            "notes": notes, "items": [], "charts": []}
