"""
Reports people repeat (ROADMAP Phase 5). Each report is a fixed recipe:

  1. gather  — a fixed list of data calls to the existing modules (no model)
  2. compute — growth rates, ranks, gaps in Python; Python writes every table
  3. write   — ONE Groq request (no tools) turns a compact fact sheet into prose,
               checked by verify.py; one revision request only if it fails
  4. export  — report_export.py: PDF, Word, Markdown, LaTeX, BibTeX, data zip

So tables can't contain invented numbers, a report costs ~2-5K Groq tokens
(a chat question: 10-30K), and the same inputs give the same structure.
Untick "Write a summary" (web) or pass --no-summary (command line) for a
summary written by Python instead: highest/lowest per indicator, no tokens. The
same happens without a Groq key or when Groq is down.

    .venv/bin/python workflows.py compare Kenya Ghana Nigeria
    .venv/bin/python workflows.py compare India China --topics income,growth,health
    .venv/bin/python workflows.py compare Peru Chile --indicators "female labor force"
    .venv/bin/python workflows.py brief Kenya
    .venv/bin/python workflows.py poverty India --no-summary
    .venv/bin/python workflows.py works cash transfers --region Africa --outcome "mental health"

Compare is here; the brief, poverty profile and "what works" recipes are in
report_recipes.py.

Files land in reports/<date>_<name>/ (gitignored). The web UI (web.py) runs the
same recipes from its Reports tab.
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import charts
import compute
import devecon
import groq_client
import verify
from groq_client import GroqUnavailable

REPORTS_DIR = Path("reports")
MAX_COUNTRIES = 6
WINDOW = 12            # years of history per indicator: the World Bank tool returns at most 13 points in full
GROWTH_YEARS = 10      # "average growth over the last N years" row
MAX_CHARTS = 8

# Topic -> World Bank indicators, in table order. The first of each topic is charted.
TOPICS = {
    "income": ["NY.GDP.PCAP.PP.CD", "NY.GDP.PCAP.CD", "NY.GNP.PCAP.CD"],
    "growth": ["NY.GDP.PCAP.KD.ZG"],          # plus a computed 10-year average (constant prices)
    "poverty": ["SI.POV.DDAY", "SI.POV.LMIC", "SI.POV.GAPS", "SI.POV.GINI"],
    "health": ["SP.DYN.LE00.IN", "SH.DYN.MORT", "SH.STA.STNT.ZS"],
    "education": ["SE.LPV.PRIM", "SE.SEC.ENRR", "SE.ADT.LITR.ZS"],
    "infrastructure": ["EG.ELC.ACCS.ZS", "SH.H2O.BASW.ZS", "IT.NET.USER.ZS"],
    "jobs": ["SL.TLF.CACT.FE.ZS", "SL.AGR.EMPL.ZS", "SL.UEM.TOTL.ZS"],
    "population": ["SP.POP.TOTL", "SP.URB.TOTL.IN.ZS"],
    "macro": ["FP.CPI.TOTL.ZG", "GC.DOD.TOTL.GD.ZS", "NE.TRD.GNFS.ZS", "BX.TRF.PWKR.DT.GD.ZS"],
}
DEFAULT_TOPICS = ["income", "growth", "poverty", "health", "education"]
TOPIC_TITLES = {"income": "Income", "growth": "Growth", "poverty": "Poverty and inequality", "health": "Health",
                "education": "Education", "infrastructure": "Infrastructure and services", "jobs": "Jobs",
                "population": "Population", "macro": "Macroeconomy", "extra": "Other indicators"}
# indicators whose values come from household surveys: years differ, often old
SURVEY_BASED = {"SI.POV.DDAY", "SI.POV.LMIC", "SI.POV.UMIC", "SI.POV.GAPS", "SI.POV.GINI", "SE.ADT.LITR.ZS",
                "SH.STA.STNT.ZS", "SE.LPV.PRIM"}

WDI_SOURCE = {"key": "worldbank_wdi", "type": "misc", "author": "{World Bank}",
              "title": "World Development Indicators", "url": "https://databank.worldbank.org/source/world-development-indicators"}

SUMMARY_PROMPT = """You write the summary at the top of a report comparing countries, for a general audience. Use ONLY the facts in the JSON below. Every number you write must appear there (you may round it as shown). Give the year with any value whose years differ across countries, and with every survey-based value (poverty, inequality, literacy, stunting, learning). When discussing income, say whether a figure is PPP (international $, best for living standards) or at market exchange rates. Describe differences only; don't explain their causes — the facts contain no evidence about causes. 150-250 words in 2-4 short paragraphs, plain markdown (bold allowed; no headings, no tables)."""


