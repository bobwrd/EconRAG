"""
"What works" evidence for analyst.py: J-PAL's summaries of randomized
evaluations (povertyactionlab.org/evaluations, ~1,100 studies) — policy
question, context, intervention, and results, with researchers, sample,
timeline, and the research papers behind them.

  search(query, n)  -> the best-matching evaluations, keyword-ranked, with
                       their results text (which the fact-check treats as evidence)

Setup (once, ~25 min at one page a second; resumable):
    .venv/bin/python jpal.py --fetch
Stored in data/jpal/evaluations.json (delete to refetch). J-PAL's robots.txt
allows these pages; the summaries are J-PAL's — cite them as such.
"""

import html
import json
import re
import sys
import time
from pathlib import Path

import requests

from bm25 import BM25

BASE = "https://www.povertyactionlab.org"
DATA = Path("data/jpal/evaluations.json")
PAUSE = 1.0  # seconds between requests
HEADERS = {"User-Agent": "econ-research-assistant/1.0 (personal research; python-requests)"}
FIELDS = ["Researchers", "Sample", "Timeline", "Target group", "Outcome of interest", "Intervention type",
          "AEA RCT registration number", "Data", "Research papers", "Partners"]
SECTIONS = ["Policy issue", "Context of the evaluation", "Details of the intervention",
            "Results and policy lessons"]
RESULTS_WORDS = 160  # per search hit: enough for the headline findings, small enough for 8K-token requests
NOTE = ("J-PAL evaluation summaries (randomized trials). Each covers one context: say where and when, "
        "and don't generalize one study to 'what works' everywhere; cite as (J-PAL: <title>).")


_FOOTER = re.compile(r"\s*J-PAL 400 Main Street.*", re.S)


def results_text(e: dict) -> str:
    """J-PAL's results, without the site footer saved after them on most pages; "" while a study is
    ongoing ("Study ongoing; results forthcoming.")."""
    text = _FOOTER.sub("", e.get("results_and_policy_lessons", "")).strip()
    return "" if "forthcoming" in text.lower() and len(text.split()) < 25 else text


def _text(fragment: str) -> str:
    fragment = re.sub(r"(?s)<(script|style|noscript).*?</\1>", "", fragment)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse(page: str, url: str) -> dict:
    """One evaluation page -> title, sectors, metadata fields, and the four text sections."""
    title = _text(re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S).group(1))
    text = _text(page)
    out = {"title": title, "url": url}
    crumbs = re.search(r"Breadcrumb Home Evaluations (.*?) " + re.escape(title), text)
    out["sectors"] = crumbs.group(1) if crumbs else ""
    labels = "|".join(re.escape(f) for f in FIELDS)
    meta = text[text.find(title + " Researchers:"):] if title + " Researchers:" in text else text
    for field in FIELDS:
        m = re.search(rf"{re.escape(field)}: (.*?)(?= (?:{labels}):| Print | Policy issue |$)", meta[:3000])
        if m:
            out[field.lower().replace(" ", "_")] = m.group(1).replace(" Print", "").strip()
    # sections: split the article on its h2/h3 headings
    parts = re.split(r"<h[23][^>]*>(.*?)</h[23]>", page, flags=re.S)
    for heading, body in zip(parts[1::2], parts[2::2]):
        name = _text(heading)
        if name in SECTIONS:
            out[name.lower().replace(" ", "_")] = _text(body)
    return out


def _listing_urls(session: requests.Session) -> list[str]:
    """Every evaluation page, from the site's sitemap (the /evaluations listing
    never runs out of pages, so paging through it doesn't terminate)."""
    urls, page = [], 1
    while True:
        r = session.get(f"{BASE}/sitemap.xml", params={"page": page}, timeout=60)
        if r.status_code == 404:
            return urls
        r.raise_for_status()
        found = re.findall(r"<loc>https://www\.povertyactionlab\.org(/evaluation/[^<]+)</loc>", r.text)
        if "<url>" not in r.text and "<loc>" not in r.text:
            return urls
        urls += [u for u in found if u not in urls]
        page += 1
        time.sleep(PAUSE)


