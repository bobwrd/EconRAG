"""
Local web UI: the same assistant as ask.py, in a browser.

    .venv/bin/python web.py            # then open http://127.0.0.1:8765
    .venv/bin/python web.py --port 9000

Listens on 127.0.0.1 only: other computers can't reach it, and requests that
name another host (DNS rebinding) or come from another website (Origin) are
refused. That matters because run_python executes model-written code — its
sandbox is for personal use only (see compute.py).

Loads the same models as ask.py (ask.load_models): run one or the other, not
both — two copies don't fit in 8GB. One question at a time. Conversations are
saved in data/sessions.db (sessions.py) and can be reopened from "Past chats".
Python's standard library only; the page is ui/ (HTML, JS, CSS, glossary).
"""

import argparse
import csv
import datetime
import io
import json
import re
import shutil
import sqlite3
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import charts
import compute
import groq_client
import report_export
import report_recipes
import sessions
import verify
import workflows
from analyst import Stopped
from groq_client import GroqUnavailable

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
CHARTS_DIR = (ROOT / charts.OUT_DIR).resolve()
HOST = "127.0.0.1"
PORT = 8765
KEEP_ANSWERS = 50  # answers kept in memory for CSV downloads and technical rewrites (saved ones reload)
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/glossary.json": ("glossary.json", "application/json; charset=utf-8")}
CHART_TYPES = {".png": "image/png", ".gif": "image/gif", ".svg": "image/svg+xml"}
# the page loads nothing from other sites, and nothing inline can run
CSP = "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"

# Technical rewrite (button on an answer, ~4K Groq tokens, only when clicked):
# a short prompt of its own, not the analyst's SYSTEM_PROMPT and tool schemas.
REWRITE_EVIDENCE_CHARS = 7000  # tool results sent (~2.2K tokens)
REWRITE_PASSAGE_CHARS = 2000   # paper/literature text sent
REWRITE_PROMPT = """Rewrite an economics answer for a technical reader (economist, analyst, graduate student). Keep its conclusions. Add, where the tool results support it: exact indicator definitions and series ids, units and price basis (PPP vintage, constant vs current prices), survey or data years, how any computed figure was derived, and caveats on comparability and coverage. Use ONLY numbers, sources, and citations that appear in the answer or the tool results below; if something technical isn't there, leave it out rather than guess. Under 300 words. Simple markdown (bold, lists, tables) allowed."""


