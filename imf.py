"""
IMF World Economic Outlook (WEO): inflation, government debt and deficits,
current account, unemployment and growth, with the IMF's projections about five
years ahead — the forecasts the World Bank's GEP doesn't have (it has growth only).

Two ways in, both open, no key:
  DataMapper (imf.org/external/datamapper/api/v1) — the current edition, named in
      its metadata ("World Economic Outlook (April 2026)"). It ignores a country in
      the URL and returns every country (~120KB per indicator), so each indicator is
      fetched once and cached in data/imf/ for a week.
  IMF data API (api.imf.org, SDMX) — one country per request, but in Oct 2026 it
      still served the October 2025 edition. Used only if DataMapper refuses (it
      rejected Python clients in early Oct 2026), and labeled as possibly older.

    .venv/bin/python imf.py inflation Kenya
"""

import datetime
import json
import re
import sys
import time
from pathlib import Path

import requests

from bm25 import BM25

DATA_DIR = Path("data/imf")
DATAMAPPER = "https://www.imf.org/external/datamapper/api/v1"
SDMX = "https://api.imf.org/external/sdmx/2.1/data/IMF.RES,WEO"
MAX_AGE = 7 * 24 * 3600   # seconds a cached indicator or catalog is used before refetching
TIMEOUT = 30
YEARS_BACK = 8            # default window: 8 years of history ...
YEARS_AHEAD = 5           # ... and the projections
# the headline WEO indicators: boosted in search, and what the country brief uses
CORE = {"PCPIPCH", "PCPIEPCH", "GGXWDG_NGDP", "GGXCNL_NGDP", "BCA_NGDPD", "NGDP_RPCH", "LUR"}
WORDS = {  # everyday words, so search finds the indicator ("deficit" isn't in its name)
    "PCPIPCH": "inflation prices cpi consumer",
    "PCPIEPCH": "inflation end of period december",
    "GGXWDG_NGDP": "public debt government debt borrowing",
    "GGXCNL_NGDP": "fiscal deficit budget balance surplus",
    "BCA_NGDPD": "current account external balance trade deficit",
    "NGDP_RPCH": "gdp growth economic growth real",
    "LUR": "unemployment jobless",
}
_session = requests.Session()


class IMFUnavailable(Exception):
    pass


def _fresh(path: Path) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < MAX_AGE


def _json(url: str) -> dict:
    r = _session.get(url, timeout=TIMEOUT)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        raise IMFUnavailable(f"IMF DataMapper answered {r.status_code} ({r.headers.get('content-type')})")
    return r.json()


def edition_year(edition: str) -> int | None:
    m = re.search(r"(?:19|20)\d{2}", edition or "")
    return int(m.group()) if m else None


def parse_sdmx(xml: str) -> tuple[dict[str, dict[int, float]], str]:
    """The IMF data API's XML -> ({ISO3: {year: value}}, newest country update date)."""
    out, updated = {}, []
    for m in re.finditer(r"<Series ([^>]*)>(.*?)</Series>", xml, re.S):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        obs = {int(y): float(v) for y, v in re.findall(r'TIME_PERIOD="(\d{4})" OBS_VALUE="([-\d.eE]+)"', m.group(2))}
        if obs:
            out[attrs.get("COUNTRY", "?")] = obs
        if attrs.get("COUNTRY_UPDATE_DATE"):
            updated.append(datetime.datetime.strptime(attrs["COUNTRY_UPDATE_DATE"], "%m/%d/%Y").date())
    return out, max(updated).isoformat() if updated else ""


