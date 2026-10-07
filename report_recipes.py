"""
The one-country and evidence reports (ROADMAP Phase 5), built the same way as
workflows.compare: Python gathers the data and writes every table; one
optional Groq request writes the summary from a compact fact sheet, checked by
verify.py (one revision at most); unticked, Python lists the highlights for free.

  country_brief(wb, "Kenya")       income, growth (incl. long-run and the GEP forecast), poverty,
                                   health, education vs the income group and region; subnational
                                   spread; J-PAL evaluations and library papers about the country
  poverty_profile(wb, "Kenya")     $3.00 / $4.20 / $8.30 lines for every survey year, poverty gap,
                                   number of poor, survey timing, peers, income by region
  what_works(wb, "cash transfers") J-PAL evaluations grouped by outcome and region (results quoted,
                                   not judged), OpenAlex reviews, library passages; every citation
                                   in the summary must be one of the fact sheet's

Optional data that isn't set up (long-run, Global Data Lab, DHS, J-PAL, the
paper library, OpenAlex offline) switches its section off with a note.
"""

import datetime
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import charts
import compute
import openalex
import verify
import workflows
from jpal import results_text
from workflows import WDI_SOURCE, fmt, python_summary, write_summary  # noqa: F401  (python_summary: tests)

MAX_STUDIES = 12        # J-PAL evaluations listed in "what works"
FACT_STUDIES = 8        # ... of which this many (with results) go into the fact sheet
MAX_REVIEWS = 5         # OpenAlex works
MAX_PASSAGES = 3        # library passages
COUNTRY_STUDIES = 5     # J-PAL evaluations in a country brief
COUNTRY_PAPERS = 3      # library papers in a country brief
RELEVANCE_FLOOR = 0.4   # J-PAL matches must score at least this share of the best match
EXCERPT_WORDS = 60      # J-PAL results quoted per study
MAX_SURVEY_ROWS = 12    # poverty profile: survey years shown
REGIONS_SHOWN = 6       # top and bottom regions in subnational tables
NOT_SET_UP = "not set up on this computer (see setup_assistant.py)"

INCOME = ["NY.GDP.PCAP.PP.CD", "NY.GDP.PCAP.CD", "NY.GNP.PCAP.CD"]
POVERTY = ["SI.POV.DDAY", "SI.POV.LMIC", "SI.POV.GINI"]
HEALTH = ["SP.DYN.LE00.IN", "SH.DYN.MORT", "SH.STA.STNT.ZS"]
EDUCATION = ["SE.LPV.PRIM", "SE.SEC.ENRR", "SE.ADT.LITR.ZS"]
LINES = [("SI.POV.DDAY", "$3.00"), ("SI.POV.LMIC", "$4.20"), ("SI.POV.UMIC", "$8.30")]
DHS_U5M = "CM_ECMR_C_U5M"
IMF_OUTLOOK = {"PCPIPCH": "Inflation, average consumer prices (%)",
               "GGXWDG_NGDP": "Government gross debt (% of GDP)",
               "GGXCNL_NGDP": "Government budget balance (net lending/borrowing, % of GDP)",
               "BCA_NGDPD": "Current account balance (% of GDP)",
               "LUR": "Unemployment rate (%)"}

SOURCES = {
    "gep": {"key": "worldbank_gep", "type": "misc", "author": "{World Bank}", "title": "Global Economic Prospects",
            "url": "https://www.worldbank.org/en/publication/global-economic-prospects"},
    "maddison": {"key": "bolt2024maddison", "type": "article", "author": "Bolt, Jutta and van Zanden, Jan Luiten",
                 "title": "Maddison style estimates of the evolution of the world economy: A new 2023 update",
                 "journal": "Journal of Economic Surveys", "year": "2024", "url": "https://doi.org/10.1111/joes.12618",
                 "note": "Maddison Project Database 2023, via Our World in Data"},
    "pwt": {"key": "feenstra2015pwt", "type": "article",
            "author": "Feenstra, Robert C. and Inklaar, Robert and Timmer, Marcel P.",
            "title": "The Next Generation of the Penn World Table", "journal": "American Economic Review",
            "year": "2015", "url": "https://doi.org/10.1257/aer.20130954", "note": "Penn World Table 11.0, via FRED"},
    "gdl": {"key": "gdl_shdi", "type": "misc", "author": "{Global Data Lab}",
            "title": "Subnational Human Development Database v10.2", "url": "https://globaldatalab.org/shdi/"},
    "dhs": {"key": "dhs_statcompiler", "type": "misc", "author": "{The DHS Program}",
            "title": "STATcompiler indicator data", "url": "https://api.dhsprogram.com"},
    "imf": {"key": "imf_weo", "type": "misc", "author": "{International Monetary Fund}",
            "title": "World Economic Outlook database", "url": "https://www.imf.org/external/datamapper"},
    "jpal": {"key": "jpal_evaluations", "type": "misc", "author": "{J-PAL}",
             "title": "Evaluation summaries", "url": "https://www.povertyactionlab.org/evaluations"},
}

BRIEF_PROMPT = """You write the summary at the top of a country brief, for a general audience. Use ONLY the facts in the JSON below. Every number you write must appear there (you may round it as shown). Give the year with every value. Poverty, inequality, literacy, stunting and learning figures come from household surveys: give their survey year and never call them current. Say whether an income figure is PPP (international $, best for living standards) or at market exchange rates. Growth forecasts are forecasts: say so and name the edition. For comparisons with the income group and region, use the "vs ..." fields exactly as given (computed from the data). Describe; don't explain causes — the facts contain no evidence about causes. 150-250 words in 2-4 short paragraphs, plain markdown (bold allowed; no headings, no tables)."""

POVERTY_PROMPT = """You write the summary at the top of a poverty profile, for a general audience. Use ONLY the facts in the JSON below. Every number you write must appear there (you may round it as shown). Poverty rates come from household surveys: give the survey year with every rate, say how old the latest survey is, and never call a rate current. Lines are in 2021 PPP dollars a day ($3.00 is the international extreme poverty line). "People below $3.00" is the rate times the population in that year, computed from the data. Regional figures are income per person, not poverty rates: say so if you mention them. For comparisons with the income group and region, use the "vs ..." fields exactly as given. Describe; don't explain causes. 150-250 words in 2-4 short paragraphs, plain markdown (bold allowed; no headings, no tables)."""