# ------------------------------------------------------------------ data behind an answer
def _flatten(value, path=""):
    """(field path, scalar) for every value in a tool result."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flatten(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        yield path, value


def seen_rows(results: list[tuple[str, dict, dict]]) -> list[list]:
    """Every value the model was shown, exactly as returned (long series are sampled)."""
    rows = [["call", "tool", "arguments", "field", "value"]]
    for i, (name, args, result) in enumerate(results, 1):
        for field, value in _flatten(result):
            rows.append([i, name, json.dumps(args, ensure_ascii=False), field, value])
    return rows


def full_specs(results: list[tuple[str, dict, dict]]) -> list[dict]:
    """compute.fetch specs that re-read, in full, the series an answer used."""
    specs = []
    for name, args, result in results:
        if "error" in result:
            continue
        if name == "run_python":
            specs += [dict(s) for s in args.get("data") or []]
        elif name == "get_data":
            source = args.get("source") or "worldbank"
            if source not in compute.SOURCES or str(args.get("series", "")).endswith("growth_accounting"):
                continue  # dhs: one value per survey, already in full; growth accounting is computed
            spec = {k: args[k] for k in ("series", "countries", "start", "end", "units") if args.get(k)}
            spec["source"] = source
            if source == "worldbank" and [c.lower() for c in spec.get("countries", [])] == ["all"] \
                    and not spec.get("start"):
                spec["start"] = datetime.date.today().year - 15  # rankings: recent years, not since 1960
            if source == "gdl":
                spec["regions"] = [c.lower() for c in spec.get("countries", [])] != ["all"]
            specs.append(spec)
    unique = {json.dumps(s, sort_keys=True): s for s in specs}
    return list(unique.values())


def full_rows(results, wb, longrun=None, gdl=None) -> tuple[list[list], list[str]]:
    """Every year of every series used, re-read from its source; plus problems to report."""
    rows, problems = [["source", "series", "code", "name", "year_or_date", "value"]], []
    for spec in full_specs(results):
        try:
            data, names = compute.fetch(spec, wb, longrun, gdl)
        except Exception as e:  # missing data file, network: say so in the zip's README
            problems.append(f"{spec.get('source')} {spec.get('series')}: {type(e).__name__}: {e}")
            continue
        for code, series in data.items():
            for when, value in sorted(series.items(), key=lambda kv: str(kv[0])):
                rows.append([spec["source"], spec["series"], code, names.get(code, code), when, value])
    return rows, problems


def _csv(rows: list[list]) -> str:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def data_zip(record: dict, kind: str, wb, longrun=None, gdl=None) -> bytes:
    """kind: 'full', 'seen', or 'both'. Always a zip, with a README describing it."""
    results = record["evidence"]["results"]
    readme = [f"Question: {record['question']}", f"Asked: {record['asked']}", ""]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if kind in ("full", "both"):
            rows, problems = full_rows(results, wb, longrun, gdl)
            z.writestr("full_series.csv", _csv(rows))
            readme += ["full_series.csv: every year of every World Bank, FRED, long-run (Maddison/PWT) and",
                       "Global Data Lab series the answer used, re-read from the source when downloaded.",
                       "DHS surveys, county data and paper searches are only in what_the_model_saw.csv."]
            readme += [f"  Not included: {p}" for p in problems] + [""]
        if kind in ("seen", "both"):
            z.writestr("what_the_model_saw.csv", _csv(seen_rows(results)))
            z.writestr("tool_calls.json", json.dumps([{"tool": n, "arguments": a, "result": r}
                                                      for n, a, r in results], indent=1, ensure_ascii=False))
            readme += ["what_the_model_saw.csv: every value in every tool result, exactly as the model saw it",
                       "(long series are sampled there; see full_series.csv for every year).",
                       "tool_calls.json: the same tool calls and results, as structured JSON.", ""]
        z.writestr("README.txt", "\n".join(readme) + "\n")
    return buf.getvalue()


def session_zip(app, session_id: int) -> bytes | None:
    """A saved chat as one zip: chat.md (questions, answers, fact-checks, charts), chat.json (the
    same as data), charts/, and per answer what the model saw (answer_N/). Nothing re-fetched."""
    s = app.saved.get(session_id) if app.saved else None
    if s is None:
        return None
    md = [f"# {s['title']}", "", f"*Saved chat, {s['created'].replace('T', ' ')} to {s['updated'].replace('T', ' ')}*", ""]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, a in enumerate(s["answers"], 1):
            md += [f"## {n}. {a['question']}", "", a.get("answer", ""), ""]
            if a.get("unverified"):
                md.append(f"> Not found in any tool result (treat with caution): {', '.join(a['unverified'])}")
            elif a.get("tools"):
                md.append("> Every number and citation was found in the tool results (checked automatically).")
            else:
                md.append("> No data was looked up for this answer.")
            md.append("")
            if a.get("tools"):
                md += ["Looked up: " + "; ".join(t.get("summary") or t["name"] for t in a["tools"]), ""]
            for url in a.get("charts", []):
                chart = CHARTS_DIR / Path(url).name
                if chart.is_file():
                    z.write(chart, f"charts/{chart.name}")
                    md += [f"![{chart.stem}](charts/{chart.name})", ""]
            record = app.answer(a["id"])
            if record and record["evidence"]["results"]:
                inner = zipfile.ZipFile(io.BytesIO(data_zip(record, "seen", None)))
                for name in inner.namelist():
                    z.writestr(f"answer_{n}/{name}", inner.read(name))
                md += [f"Data: `answer_{n}/what_the_model_saw.csv`", ""]
            if record and record.get("technical"):
                md += ["**Technical version:**", "", record["technical"]["text"], ""]
        z.writestr("chat.md", "\n".join(md) + "\n")
        z.writestr("chat.json", json.dumps({k: v for k, v in s.items() if k != "pairs"}, indent=1,
                                           ensure_ascii=False, default=str))
        z.writestr("README.txt", "A saved chat from the Development Economics Assistant.\n\n"
                   "chat.md: the questions and answers, with fact-check results and charts.\n"
                   "chat.json: the same as data (answers, tools used, where each number came from).\n"
                   "answer_N/: every value the model saw for answer N (what_the_model_saw.csv, tool_calls.json).\n"
                   "For every year of each series, use the Download button on that answer in the web page.\n")
    return buf.getvalue()


# ------------------------------------------------------------------ follow-up suggestions (no tokens)
MAX_FOLLOWUPS = 3


def followups(question: str, results: list) -> list[str]:
    """Up to 3 next questions, written by Python from what this answer looked up (the page puts
    a clicked one in the question box to edit). Plain templates, no model, no tokens."""
    out = []
    for name, args, result in results:
        if not isinstance(result, dict) or result.get("error"):
            continue
        source = args.get("source", "worldbank")
        if name == "get_data" and source in ("worldbank", "imf", "longrun") and result.get("economies"):
            what = (result.get("name") or args.get("series", "")).split(" (")[0].split(", ")[0]
            if what[1:2].islower():  # "Life expectancy" -> "life expectancy"; "GDP per capita" stays
                what = what[0].lower() + what[1:]
            places = [e["economy"] for e in result["economies"] if e.get("economy")][:4]
            if not what or not places:
                continue
            if len(places) == 1:
                out += [f"How does {places[0]} compare with its region and income group on {what}?",
                        f"How has {what} in {places[0]} changed since 1990?"]
            else:
                out += [f"Which of {', '.join(places[:-1])} and {places[-1]} improved fastest on {what}?"]
        elif name == "county_profile" and result.get("county"):
            out += [f"Which nearby counties have higher upward mobility than {result['county']}?",
                    f"How does {result['county']} compare for Black and white children?"]
        elif name in ("search_papers", "search_literature") and args.get("query"):
            out += [f"What do randomized evaluations find about {args['query']}?"]
        elif name == "search_evaluations" and args.get("query"):
            out += [f"Are there reviews or meta-analyses of {args['query']}?"]
    seen, picked = {question.strip().lower()}, []
    for q in out:
        if q.lower() not in seen:
            seen.add(q.lower())
            picked.append(q)
    return picked[:MAX_FOLLOWUPS]


# ------------------------------------------------------------------ evidence inspector (no tokens)
def number_sources(answer: str, evidence: dict) -> list[dict]:
    """Where each number in the answer came from (verify.locate_numbers), for click-to-see on the
    page. Never stops an answer: on any problem the numbers just aren't clickable."""
    if not evidence["results"] and not evidence["passages"]:
        return []
    try:
        return verify.locate_numbers(answer, evidence)
    except Exception as e:  # noqa: BLE001
        print(f"  (couldn't trace the answer's numbers: {type(e).__name__}: {e})", flush=True)
        return []