# ------------------------------------------------------------------ formatting
def fmt(value: float | None, indicator: str = "") -> str:
    """How a value appears in tables and in the fact sheet (the model copies these)."""
    if value is None:
        return "–"
    if indicator == "SP.POP.TOTL":
        return f"{value / 1e6:,.1f} million"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60]


# ------------------------------------------------------------------ the model's part
def write_summary(facts: dict, prompt: str, emit=lambda *_: None, check=None) -> dict:
    """One request (two if the fact-check fails). Never raises: without Groq the
    report simply has no summary. check(text, facts_text) -> extra problems
    (citations not in the fact sheet), on top of the number check."""
    facts_text = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": facts_text}]
    tokens = 0
    if not groq_client.GROQ_API_KEY and not groq_client.OPENROUTER_API_KEY:
        return {"text": "", "status": "unavailable", "reason": "no GROQ_API_KEY in .env", "unverified": [], "tokens": 0}
    try:
        for attempt in range(2):
            emit("step", "Writing the summary" if attempt == 0 else "Fact-check flagged numbers — asking for a fix")
            response = groq_post({"messages": messages, "temperature": 0.2, "max_tokens": 2048,
                                  "reasoning_effort": "low"}).json()
            tokens += response.get("usage", {}).get("total_tokens", 0)
            text = (response["choices"][0]["message"].get("content") or "").strip()
            unverified = verify.unsupported_numbers(text, facts_text, "")
            cited = check(text, facts_text) if check else []
            if not unverified and not cited:
                break
            problems = ([f"These numbers are not in the facts: {unverified}."] if unverified else []) + \
                ([f"These citations are not in the facts: {cited}."] if cited else [])
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": (
                " ".join(problems) + " Rewrite the summary without them (use only numbers and sources from "
                "the facts). Don't mention this check.")}]
            unverified = unverified + cited
    except GroqUnavailable as e:
        return {"text": "", "status": "unavailable", "reason": str(e)[:200], "unverified": [], "tokens": tokens}
    return {"text": text, "status": "unverified" if unverified else "ok", "unverified": unverified,
            "tokens": tokens, "backend": groq_client.last_provider}


groq_post = groq_client.post  # replaced in tests


def python_summary(facts: dict, reason: str = "") -> dict:
    """The free summary: one line per indicator, highest and lowest, straight from
    the fact sheet (so nothing to fact-check). Used when the checkbox is unticked,
    or when the model is unavailable."""
    lines = []
    for row in facts["indicators"]:
        values = row["values"]
        if len(values) < 2 or "highest" not in row and not row["indicator"].startswith("Average annual growth"):
            continue
        when = lambda v: v.get("year") or v.get("years")  # noqa: E731
        if "highest" in row:
            hi, lo = row["highest"], row["lowest"]
        else:  # computed growth row: rank the formatted values
            ranked = sorted(values, key=lambda n: float(values[n]["value"].replace(",", "")))
            hi, lo = ranked[-1], ranked[0]
        line = (f"**{row['indicator']}**: highest in {hi} ({values[hi]['value']}, {when(values[hi])}), "
                f"lowest in {lo} ({values[lo]['value']}, {when(values[lo])})")
        if row.get("highest_to_lowest_ratio"):
            line += f", {row['highest_to_lowest_ratio']} times as high"
        if len({str(when(v)) for v in values.values()}) > 1:
            line += "; years differ"
        lines.append(f"- {line}.")
    return {"text": "\n".join(lines), "status": "python", "reason": reason, "unverified": [], "tokens": 0}


# ------------------------------------------------------------------ compare countries
def resolve_indicator(wb, text: str) -> str:
    """An indicator id, or the best catalog match for a phrase ("female labor force")."""
    if text in wb.by_id:
        return text
    found = wb.search(text)
    if not found:
        raise ValueError(f"no World Bank indicator matches {text!r}")
    return found[0]["id"]