def fetch(path: Path = DATA, limit: int | None = None):
    """Downloads every evaluation summary (resumable: already-fetched pages are kept)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    done = {e["url"]: e for e in json.loads(path.read_text())} if path.exists() else {}
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = _listing_urls(session)
    print(f"{len(urls)} evaluations listed, {len(done)} already fetched", flush=True)
    todo = [u for u in urls if BASE + u not in done][:limit]
    for i, u in enumerate(todo):
        try:
            r = session.get(BASE + u, timeout=30)
            r.raise_for_status()
            done[BASE + u] = parse(r.text, BASE + u)
        except (requests.RequestException, AttributeError) as e:
            print(f"  skipped {u}: {type(e).__name__}", flush=True)
        if i % 50 == 49:
            path.write_text(json.dumps(list(done.values()), ensure_ascii=False))
            print(f"  {i + 1}/{len(todo)}", flush=True)
        time.sleep(PAUSE)
    path.write_text(json.dumps(list(done.values()), ensure_ascii=False))
    print(f"saved {len(done)} evaluations to {path}")


def _country_names() -> dict[str, str]:
    """Lowercase country name or alias -> display name, from the cached World Bank list."""
    import worldbank
    countries = json.loads((worldbank.CATALOG_DIR / "countries.json").read_text())
    short = [c["name"].split(",")[0] for c in countries if not c["aggregate"]]
    display = {c["id"]: c["name"] if short.count(c["name"].split(",")[0]) > 1 else c["name"].split(",")[0]
               for c in countries if not c["aggregate"]}  # "Congo, Dem. Rep." stays distinct
    names = {c["name"].split(",")[0].lower(): display[c["id"]] for c in countries
             if not c["aggregate"] and short.count(c["name"].split(",")[0]) == 1}
    by_code = display
    names |= {alias: by_code[code] for alias, code in worldbank.ALIASES.items() if code in by_code and len(alias) > 3}
    return names


def countries_in(text: str, names: dict[str, str]) -> list[str]:
    """Countries named in a study's title and context (J-PAL pages have no country field)."""
    low, found = text.lower(), set()
    for key in sorted(names, key=len, reverse=True):  # longest first, and blank out each match, so
        pattern = rf"\b{re.escape(key)}\b"            # "republic of the congo" can't match inside the DRC's name
        if re.search(pattern, low):
            found.add(names[key])
            low = re.sub(pattern, " ", low)
    return sorted(found)


class JPAL:
    def __init__(self, path: Path = DATA):
        if not path.exists():
            raise FileNotFoundError(f"J-PAL evaluations not fetched: run `python jpal.py --fetch` ({path})")
        self.evals = json.loads(path.read_text())
        names = _country_names()
        for e in self.evals:
            e["countries"] = countries_in(e["title"] + " " + " ".join(e.get("context_of_the_evaluation", "")
                                                                      .split()[:80]), names)
        self._bm25 = BM25([" ".join([e["title"]] * 2 + [e.get(k, "") for k in (
            "sectors", "intervention_type", "outcome_of_interest", "target_group", "policy_issue",
            " ".join(e["countries"]),
            "context_of_the_evaluation")] + [results_text(e)]) for e in self.evals])

    def search(self, query: str, n: int = 4) -> dict:
        scores = self._bm25.scores(query)
        top = sorted(range(len(self.evals)), key=lambda i: -scores[i])[:n]
        hits = []
        for i in top:
            if scores[i] <= 0:
                break
            e = self.evals[i]
            results = results_text(e).split()
            hits.append({
                "title": e["title"], "cite_as": f"J-PAL: {e['title']}",
                "researchers": e.get("researchers", ""), "countries": e["countries"],
                "timeline": e.get("timeline", ""),
                "sample": e.get("sample", ""), "intervention": e.get("intervention_type", ""),
                "results": (" ".join(results[:RESULTS_WORDS]) + (" ..." if len(results) > RESULTS_WORDS else "")
                            if results else "no results reported yet (ongoing study or no summary): "
                                            "don't state findings for it"),
                "papers": e.get("research_papers", ""), "url": e["url"]})
        return {"evaluations": hits, "note": NOTE}


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fetch"]:
        fetch(limit=int(sys.argv[2]) if len(sys.argv) > 2 else None)
        sys.exit()
    print(json.dumps(JPAL().search(" ".join(sys.argv[1:]) or "cash transfers"), indent=1, ensure_ascii=False))