# ------------------------------------------------------------------ technical view (no tokens)
def _key_values(result: dict) -> list[list]:
    """A few (label, year, value) rows from a data result, whatever its source."""
    rows = []
    for e in result.get("economies", []):
        if "latest" in e:
            rows.append([e["economy"], e["latest"]["year"], e["latest"]["value"]])
    for e in result.get("ranked", []):
        label = e.get("economy") or e.get("region")
        rows.append([f"{label}, {e['country']}" if "region" in e and "country" in e else label,
                     e.get("year"), e.get("value")])
    for c in result.get("countries", []) if isinstance(result.get("countries"), list) else []:
        point = c.get("latest") or c.get("last")
        if isinstance(point, dict):
            rows.append([c.get("country"), point.get("year"), point.get("value")])
        elif "national" in c:
            rows.append([c.get("country"), c.get("year"), c.get("national")])
    outcomes = next((v for k, v in result.items() if k.startswith("outcomes [")), None)
    if isinstance(outcomes, dict):  # county_profile: [county, state_avg, national_avg, percentile]
        for k, v in outcomes.items():
            if isinstance(v, list) and v and v[0] is not None:
                rows.append([f"{k} (state {v[1]}, US {v[2]})", "", v[0]])
    for c in result.get("counties", []) if isinstance(result.get("counties"), list) else []:
        rows.append([c.get("county"), "", c.get("value")])  # rank_counties
    if "series_id" in result and isinstance(result.get("latest"), dict):  # FRED
        for k in ("latest", "max", "min"):
            if isinstance(result.get(k), dict):
                rows.append([k, result[k].get("date"), result[k].get("value")])
    return rows[:10]