def compare(wb, countries: list[str], topics: list[str] | None = None, indicators: list[str] | None = None,
            emit=lambda *_: None, write: bool = True) -> dict:
    """Report comparing 2-6 countries on topic bundles and/or extra indicators.
    write=False: the summary is written by Python (no Groq tokens)."""
    codes = list(dict.fromkeys(wb.country(c) for c in countries))
    if not 2 <= len(codes) <= MAX_COUNTRIES:
        raise ValueError(f"compare needs 2-{MAX_COUNTRIES} different countries (got {len(codes)})")
    names = [wb.countries[c]["name"] for c in codes]
    topics = [t for t in (topics or ([] if indicators else DEFAULT_TOPICS))]
    unknown = [t for t in topics if t not in TOPICS]
    if unknown:
        raise ValueError(f"unknown topic(s) {unknown}; choose from {', '.join(TOPICS)}")
    plan = [(t, i) for t in topics for i in TOPICS[t]]
    plan += [("extra", resolve_indicator(wb, text)) for text in indicators or []]
    start = datetime.date.today().year - WINDOW

    results, sections, facts_rows, used = [], {}, [], []
    for topic, indicator in plan:
        emit("step", f"World Bank: {wb.by_id[indicator]['name']}")
        args = {"series": indicator, "countries": codes, "start": start, "peers": True}
        try:
            result = wb.get(indicator, codes, start, None, peers=True)
        except Exception as e:  # one failed indicator shouldn't sink the report
            emit("step", f"  skipped {indicator}: {e}")
            continue
        results.append(("get_data", args, result))
        used.append(indicator)
        by_code = {e["code"]: e for e in result["economies"]}
        cells, fact_values = [], {}
        for code, name in zip(codes, names):
            latest = by_code.get(code, {}).get("latest")
            cells.append(f"{fmt(latest['value'], indicator)} ({latest['year']})" if latest else "–")
            if latest:
                fact_values[name] = {"value": fmt(latest["value"], indicator), "year": latest["year"]}
                peers = by_code[code].get("peers", {}).get("income_group")
                if peers and "rank" in peers:
                    fact_values[name]["rank_in_income_group"] = f"{peers['rank']} of {peers['of']} ({peers['group']})"
        row_name = wb.by_id[indicator]["name"]
        sections.setdefault(topic, {"rows": [], "notes": set(), "results": []})
        sections[topic]["rows"].append([row_name] + cells)
        sections[topic]["results"].append(("get_data", args, result))
        if indicator in SURVEY_BASED:
            sections[topic]["notes"].add("Survey-based figures exist only for survey years, which differ across "
                                         "countries and can be several years old.")
        if indicator.startswith("NY.GDP.PCAP.PP") or indicator == "NY.GDP.PCAP.CD":
            sections[topic]["notes"].add("PPP (international $) adjusts for price differences and is the better "
                                         "measure of living standards; current US$ uses market exchange rates.")
        values = [(n, by_code[c]["latest"]["value"]) for c, n in zip(codes, names) if by_code.get(c, {}).get("latest")]
        fact = {"indicator": row_name, "values": fact_values}
        if len(values) >= 2:
            hi, lo = max(values, key=lambda v: v[1]), min(values, key=lambda v: v[1])
            fact["highest"], fact["lowest"] = hi[0], lo[0]
            # ratios only where "x times as high" means something: money levels, rates per 1,000
            if lo[1] > 0 and indicator not in SURVEY_BASED and ("$" in row_name or "per 1,000" in row_name):
                fact["highest_to_lowest_ratio"] = round(hi[1] / lo[1], 1)
        facts_rows.append(fact)

    if "growth" in topics:  # average growth, constant prices, computed here
        emit("step", f"Computing average growth over the last {GROWTH_YEARS} years")
        row, fact_values = growth_row(wb, codes, names)
        if row:
            sections.setdefault("growth", {"rows": [], "notes": set(), "results": []})
            sections["growth"]["rows"].insert(0, row)
            sections["growth"]["notes"].add(f"Average growth: compound annual growth of GDP per capita in constant "
                                            f"2015 US$ (NY.GDP.PCAP.KD) over the last {GROWTH_YEARS} years with data, "
                                            "computed here.")
            facts_rows.insert(0, {"indicator": f"Average annual growth of GDP per capita, last {GROWTH_YEARS} years "
                                               "(constant prices, %)", "values": fact_values})
            used.append("NY.GDP.PCAP.KD")

    emit("step", "Drawing charts")
    headline = [sections[t]["results"][0] for t in sections if sections[t]["results"]][:MAX_CHARTS]
    try:
        chart_paths = [str(p) for p in charts.auto(headline, wb=wb, kinds={"line", "map"})]
    except Exception as e:  # charts are a bonus
        emit("step", f"  charts failed: {e}")
        chart_paths = []

    title = f"{', '.join(names[:-1])} and {names[-1]}: a comparison"
    facts = {"countries": names, "indicators": facts_rows}
    summary = write_summary(facts, SUMMARY_PROMPT, emit) if write else python_summary(facts)
    if summary["status"] == "unavailable":  # no Groq: the free summary instead of none
        summary = python_summary(facts, reason=summary["reason"])
    report = {
        "kind": "compare", "title": title, "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "inputs": {"countries": names, "topics": topics, "indicators": indicators or []},
        "summary": summary,
        "sections": [{"heading": TOPIC_TITLES[t], "table": {"columns": ["Indicator"] + names,
                                                            "rows": s["rows"]},
                      "notes": sorted(s["notes"]),
                      "charts": [p for p in chart_paths if any(r[2]["indicator"] in Path(p).name
                                                                for r in s["results"])]}
                     for t, s in sections.items()],
        "about": ["Values are the latest year with data for each country (shown in brackets), from the World "
                  "Bank's World Development Indicators, fetched when the report was made.",
                  "Ranks within income groups compare each country with the World Bank income group it belongs to."],
        "sources": [{**WDI_SOURCE, "note": "Indicators: " + ", ".join(dict.fromkeys(used)),
                     "accessed": datetime.date.today().isoformat()}],
        "facts": facts,
        "results": results,
    }
    emit("step", "Done")
    return report