WORKS_PROMPT = """You write the summary at the top of an evidence review of an intervention, for a general audience. Use ONLY the JSON below. Attribute every finding inline to its source, written exactly as its cite_as: (J-PAL: <title>) for evaluations, (Author (Year)) or (Author et al. (Year)) for reviews and library papers. Never mention a study, author, title or number that is not in the JSON. Where studies find different things, say so and attribute each finding to its own study; don't average, reconcile or rank them, and claim nothing about what works "in general" beyond what the studies and reviews say. Describe how much evidence there is (number of evaluations, outcomes, regions) using the counts given. Results excerpts are J-PAL's own summaries and may be cut off: don't guess what follows. 150-250 words in 2-4 short paragraphs, plain markdown (bold allowed; no headings, no tables)."""


# ------------------------------------------------------------------ optional data
_loaded: dict[str, object] = {}
_missing: dict[str, str] = {}
LOADERS = {"longrun": ("longrun", "LongRun"), "gdl": ("gdl", "GDL"), "dhs": ("dhs", "DHS"), "jpal": ("jpal", "JPAL"),
           "imf": ("imf", "IMF")}


def load(name: str):
    """An optional data module, loaded once; None when it isn't set up (reason in _missing)."""
    if name not in _loaded:
        module, cls = LOADERS[name]
        try:
            _loaded[name] = getattr(importlib.import_module(module), cls)()
        except Exception as e:  # missing file, no network for a first catalog download
            _loaded[name], _missing[name] = None, f"{type(e).__name__}: {e}"[:200]
    return _loaded[name]


PAPERS = json.loads(Path("papers.json").read_text()) if Path("papers.json").exists() else {}
CHUNKS = Path("data/chunks.json")


def library_search(models=None):
    """(query, n) -> library passages [{source, text}], or None without a library.
    With web.py's loaded models: retrieval + Laya rerank, as in chat. Otherwise
    (command line): retrieval only, bge-small + BM25 — Laya needs ~1.7GB."""
    try:
        import ask
        if models is not None and getattr(models, "chunks", None):
            m = models
            agent = m.agent
        else:
            chunks, embeddings, bm25 = ask.load_index()
            m = SimpleNamespace(chunks=chunks, embeddings=embeddings, bm25=bm25, model=ask.load_embedder())
            agent = None
    except Exception:  # no index (papers not set up), or a model that won't load
        return None

    def search(query: str, n: int) -> list[dict]:
        found = ask.top_k_chunks(query, m.model, m.chunks, m.embeddings, m.bm25)
        if agent is not None and found:
            found = ask.rerank_with_laya(query, found, agent)
        return [{"source": c["source"], "text": c["text"]} for c, _ in found[:n]]
    return search


def paper_ref(source: str) -> dict:
    """A library PDF as a citation label, a list item and a BibTeX entry, from papers.json."""
    meta = PAPERS.get(source, {})
    cite = meta.get("cite", source)
    m = re.match(r"(.+?)\s*\((\d{4})\)$", cite)
    authors, year = (m.group(1), m.group(2)) if m else (cite, "")
    return {"cite": cite, "title": meta.get("title", source), "url": meta.get("url", ""),
            "source": {"key": "lib_" + workflows.slug(Path(source).stem).lower(), "type": "misc",
                       "author": authors.replace(", ", " and ").replace(" and and ", " and "),
                       "title": meta.get("title", source), "year": year, "url": meta.get("url", "")}}


def words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n]) + (" …" if len(w) > n else "")


# ------------------------------------------------------------------ shared pieces
def new_log() -> SimpleNamespace:
    """What a recipe collects as it goes: tool results (data zip, charts), indicator ids, fact rows, sources."""
    return SimpleNamespace(results=[], used=[], facts=[], sources={})


def groups(wb, code: str) -> list[tuple[str, str | None]]:
    """[(income group, its aggregate code), (region, its aggregate code)]."""
    meta = wb.countries[code]
    return [(meta[f], wb._aggregate_codes.get(meta[f])) for f in ("income", "region")]


def peer_columns(wb, code: str) -> list[str]:
    (inc, _), (reg, _) = groups(wb, code)
    return ["Indicator", wb.countries[code]["name"], f"{inc} (aggregate)", f"{reg} (aggregate)",
            f"Rank among {inc.lower()} economies"]


def peer_rows(wb, code: str, indicators: list[str], start: int, log, emit) -> tuple[list[list], list]:
    """One row per indicator: the country, its income-group and region aggregates
    (World Bank figures, population-weighted), and its rank in the income group.
    Fact rows say "higher"/"lower" than each group, computed here."""
    (inc, inc_code), (reg, reg_code) = groups(wb, code)
    codes = [code] + [c for c in (inc_code, reg_code) if c]
    rows, results = [], []
    for ind in indicators:
        name = wb.by_id[ind]["name"]
        emit("step", f"World Bank: {name}")
        args = {"series": ind, "countries": codes, "start": start, "peers": True}
        try:
            result = wb.get(ind, codes, start, None, peers=True)
        except Exception as e:  # one failed indicator shouldn't sink the report
            emit("step", f"  skipped {ind}: {e}")
            continue
        log.results.append(("get_data", args, result))
        results.append(("get_data", args, result))
        log.used.append(ind)
        by = {e["code"]: e for e in result["economies"]}
        latest = lambda c: by.get(c, {}).get("latest") if c else None  # noqa: E731
        own = latest(code)
        rank = by.get(code, {}).get("peers", {}).get("income_group", {})
        rows.append([name] + [f"{fmt(v['value'], ind)} ({v['year']})" if v else "–"
                              for v in (own, latest(inc_code), latest(reg_code))]
                    + [f"{rank['rank']} of {rank['of']}" if "rank" in rank and own else "–"])
        if not own:
            continue
        fact = {"indicator": name, "value": fmt(own["value"], ind), "year": own["year"]}
        for label, c in ((inc, inc_code), (reg, reg_code)):
            if (g := latest(c)):
                fact[label] = f"{fmt(g['value'], ind)} ({g['year']})"
                fact[f"vs {label}"] = ("higher" if own["value"] > g["value"] else
                                       "lower" if own["value"] < g["value"] else "the same")
        if "rank" in rank:
            fact["rank_in_income_group"] = f"{rank['rank']} of {rank['of']} (1 = highest)"
        log.facts.append(fact)
    return rows, results


def peer_notes(rows_ids: list[str]) -> list[str]:
    notes = ["Group figures are World Bank aggregates (weighted by population), not averages of countries. "
             "Rank: 1 = highest value among the income group's economies with data in recent years "
             "(for poverty or mortality, a high rank is bad)."]
    if set(rows_ids) & workflows.SURVEY_BASED:
        notes.append("Survey-based figures exist only for survey years, which differ across countries and can be "
                     "several years old.")
    if set(rows_ids) & {"NY.GDP.PCAP.PP.CD", "NY.GDP.PCAP.CD"}:
        notes.append("PPP (international $) adjusts for price differences and is the better measure of living "
                     "standards; current US$ uses market exchange rates.")
    return notes