def technical_details(results: list[tuple[str, dict, dict]]) -> list[dict]:
    """Per tool call: what it is, its definition, units, notes and key values —
    read straight from the tool results, so it costs no tokens."""
    items = []
    for name, args, result in results:
        if "error" in result:
            items.append({"title": f"{name}: error", "notes": [str(result["error"])]})
            continue
        item = {"title": name, "notes": []}
        if name == "get_data":
            sid = result.get("indicator") or result.get("variable") or result.get("series_id") or result.get("metric")
            label = result.get("name") or result.get("title") or ""
            item["title"] = f"{sid} — {label}" if label else str(sid or args.get("series"))
            item["source"] = result.get("source") or {"worldbank": "World Bank, World Development Indicators",
                                                      "fred": "FRED, Federal Reserve Bank of St. Louis"}.get(
                                                          args.get("source") or "worldbank", "")
            item["definition"] = result.get("definition") or ""
            item["units"] = result.get("units") or result.get("unit") or ""
            item["notes"] = [n for n in result.get("notes") or [] if isinstance(n, str)]
            item["values"] = _key_values(result)
        elif name == "run_python":
            item["title"] = "run_python — calculation"
            item["data"] = result.get("data") or []
            item["code"] = args.get("code", "")
            item["output"] = result.get("output", "")
        elif name in ("search_papers", "search_literature", "search_evaluations"):
            found = (result.get("sources") or [w.get("cite_as") for w in result.get("papers", [])]
                     or [e.get("cite_as") for e in result.get("evaluations", [])])
            item["title"] = f"{name}: “{args.get('query', '')}”"
            item["notes"] = [str(f) for f in found if f]
        elif name in ("county_profile", "rank_counties", "correlate_counties"):
            item["source"] = "Opportunity Atlas (Chetty et al.), county data"
            item["notes"] = [result[k] for k in ("interpretation", "note", "metric_definition") if isinstance(result.get(k), str)]
            item["values"] = _key_values(result)
            item["title"] = f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})"
        elif name == "search_data":
            item["title"] = f"search_data: “{args.get('query', '')}”"
            item["notes"] = [f"{r.get('id') or r.get('series_id')}: {r.get('name') or r.get('title')}"
                             for r in result.get("results", [])[:5]]
        items.append(item)
    return items


def rewrite_technical(record: dict) -> dict:
    """One Groq request: the answer rewritten for a technical reader, then fact-checked."""
    ev = record["evidence"]
    evidence = []
    budget = REWRITE_EVIDENCE_CHARS // max(1, len(ev["results"]))
    for name, args, result in ev["results"]:
        if name in ("search_papers", "search_literature", "search_evaluations"):
            continue  # their text goes in below, trimmed
        evidence.append(f"{name}({json.dumps(args, ensure_ascii=False)}): "
                        f"{json.dumps(result, ensure_ascii=False)[:budget]}")
    passages = "\n".join(ev["passages"])[:REWRITE_PASSAGE_CHARS]
    user = (f"Question: {record['question']}\n\nAnswer to rewrite:\n{record['answer']}\n\n"
            f"Tool results:\n" + "\n".join(evidence) + (f"\n\nResearch passages:\n{passages}" if passages else ""))
    response = groq_post({"messages": [{"role": "system", "content": REWRITE_PROMPT},
                                       {"role": "user", "content": user}],
                          "temperature": 0.2, "max_tokens": 2048, "reasoning_effort": "low"}).json()
    text = (response["choices"][0]["message"].get("content") or "").strip()
    unverified = (verify.unsupported_numbers(text, "\n".join(ev["structured"]), "\n".join(ev["passages"]))
                  + verify.unsupported_citations(text, "\n".join(ev["passages"]), ev["sources"]))
    return {"text": text, "unverified": unverified,
            "tokens": response.get("usage", {}).get("total_tokens", 0), "backend": groq_client.last_provider}


groq_post = groq_client.post  # replaced in tests


# ------------------------------------------------------------------ setup check
def status(app) -> dict:
    """What's set up on this machine; missing pieces are turned off, not errors."""
    import requests
    try:
        requests.get("http://localhost:11434/api/tags", timeout=1)
        ollama = True
    except requests.RequestException:
        ollama = False
    data = ROOT / "data"
    return {
        "listening": f"{HOST}:{app.port}",
        "groq": bool(groq_client.GROQ_API_KEY), "openrouter": bool(groq_client.OPENROUTER_API_KEY),
        "offline_model": ollama,
        "pieces": {
            "Opportunity Atlas (US counties)": all((data / "atlas" / f).exists() for f in (
                "county_outcomes_simple.csv", "cty_covariates.csv", "national_county.txt")),
            "Charts (Ask)": charts.ask_available(),
            "Offline answers (Ollama)": ollama,
            "run_python (macOS sandbox)": shutil.which("sandbox-exec") is not None,
            "Global Data Lab": any((data / "gdl").glob("*.csv")),
            "J-PAL evaluations": (data / "jpal" / "evaluations.json").exists(),
            "Long-run data (Maddison/PWT)": (data / "longrun").exists() and any((data / "longrun").iterdir()),
        },
        "papers": len(app.models.chunks) if app.models else 0,
        "report_topics": {t: workflows.TOPIC_TITLES[t] for t in workflows.TOPICS},
        "report_default_topics": workflows.DEFAULT_TOPICS,
    }


