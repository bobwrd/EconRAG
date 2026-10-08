"""
A benchmark of the online analyst, scored by code.

    .venv/bin/python eval/benchmark.py run [name]      # answers -> eval/benchmark/<name>.jsonl
    .venv/bin/python eval/benchmark.py run [name] --only retest   # the ~22 questions recent fixes target
    .venv/bin/python eval/benchmark.py score [name]    # grade one run (default: latest)
    .venv/bin/python eval/benchmark.py questions       # write eval/external/questions.md
    .venv/bin/python eval/benchmark.py score-external chatgpt   # grade eval/external/chatgpt.md

`run` is resumable — questions already in the file are skipped — and slow by
design. Groq's free tier allows 8,000 tokens/minute AND 200,000 tokens per
rolling 24 hours (the daily cap isn't in the response headers; it shows up
only in the 429 error). The full benchmark doesn't fit in one day's budget, so
`run` stops cleanly at the daily limit, without recording a result for the
question it was on; rerun the same command later to continue. Each question
starts with an empty conversation history; tokens used are recorded per
question.

Correct answers are computed at scoring time from the sources (World Bank API,
FRED, the Atlas files), so they stay right when the data updates. Scoring is
literal — read the answers in the .jsonl too:
  - a number in the answer must be within tolerance of the truth
    ("232.7 million" and "232,679,478" both parse)
  - `must` / `must_not` regexes (case-insensitive)
  - `must_year`: the answer must name the data's year (poverty surveys are old)

External answers (Phase 0c) go in eval/external/<name>.md as "## <question id>"
headings, each followed by the pasted answer.
"""

import contextlib
import csv
import io
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)  # ask.py and atlas.py resolve data/ relative to the project root

import requests  # noqa: E402

EVAL_DIR = ROOT / "eval"
OUT_DIR = EVAL_DIR / "benchmark"
EXTERNAL_DIR = EVAL_DIR / "external"
WB_URL = "https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_NUM = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:\s*(trillion|billion|million|thousand)\b)?", re.I)


def load_questions() -> list[dict]:
    old = {q["id"]: q for q in json.loads((EVAL_DIR / "questions.json").read_text())}
    out = []
    for q in json.loads((EVAL_DIR / "benchmark_questions.json").read_text()):
        if "from_eval" in q:
            src = old[q["from_eval"]]
            q = {"id": src["id"], "category": q["category"], "question": src["question"],
                 "must": src["must"]}
        out.append(q)
    return out


# ---------------------------------------------------------------- truths
def wb_latest(indicator: str, country: str) -> tuple[float, str, str]:
    """(value, year, indicator name) of the most recent non-empty observation."""
    r = requests.get(WB_URL.format(country=country, indicator=indicator),
                     params={"format": "json", "mrnev": 1}, timeout=30)
    r.raise_for_status()
    row = r.json()[1][0]
    return row["value"], row["date"], row["indicator"]["value"]


def wb_value(indicator: str, country: str, year: int) -> float:
    r = requests.get(WB_URL.format(country=country, indicator=indicator),
                     params={"format": "json", "date": year}, timeout=30)
    r.raise_for_status()
    value = r.json()[1][0]["value"]
    if value is None:
        raise ValueError(f"no {indicator} for {country} in {year}")
    return value