def growth_row(wb, codes: list[str], names: list[str]) -> tuple[list | None, dict]:
    end_year = datetime.date.today().year
    data, _ = compute.fetch({"series": "NY.GDP.PCAP.KD", "countries": codes, "start": end_year - GROWTH_YEARS - 6}, wb)
    cells, facts = [], {}
    for code, name in zip(codes, names):
        series = data.get(code, {})
        last = max(series) if series else None
        first = last - GROWTH_YEARS if last else None
        if last and first in series and series[first] > 0 and series[last] > 0:
            g = devecon.cagr(series[first], series[last], GROWTH_YEARS)
            cells.append(f"{g:.1f} ({first}-{last})")
            facts[name] = {"value": f"{g:.1f}", "years": f"{first}-{last}"}
        else:
            cells.append("–")
    if not facts:
        return None, {}
    return [f"Average annual growth of GDP per capita, last {GROWTH_YEARS} years (constant prices, %)"] + cells, facts


# ------------------------------------------------------------------ command line
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build a report.", epilog=f"Topics: {', '.join(TOPICS)}")
    sub = parser.add_subparsers(dest="kind", required=True)
    p = sub.add_parser("compare", help=f"compare 2-{MAX_COUNTRIES} countries")
    p.add_argument("countries", nargs="+")
    p.add_argument("--topics", help=f"comma-separated, default {','.join(DEFAULT_TOPICS)}")
    p.add_argument("--indicators", action="append", help="extra indicator: id or a few words (repeatable)")
    for name, text in (("brief", "country brief: growth, income, poverty, health, education vs peers, research"),
                       ("poverty", "poverty profile: every survey year, number of poor, survey gaps, regions")):
        q = sub.add_parser(name, help=text)
        q.add_argument("country")
    q = sub.add_parser("works", help="'what works' review: J-PAL evaluations, OpenAlex reviews, library passages")
    q.add_argument("intervention", nargs="+")
    q.add_argument("--region", default="", help="a World Bank region (any part of its name) or a country")
    q.add_argument("--outcome", default="", help="e.g. 'student learning'")
    for q in sub.choices.values():
        q.add_argument("--out", default=str(REPORTS_DIR))
        q.add_argument("--no-summary", action="store_true", help="summary written by Python: no Groq tokens")
    args = parser.parse_args(argv)

    import report_export
    import report_recipes
    from worldbank import WorldBank
    wb = WorldBank()
    say = lambda kind, text: print(f"  {text}", flush=True)  # noqa: E731
    if args.kind == "compare":
        report = compare(wb, args.countries, args.topics.split(",") if args.topics else None,
                         args.indicators, emit=say, write=not args.no_summary)
    elif args.kind == "brief":
        report = report_recipes.country_brief(wb, args.country, emit=say, write=not args.no_summary)
    elif args.kind == "poverty":
        report = report_recipes.poverty_profile(wb, args.country, emit=say, write=not args.no_summary)
    else:  # library passages without Laya on the command line (it needs ~1.7GB; web.py uses the full search)
        report = report_recipes.what_works(wb, " ".join(args.intervention), args.region, args.outcome, emit=say,
                                           write=not args.no_summary, library=report_recipes.library_search())
    folder = report_export.write_all(report, Path(args.out), wb)
    s = report["summary"]
    print(f"\n{report['title']}\nSummary: {s['status']}" + (f" — not verified: {s['unverified']}" if s["unverified"] else "")
          + (f" ({s['reason']})" if s.get("reason") else "") + f", {s['tokens']} Groq tokens"
          + f"\nSaved in {folder}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