# ------------------------------------------------------------------ the server
class App:
    """Holds the loaded models, the analyst, and recent answers."""

    def __init__(self, models=None, analyst=None, pool=None, answer_locally=None, port=PORT, saved=None):
        self.models, self.analyst, self.pool, self.port = models, analyst, pool, port
        self.saved = saved  # sessions.Sessions, or None: answers then live in memory only
        self.session_id = None  # the conversation new answers join; None = start one on the next question
        self.stop = threading.Event()  # set by the Stop button: the running question ends at its next step
        self.answer_locally = answer_locally  # (question) -> str, or None when offline isn't possible
        self.lock = threading.Lock()  # one question (or rewrite) at a time
        self.answers: dict[str, dict] = {}
        self.reports: dict[str, dict] = {}  # report id -> report (workflows.py), for downloads
        self.next_id = 1
        self._wb = None
        self.library = None  # report_recipes.library_search(models), made on first use; False = no library
        self.warm = None  # ask.warm_up running between questions

    def store(self, record: dict, where: dict | None = None, rid: str | None = None) -> str:
        where = self.answers if where is None else where
        if rid is None:
            # "m" ids can't collide with saved answers' ids
            rid = ("m" if self.saved else "") + str(self.next_id)
            self.next_id += 1
        where[rid] = record
        for old in list(where)[:-KEEP_ANSWERS]:
            del where[old]
        return rid

    def save(self, record: dict, view: dict) -> str:
        """Saves an answer to the current conversation (starting one if needed); returns its id."""
        if self.saved:
            try:
                if self.session_id is None:
                    self.session_id = self.saved.new(record["question"])
                return self.store(record, rid=str(self.saved.add(self.session_id, record, view)))
            except sqlite3.Error as e:
                print(f"  (couldn't save this answer: {e}; it's kept until web.py stops)", flush=True)
        return self.store(record)

    def answer(self, rid: str) -> dict | None:
        """An answer's record: from memory, or reloaded from a saved conversation."""
        record = self.answers.get(rid)
        if record is None and self.saved and rid.isdigit():
            record = self.saved.record(int(rid))
            if record is not None:
                self.store(record, rid=rid)
        return record

    def open_session(self, session_id: int) -> dict | None:
        """Makes a saved conversation the current one: its answers for the page, and its last
        questions as the analyst's memory, so follow-ups continue it."""
        s = self.saved.get(session_id) if self.saved else None
        if s is None:
            return None
        self.session_id = session_id
        if self.analyst:
            self.analyst.history = []
            for question, answer in s.pop("pairs"):
                self.analyst.remember(question, answer)  # keeps only the last few
        else:
            s.pop("pairs")
        return s

    def new_conversation(self):
        self.session_id = None
        if self.analyst:
            self.analyst.history = []

    def wb(self):
        """The World Bank client already loaded for the analyst, or a new one (reports work without Groq)."""
        if self.analyst:
            return self.analyst.wb
        if self._wb is None:
            from worldbank import WorldBank
            self._wb = WorldBank()
        return self._wb

    def report(self, params: dict, emit) -> dict:
        """Builds a report (workflows.py, report_recipes.py) and returns it as the page shows it."""
        kind = params.get("kind")
        say = lambda kind, text: emit({"kind": kind, "text": text})  # noqa: E731
        write = params.get("summary", True) is not False
        text = lambda key: str(params.get(key) or "").strip()  # noqa: E731

        def listed(key):  # a list, or text separated by commas
            value = params.get(key) or []
            items = re.split(r"[,;\n]+", value) if isinstance(value, str) else value
            return [str(i).strip() for i in items if str(i).strip()]
        if kind == "compare":
            countries = params.get("countries") or []
            if isinstance(countries, str):
                countries = [c for c in re.split(r"[,;\n]+", countries) if c.strip()]
            report = workflows.compare(self.wb(), countries, params.get("topics") or None, listed("indicators"),
                                       emit=say, write=write, start=params.get("start"), end=params.get("end"))
        elif kind == "brief":
            report = report_recipes.country_brief(self.wb(), text("country"), emit=say, write=write,
                                                  start=params.get("start"), end=params.get("end"),
                                                  indicators=listed("indicators"), peers=listed("peers"))
        elif kind == "poverty":
            report = report_recipes.poverty_profile(self.wb(), text("country"), emit=say, write=write)
        elif kind == "works":
            if self.library is None:  # the chat's paper search (with Laya) when the models are loaded
                self.library = report_recipes.library_search(self.models) if self.models else False
            report = report_recipes.what_works(self.wb(), text("intervention"), text("region"), text("outcome"),
                                               emit=say, write=write, library=self.library or None)
        else:
            raise ValueError("unknown report kind")
        rid = None
        if self.saved:  # kept, so it can be reopened and downloaded after a restart
            try:
                rid = self.store(report, self.reports, rid=str(self.saved.add_report(report)))
            except sqlite3.Error as e:
                print(f"  (couldn't save this report: {e}; it's kept until web.py stops)", flush=True)
        return report_view(report, rid or self.store(report, self.reports))

    def report_by_id(self, rid: str) -> dict | None:
        """A report: from memory, or reloaded from the saved ones."""
        report = self.reports.get(rid)
        if report is None and self.saved and rid.isdigit():
            report = self.saved.report(int(rid))
            if report is not None:
                self.store(report, self.reports, rid=rid)
        return report

    def ask(self, question: str, emit) -> dict:
        """Answers one question, sending progress through emit(event dict)."""
        if self.warm:
            self.warm.result()
        started = time.time()
        self.stop.clear()
        events = []  # the progress trail, saved so a reopened answer shows it too

        def emit(event: dict, send=emit):
            events.append(event)
            send(event)
        record = {"question": question, "asked": datetime.datetime.now().isoformat(timespec="seconds"),
                  "evidence": {"structured": [], "passages": [], "sources": set(), "results": []}}
        result = None
        if self.analyst:
            self.analyst.on_event = lambda kind, text, **data: emit({"kind": kind, "text": text.strip(), **data})
            self.analyst.should_stop = self.stop.is_set
            groq_client.notify = lambda text: emit({"kind": "notice", "text": text.strip()})
            try:
                self.analyst.run(question)
                result = dict(self.analyst.last_result)
                record["evidence"] = self.analyst.evidence()
            except Stopped:  # discarded: not saved, and follow-ups don't remember it
                print("  (stopped)", flush=True)
                return {"stopped": True, "seconds": round(time.time() - started)}
            except GroqUnavailable as e:
                emit({"kind": "notice", "text": f"Groq unavailable ({e}). Answering offline with phi3.5."})
            finally:
                self.analyst.on_event = self.analyst.should_stop = None
                groq_client.notify = _print
        if result is None:
            if not self.answer_locally:
                raise RuntimeError("No online model (GROQ_API_KEY) and no offline model available.")
            emit({"kind": "notice", "text": "Offline model (phi3.5): slower, uses only the paper library "
                                            "and FRED, and is not fact-checked."})
            answer = self.answer_locally(question)
            if self.analyst:
                self.analyst.remember(question, answer)
            result = {"answer": answer, "unverified": [], "revised": False, "charts": [], "tools": [],
                      "tokens": 0, "backend": "offline"}
        if self.models and self.pool:
            from ask import warm_up
            self.warm = self.pool.submit(warm_up, self.models.agent, self.models.model)
        record.update(answer=result["answer"], result=result)
        view = {**result, "seconds": round(time.time() - started),
                "charts": ["/charts/" + Path(p).name for p in result["charts"]],
                "technical": technical_details(record["evidence"]["results"]), "events": events,
                "numbers": number_sources(result["answer"], record["evidence"]),
                "followups": followups(question, record["evidence"]["results"])}
        return {**view, "id": self.save(record, view), "session": self.session_id}


