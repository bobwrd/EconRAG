"""
OpenAlex (openalex.org): an open index of ~250M scholarly works. Used by
analyst.py for two things:

  search(query)          -> real papers with abstracts (the search_literature tool)
  check_citation(text)   -> does an author-year citation match any real paper?

Free within a daily budget: anonymous use gets $0.10/day (a search costs
$0.001, so ~100 searches/day; headers x-ratelimit-*-usd, Oct 2026). Without
billing on file nothing is ever charged — over budget, the API returns 429.
An optional free key (OPENALEX_API_KEY in .env) raises the budget 10x.
`python openalex.py "deworming school attendance"` prints a search.
"""

import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

API = "https://api.openalex.org/works"
TIMEOUT = 20
FIELDS = "display_name,publication_year,authorships,primary_location,cited_by_count,doi,abstract_inverted_index"
_session = requests.Session()
_cache: dict[tuple, list] = {}


def _get(**params) -> list[dict]:
    key = tuple(sorted(params.items()))
    if key not in _cache:
        if os.environ.get("OPENALEX_API_KEY"):
            params["api_key"] = os.environ["OPENALEX_API_KEY"]
        r = _session.get(API, params={"select": FIELDS, **params}, timeout=TIMEOUT)
        r.raise_for_status()
        _cache[key] = r.json()["results"]
    return _cache[key]


def _surname(display_name: str) -> str:
    return display_name.split()[-1] if display_name else ""


def _abstract(inverted: dict | None, limit: int = 700) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inverted:
        return ""
    words = sorted((pos, word) for word, positions in inverted.items() for pos in positions)
    text = " ".join(word for _, word in words)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def _authors(work: dict) -> list[str]:
    return [_surname(a["author"]["display_name"]) for a in work.get("authorships", [])]


def citation_label(work: dict) -> str:
    """'Skoufias and Parker (2001)' / 'Miguel et al. (2004)': the form the
    answer should cite, and the form verify.py looks for in passages."""
    names = _authors(work)
    who = (names[0] if len(names) == 1 else " and ".join(names) if len(names) == 2
           else f"{names[0]} et al.") if names else "Unknown"
    return f"{who} ({work.get('publication_year')})"


def search(query: str, n: int = 5, from_year: int | None = None) -> list[dict]:
    """Top n of the 15 most relevant works, re-ranked by relevance rank +
    citation rank: by relevance alone, "deworming school attendance Kenya"
    returned the 2015 replication debate but not the 2004 study it debated."""
    params = {"search": query, "per_page": 15, "sort": "relevance_score:desc"}
    if from_year:
        params["filter"] = f"publication_year:>{int(from_year) - 1}"
    works, seen = [], {}
    for w in _get(**params):  # one entry per title: working paper + journal versions are separate works
        title = re.sub(r"[^a-z0-9]", "", (w.get("display_name") or "").lower())
        if title not in seen or (w.get("cited_by_count") or 0) > (works[seen[title]].get("cited_by_count") or 0):
            if title in seen:
                works[seen[title]] = w
            else:
                seen[title] = len(works)
                works.append(w)
    by_citations = sorted(range(len(works)), key=lambda i: -(works[i].get("cited_by_count") or 0))
    citation_rank = {i: r for r, i in enumerate(by_citations)}
    order = sorted(range(len(works)), key=lambda i: i + citation_rank[i])[:n]
    out = []
    for w in (works[i] for i in order):
        source = (w.get("primary_location") or {}).get("source") or {}
        out.append({"cite_as": citation_label(w), "title": w["display_name"],
                    "authors": ", ".join(_authors(w)[:6]), "year": w.get("publication_year"),
                    "venue": source.get("display_name"), "cited_by": w.get("cited_by_count"),
                    "doi": w.get("doi"), "abstract": _abstract(w.get("abstract_inverted_index"))})
    return out


_CITE = re.compile(r"([A-Z][a-zA-Z'’-]+(?:\s*(?:,|and|&)\s*[A-Z][a-zA-Z'’-]+)*)(?:\s+et\s+al\.?)?,?\s*\(?((?:19|20)\d{2})")
_NOT_NAMES = {"and", "et", "al", "the", "see"}


def check_citation(citation: str) -> dict | None:
    """Looks an author-year citation ('Okonjo and Whitfield (2019)',
    'Chetty et al. 2016') up in OpenAlex. Returns {"exists": bool, "match":
    title} — or None if it can't be parsed or OpenAlex can't be reached."""
    m = _CITE.search(citation)
    if not m:
        return None
    surnames = [s for s in re.split(r"\s*(?:,|and|&)\s*", m.group(1)) if s and s.lower() not in _NOT_NAMES]
    year = int(m.group(2))
    try:
        # +-1 year: working paper vs journal versions are often cited with different years
        works = _get(search=" ".join(surnames), filter=f"publication_year:{year - 1}-{year + 1}", per_page=25)
    except requests.RequestException:
        return None
    for w in works:
        authors = {a.lower() for a in _authors(w)}
        if all(s.lower() in authors for s in surnames):
            return {"exists": True, "match": f"{citation_label(w)} {w['display_name']}"}
    return {"exists": False, "match": None}


if __name__ == "__main__":
    import json
    print(json.dumps(search(" ".join(sys.argv[1:]) or "conditional cash transfers schooling"),
                     indent=1, ensure_ascii=False))