def compute_truth(q: dict) -> dict | None:
    t = q.get("truth")
    if not t:
        return None
    kind = t["kind"]
    if kind == "wb":
        value, year, name = wb_latest(t["indicator"], t["country"])
        return {"values": [value], "year": year, "label": f"{name}, {year}"}
    if kind == "wb_cagr":  # annual growth between two years, % (or doubling time in years)
        a, b = (wb_value(t["indicator"], t["country"], y) for y in (t["from"], t["to"]))
        g = (b / a) ** (1 / (t["to"] - t["from"])) - 1
        if t.get("doubling"):
            years = math.log(2) / math.log(1 + g)
            return {"values": [years], "label": f"{t['indicator']} {t['from']}-{t['to']}: {g:.2%}/yr -> doubles in {years:.1f}y"}
        return {"values": [100 * g], "label": f"{t['indicator']} {t['country']} {t['from']}->{t['to']}: {a:.0f}->{b:.0f}"}
    if kind == "wb_ratio":  # a / b in the latest year both have
        (va, ya, name), (vb, yb, _) = wb_latest(t["indicator"], t["a"]), wb_latest(t["indicator"], t["b"])
        if ya != yb:
            year = min(int(ya), int(yb))
            va, vb, ya = wb_value(t["indicator"], t["a"], year), wb_value(t["indicator"], t["b"], year), str(year)
        return {"values": [va / vb], "label": f"{name} {t['a']} {va:.0f} / {t['b']} {vb:.0f} ({ya}) = {va / vb:.2f}"}
    if kind == "wb_weighted":  # population-weighted mean over countries, in one year
        vals = [(wb_value(t["indicator"], c, t["year"]), wb_value("SP.POP.TOTL", c, t["year"])) for c in t["countries"]]
        mean = sum(v * w for v, w in vals) / sum(w for _, w in vals)
        simple = sum(v for v, _ in vals) / len(vals)
        return {"values": [mean], "label": f"{t['indicator']} {t['year']} weighted {mean:.2f} (simple {simple:.2f})"}
    if kind == "wb_multi":  # every country's latest value must appear
        rows = [wb_latest(t["indicator"], c) for c in t["countries"]]
        return {"values": [v for v, _, _ in rows], "all_required": True,
                "label": "; ".join(f"{c} {v:.1f} ({y})" for c, (v, y, _) in zip(t["countries"], rows))}
    if kind.startswith("mpd"):  # Maddison, read straight from the converted file (not via longrun.py)
        with open(ROOT / "data/longrun/maddison.csv") as f:
            gdppc = {(r["code"], int(r["year"])): float(r["gdppc"]) for r in csv.DictReader(f) if r["gdppc"]}
        last = lambda c: max(y for code, y in gdppc if code == c)  # noqa: E731
        point = lambda c, y: gdppc[c, last(c) if y == "latest" else y]  # noqa: E731
        if kind == "mpd":  # every (country, year) value must appear
            pts = [(c, last(c) if y == "latest" else y) for c, y in t["points"]]
            return {"values": [gdppc[p] for p in pts], "all_required": True,
                    "label": "; ".join(f"{c} {y} {gdppc[c, y]:.0f}" for c, y in pts)}
        if kind == "mpd_ratio":
            (c1, y1), (c0, y0) = t["of"], t["to"]
            v = point(c1, y1) / point(c0, y0)
            return {"values": [v], "label": f"{c1} {y1} / {c0} {y0} = {v:.2f}"}
        if kind == "mpd_cross":  # first year from which `a` stays above `b`
            a, b = t["a"], t["b"]
            years = sorted(y for c, y in gdppc if c == a and (b, y) in gdppc)
            below = [y for y in years if gdppc[a, y] <= gdppc[b, y]]
            year = next(y for y in years if y > max(below))
            return {"values": [], "must": [str(year)], "label": f"{a} above {b} from {year}"}
    import fred
    if kind == "fred_latest":
        s = fred.series_stats(t["series"], units=t.get("units", "lin"))
        return {"values": [s["latest"]["value"]], "label": f"{t['series']} {s['latest']['date']}"}
    if kind == "fred_max":
        s = fred.series_stats(t["series"], t["start"], t["end"])
        return {"values": [s["max"]["value"]], "label": f"{t['series']} max {s['max']['date']}"}
    if kind == "fred_diff":
        a = fred.series_stats(t["series"], t["from"], t["from"])["latest"]["value"]
        b = fred.series_stats(t["series"], t["to"], t["to"])["latest"]["value"]
        return {"values": [round(b - a, 4)], "label": f"{t['series']} {t['from']}->{t['to']}: {a}->{b}"}
    from atlas import Atlas
    atlas = Atlas()
    if kind == "atlas_profile":
        p = atlas.county_profile(t["county"], t["state"])
        row = next(v for k, v in p.items() if k.startswith("outcomes"))["upward_mobility"]
        values = [row[0]] + ([row[2]] if t.get("also_national") else [])
        return {"values": values, "all_required": True, "label": f"{p['county']} {row[0]} vs national {row[2]}"}
    if kind == "atlas_top":
        top = atlas.rank_counties("upward_mobility", state=t["state"], n=1)["counties"][0]["county"]
        name = top.split(",")[0].replace(" County", "")
        return {"values": [], "must": [re.escape(name)], "label": top}
    if kind == "atlas_corr":
        r = atlas.correlate(t["x"], t["y"])["weighted_correlation"]
        return {"values": [r], "label": f"weighted r = {r}"}
    raise ValueError(f"unknown truth kind {kind}")