def report_view(report: dict, rid: str) -> dict:
    """The report as JSON for the page: no raw tool results, chart paths as URLs."""
    view = {k: v for k, v in report.items() if k not in ("results", "facts")}
    view["sections"] = [{**sec, "charts": ["/charts/" + Path(p).name for p in sec["charts"]]}
                        for sec in report["sections"]]
    view["id"] = rid
    view["formats"] = {f: name for f, (name, _) in report_export.FORMATS.items()}
    return view


def _print(text: str):
    print(text, flush=True)


class Handler(BaseHTTPRequestHandler):
    app: App  # set by serve()

    def log_message(self, fmt, *args):  # quiet: the terminal shows the analyst's own trail
        pass

    def _allowed(self, post: bool) -> bool:
        """Only this machine, by name: a page from another site can't drive the assistant."""
        hosts = {f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}"}
        if self.headers.get("Host") not in hosts:
            self._send(403, b"Forbidden: this server only answers at http://127.0.0.1", "text/plain")
            return False
        origin = self.headers.get("Origin")
        if post and origin not in {f"http://{h}" for h in hosts}:
            self._send(403, b"Forbidden: requests from other sites are refused", "text/plain")
            return False
        return True

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json")

    def _body(self) -> dict:
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 100_000)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self):
        if not self._allowed(post=False):
            return
        url = urlparse(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            return self._send(200, (UI_DIR / name).read_bytes(), ctype)
        if url.path.startswith("/charts/"):
            name = url.path.removeprefix("/charts/")
            path = (CHARTS_DIR / name).resolve()
            if (re.fullmatch(r"[\w.\-]+", name) and path.parent == CHARTS_DIR
                    and path.suffix in CHART_TYPES and path.is_file()):
                return self._send(200, path.read_bytes(), CHART_TYPES[path.suffix])
            return self._send(404, b"not found", "text/plain")
        if url.path == "/api/status":
            return self._json(status(self.app))
        if url.path == "/api/sessions":  # ?q= searches titles, questions and answers
            q = parse_qs(url.query).get("q", [""])[0][:200]
            return self._json({"sessions": self.app.saved.list(query=q) if self.app.saved else [],
                               "current": self.app.session_id, "saving": bool(self.app.saved)})
        if url.path == "/api/reports":
            return self._json({"reports": self.app.saved.reports() if self.app.saved else [],
                               "saving": bool(self.app.saved)})
        if url.path == "/api/reports/view":
            rid = parse_qs(url.query).get("id", [""])[0]
            report = self.app.report_by_id(rid)
            return self._json(report_view(report, rid)) if report else self._json({"error": "unknown report"}, 404)
        if url.path == "/api/sessions/export":
            query = parse_qs(url.query)
            try:
                body = session_zip(self.app, int(query.get("id", [""])[0]))
            except ValueError:
                body = None
            if body is None:
                return self._json({"error": "unknown conversation"}, 404)
            return self._send(200, body, "application/zip", {
                "Content-Disposition": f'attachment; filename="chat{int(query["id"][0])}.zip"'})
        if url.path == "/api/report-options":  # the drop-down lists of the report form
            return self._json(report_recipes.options(self.app.wb()))
        if url.path == "/api/usage":  # tokens in the last 24 hours (groq_client.usage)
            return self._json(groq_client.usage())
        if url.path == "/api/report":
            query = parse_qs(url.query)
            report = self.app.report_by_id(query.get("id", [""])[0])
            fmt = query.get("fmt", [""])[0]
            if not report or fmt not in report_export.FORMATS:
                return self._json({"error": "unknown report or format"}, 404)
            name, ctype = report_export.FORMATS[fmt]
            filename = f"{workflows.slug(report['title'])}_{name}"
            return self._send(200, report_export.export(report, fmt, self.app.wb()), ctype,
                              {"Content-Disposition": f'attachment; filename="{filename}"'})
        if url.path == "/api/data":
            query = parse_qs(url.query)
            record = self.app.answer(query.get("id", [""])[0])
            kind = query.get("kind", ["both"])[0]
            if not record or kind not in ("full", "seen", "both"):
                return self._json({"error": "unknown answer or kind"}, 404)
            a = self.app.analyst
            body = data_zip(record, kind, a.wb if a else None,
                            a._longrun if a else None, a._gdl if a else None)
            return self._send(200, body, "application/zip", {
                "Content-Disposition": f'attachment; filename="answer{query["id"][0]}_{kind}.zip"'})
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self._allowed(post=True):
            return
        path = urlparse(self.path).path
        if path == "/api/ask":
            return self._ask(str(self._body().get("question") or "").strip()[:2000])
        if path == "/api/report":
            params = self._body()
            return self._stream(lambda emit: self.app.report(params, emit), f"Report: {params.get('kind')} "
                                f"{params.get('countries') or params.get('country') or params.get('intervention')}")
        if path == "/api/technical":
            rid = str(self._body().get("id"))
            record = self.app.answer(rid)
            if not record or not record["evidence"]["results"]:
                return self._json({"error": "This answer used no tools, so there's nothing to rewrite from."}, 400)
            if not self.app.lock.acquire(blocking=False):
                return self._json({"error": "Busy with another question — try again when it finishes."}, 409)
            try:
                if not record.get("technical"):
                    record["technical"] = rewrite_technical(record)
                    if self.app.saved and rid.isdigit():
                        self.app.saved.update_record(int(rid), record)
                return self._json(record["technical"])
            except GroqUnavailable as e:
                return self._json({"error": f"Groq unavailable: {e}"}, 503)
            finally:
                self.app.lock.release()
        if path == "/api/stop":  # no lock: the running question holds it; it ends at its next step
            self.app.stop.set()
            return self._json({"ok": True})
        if path == "/api/reset":
            self.app.new_conversation()
            return self._json({"ok": True})
        if path in ("/api/sessions/rename", "/api/sessions/pin", "/api/reports/delete"):
            body = self._body()
            try:
                item = int(body.get("id"))
            except (TypeError, ValueError):
                return self._json({"error": "unknown item"}, 404)
            saved = self.app.saved
            ok = bool(saved) and (saved.rename(item, str(body.get("title") or "")) if path.endswith("rename") else
                                  saved.pin(item, bool(body.get("pinned"))) if path.endswith("pin") else
                                  saved.delete_report(item))
            if ok and path.endswith("reports/delete"):
                self.app.reports.pop(str(item), None)
            return self._json({"ok": True}) if ok else self._json({"error": "unknown item or empty name"}, 404)
        if path in ("/api/sessions/open", "/api/sessions/delete"):
            try:
                session_id = int(self._body().get("id"))
            except (TypeError, ValueError):
                return self._json({"error": "unknown conversation"}, 404)
            if not self.app.lock.acquire(blocking=False):  # not while a question changes the current one
                return self._json({"error": "Busy with another question — try again when it finishes."}, 409)
            try:
                if path.endswith("open"):
                    s = self.app.open_session(session_id)
                    return self._json(s) if s else self._json({"error": "unknown conversation"}, 404)
                if not (self.app.saved and self.app.saved.delete(session_id)):
                    return self._json({"error": "unknown conversation"}, 404)
                if self.app.session_id == session_id:
                    self.app.new_conversation()
                return self._json({"ok": True})
            finally:
                self.app.lock.release()
        self._send(404, b"not found", "text/plain")

    def _ask(self, question: str):
        if not question:
            return self._json({"error": "empty question"}, 400)
        return self._stream(lambda emit: self.app.ask(question, emit), f"Question: {question}")

    def _stream(self, work, label: str):
        """Runs work(emit) while streaming its progress, one JSON object per line."""
        if not self.app.lock.acquire(blocking=False):
            return self._json({"error": "Busy with another question or report — one at a time."}, 409)
        # progress streams as one JSON object per line while the question runs
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        connected = True

        def emit(event: dict):
            nonlocal connected
            if not connected:
                return
            try:
                self.wfile.write((json.dumps(event, ensure_ascii=False, default=str) + "\n").encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                connected = False  # page closed: finish the answer anyway, it stays in memory

        try:
            print(f"\n{label}", flush=True)
            emit({**work(emit), "kind": "done"})  # last: a report has a "kind" of its own
        except Exception as e:
            emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            self.app.lock.release()
        self.close_connection = True


def serve(app: App, port: int = PORT):
    app.port = port
    Handler.app = app
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"\nOpen http://{HOST}:{port} in your browser.\n"
          f"Listening on {HOST} only: other computers can't connect. Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=PORT)
    port = parser.parse_args().port
    import ask  # heavy imports (torch, transformers) only when actually serving
    pool = ThreadPoolExecutor(max_workers=4)
    models = ask.load_models(pool)
    try:
        saved = sessions.Sessions()
    except sqlite3.Error as e:
        print(f"Saved conversations are off ({e}): answers are kept until web.py stops.", flush=True)
        saved = None
    app = App(models, ask.make_analyst(models), pool,
              lambda q: ask.answer_locally(q, models, pool), port, saved)
    app.warm = pool.submit(ask.warm_up, models.agent, models.model)
    try:
        serve(app, port)
    finally:
        ask.unload_llm()
        pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    sys.exit(main())