def section(heading: str, table: dict | None = None, notes: list | None = None, items: list | None = None,
            results: list | None = None) -> dict:
    """A report section: a table, a list of items (studies, papers), or just notes (a section switched off)."""
    return {"heading": heading, "table": table, "notes": notes or [], "items": items or [], "charts": [],
            "_results": results or []}


def draw(sections: list[dict], wb, emit):
    """Line charts for each section's first data result (no peer bars, no maps: see NOTES.md)."""
    emit("step", "Drawing charts")
    headline = [s["_results"][0] for s in sections if s["_results"]][:workflows.MAX_CHARTS]
    try:
        paths = [str(p) for p in charts.auto(headline, wb=wb, kinds={"line"})]
    except Exception as e:  # charts are a bonus
        emit("step", f"  charts failed: {e}")
        paths = []
    for s in sections:
        key = next((s["_results"][0][2].get(k) for k in ("indicator", "variable", "metric")
                    if s["_results"] and s["_results"][0][2].get(k)), None)
        s["charts"] = [p for p in paths if key and key in Path(p).name]
        del s["_results"]


def listed_summary(facts: dict, reason: str = "") -> dict:
    """The free summary for one-country reports: every fact row as one line,
    straight from the fact sheet (nothing to fact-check)."""
    lines = []
    for row in facts["rows"]:
        when = row.get("year") or row.get("years")
        line = f"**{row['indicator']}**: {row['value']}" + (f" ({when})" if when else "")
        comparisons = [f"{row[k]} than {k[3:]} ({row[k[3:]]})" for k in row if k.startswith("vs ")]
        if comparisons:
            line += "; " + ", ".join(comparisons)
        if row.get("rank_in_income_group"):
            line += f"; rank {row['rank_in_income_group']}"
        lines.append(f"- {line}.")
    return {"text": "\n".join(lines), "status": "python", "reason": reason, "unverified": [], "tokens": 0}


def summarize(facts: dict, prompt: str, write: bool, free, emit, check=None) -> dict:
    summary = write_summary(facts, prompt, emit, check) if write else free(facts)
    if summary["status"] == "unavailable":  # no Groq: the free summary instead of none
        summary = free(facts, reason=summary["reason"])
    return summary


def finish(kind: str, title: str, inputs: dict, summary: dict, sections: list[dict], about: list[str],
           log, facts: dict) -> dict:
    today = datetime.date.today().isoformat()
    sources = [{**WDI_SOURCE, "note": "Indicators: " + ", ".join(dict.fromkeys(log.used)), "accessed": today}] \
        if log.used else []
    sources += [{**s, "accessed": today} for s in log.sources.values()]
    return {"kind": kind, "title": title, "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "inputs": inputs, "summary": summary, "sections": sections, "about": about, "sources": sources,
            "facts": facts, "results": log.results}


def _series_points(entry: dict) -> list[tuple[int, float]]:
    key = next((k for k in entry if k.startswith(("series", "sampled"))), None)
    return [(int(y), v) for y, v in entry.get(key, [])] if key else []


# ------------------------------------------------------------------ country brief
def country_brief(wb, country: str, emit=lambda *_: None, write: bool = True) -> dict:
    code = wb.country(country)
    if wb.countries[code]["aggregate"]:
        raise ValueError(f"{wb.countries[code]['name']} is a group of countries; a brief needs one country")
    name = wb.countries[code]["name"]
    (inc, inc_code), (reg, reg_code) = groups(wb, code)
    start = datetime.date.today().year - workflows.WINDOW
    log, sections = new_log(), []
    cols = peer_columns(wb, code)

    def peer_section(heading, indicators, extra_notes=()):
        rows, results = peer_rows(wb, code, indicators, start, log, emit)
        if rows:
            sections.append(section(heading, {"columns": cols, "rows": rows},
                                    peer_notes(indicators) + list(extra_notes), results=results))
        return rows

    peer_section("Income", INCOME)

    # growth: 10-year average (computed), annual growth, the GEP forecast
    emit("step", f"Computing average growth over the last {workflows.GROWTH_YEARS} years")
    codes = [code] + [c for c in (inc_code, reg_code) if c]
    names = [name] + [g for g, c in ((inc, inc_code), (reg, reg_code)) if c]
    growth_rows, growth_results = [], []
    row, values = workflows.growth_row(wb, codes, names)
    if row:
        cells = dict(zip(names, row[1:]))
        growth_rows.append([row[0], cells.get(name, "–"), cells.get(inc, "–"), cells.get(reg, "–"), "–"])
        if name in values:
            fact = {"indicator": row[0], "value": values[name]["value"], "years": values[name]["years"]}
            for g in (inc, reg):
                if g in values:
                    fact[g] = f"{values[g]['value']} ({values[g]['years']})"
                    fact[f"vs {g}"] = ("higher" if float(values[name]["value"]) > float(values[g]["value"]) else
                                       "lower" if float(values[name]["value"]) < float(values[g]["value"]) else
                                       "the same")
            log.facts.append(fact)
        log.used.append("NY.GDP.PCAP.KD")
    rows, results = peer_rows(wb, code, ["NY.GDP.PCAP.KD.ZG"], start, log, emit)
    growth_rows += rows
    growth_results += results
    emit("step", "World Bank: Global Economic Prospects growth forecast")
    growth_notes = [f"Average growth: compound annual growth of GDP per capita in constant 2015 US$ "
                    f"(NY.GDP.PCAP.KD) over the last {workflows.GROWTH_YEARS} years with data, computed here."]
    try:
        import worldbank
        forecast = wb.get(worldbank.FORECAST, codes)
        edition = wb.forecast_edition()
        log.results.append(("get_data", {"series": worldbank.FORECAST, "countries": codes}, forecast))
        by = {e["code"]: dict(_series_points(e)) for e in forecast["economies"]}
        first = int(edition[:4]) - 1
        for year in sorted(y for y in by.get(code, {}) if y >= first):
            label = f"Real GDP growth {year}, % ({'estimate' if year == first else 'FORECAST'}, GEP {edition})"
            cell = lambda c: f"{by[c][year]:.1f}" if c in by and year in by[c] else "–"  # noqa: E731
            growth_rows.append([label, cell(code), cell(inc_code), cell(reg_code), "–"])
            log.facts.append({"indicator": label, "value": cell(code)})
        if by.get(code):
            log.sources["gep"] = SOURCES["gep"]
            growth_notes.append(f"Forecasts: World Bank Global Economic Prospects, edition dated {edition}; GDP "
                                "growth (not per person), revised every January and June.")
    except Exception as e:
        growth_notes.append(f"Growth forecast unavailable ({type(e).__name__}).")
    if growth_rows:
        sections.append(section("Growth", {"columns": cols, "rows": growth_rows}, growth_notes,
                                 results=growth_results))

    sections.append(imf_outlook(wb, code, name, log, emit))
    sections.append(long_run(code, name, log, emit))
    peer_section("Poverty and inequality", POVERTY,
                 ["The poverty profile report shows every survey year, the $8.30 line and the number of poor."])
    peer_section("Health", HEALTH)
    peer_section("Education", EDUCATION)
    sections.append(subnational(name, log, emit))
    sections.append(country_research(wb, code, name, log, emit))

    draw(sections, wb, emit)
    facts = {"country": name, "income_group": inc, "region": reg, "rows": log.facts}
    summary = summarize(facts, BRIEF_PROMPT, write, listed_summary, emit)
    about = [f"Values are the latest year with data (shown in brackets), from the World Bank's World Development "
             f"Indicators unless stated, fetched when the report was made; {name} is compared with its World "
             f"Bank income group ({inc}) and region ({reg}).",
             "Research lists studies and papers about the country; the summary doesn't describe their findings."]
    emit("step", "Done")
    return finish("brief", f"{name}: country brief", {"country": name}, summary, sections, about, log, facts)