# ---------------------------------------------------------------- scoring
def numbers(text: str) -> list[float]:
    out = []
    for whole, frac, scale in _NUM.findall(text):
        v = float(whole.replace(",", "") + (frac or ""))
        out.append(v * SCALE.get((scale or "").lower(), 1))
    return out


def grade(q: dict, truth: dict | None, answer: str) -> list[str]:
    """Returns the list of failed checks (empty = pass)."""
    fails = []
    found = numbers(answer)
    if truth:
        t = q["truth"]
        for v in truth["values"]:
            tol = t.get("abs_tol", 0) or abs(v) * t.get("rel_tol", 0)
            ok = any(abs(x - v) <= tol + 1e-9 or abs(x - abs(v)) <= tol + 1e-9 for x in found)
            if not ok:
                fails.append(f"number {v} (±{tol:g}) not found [{truth['label']}]")
                if not truth.get("all_required"):
                    break
        if t.get("must_year") and truth.get("year") and truth["year"] not in answer:
            fails.append(f"doesn't name the data year {truth['year']}")
        for pattern in truth.get("must", []):
            if not re.search(pattern, answer, re.I):
                fails.append(f"missing /{pattern}/ [{truth['label']}]")
    for pattern in q.get("must", []):
        if not re.search(pattern, answer, re.I):
            fails.append(f"missing /{pattern}/")
    for pattern in q.get("must_not", []):
        if re.search(pattern, answer, re.I):
            fails.append(f"contains /{pattern}/")
    return fails


def score(rows: dict[str, str], title: str):
    questions = load_questions()
    results, by_cat = [], {}
    for q in questions:
        if q["id"] not in rows:
            continue
        try:
            truth = compute_truth(q)
        except Exception as e:
            print(f"  (couldn't compute truth for {q['id']}: {e})")
            continue
        fails = grade(q, truth, rows[q["id"]])
        results.append({"id": q["id"], "category": q["category"], "passed": not fails, "fails": fails,
                        "truth": truth and truth["label"]})
        cat = by_cat.setdefault(q["category"], [0, 0])
        cat[0] += not fails
        cat[1] += 1
    print(f"\n=== {title}")
    for r in results:
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['id']:24s} {'; '.join(r['fails'])}")
    total = sum(r["passed"] for r in results)
    print(f"\n  {'category':12s} passed")
    for cat, (p, n) in by_cat.items():
        print(f"  {cat:12s} {p}/{n}")
    print(f"  {'TOTAL':12s} {total}/{len(results)}")
    return results


# ---------------------------------------------------------------- running
# `run <name> --only retest`: the questions the Oct 2026 fixes should change —
# the first run's real failures plus the one never run, the four correct answers
# that got false "not verified" flags, and every question added since (~1 day of
# Groq budget instead of ~2). `--only` also takes a comma-separated list of ids.
RETEST = ["wb_ner_fertility", "trap_ind_ppp_vs_market", "race_gap_shrink", "unanswerable_minwage",
          "wb_ken_gdppc", "wb_nga_lifeexp", "wb_nga_pop", "trap_us_employment"]
NEW_CATEGORIES = ("compute", "dev_lit", "longrun")


def selected(questions: list[dict], only: str | None) -> list[dict]:
    if not only:
        return questions
    ids = set(RETEST) if only == "retest" else set(only.split(","))
    keep = [q for q in questions if q["id"] in ids or (only == "retest" and q["category"] in NEW_CATEGORIES)]
    unknown = ids - {q["id"] for q in questions}
    if unknown:
        sys.exit(f"unknown question ids: {', '.join(sorted(unknown))}")
    return keep


