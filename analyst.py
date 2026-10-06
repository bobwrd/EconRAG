"""
The online answer path: Groq's model as a tool-using analyst. The model
decides which tools to call; Python runs them and computes every number; the
model writes the answer from the results. Tools:

  search_papers       the local PDF library (hybrid retrieval + Laya rerank)
  search_literature   published research beyond the library (OpenAlex): real
                      papers with abstracts
  county_profile      Opportunity Atlas outcomes + characteristics for a county
  rank_counties       top/bottom counties on any Atlas metric
  correlate_counties  how a county characteristic tracks an outcome
  search_data         find series ids: World Bank WDI (default) or FRED
  get_data            computed stats for a series: World Bank (any economies,
                      or all of them ranked) or FRED (any date range)

Every answer is checked by verify.py (numbers and citations must trace to a
tool result); the model gets one chance to fix what fails, and anything still
unverified is flagged to the user.

Raises GroqUnavailable (from groq_client) if Groq can't be used, so ask.py can
fall back to the local pipeline.
"""

import datetime
import json
from pathlib import Path
import time

import fred
import openalex
import verify
import charts
import compute
import dhs
import gdl
import jpal
import longrun
import worldbank
from atlas import CORRELATE_METRICS, GENDERS, METRICS, RACES, Atlas
from groq_client import GroqUnavailable
from groq_client import post as groq_post

MAX_ROUNDS = 6       # tool-calling rounds per question before forcing an answer
HISTORY_TURNS = 2    # past question/answer pairs kept for follow-ups ("and for Black children?")
# Groq's free tier allows 8,000 tokens/minute (all models), and every tool
# round re-sends the whole conversation, so a question that searches papers
# can hit it. Waiting out a short limit beats falling back to phi3.5.
MAX_RATE_LIMIT_WAIT = 60  # seconds
# A single request over 8,000 tokens can never succeed (HTTP 413), so older
# tool results are trimmed to stay under this estimate (~3.2 chars/token for
# JSON-heavy text, conservative).
REQUEST_TOKEN_LIMIT = 7000
CHARS_PER_TOKEN = 3.2