def imf_outlook(wb, code: str, name: str, log, emit) -> dict:
    """Inflation, debt, deficit, current account, unemployment: last year's estimate and the IMF's
    projections for three years (the World Bank's GEP forecasts growth only)."""
    heading = "IMF outlook"
    imf_ = load("imf")
    if imf_ is None:
        return section(heading, notes=[f"IMF data couldn't be reached ({_missing.get('imf', '')[:80]})."])
    emit("step", "IMF: World Economic Outlook")
    rows, edition, years = [], "", []
    for ind, label in IMF_OUTLOOK.items():
        try:
            result = imf_.get(ind, [code], name=lambda c: name)
        except Exception as e:
            emit("step", f"  skipped IMF {ind}: {e}")
            continue
        edition = result["edition"]
        first = result["economies"][0].get("projections_from") or datetime.date.today().year
        years = [first - 1, first, first + 1, first + 2]
        series = dict(map(tuple, result["economies"][0].get("series [year, value]", [])))
        if not series:
            continue
        cells = [f"{series[y]:.1f}" if y in series else "–" for y in years]
        rows.append([label] + cells)
        log.results.append(("get_data", {"source": "imf", "series": ind, "countries": [code]}, result))
        log.facts.append({"indicator": f"{label}, IMF {edition}",
                          "value": ", ".join(f"{y}: {c}" + (" (estimate)" if y == years[0] else " (projection)")
                                             for y, c in zip(years, cells) if c != "–")})
    if not rows:
        return section(heading, notes=[f"No IMF World Economic Outlook data for {name}."])
    log.sources["imf"] = {**SOURCES["imf"], "note": edition}
    return section(heading, {"columns": ["Measure"] + [f"{y}{' (est.)' if i == 0 else ''}" for i, y in
                                                      enumerate(years)], "rows": rows},
                   [f"IMF {edition}: {years[1]} onward are projections and {years[0]} is an estimate for many "
                    "countries. Revised every April and October."])


def long_run(code: str, name: str, log, emit) -> dict:
    """Maddison GDP per person since 1950, and PWT's growth accounting when installed."""
    lr = load("longrun")
    if lr is None or "mpd.gdppc" not in lr.vars:
        return section("Long-run growth", notes=[f"Long-run data (Maddison, Penn World Table) {NOT_SET_UP}."])
    emit("step", "Long-run data: Maddison and Penn World Table")
    rows, notes, results = [], [], []
    try:
        args = {"source": "longrun", "series": "mpd.gdppc", "countries": [code], "start": 1950}
        result = lr.get("mpd.gdppc", [code], 1950)
        c = result["countries"][0]
        first, last = c["first"], c["last"]
        rows.append(["GDP per person (Maddison, 2011 international $)",
                     f"{first['value']:,.0f} ({first['year']}) → {last['value']:,.0f} ({last['year']})"])
        rows.append([f"Average growth per year, {first['year']}-{last['year']} (%)", f"{c['growth_per_year_pct']}"])
        log.facts += [{"indicator": "GDP per person, Maddison (2011 international $)",
                       "value": f"{first['value']:,.0f} in {first['year']}, {last['value']:,.0f} in {last['year']}"},
                      {"indicator": "Average growth of GDP per person per year (Maddison, %)",
                       "value": str(c["growth_per_year_pct"]), "years": f"{first['year']}-{last['year']}"}]
        results.append(("get_data", args, result))
        log.results.append(("get_data", args, result))
        log.sources["maddison"] = SOURCES["maddison"]
        notes.append("Maddison values are in 2011 international dollars: not comparable with the World Bank's "
                     "current-dollar or 2021 PPP figures above.")
    except Exception as e:
        notes.append(f"No Maddison data for {name} ({type(e).__name__}).")
    if "pwt.rgdpna" in lr.vars:
        try:
            ga = lr.growth_accounting(code)
            g = ga["growth_pct_per_year"]
            for label, key in (("Output per worker", "output_per_worker"),
                               ("  from more capital per worker", "capital_deepening_contribution"),
                               ("  from more schooling", "human_capital_contribution"),
                               ("  from productivity (TFP, the residual)", "tfp_contribution")):
                rows.append([f"{label}, {ga['period']} (% per year)", f"{g[key]}"])
            log.facts.append({"indicator": "Growth accounting, % per year (Penn World Table)", "years": ga["period"],
                              "value": ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in g.items())})
            log.results.append(("get_data", {"source": "longrun", "series": "pwt.growth_accounting",
                                             "countries": [code]}, ga))
            log.sources["pwt"] = SOURCES["pwt"]
            notes.append("Growth accounting (Penn World Table 11.0): growth of output per worker split into "
                         "capital, schooling and productivity; the parts add up. Productivity is what's left over, "
                         "so it also absorbs measurement error.")
        except Exception as e:
            notes.append(f"No Penn World Table growth accounting for {name} ({type(e).__name__}).")
    else:
        notes.append(f"Penn World Table (growth accounting) {NOT_SET_UP}.")
    table = {"columns": ["Measure", name], "rows": rows} if rows else None
    return section("Long-run growth", table, notes, results=results)