class IMF:
    def __init__(self, directory: Path = DATA_DIR):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "indicators.json"
        if not _fresh(path):
            try:
                catalog = _json(f"{DATAMAPPER}/indicators")["indicators"]
                path.write_text(json.dumps({k: v for k, v in catalog.items() if v.get("dataset") == "WEO"}))
            except (requests.RequestException, IMFUnavailable):
                if not path.exists():
                    raise
        self.indicators = json.loads(path.read_text())
        self.ids = list(self.indicators)
        self._bm25 = BM25([f"{k} {v.get('label', '')} {v.get('description', '')[:300]} {WORDS.get(k, '')} "
                           f"{WORDS.get(k, '')}" for k, v in self.indicators.items()])

    # ---- data ------------------------------------------------------------
    def series(self, indicator: str, codes: list[str] | None = None) -> tuple[dict[str, dict[int, float]], str]:
        """({ISO3: {year: value}}, edition label) for one indicator, all countries (cached a week).
        Falls back to the IMF data API for `codes` if DataMapper refuses."""
        if indicator not in self.indicators:
            raise ValueError(f"unknown IMF indicator {indicator!r}: use search to find its id")
        path = self.dir / f"{indicator}.json"
        if not _fresh(path):
            try:
                values = _json(f"{DATAMAPPER}/{indicator}")["values"][indicator]
                path.write_text(json.dumps({"edition": self.indicators[indicator].get("source", ""),
                                            "values": values}))
            except (requests.RequestException, IMFUnavailable, KeyError):
                if not path.exists():
                    return self._sdmx(indicator, codes or [])
        cached = json.loads(path.read_text())
        data = {c: {int(y): float(v) for y, v in s.items() if v is not None} for c, s in cached["values"].items()}
        return data, cached["edition"]

    def _sdmx(self, indicator: str, codes: list[str]) -> tuple[dict[str, dict[int, float]], str]:
        if not codes:
            raise IMFUnavailable("IMF DataMapper unavailable, and the fallback needs country codes")
        r = _session.get(f"{SDMX}/{'+'.join(codes)}.{indicator}.A", timeout=TIMEOUT)
        r.raise_for_status()
        data, updated = parse_sdmx(r.text)
        return data, (f"World Economic Outlook via the IMF data API (country data updated {updated}; "
                      "may be one edition older than the latest)")

    # ---- tools -----------------------------------------------------------
    def search(self, query: str, n: int = 6) -> list[dict]:
        scores = self._bm25.scores(query)
        for i, k in enumerate(self.ids):
            if k in CORE:
                scores[i] *= 1.5
        top = [i for i in scores.argsort()[::-1][:n] if scores[i] > 0]
        return [{"id": self.ids[i], "name": self.indicators[self.ids[i]].get("label", ""),
                 "unit": self.indicators[self.ids[i]].get("unit", "")} for i in top]

    def get(self, indicator: str, countries: list[str], start: int | None = None, end: int | None = None,
            resolve=lambda c: c, name=lambda code: code) -> dict:
        """Per country: the series in [start, end] (default: 8 years back to 5 ahead), latest
        actual-or-projected year, and which years are projections. resolve: name -> ISO3;
        name: ISO3 -> display name (the World Bank client's, in the analyst)."""
        codes = list(dict.fromkeys(resolve(c) for c in countries))[:6]
        data, edition = self.series(indicator, codes)
        meta = self.indicators[indicator]
        first_projection = edition_year(edition)  # April and October editions both project their own year
        this_year = datetime.date.today().year
        start, end = start or this_year - YEARS_BACK, end or this_year + YEARS_AHEAD
        out = {"indicator": indicator, "name": meta.get("label", indicator), "unit": meta.get("unit", ""),
               "source": f"IMF, {edition}", "definition": (meta.get("description") or "").strip()[:300],
               "notes": [f"IMF {edition}: values from {first_projection} on are PROJECTIONS (and "
                         f"{first_projection - 1} is an estimate for many countries) — say so and name the edition."
                         if first_projection else f"IMF {edition}: recent years are projections — say so."],
               "economies": []}
        for code in codes:
            series = {y: v for y, v in data.get(code, {}).items() if start <= y <= end}
            entry = {"economy": name(code), "code": code}
            if not series:
                entry["data"] = "no IMF data for this economy in this period"
            else:
                years = sorted(series)
                entry.update({"latest": {"year": years[-1], "value": round(series[years[-1]], 2)},
                              "first": {"year": years[0], "value": round(series[years[0]], 2)},
                              "series [year, value]": [[y, round(series[y], 2)] for y in years]})
                if first_projection:
                    entry["projections_from"] = first_projection
            out["economies"].append(entry)
        out["edition"] = edition
        return out


if __name__ == "__main__":
    imf = IMF()
    hits = imf.search(sys.argv[1] if len(sys.argv) > 1 else "inflation")
    print(json.dumps(hits, indent=1))
    if len(sys.argv) > 2:
        from worldbank import WorldBank
        wb = WorldBank()
        print(json.dumps(imf.get(hits[0]["id"], sys.argv[2:], resolve=wb.country,
                                 name=lambda c: wb.countries.get(c, {}).get("name", c)), indent=1, ensure_ascii=False))