SYSTEM_PROMPT = """You are an economics research analyst specializing in development economics worldwide and in economic opportunity in the United States, for a general audience. Today is {today}.

Answer from your tools, not from memory:
- search_papers: the user's research library (mostly Opportunity Insights research: intergenerational mobility, neighborhoods, credit access, migration, the Economic Tracker) — full-text passages. Use it for research findings, mechanisms, and "why" questions on those topics.
- search_literature: published research beyond the library (OpenAlex, ~250M papers): real papers with authors, year, venue, citation count, and abstract. Use it for "what does research say" / "what works" questions the library doesn't cover — most development topics (cash transfers, microfinance, deworming, ...). Search with the topic's standard terms; you only see abstracts, so claim no more than an abstract states.
- search_evaluations: J-PAL's summaries of ~1,100 randomized evaluations (mostly in developing countries) with their results. Use it with search_literature for "what works" / "does X work" questions; say where and when each study ran.
- run_python: calculations the other tools don't do (growth needed to reach a target, projections, regressions, population-weighted averages, convergence). List the data in `data`; the script gets DATA[series][ISO3] = {{year: value}} (FRED under "US"), NAMES, devecon (cagr, doubling_time, weighted_mean, gini, fgt, beta_convergence, growth_decomposition), np, scipy.stats. print() every number you will cite, with a label.
- county_profile / rank_counties / correlate_counties: the Opportunity Atlas (every US county). Use them for questions about specific places, comparisons between places, or which local characteristics go with mobility. For a city, use its main county (Chicago -> Cook County, IL) and say so.
- search_data / get_data: source "worldbank" (default) = World Development Indicators, ~1,500 indicators for ~217 economies plus regions and income groups (e.g. "Sub-Saharan Africa", "Low income") — growth, poverty, inequality, health, education, labor, trade — plus World Bank GDP growth FORECASTS (search "growth forecast"). Present forecasts as forecasts, with the edition date the result gives. Use countries ["all"] to rank every economy. Source "fred" = US and high-frequency series (monthly unemployment, CPI); for inflation from a price index use units "pc1": the raw index is a level, not a rate. Source "longrun" = history before WDI: Maddison GDP per capita back to year 1 (mpd.gdppc, 2011 int$) and Penn World Table output, capital, schooling, TFP since 1950; 2+ countries also returns ratios, overtaking and divergence years; series pwt.growth_accounting splits growth per worker into capital, schooling and TFP. Source "dhs" = Demographic and Health Surveys, ~90 developing countries, one value per survey every ~5 years (child mortality, stunting, fertility, contraception, maternal care, vaccination, schooling, water, HIV, women's empowerment); regions true for subnational values; always state the survey year. Source "gdl" = Global Data Lab subnational HDI, life expectancy, schooling, GNI per capita (2021 PPP) for 1,805 regions in 188 countries, 1990-2023: every region of a country, or ["all"] to rank regions worldwide; metrics take f/m for female/male (lifexpf). Search first unless you know the exact id.

Economics conventions:
- "Employment rate" means the employment-population ratio (FRED EMRATIO), NOT 100 minus the unemployment rate (the unemployed are only those looking for work). Labor force participation is CIVPART.
- Inflation is the % change of a price index: CPIAUCSL (core: CPILFESL) with units "pc1". Say "percentage points" for differences between rates.
- Recession periods: FRED series USREC (1 = recession month). Compare dollar amounts across years in real (inflation-adjusted) terms where the series allows.
- Poverty and inequality figures come from household surveys: give the survey year with every value, and never present an old survey as "today". The international (extreme) poverty line is $3.00/day in 2021 PPP (SI.POV.DDAY) — older sources used $2.15 (2017 PPP) or $1.90; figures on different lines aren't comparable.
- GDP per capita: say whether it's at market exchange rates (current US$) or PPP (international $). If the question doesn't specify and the answer depends on it, give both. Growth over time: constant-price (.KD) series, not current-price (.CD).
- Every value has its own latest year, which can differ across countries: always state the year.
- Comparisons with peers ("vs similar countries", "vs Sub-Saharan Africa"): get_data with peers true.
- Don't present a county's racial composition as an explanation for its outcomes. The research attributes racial gaps to factors like segregation, discrimination, and neighborhood conditions: report what the papers say.

Rules:
1. Every number and every factual claim about a place or trend must come from a tool result in this conversation — if you didn't fetch it, don't assert it. For correlations, use the tool's "interpretation" for the direction. You may do simple arithmetic on those numbers (differences, ratios); show it. Your answer is automatically checked: numbers not found in tool results are flagged to the user.
2. For "why" or "what explains" questions about places, combine the county data with search_papers. Build the local part ONLY from county_profile's characteristics_consistent_with_gap (computed: differs notably from average AND correlates with mobility). A characteristic marked "about average" cannot explain a gap — mention it only to rule it out; also say which characteristics point the other way. Say "is consistent with" / "is associated with", never "because", "due to", or "largely explains" — unless a search_papers passage makes that causal claim, then attribute it to the paper.
3. Mention a paper or research finding ONLY if it appeared in a search_papers, search_literature, or search_evaluations result in this conversation; if you didn't search, don't cite research. Cite inline: for library passages, the author-year their label gives, e.g. (Chetty, Hendren, Kline, Saez and Turner (2014)) or (Chetty et al. (2014)), the result's cite_as form, e.g. (Banerjee et al. (2015)), for search_literature, (J-PAL: <title>) for search_evaluations, (Opportunity Atlas) for county data, (World Bank: <indicator id>, <year>) and (FRED: <series id>, <date>) for data. Never invent paper titles, authors, years, table numbers, or figure numbers — citations are automatically checked against the passages and against OpenAlex.
4. Earlier turns are context for follow-ups ("there", "what about...", "these results"): build on your previous answer — explain, interpret, or extend it; don't repeat its table or re-fetch data it already gave. A self-contained question is about the US unless it names a place. If a county lookup is ambiguous and the question doesn't make the right one obvious, ask the user which one they mean.
5. If the tools don't contain the answer, say so plainly instead of filling the gap.
6. Call independent tools together in the same turn rather than one per turn. Don't repeat a search with near-identical wording.
7. To compare episodes (e.g. two recessions), call get_data once per episode's date range: max and min are exact only for the range requested; sampled points can miss peaks.
8. The first time you use the Opportunity Atlas mobility measure, explain it plainly, e.g. "children from low-income families (parents at the 25th income percentile) who grew up in Cook County reached, on average, the 38.5th percentile of household income as adults" — these are children born 1978-83, with incomes measured in 2014-15.
9. A yes/no, above/below, or more/less question gets an explicit answer in the first sentence ("Below: ..."; if it differs by measure, e.g. market rates vs PPP, give the answer for each).
10. Answer first, in plain prose: the explanation, the mechanisms, what research finds. Data supports the answer: cite the 2-4 figures that matter, inline. Use a table only when the user asks for a list or ranking, or many numbers are the point. Be concise — under 250 words. Output is shown in a terminal: plain text and simple markdown only."""