def _regions_table(regions: list[list], total: int, fmt_value=lambda v: f"{v}") -> tuple[list, str]:
    """Rows for the regions table; gdl.py already keeps only the top and bottom 6 (with a "..." row)."""
    rows = [[r[0], fmt_value(r[1])] for r in regions if isinstance(r[1], (int, float))]
    note = "" if len(rows) >= total else (f"Highest and lowest {len(rows) // 2} of {total} regions shown; "
                                          "the data download has them all.")
    return rows, note


def subnational(name: str, log, emit) -> dict:
    """Global Data Lab HDI by region; DHS under-5 mortality by region."""
    rows, notes = [], []
    g = load("gdl")
    if g is None:
        notes.append(f"Global Data Lab (HDI by region) {NOT_SET_UP}.")
    else:
        emit("step", "Global Data Lab: HDI by region")
        try:
            result = g.get("shdi", [name])
            c = result["countries"][0]
            rows += [["Human Development Index, national", f"{c['national']} ({c['year']})"],
                     ["  highest region", f"{c['highest']['region']}: {c['highest']['value']}"],
                     ["  lowest region", f"{c['lowest']['region']}: {c['lowest']['value']}"],
                     ["  number of regions", str(c["n_regions"])]]
            log.facts.append({"indicator": "Human Development Index by region (Global Data Lab)", "year": c["year"],
                              "value": f"national {c['national']}; highest {c['highest']['region']} "
                                       f"{c['highest']['value']}, lowest {c['lowest']['region']} "
                                       f"{c['lowest']['value']} ({c['n_regions']} regions)"})
            log.results.append(("get_data", {"source": "gdl", "series": "shdi", "countries": [name]}, result))
            log.sources["gdl"] = SOURCES["gdl"]
            notes.append("HDI by region: Global Data Lab estimates, modeled from household surveys; regions follow "
                         "survey boundaries.")
        except Exception as e:
            notes.append(f"No Global Data Lab data for {name} ({type(e).__name__}).")
    d = load("dhs")
    if d is None:
        notes.append(f"DHS surveys {NOT_SET_UP}.")
    else:
        emit("step", "DHS: under-5 mortality by region")
        try:
            result = d.get(DHS_U5M, [name], regions=True)
            r = result["countries"][0].get("regions")
            if not isinstance(r, dict):
                raise ValueError("no regional data")
            rows += [[f"Under-5 deaths per 1,000 births, {r['year']} DHS survey (national, ten years before it)",
                      str(r["national"])],
                     ["  highest region", f"{r['highest']['region']}: {r['highest']['value']}"],
                     ["  lowest region", f"{r['lowest']['region']}: {r['lowest']['value']}"],
                     ["  number of regions", str(r["n_regions"])]]
            log.facts.append({"indicator": "Under-5 deaths per 1,000 births by region (DHS survey)",
                              "year": r["year"],
                              "value": f"national {r['national']}; highest {r['highest']['region']} "
                                       f"{r['highest']['value']}, lowest {r['lowest']['region']} "
                                       f"{r['lowest']['value']} ({r['n_regions']} regions)"})
            log.results.append(("get_data", {"source": "dhs", "series": DHS_U5M, "countries": [name],
                                             "regions": True}, result))
            log.sources["dhs"] = SOURCES["dhs"]
            notes.append("DHS regional estimates rest on small samples: confidence intervals are wide, so a gap "
                         "between two regions can be noise."
                         + (f" Clearly above the national rate: {', '.join(r['clearly_above_national'])}."
                            if r.get("clearly_above_national") else ""))
        except Exception as e:
            notes.append(f"No DHS regional data for {name} ({type(e).__name__}: {str(e)[:80]}).")
    table = {"columns": ["Measure", "Value"], "rows": rows} if rows else None
    return section("Differences within the country", table, notes)


def researchers(e: dict) -> str:
    """J-PAL's researcher field, without the "Initiative(s): ..." text that follows it on some pages."""
    return re.split(r"\s*Initiative\(s\):", e.get("researchers", ""))[0].strip()


def _end_year(timeline: str) -> int:
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", timeline or "")]
    return max(years) if years else 0


def country_research(wb, code: str, name: str, log, emit) -> dict:
    """J-PAL evaluations in the country and the library papers that mention it most: a list, not findings."""
    items, notes = [], []
    jp = load("jpal")
    if jp is None:
        notes.append(f"J-PAL evaluations {NOT_SET_UP}.")
    else:
        emit("step", "J-PAL: evaluations in the country")
        here = [e for e in jp.evals if code in {_code(wb, c) for c in e["countries"]}]
        here.sort(key=lambda e: (bool(results_text(e)), _end_year(e.get("timeline"))),
                  reverse=True)
        for e in here[:COUNTRY_STUDIES]:
            items.append({"title": f"J-PAL: {e['title']}", "url": e["url"],
                          "text": "; ".join(x for x in (researchers(e), e.get("timeline", "")) if x)})
        if here:
            log.sources["jpal"] = SOURCES["jpal"]
        notes.append(f"J-PAL lists {len(here)} evaluation(s) naming {name}"
                     + (f"; the {min(len(here), COUNTRY_STUDIES)} most recent with results are shown." if here else "."))
    if not CHUNKS.exists():
        notes.append(f"Paper library {NOT_SET_UP}.")
    else:
        counts: dict[str, int] = {}
        pattern = re.compile(rf"\b{re.escape(name.split(',')[0])}\b")
        for chunk in json.loads(CHUNKS.read_text()):
            n = len(pattern.findall(chunk["text"]))
            if n:
                counts[chunk["source"]] = counts.get(chunk["source"], 0) + n
        top = [s for s, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= 3][:COUNTRY_PAPERS]
        for source in top:
            ref = paper_ref(source)
            items.append({"title": f"{ref['cite']}, \"{ref['title']}\"", "url": ref["url"],
                          "text": f"Library paper; names {name} {counts[source]} times."})
            log.sources[ref["source"]["key"]] = ref["source"]
        notes.append(f"Library papers: those that name {name} most often (at least 3 times) — a word count, not a "
                     "judgment of relevance." if top else f"No library paper names {name} 3 or more times.")
    return section("Research about the country", notes=notes, items=items)


_codes: dict[str, str | None] = {}


def _code(wb, country_name: str) -> str | None:
    """J-PAL's detected country names -> ISO3 (cached; None if unknown)."""
    if country_name not in _codes:
        try:
            _codes[country_name] = wb.country(country_name)
        except Exception:
            _codes[country_name] = None
    return _codes[country_name]