def run(name: str, only: str | None = None):
    import laya
    from sentence_transformers import SentenceTransformer
    from transformers import AutoTokenizer

    import analyst
    import ask
    from atlas import Atlas
    import groq_client
    from groq_client import GROQ_MODEL, GroqUnavailable

    analyst.MAX_RATE_LIMIT_WAIT = 300  # unattended: wait out longer limits too
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{name}.jsonl"
    done = {json.loads(l)["id"] for l in path.read_text().splitlines()} if path.exists() else set()
    todo = [q for q in selected(load_questions(), only) if q["id"] not in done]
    print(f"{len(done)} done, {len(todo)} to go -> {path}", flush=True)
    if not todo:
        return

    chunks, embeddings, bm25 = ask.load_index()
    model = SentenceTransformer(ask.EMBEDDING_MODEL)
    agent = laya.load(ask.LAYA_MODEL, device="cpu")
    tokenizer = AutoTokenizer.from_pretrained(ask.OLLAMA_TOKENIZER)
    bot = analyst.Analyst(ask.paper_searcher(model, chunks, embeddings, bm25, agent, tokenizer), Atlas())

    for q in todo:
        for attempt in range(3):
            bot.history = []  # every question stands alone
            buf, start = io.StringIO(), time.time()
            try:
                with contextlib.redirect_stdout(buf):
                    answer = bot.run(q["question"])
                break
            except GroqUnavailable as e:
                # Only successful answers are recorded, so a resumed run retries
                # this question. The daily cap won't lift within minutes: stop.
                if "per day" in str(e) or (e.retry_after or 0) > analyst.MAX_RATE_LIMIT_WAIT:
                    print(f"\nStopped at {q['id']}: {e}\nRerun `benchmark.py run {name}` later to "
                          f"continue ({len(done) + todo.index(q)} of {len(done) + len(todo)} done).", flush=True)
                    return
                print(f"  {q['id']}: {e} — retrying in 120s", flush=True)
                time.sleep(120)
        else:
            print(f"\nStopped: Groq unreachable after 3 tries. Rerun later to continue.", flush=True)
            return
        log = buf.getvalue()
        status = "ok"
        row = {"id": q["id"], "question": q["question"], "answer": answer, "status": status,
               "seconds": round(time.time() - start, 1),
               "tools": [l.strip() for l in log.splitlines() if l.strip().startswith("→")],
               "unverified": next((l.split(":", 1)[1].strip() for l in log.splitlines()
                                   if "Not verified" in l), None),
               "revised": "fact-check" in log, "model": GROQ_MODEL, "provider": groq_client.last_provider, "tokens": bot.last_tokens,
               "at": datetime.now().isoformat(timespec="seconds")}
        with path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  {q['id']:24s} {row['seconds']:6.1f}s  tools={len(row['tools'])}  tokens={bot.last_tokens}",
              flush=True)


def latest_run() -> str:
    runs = sorted(OUT_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not runs:
        sys.exit("no runs yet — use `run` first")
    return runs[-1].stem


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    if cmd == "run":
        run(arg if arg and arg != "--only" else datetime.now().strftime("%Y-%m-%d"), only)
    elif cmd == "score":
        name = arg or latest_run()
        rows = {r["id"]: r["answer"] for r in map(json.loads, (OUT_DIR / f"{name}.jsonl").read_text().splitlines())}
        results = score(rows, f"analyst run {name}")
        (OUT_DIR / f"{name}.score.json").write_text(json.dumps(results, indent=1))
    elif cmd == "questions":
        EXTERNAL_DIR.mkdir(exist_ok=True)
        lines = ["Paste each question into the chatbot (a fresh chat each time is fairer), then paste",
                 "its answer under the matching heading in eval/external/<chatbot>.md:", ""]
        for q in load_questions():
            lines += [f"## {q['id']}", q["question"], ""]
        (EXTERNAL_DIR / "questions.md").write_text("\n".join(lines))
        print(f"wrote {EXTERNAL_DIR / 'questions.md'}")
    elif cmd == "score-external":
        text = (EXTERNAL_DIR / f"{arg}.md").read_text()
        rows = {m.group(1): m.group(2).strip() for m in re.finditer(r"^## (\S+)\n(.*?)(?=^## |\Z)", text, re.M | re.S)}
        score(rows, f"external: {arg}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