# Kept terse: the schemas are re-sent with every request (see MAX_RATE_LIMIT_WAIT).
# Metric descriptions appear once, in rank_counties; results carry them too.
_race = {"type": "string", "enum": list(RACES)}
_gender = {"type": "string", "enum": list(GENDERS)}
_state = {"type": "string", "description": "2-letter abbreviation; omit for all US"}
_source = {"type": "string", "enum": ["worldbank", "fred", "longrun", "dhs", "gdl"], "description": "default worldbank"}
_metric_doc = ("upward_mobility: avg adult income percentile of kids from 25th-percentile families; "
               "incarceration: % of them incarcerated in 2010. race/gender apply to these two only. "
               "Other metrics are county characteristics, mostly ~2010.")


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = [
    _tool("search_papers", "Search the user's research paper library; returns the most relevant "
          "passages labeled with source file.", {"query": {"type": "string"}}, ["query"]),
    _tool("search_literature", "Search published research (OpenAlex): real papers with abstracts.",
          {"query": {"type": "string"}, "from_year": {"type": "integer", "description": "optional"}}, ["query"]),
    _tool("search_evaluations", "Search J-PAL randomized evaluations.",
          {"query": {"type": "string"}}, ["query"]),
    _tool("run_python", "Run Python on fetched data (sandboxed).",
          {"code": {"type": "string"},
           "data": {"type": "array", "items": {"type": "object", "properties": {
               "source": {"type": "string", "enum": list(compute.SOURCES)},
               "series": {"type": "string"}, "countries": {"type": "array", "items": {"type": "string"}},
               "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["series"]}}},
          ["code"]),
    _tool("county_profile", "Opportunity Atlas profile of one US county: upward mobility and "
          "incarceration for kids from low-income families (overall/by race/by gender) vs state and "
          "national averages, plus county characteristics.",
          {"county": {"type": "string", "description": "e.g. 'Cook'"}, "state": _state,
           "demographics": {"type": "boolean", "description": "include racial composition; only when "
                            "the user asks about demographics"}}, ["county"]),
    _tool("rank_counties", "Top or bottom US counties on a metric. " + _metric_doc,
          {"metric": {"type": "string", "enum": METRICS}, "state": _state, "race": _race, "gender": _gender,
           "order": {"type": "string", "enum": ["highest", "lowest"]},
           "n": {"type": "integer", "description": "default 10, max 25"},
           "min_children": {"type": "integer", "description": "skip smaller (noisy) counties; default 1000"}},
          ["metric"]),
    _tool("correlate_counties", "Weighted correlation of two metrics across counties, plus avg y in each "
          "fifth of x. Metrics as in rank_counties.",
          {"x_metric": {"type": "string", "enum": CORRELATE_METRICS},
           "y_metric": {"type": "string", "enum": CORRELATE_METRICS},
           "state": _state, "race": _race, "gender": _gender}, ["x_metric", "y_metric"]),
    _tool("search_data", "Find series ids by description.",
          {"query": {"type": "string"}, "source": _source}, ["query"]),
    _tool("get_data", "Computed stats for one series: latest, first, exact max/min with years/dates, "
          "sampled points, sparkline, definition and caveats.",
          {"series": {"type": "string", "description": "id from search_data, e.g. SI.POV.DDAY or UNRATE"},
           "source": _source,
           "countries": {"type": "array", "items": {"type": "string"},
                         "description": "worldbank/longrun: names, ISO3 codes, or regions (max 20); "
                                        "[\"all\"] ranks every economy"},
           "start": {"type": "string", "description": "worldbank/longrun: year; fred: YYYY-MM-DD"},
           "end": {"type": "string", "description": "worldbank/longrun: year; fred: YYYY-MM-DD"},
           "units": {"type": "string", "enum": list(fred.UNITS),
                     "description": "fred only. lin=level, pc1/pch=% change vs year ago/previous period"},
           "order": {"type": "string", "enum": ["highest", "lowest"], "description": "with [\"all\"]"},
           "n": {"type": "integer", "description": "with [\"all\"]: how many, default 10"},
           "regions": {"type": "boolean", "description": "dhs: values by region within the (first) country"},
           "peers": {"type": "boolean", "description": "worldbank: add income-group and region "
                     "figures and rank within each"}},
          ["series"]),
]


def _summary(name: str, result: dict) -> str:
    """One line per tool call, so the user sees what the model looked up."""
    if "error" in result:
        return f"error: {result['error']}"
    if name == "search_literature":
        return "; ".join(w["cite_as"] for w in result["papers"]) or "no papers found"
    if name == "run_python":
        out = (result.get("output") or "").strip().splitlines()
        return ("; ".join(out[:3]) + (" ..." if len(out) > 3 else "")) or result.get("note", "")
    if name == "search_evaluations":
        return "; ".join(f"{e['title']} ({', '.join(e['countries']) or '?'})" for e in result["evaluations"]) \
            or "no evaluations found"
    if name == "search_papers":
        return "passages from " + ", ".join(dict.fromkeys(result["sources"]))
    if name == "county_profile":
        if "ambiguous" in result:
            return f"ambiguous: {len(result['matches'])} matches"
        outcomes = next(v for k, v in result.items() if k.startswith("outcomes"))
        return f"{result['county']}: upward mobility {outcomes['upward_mobility'][0]}"
    if name == "rank_counties":
        top = result["counties"][0] if result["counties"] else None
        return f"{len(result['counties'])} counties" + (f", #1 {top['county']} ({top['value']})" if top else "")
    if name == "correlate_counties":
        return result["interpretation"]
    if name == "search_data":
        return ", ".join(s.get("id") or s.get("series_id") for s in result["results"][:5]) or "no matches"
    if name == "get_data" and "series_id" in result:  # FRED
        return (f"{result['series_id']} {result['first']['date']}..{result['latest']['date']}  "
                f"{result['sparkline']}  latest {result['latest']['value']}")
    if name == "get_data" and str(result.get("source", "")).startswith("Global Data Lab"):
        if "ranked" in result:
            top = result["ranked"][0] if result["ranked"] else None
            return f"{result['metric']}: {len(result['ranked'])} of {result['regions_with_data']} regions" + \
                (f", #1 {top['region']}, {top['country']} ({top['value']}, {top['year']})" if top else "")
        parts = [f"{c['country']} {c.get('national')} ({c.get('year')}), regions {c['lowest']['value']}-"
                 f"{c['highest']['value']}" if "highest" in c else f"{c['country']}: no regional data"
                 for c in result["countries"]]
        return f"{result['metric']}: " + "; ".join(parts)
    if name == "get_data" and "ranked" in result:
        top = result["ranked"][0] if result["ranked"] else None
        return f"{result['indicator']}: {len(result['ranked'])} of {result['economies_with_data']} economies" + \
            (f", #1 {top['economy']} ({top['value']}, {top['year']})" if top else "")
    if name == "get_data" and str(result.get("source", "")).startswith("DHS"):
        parts = [f"{c['country']} {c['latest']['value']} ({c['latest']['year']})" if "latest" in c
                 else f"{c['country']}: no survey" for c in result["countries"][:4]]
        return f"{result['indicator']}: " + "; ".join(parts)
    if name == "get_data" and "countries" in result:  # longrun
        parts = [f"{c['country']} {c['first']['year']} {c['first']['value']} -> {c['last']['year']} {c['last']['value']}"
                 if "last" in c else f"{c['country']}: no data" for c in result["countries"][:4]]
        return f"{result['variable']}: " + "; ".join(parts)
    if name == "get_data" and "capital_share_alpha" in result:  # longrun growth accounting
        g = result["growth_pct_per_year"]
        return (f"{result['country']} {result['period']}: output/worker {g['output_per_worker']}%/yr = capital "
                f"{g['capital_deepening_contribution']} + schooling {g['human_capital_contribution']} + TFP {g['tfp_contribution']}")
    if name == "get_data":
        parts = [f"{e['economy']} {e['latest']['value']} ({e['latest']['year']}) {e.get('sparkline', '')}".strip()
                 if "latest" in e else f"{e['economy']}: no data" for e in result["economies"][:4]]
        return f"{result['indicator']}: " + "; ".join(parts) + (" ..." if len(result["economies"]) > 4 else "")
    return ""


# author-year + title per PDF in docs/ (docs/ itself is gitignored); used by ask.py
PAPERS = json.loads(Path("papers.json").read_text()) if Path("papers.json").exists() else {}


def paper_label(source: str) -> str:
    """How a library passage is labeled for the model: author-year and title
    from papers.json (so citations are real references), plus the file name."""
    meta = PAPERS.get(source)
    return f'{meta["cite"]}, "{meta["title"]}"; file {source}' if meta else source


DHS_REGIONS_SHOWN = 6  # top and bottom; Kenya alone has 47 counties (~2.7K chars)


def _trim_regions(result: dict) -> dict:
    """Keeps a DHS regional breakdown small enough for the 8K-token request limit:
    the highest and lowest regions; spread, median and national value are already computed."""
    for c in result.get("countries", []):
        regions = c.get("regions")
        if not isinstance(regions, dict):
            continue
        key = next((k for k in regions if k.startswith("regions [")), None)
        if key and len(regions[key]) > 2 * DHS_REGIONS_SHOWN:
            rows = regions[key]
            regions[key] = rows[:DHS_REGIONS_SHOWN] + [["...", f"{len(rows) - 2 * DHS_REGIONS_SHOWN} more"]] \
                + rows[-DHS_REGIONS_SHOWN:]
    return result


FRED_NEWER_WINDOW = datetime.timedelta(days=3 * 365)


def _with_newest_fred(stats: dict, args: dict) -> dict:
    """If the model passed a recent end date (assuming it's the newest data,
    as it did with World Bank years), attach the newest observation: one extra
    request, skipped for older end dates (deliberate history, e.g. 2020)."""
    try:
        end = datetime.date.fromisoformat(str(args.get("end"))[:10])
    except ValueError:
        return stats
    if end < datetime.date.today() - FRED_NEWER_WINDOW:
        return stats
    newest_date, newest_value = fred.get_latest_observation(args["series"], args.get("units") or "lin")
    if newest_date > stats["latest"]["date"]:
        stats["newer_data"] = (f"newest available: {newest_date} = {newest_value} — this result stops at the "
                               "requested end; use the newest unless the user asked for an earlier period")
    return stats


class Analyst:
    def __init__(self, search_papers, atlas: Atlas | None = None, wb: "worldbank.WorldBank | None" = None):
        """search_papers(query, exclude) -> (context_text, [source filenames],
        ids of the chunks included); `exclude` = chunk ids already shown this
        question, so a second search doesn't spend tokens re-sending them."""
        self.search_papers = search_papers
        self.atlas = atlas or Atlas()
        self.wb = wb or worldbank.WorldBank()
        self._longrun = None  # loaded on first use: most questions don't need it
        self._jpal = None
        self._dhs = None
        self._chart_keys: set[str] = set()  # charts drawn for the previous question
        self._gdl = None
        self.history: list[dict] = []
        self.last_tokens = 0  # Groq tokens used by the latest run() (free tier: 200K/day)
        self._tools_chars = len(json.dumps(TOOLS))
        self._reset_evidence()

    def _reset_evidence(self):
        self._structured: list[str] = []   # FRED + Atlas results (numbers may be combined)
        self._passages: list[str] = []     # paper text (numbers/citations must match directly)
        self._sources: set[str] = set()
        self._seen_chunks: set[int] = set()
        self._results: list[tuple[str, dict, dict]] = []  # every tool call this question, for charts

    def _call(self, name: str, args: dict) -> dict:
        try:
            if name == "search_papers":
                text, sources, ids = self.search_papers(args["query"], self._seen_chunks)
                self._seen_chunks |= set(ids)
                self._passages.append(text)
                self._sources |= set(sources)
                if not text:
                    return {"passages": "", "note": "no new passages beyond those already shown"}
                return {"passages": text, "sources": sources}
            if name == "search_literature":
                papers = openalex.search(args["query"], from_year=args.get("from_year"))
                # abstracts count as passages: numbers and citations in them verify
                self._passages += [f"{w['cite_as']} {w['title']}. {w['venue'] or ''}. {w['abstract']}"
                                   for w in papers]
                self._sources |= {w["cite_as"] for w in papers}
                return {"papers": papers}
            if name == "search_evaluations":
                if self._jpal is None:
                    self._jpal = jpal.JPAL()  # FileNotFoundError (not fetched yet) reaches the model
                result = self._jpal.search(args["query"])
                # results text counts as evidence: its numbers and the J-PAL cite verify
                self._passages += [f"{e['cite_as']}. {e['researchers']} ({e['timeline']}). {e['results']}"
                                   for e in result["evaluations"]]
                self._sources |= {e["cite_as"] for e in result["evaluations"]}
                return result
            if name == "run_python":
                specs = args.get("data") or []
                sources = {s.get("source", "worldbank") for s in specs}
                return compute.run_python(args["code"], specs, self.wb,
                                          self.longrun() if "longrun" in sources else None,
                                          self.gdl() if "gdl" in sources else None)
            if name == "county_profile":
                return self.atlas.county_profile(args["county"], args.get("state"),
                                                 bool(args.get("demographics")))
            if name == "rank_counties":
                return self.atlas.rank_counties(**args)
            if name == "correlate_counties":
                return self.atlas.correlate(**args)
            if name == "search_data":
                source = args.get("source")
                results = (fred.search_series(args["query"]) if source == "fred" else
                           self.longrun().search(args["query"]) if source == "longrun" else
                           self.dhs().search(args["query"]) if source == "dhs" else
                           self.gdl().search(args["query"]) if source == "gdl" else
                           self.wb.search(args["query"]))
                if not results:
                    return {"results": [], "note": "No series matched. Try 2-3 plain keywords or the other "
                            "source; if two searches find nothing, say the data isn't available."}
                return {"results": results}
            if name == "get_data":
                if args.get("source") == "fred":
                    stats = fred.series_stats(args["series"], args.get("start"), args.get("end"),
                                              args.get("units") or "lin")
                    return _with_newest_fred(stats, args)
                year = lambda v: int(str(v)[:4]) if v else None  # noqa: E731
                if args.get("source") == "gdl":
                    if not args.get("countries"):
                        return {"error": "gdl data needs `countries` (names, ISO3 codes, or [\"all\"])"}
                    return self.gdl().get(args["series"], args["countries"], year(args.get("start")),
                                          year(args.get("end")), args.get("order") or "highest", args.get("n") or 10)
                if args.get("source") == "dhs":
                    return _trim_regions(self.dhs().get(args["series"], args.get("countries") or [],
                                                        year(args.get("start")), year(args.get("end")),
                                                        bool(args.get("regions"))))
                if args.get("source") == "longrun":
                    return self._get_longrun(args["series"], args.get("countries") or [],
                                             year(args.get("start")), year(args.get("end")))
                if not args.get("countries"):
                    return {"error": "worldbank data needs `countries` (names, ISO3 codes, or [\"all\"])"}
                return self.wb.get(args["series"], args["countries"], year(args.get("start")),
                                   year(args.get("end")), args.get("order") or "highest", args.get("n") or 10,
                                   bool(args.get("peers")))
            return {"error": f"unknown tool {name}"}
        except Exception as e:  # bad arguments, FRED errors: let the model see and recover
            return {"error": f"{type(e).__name__}: {e}"}

    def gdl(self) -> gdl.GDL:
        if self._gdl is None:
            self._gdl = gdl.GDL()  # FileNotFoundError (file not saved yet) reaches the model
        return self._gdl

    def dhs(self) -> dhs.DHS:
        if self._dhs is None:
            self._dhs = dhs.DHS()
        return self._dhs

    def longrun(self) -> longrun.LongRun:
        if self._longrun is None:
            self._longrun = longrun.LongRun()  # FileNotFoundError (data not downloaded) reaches the model
        return self._longrun

    def _get_longrun(self, series: str, countries: list[str], start: int | None, end: int | None) -> dict:
        if not countries:
            return {"error": "longrun data needs `countries`"}
        if series.split(".")[-1] == "growth_accounting":
            return self.longrun().growth_accounting(countries[0], start, end)
        result = self.longrun().get(series, countries, start, end)
        if len(countries) > 1:
            result["comparison"] = self.longrun().compare(series, countries, start, end)
        return result

    def remember(self, question: str, answer: str):
        self.history = (self.history + [{"role": "user", "content": question},
                                        {"role": "assistant", "content": answer}])[-2 * HISTORY_TURNS:]

    def _fit(self, messages: list[dict]):
        """Trims the oldest tool results until the request fits under one
        minute's token allowance (a request over it fails outright)."""
        def size():
            return (len(json.dumps(messages, ensure_ascii=False)) + self._tools_chars) / CHARS_PER_TOKEN
        for msg in messages:
            if size() <= REQUEST_TOKEN_LIMIT:
                return
            if msg["role"] == "tool" and len(msg["content"]) > 1500:
                msg["content"] = msg["content"][:1500] + " ...[trimmed to fit Groq's free-tier limit]"

    def _post(self, payload: dict) -> dict:
        self._fit(payload["messages"])
        for _ in range(3):
            try:
                response = groq_post(payload).json()
                self.last_tokens += response.get("usage", {}).get("total_tokens", 0)
                return response
            except GroqUnavailable as e:
                if e.retry_after is None or e.retry_after > MAX_RATE_LIMIT_WAIT:
                    raise
                print(f"  (Groq free-tier rate limit — waiting {e.retry_after:.0f}s)", flush=True)
                time.sleep(e.retry_after + 0.5)
        return groq_post(payload).json()

    def _check(self, answer: str) -> tuple[list[str], list[str]]:
        passages = "\n".join(self._passages)
        citations = []
        prior = "\n".join(m["content"] for m in self.history if m["role"] == "assistant")
        for c in verify.unsupported_citations(answer, passages + "\n" + prior, self._sources):
            found = None if c.endswith(".pdf") else openalex.check_citation(c)
            # "real but not consulted" and "no such paper" need different fixes
            citations.append(c if found is None else
                             f"{c} [real paper, but no tool returned it]" if found["exists"] else
                             f"{c} [no matching publication exists in OpenAlex — likely invented]")
        # numbers and citations from the previous answers count too: they were checked (or
        # flagged) when first given, and follow-ups reuse them ("what about these results?")
        prior = "\n".join(m["content"] for m in self.history if m["role"] == "assistant")
        return (verify.unsupported_numbers(answer, "\n".join(self._structured + [prior]), passages),
                citations)

    def run(self, question: str) -> str:
        self._reset_evidence()
        self.last_tokens = 0
        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(today=datetime.date.today())},
                    *self.history, {"role": "user", "content": question}]
        revised = False
        for round_ in range(MAX_ROUNDS):
            payload = {"messages": messages, "temperature": 0.2, "max_tokens": 4096,
                       # medium, not low: choosing tools well takes more thought
                       # than answering from a fixed context does
                       "reasoning_effort": "medium"}
            if round_ < MAX_ROUNDS - 1:
                payload["tools"] = TOOLS
            else:  # last round: no tools at all (gpt-oss ignored tool_choice "none" in testing)
                messages.append({"role": "user", "content": "Tool budget used up: answer now from the "
                                 "tool results above, and say what you couldn't find."})
            response = self._post(payload)
            msg = response["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                answer = (msg.get("content") or "").strip()
                if not answer:
                    answer = "(The model returned no answer — try rephrasing.)"
                numbers, citations = self._check(answer)
                if (numbers or citations) and not revised and round_ < MAX_ROUNDS - 1:
                    # one chance to fix it, with tools still available
                    revised = True
                    print(f"  (fact-check: unsupported {', '.join(numbers + citations)} — asking for a revision)",
                          flush=True)
                    messages += [{"role": "assistant", "content": answer}, {"role": "user", "content": (
                        "Automatic fact-check of your answer: "
                        + (f"these numbers appear in no tool result (nor as a difference/ratio of tool "
                           f"numbers): {numbers}. " if numbers else "")
                        + (f"These citations appear in no search_papers/search_literature result: {citations}. "
                           if citations else "")
                        + "Rewrite the full answer for the user: remove or correct each of these, calling "
                          "tools if you need the real figure. Don't mention this check.")}]
                    continue
                print(f"\nAnswer:\n{answer}")
                if numbers or citations:
                    print(f"\n  ⚠ Not verified against tool results: {', '.join(numbers + citations)}")
                # plain Python, no tokens; only when the data is the point, and no repeats
                self.last_charts = charts.auto(self._results, wb=self.wb, question=question,
                                               previous=self._chart_keys)
                self._chart_keys = {charts.spec_key(s) for s in charts.last_specs}
                for path in self.last_charts:
                    print(f"  Chart: {path}")
                self.remember(question, answer)
                return answer
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args, result = {}, {"error": "arguments were not valid JSON"}
                else:
                    result = self._call(name, args)
                    self._results.append((name, args, result))
                    if name not in ("search_papers", "search_literature", "search_evaluations"):
                        self._structured.append(json.dumps(result))
                shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
                print(f"  → {name}({shown})\n      {_summary(name, result)}", flush=True)
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
        raise AssertionError("unreachable: the last round forbids tool calls")