# ------------------------------------------------------------------ poverty profile
def poverty_profile(wb, country: str, emit=lambda *_: None, write: bool = True) -> dict:
    code = wb.country(country)
    if wb.countries[code]["aggregate"]:
        raise ValueError(f"{wb.countries[code]['name']} is a group of countries; a profile needs one country")
    name = wb.countries[code]["name"]
    log, sections = new_log(), []

    emit("step", "World Bank: poverty rates, poverty gap, Gini and population for every survey year")
    series = {}
    for ind in [i for i, _ in LINES] + ["SI.POV.GAPS", "SI.POV.GINI", "SP.POP.TOTL"]:
        data, _ = compute.fetch({"series": ind, "countries": [code], "start": 1960}, wb)
        series[ind] = data.get(code, {})
    surveys = sorted(series["SI.POV.DDAY"])
    if not surveys:
        raise ValueError(f"the World Bank has no poverty survey data for {name}")

    rows = []
    for year in surveys[-MAX_SURVEY_ROWS:]:
        rate, pop = series["SI.POV.DDAY"][year], series["SP.POP.TOTL"].get(year)
        cells = [str(year)] + [f"{series[i][year]:.1f}" if year in series[i] else "–"
                               for i in ("SI.POV.DDAY", "SI.POV.LMIC", "SI.POV.UMIC", "SI.POV.GAPS", "SI.POV.GINI")]
        cells.append(people(rate * pop / 100) if pop else "–")
        rows.append(cells)
    latest = surveys[-1]
    log.facts.append({"indicator": "Poverty by survey year (% below $3.00 / $4.20 / $8.30; poverty gap at $3.00; "
                                   "Gini; people below $3.00)",
                      "value": "; ".join(f"{r[0]}: {r[1]} / {r[2]} / {r[3]}, gap {r[4]}, Gini {r[5]}, {r[6]}"
                                         for r in rows[-6:])})
    notes = ["Poverty rates and the Gini index come from household surveys and exist only for survey years. "
             "Lines are in 2021 PPP dollars a day; $3.00 is the international extreme poverty line.",
             "People below $3.00 = the rate times the World Bank's population estimate for that year, computed here."]
    if len(surveys) > MAX_SURVEY_ROWS:
        notes.append(f"The latest {MAX_SURVEY_ROWS} of {len(surveys)} survey years are shown; the data download has "
                     "them all.")
    chart = {"indicator": f"poverty_lines_{code}", "name": f"Share of people below each poverty line, {name}",
             "economies": [{"economy": f"{label} a day", "latest": {"year": latest, "value": series[i].get(latest)},
                            "series [year, value]": [[y, round(v, 2)] for y, v in sorted(series[i].items())]}
                           for i, label in LINES if series[i]]}
    sections.append(section("Poverty by survey year", {"columns": [
        "Survey year", "% below $3.00", "% below $4.20", "% below $8.30", "Poverty gap at $3.00 (%)", "Gini",
        "People below $3.00"], "rows": rows}, notes, results=[("get_data", {}, chart)]))

    # survey timing
    this_year = datetime.date.today().year
    gaps = [(b - a, a, b) for a, b in zip(surveys, surveys[1:])]
    timing = [["Survey years with a poverty rate", str(len(surveys))],
              ["First and latest", f"{surveys[0]} and {latest}"],
              ["Years since the latest survey", str(this_year - latest)]]
    fact = {"indicator": "Survey timing", "value": f"{len(surveys)} survey years, first {surveys[0]}, latest "
                                                   f"{latest}, {this_year - latest} years before {this_year}"}
    if gaps:
        longest = max(gaps)
        average = (surveys[-1] - surveys[0]) / len(gaps)
        timing += [["Longest gap between surveys", f"{longest[0]} years ({longest[1]}-{longest[2]})"],
                   ["Average gap between surveys", f"{average:.1f} years"]]
        fact["value"] += f"; longest gap {longest[0]} years ({longest[1]}-{longest[2]}), average gap {average:.1f} years"
    log.facts.append(fact)
    sections.append(section("Survey timing", {"columns": ["Measure", "Value"], "rows": timing},
                            ["Between surveys there is no measured poverty rate; the World Bank's regional and "
                             "global figures fill gaps with estimates, but country rates here are survey years only."]))

    peer_ids = [i for i, _ in LINES] + ["SI.POV.GAPS", "SI.POV.GINI"]
    peer_rows_, _ = peer_rows(wb, code, peer_ids, 1960, log, emit)  # from 1960: the data zip gets every survey
    if peer_rows_:
        sections.append(section("Compared with the income group and region",
                                {"columns": peer_columns(wb, code), "rows": peer_rows_}, peer_notes(peer_ids)))
    try:  # the population behind "people below $3.00", for the data zip
        args = {"series": "SP.POP.TOTL", "countries": [code], "start": 1960}
        log.results.append(("get_data", args, wb.get("SP.POP.TOTL", [code], 1960)))
        log.used.append("SP.POP.TOTL")
    except Exception:
        pass

    sections.append(income_by_region(name, log, emit))
    draw(sections, wb, emit)
    facts = {"country": name, "rows": log.facts}
    summary = summarize(facts, POVERTY_PROMPT, write, listed_summary, emit)
    about = ["Poverty data: the World Bank's Poverty and Inequality Platform, as published in the World Development "
             "Indicators, fetched when the report was made.",
             "Number of poor, survey gaps and comparisons are computed here from those figures."]
    emit("step", "Done")
    return finish("poverty", f"{name}: poverty profile", {"country": name}, summary, sections, about, log, facts)


def people(n: float) -> str:
    return f"{n / 1e6:,.1f} million" if n >= 1e6 else f"{n / 1e3:,.0f} thousand"


def income_by_region(name: str, log, emit) -> dict:
    heading = "Income by region"
    g = load("gdl")
    if g is None:
        return section(heading, notes=[f"Global Data Lab (income by region) {NOT_SET_UP}."])
    emit("step", "Global Data Lab: income per person by region")
    try:
        result = g.get("gnic", [name])
        c = result["countries"][0]
        key = next(k for k in c if k.startswith("regions ["))
        rows, shown = _regions_table(c[key], c["n_regions"], lambda v: f"{v:,.0f}")
    except Exception as e:
        return section(heading, notes=[f"No Global Data Lab data for {name} ({type(e).__name__})."])
    log.results.append(("get_data", {"source": "gdl", "series": "gnic", "countries": [name]}, result))
    log.sources["gdl"] = SOURCES["gdl"]
    log.facts.append({"indicator": "Income per person by region (GNI per capita, 2021 PPP $, Global Data Lab; not a "
                                   "poverty rate)", "year": c["year"],
                      "value": f"national {c['national']:,.0f}; highest {c['highest']['region']} "
                               f"{c['highest']['value']:,.0f}, lowest {c['lowest']['region']} "
                               f"{c['lowest']['value']:,.0f}; highest is {c['ratio_max_to_min']} times the lowest"})
    notes = [f"This is income per person by region ({c['year']}), not a poverty rate: no free source used here gives "
             "poverty rates by region. Global Data Lab estimates, modeled from household surveys; regions follow "
             "survey boundaries.", f"National: {c['national']:,.0f}; the highest region has {c['ratio_max_to_min']} "
             "times the income of the lowest."] + ([shown] if shown else [])
    return section(heading, {"columns": ["Region", f"GNI per person, {c['year']} (2021 PPP $)"], "rows": rows}, notes)


# ------------------------------------------------------------------ what works
JOINED = {"Behavior", "Official", "Performance", "Protection", "Transfers", "Analysis"}
_SPLIT = re.compile(r"(?<!\band)(?<!\bof)(?<!\bthe) (?=[A-Z])")


def outcome_labels(text: str) -> list[str]:
    """J-PAL's outcome field is its labels run together ("Earnings and income Employment"):
    split before capitals, rejoining labels that have capitals inside ("Voter Behavior")."""
    out = []
    for part in _SPLIT.split((text or "").strip()):
        if out and part in JOINED:
            out[-1] += " " + part
        elif part:
            out.append(part)
    return out


def _region_codes(wb, region: str) -> tuple[set[str], str]:
    """A World Bank region (by any part of its name) or one country -> the ISO3 codes it covers."""
    low = region.strip().lower()
    regions = {m["region"] for m in wb.countries.values() if not m["aggregate"] and m["region"]}
    hits = [r for r in regions if low in r.lower()]
    if hits:
        return ({c for c, m in wb.countries.items() if m["region"] in hits and not m["aggregate"]}, " / ".join(sorted(hits)))
    code = wb.country(region)
    return {code}, wb.countries[code]["name"]


def what_works(wb, intervention: str, region: str = "", outcome: str = "", emit=lambda *_: None,
               write: bool = True, library=None) -> dict:
    """library: library_search(...)'s function, or None (then that section is off)."""
    intervention, region, outcome = intervention.strip(), (region or "").strip(), (outcome or "").strip()
    if not intervention:
        raise ValueError("name an intervention, e.g. cash transfers")
    log, sections = new_log(), []
    allowed, region_name = _region_codes(wb, region) if region else (None, "")
    query = f"{intervention} {outcome}".strip()
    studies, found = [], 0

    jp = load("jpal")
    if jp is None:
        sections.append(section("Randomized evaluations (J-PAL)", notes=[f"J-PAL evaluations {NOT_SET_UP}."]))
    else:
        emit("step", "J-PAL: searching 1,300 randomized evaluations")
        scores = jp._bm25.scores(query)
        best = max(scores) if len(scores) else 0
        ranked = [i for i in sorted(range(len(jp.evals)), key=lambda i: -scores[i])
                  if best > 0 and scores[i] >= RELEVANCE_FLOOR * best]
        out_words = [w for w in re.findall(r"[a-z]{4,}", outcome.lower())]
        for i in ranked:
            e = jp.evals[i]
            codes = {c for c in (_code(wb, n) for n in e["countries"]) if c}
            if allowed is not None and not codes & allowed:
                continue
            if out_words and not any(w in (e.get("outcome_of_interest", "") + " " + e["title"]).lower()
                                     for w in out_words):
                continue
            found += 1
            if len(studies) < MAX_STUDIES:
                studies.append({"e": e, "outcomes": outcome_labels(e.get("outcome_of_interest", "")) or ["Not stated"],
                                "regions": sorted({wb.countries[c]["region"] for c in codes}) or ["Not stated"]})
        log.results.append(("search_evaluations", {"query": query, "region": region, "outcome": outcome},
                            {"evaluations": [{"title": s["e"]["title"], "url": s["e"]["url"],
                                              "countries": s["e"]["countries"], "outcomes": s["outcomes"],
                                              "results": results_text(s["e"])}
                                             for s in studies]}))
        sections += evidence_sections(studies, found, query, region_name, outcome)
        if studies:
            log.sources["jpal"] = SOURCES["jpal"]

    reviews = []
    emit("step", "OpenAlex: reviews and meta-analyses (1 search)")
    try:
        reviews = openalex.search(f"{intervention} {outcome} systematic review meta-analysis".replace("  ", " "),
                                  n=MAX_REVIEWS)
        log.results.append(("search_literature", {"query": intervention}, {"results": reviews}))
    except Exception as e:
        sections.append(section("Reviews and meta-analyses (OpenAlex)",
                                notes=[f"OpenAlex couldn't be reached ({type(e).__name__})."]))
    if reviews:
        rows = [[w["cite_as"], w["title"], w.get("venue") or "–", str(w.get("cited_by") or 0)] for w in reviews]
        sections.append(section("Reviews and meta-analyses (OpenAlex)",
                                {"columns": ["Citation", "Title", "Published in", "Cited by"], "rows": rows,
                                 "align": ["l", "l", "l", "r"]},
                                ["Found by one OpenAlex search for reviews and meta-analyses; ranked by relevance and "
                                 "citations. Not every result is a formal systematic review: check the titles."]))
        for w in reviews:
            key = "oa_" + workflows.slug(w["cite_as"] + " " + w["title"][:20]).lower()
            log.sources[key] = {"key": key, "type": "article", "author": w["authors"].replace(", ", " and "),
                                "title": w["title"], "year": str(w.get("year") or ""), "journal": w.get("venue") or "",
                                "url": w.get("doi") or ""}

    passages = []
    if library is None:
        sections.append(section("From the paper library", notes=[f"Paper library {NOT_SET_UP}, or not loaded."]))
    else:
        emit("step", "Searching the paper library")
        try:
            passages = library(query, MAX_PASSAGES)
        except Exception as e:
            sections.append(section("From the paper library", notes=[f"Library search failed ({type(e).__name__})."]))
        if passages:
            items = []
            for p in passages:
                ref = paper_ref(p["source"])
                p["ref"] = ref
                items.append({"title": f"{ref['cite']}, \"{ref['title']}\"", "url": ref["url"],
                              "text": "“" + words(p["text"], 80) + "”"})
                log.sources[ref["source"]["key"]] = ref["source"]
            sections.append(section("From the paper library", items=items,
                                    notes=["The passages that best match the intervention, quoted from your library."]))

    facts = works_facts(intervention, region_name, outcome, studies, found, reviews, passages)
    summary = summarize(facts, WORKS_PROMPT, write, works_summary, emit, check=citations_outside)
    where = f" in {region_name}" if region_name else ""
    title = f"What works: {intervention}" + (f" ({outcome})" if outcome else "") + where
    about = ["J-PAL evaluations: randomized evaluations by J-PAL affiliates, matched by keyword search over their "
             "summaries; results are J-PAL's own words, quoted, not judged or averaged here.",
             "Studies that disagree are listed side by side: read each result in its own setting (country, years, "
             "sample).",
             "The summary may cite only the evaluations, reviews and papers in this report; every citation and number "
             "is checked automatically (the wording is not)."]
    emit("step", "Done")
    return finish("works", title, {"intervention": intervention, "region": region, "outcome": outcome}, summary,
                  sections, about, log, facts)


def evidence_sections(studies: list[dict], found: int, query: str, region_name: str, outcome: str) -> list[dict]:
    if not studies:
        return [section("Randomized evaluations (J-PAL)", notes=[
            f"No J-PAL evaluation matches “{query}”" + (f" in {region_name}" if region_name else "") + "."])]
    by_outcome: dict[str, list] = {}
    for s in studies:
        for o in s["outcomes"]:
            by_outcome.setdefault(o, []).append(s)
    glance = []
    for o, group in sorted(by_outcome.items(), key=lambda kv: -len(kv[1])):
        regions: dict[str, int] = {}
        for s in group:
            for r in s["regions"]:
                regions[r] = regions.get(r, 0) + 1
        glance.append([o, str(len(group)), str(sum(1 for s in group if _has_results(s))),
                       ", ".join(f"{r} {n}" for r, n in sorted(regions.items(), key=lambda kv: -kv[1]))])
    singles = [row[0] for row in glance if row[1] == "1"]
    if len(glance) > 10 and len(singles) > 1:  # a long tail of one-study outcomes: one row
        glance = [row for row in glance if row[1] != "1"] + [
            [f"Other outcomes, one study each: {', '.join(singles)}", "–", "–", "–"]]
    by_region: dict[str, list] = {}
    for s in studies:
        for r in s["regions"]:
            by_region.setdefault(r, []).append(s)
    region_rows = [[r, str(len(g)), ", ".join(sorted({c for s in g for c in s["e"]["countries"]})) or "–"]
                   for r, g in sorted(by_region.items(), key=lambda kv: -len(kv[1]))]
    items = []
    for s in studies:
        e = s["e"]
        result = results_text(e)
        text = "; ".join(x for x in (researchers(e), ", ".join(e["countries"]), e.get("timeline", "")) if x)
        text += f". Outcomes: {', '.join(s['outcomes'])}. "
        text += ("Results (J-PAL's summary, quoted): “" + words(result, EXCERPT_WORDS) + "”") if result else \
            "No results published yet."
        items.append({"title": f"J-PAL: {e['title']}", "url": e["url"], "text": text})
    shown = f"the {len(studies)} most relevant are listed" if found > len(studies) else "all are listed"
    notes = [f"{found} J-PAL evaluation(s) match “{query}”" + (f" in {region_name}" if region_name else "")
             + f"; {shown}. A study counts once per outcome it measured, so the outcome counts can add up to more "
               "than the number of studies.",
             "Regions are the World Bank regions of the countries named in each study's summary."]
    return [section("Evidence at a glance (J-PAL)", {"columns": ["Outcome", "Studies", "With results", "Regions"],
                                                     "rows": glance, "align": ["l", "r", "r", "l"]}, notes),
            section("Where the studies were done", {"columns": ["Region", "Studies", "Countries"], "rows": region_rows,
                                                    "align": ["l", "r", "l"]}),
            section("The studies", items=items, notes=[
                "Results are J-PAL's own summaries, cut after a few sentences; follow the link for the full study. "
                "Where studies disagree, they are not reconciled here."])]


def _has_results(s: dict) -> bool:
    return bool(results_text(s["e"]))


def works_facts(intervention, region_name, outcome, studies, found, reviews, passages) -> dict:
    counts = {}
    for s in studies:
        for o in s["outcomes"]:
            counts[o] = counts.get(o, 0) + 1
    with_results = [s for s in studies if _has_results(s)]
    return {
        "intervention": intervention, "region": region_name or "all regions", "outcome": outcome or "any",
        "evaluations_found": found, "evaluations_listed": len(studies), "listed_with_results": len(with_results),
        "studies_by_outcome": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8]),
        "studies": [{"cite_as": f"J-PAL: {s['e']['title']}", "countries": s["e"]["countries"],
                     "outcomes": s["outcomes"], "results_excerpt": words(results_text(s["e"]),
                                                                          EXCERPT_WORDS)}
                    for s in with_results[:FACT_STUDIES]],
        "reviews": [{"cite_as": w["cite_as"], "title": w["title"], "abstract": words(w.get("abstract") or "", 80)}
                    for w in reviews],
        "library": [{"cite_as": p["ref"]["cite"], "title": p["ref"]["title"], "passage": words(p["text"], 100)}
                    for p in passages if "ref" in p],
    }


_JPAL_CITE = re.compile(r"J-PAL:\s*([^)\n]+)")


def citations_outside(text: str, facts_text: str) -> list[str]:
    """Citations in the summary that aren't in the fact sheet: author-year ones
    (verify.py's check) and "(J-PAL: <title>)" ones (the title must be there)."""
    bad = verify.unsupported_citations(text, facts_text, set())
    for m in _JPAL_CITE.finditer(text):
        for title in m.group(1).split("; J-PAL:"):
            title = title.strip().rstrip(".,;")
            if title and title not in facts_text:
                bad.append(f"J-PAL: {title}")
    return list(dict.fromkeys(bad))


def works_summary(facts: dict, reason: str = "") -> dict:
    """The free summary: how much evidence, by outcome, and which reviews — counts only, no findings."""
    where = f" in {facts['region']}" if facts["region"] != "all regions" else ""
    lines = [f"- **{facts['evaluations_found']} J-PAL evaluation(s)** match{where}; "
             f"{facts['evaluations_listed']} listed below, {facts['listed_with_results']} of them with published "
             "results."]
    lines += [f"- **{o}**: {n} of the listed studies." for o, n in facts["studies_by_outcome"].items()]
    if facts["reviews"]:
        lines.append("- **Reviews found (OpenAlex)**: " + "; ".join(f"{w['cite_as']}, “{w['title']}”"
                                                                     for w in facts["reviews"]) + ".")
    if facts["library"]:
        lines.append("- **From your library**: " + "; ".join(dict.fromkeys(p["cite_as"] for p in facts["library"]))
                     + ".")
    return {"text": "\n".join(lines), "status": "python", "reason": reason, "unverified": [], "tokens": 0}
