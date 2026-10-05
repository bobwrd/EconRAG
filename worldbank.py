"""
World Bank World Development Indicators (WDI) for analyst.py: ~1,500
indicators x ~217 economies + regional and income-group aggregates, including
the Poverty & Inequality Platform's poverty and Gini series. Free, no key.

  search(query)                         -> indicator ids, keyword-ranked
  get(indicator, countries, start, end) -> per-country stats computed here
  get(indicator, ["all"], order, n)     -> every economy's latest value, ranked

Growth forecasts come from the World Bank's Global Economic Prospects (API
source 27, one indicator, NYGDPMKTPKDZ), added to the catalog as if it were a
WDI series. (The IMF's WEO is the usual forecast source, but its API rejects
Python clients and DBnomics' mirror stopped at the April 2025 edition.)

The indicator and country lists are fetched once into data/worldbank/ (~2 MB);
delete that folder to refresh them. Values are fetched live and cached in
memory for the session. `python worldbank.py "extreme poverty" Ethiopia` prints
a search and a lookup.
"""

import difflib
import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bm25 import BM25
from fred import sparkline

API = "https://api.worldbank.org/v2"
CATALOG_DIR = Path("data/worldbank")
TIMEOUT = 30

_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))))

# Indicators most development questions need; ranked above lookalikes in search
# (WDI has e.g. 10+ "GDP per capita" variants).
CORE = {
    "NY.GDP.PCAP.CD", "NY.GDP.PCAP.PP.CD", "NY.GDP.PCAP.KD", "NY.GDP.PCAP.PP.KD", "NY.GDP.MKTP.CD",
    "NY.GDP.MKTP.KD.ZG", "NY.GDP.PCAP.KD.ZG", "NY.GNP.PCAP.CD", "NY.GNP.PCAP.PP.CD",
    "FP.CPI.TOTL.ZG", "SP.POP.TOTL", "SP.POP.GROW", "SP.URB.TOTL.IN.ZS", "SP.DYN.LE00.IN",
    "SP.DYN.TFRT.IN", "SH.DYN.MORT", "SP.DYN.IMRT.IN", "SH.STA.MMRT", "SH.STA.STNT.ZS",
    "SI.POV.DDAY", "SI.POV.LMIC", "SI.POV.UMIC", "SI.POV.NAHC", "SI.POV.GINI", "SI.DST.10TH.10",
    "SE.PRM.CMPT.ZS", "SE.PRM.ENRR", "SE.SEC.ENRR", "SE.ADT.LITR.ZS", "EG.ELC.ACCS.ZS",
    "SH.H2O.BASW.ZS", "SH.STA.BASS.ZS", "IT.NET.USER.ZS", "BX.TRF.PWKR.DT.GD.ZS",
    "BX.KLT.DINV.WD.GD.ZS", "DT.ODA.ODAT.GN.ZS", "NE.TRD.GNFS.ZS", "GC.DOD.TOTL.GD.ZS",
    "SL.UEM.TOTL.ZS", "SL.AGR.EMPL.ZS", "SL.TLF.CACT.FE.ZS", "SL.EMP.TOTL.SP.ZS",
    "NV.AGR.TOTL.ZS", "NV.IND.MANF.ZS", "EN.GHG.CO2.PC.CE.AR5", "AG.LND.FRST.ZS",
}

FORECAST = "NYGDPMKTPKDZ"  # GEP real GDP growth, incl. forecasts (API source 27)
# Named for forecasts only, so plain "real GDP growth" still finds the WDI actuals first.
FORECAST_ENTRY = {"id": FORECAST, "name": "Growth forecasts and projections, World Bank Global "
                  "Economic Prospects", "note": "Real GDP growth at constant prices from the World Bank's "
                  "Global Economic Prospects report (published January and June): recent actuals, an "
                  "estimate for last year, and forecasts for this year and the next two."}

# Everyday words missing from the official names ("Poverty headcount ratio at
# $3.00 a day" never says "extreme poverty"), added to the search index.
SYNONYMS = {
    "SI.POV.DDAY": "extreme poverty international poverty line poor people share",
    "NY.GDP.PCAP.CD": "market exchange rates current us dollars nominal income per person",
    "NY.GDP.PCAP.PP.CD": "ppp purchasing power parity income per person living standards",
    FORECAST: "forecast projected outlook prediction expected future next year",
    "SI.POV.GINI": "inequality", "SH.DYN.MORT": "child mortality",
    "SP.DYN.TFRT.IN": "births per woman", "SH.STA.STNT.ZS": "malnutrition",
}

# Conventions attached to results, so the model can't miss them.
NOTES = {
    "SI.POV.": "Survey-based: values exist only for survey years, often several years old and "
               "different across countries. Always state the year; never call it today's rate.",
    "SI.DST.": "Survey-based: values exist only for survey years. Always state the year.",
    ".PP.": "PPP (international dollars): adjusts for price levels; not comparable with current-US$ figures.",
    "NY.GDP.PCAP.CD": "Current US dollars at market exchange rates; for living standards, PPP "
                      "(NY.GDP.PCAP.PP.CD) is usually the better comparison.",
}
# Current-price money series: changes over time include inflation.
CURRENT_PRICE_NOTE = ("Current prices: changes over time include inflation. For real growth over time "
                      "use the constant-price version (same id ending .KD instead of .CD).")

ALIASES = {
    "ivory coast": "CIV", "cote divoire": "CIV", "south korea": "KOR", "korea": "KOR",
    "north korea": "PRK", "russia": "RUS", "iran": "IRN", "egypt": "EGY", "vietnam": "VNM",
    "turkey": "TUR", "syria": "SYR", "laos": "LAO", "drc": "COD", "dr congo": "COD",
    "democratic republic of the congo": "COD", "congo kinshasa": "COD",
    "republic of the congo": "COG", "congo brazzaville": "COG", "gambia": "GMB",
    "bahamas": "BHS", "yemen": "YEM", "venezuela": "VEN", "kyrgyzstan": "KGZ",
    "slovakia": "SVK", "czech republic": "CZE", "uk": "GBR", "britain": "GBR",
    "great britain": "GBR", "us": "USA", "usa": "USA", "america": "USA", "macedonia": "MKD",
    "palestine": "PSE", "swaziland": "SWZ", "burma": "MMR", "cape verde": "CPV",
    "east timor": "TLS", "hong kong": "HKG", "macau": "MAC", "micronesia": "FSM",
    "st lucia": "LCA", "saint lucia": "LCA", "sub saharan africa": "SSF", "africa": "SSF",
    "latin america": "LCN", "middle east": "MEA", "mena": "MEA", "east asia": "EAS",
    "europe": "ECS", "low income countries": "LIC", "lower middle income countries": "LMC",
    "upper middle income countries": "UMC", "high income countries": "HIC",
    "developing countries": "LMY", "low and middle income": "LMY",
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z ]", "", name.lower().replace("&", "and").replace("-", " ")).strip()


def _fetch_all(path: str, **params) -> list[dict]:
    rows, page = [], 1
    while True:
        r = _session.get(f"{API}/{path}", params={"format": "json", "per_page": 1000, "page": page, **params},
                         timeout=TIMEOUT)
        r.raise_for_status()
        meta, data = r.json()[0], r.json()[1] or []
        rows += data
        if page >= meta["pages"]:
            return rows
        page += 1


def build_catalog(directory: Path = CATALOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)
    indicators = [{"id": i["id"], "name": i["name"], "note": " ".join((i.get("sourceNote") or "").split())[:400]}
                  for i in _fetch_all("source/2/indicator")]
    countries = [{"id": c["id"], "name": c["name"], "region": c["region"]["value"].strip(),
                  "income": c["incomeLevel"]["value"], "aggregate": c["region"]["value"] == "Aggregates"}
                 for c in _fetch_all("country")]
    (directory / "indicators.json").write_text(json.dumps(indicators))
    (directory / "countries.json").write_text(json.dumps(countries))


class WorldBank:
    def __init__(self, directory: Path = CATALOG_DIR):
        if not (directory / "indicators.json").exists():
            build_catalog(directory)
        self.indicators = json.loads((directory / "indicators.json").read_text()) + [FORECAST_ENTRY]
        self.by_id = {i["id"]: i for i in self.indicators}
        # name counted three times: a name match beats a passing mention in a note
        self._bm25 = BM25([f"{i['name']} {i['name']} {i['name']} {i['id']} {SYNONYMS.get(i['id'], '')} "
                           f"{SYNONYMS.get(i['id'], '')} {i['note'][:200]}" for i in self.indicators])
        self.countries = {c["id"]: c for c in json.loads((directory / "countries.json").read_text())}
        self._names = {}
        for c in self.countries.values():
            self._names[_norm(c["name"])] = c["id"]
            self._names.setdefault(_norm(c["name"].split(",")[0]), c["id"])  # "Egypt, Arab Rep." -> egypt
        for alias, code in ALIASES.items():
            self._names.setdefault(alias, code)
        self._cache: dict[tuple, list] = {}

    # ---- lookup helpers ------------------------------------------------
    def country(self, name: str) -> str:
        s = name.strip()
        if s.upper() in self.countries:
            return s.upper()
        key = _norm(s)
        if key in ("congo",):
            raise ValueError("'Congo' is ambiguous: Congo, Dem. Rep. (COD) or Congo, Rep. (COG)")
        if key in self._names:
            return self._names[key]
        close = difflib.get_close_matches(key, self._names, n=1, cutoff=0.85)
        if close:
            return self._names[close[0]]
        raise ValueError(f"unknown country or region {name!r}")

    def _notes(self, indicator: str) -> list[str]:
        if indicator == FORECAST:
            edition = self.forecast_edition()
            year = int(edition[:4])
            return [f"Global Economic Prospects data dated {edition}: {year} onward are FORECASTS and "
                    f"{year - 1} is an estimate — say so, and give the edition date. Forecasts are "
                    "revised every January and June."]
        notes = [note for key, note in NOTES.items() if key in indicator]
        if indicator.endswith(".CD") and indicator[:-3] + ".KD" in self.by_id:
            notes.append(CURRENT_PRICE_NOTE)
        return notes

    def _observations(self, indicator: str, codes: list[str], start: int, end: int) -> list[dict]:
        key = (indicator, tuple(codes), start, end)
        if key not in self._cache:
            extra = {"source": 27} if indicator == FORECAST else {}
            self._cache[key] = _fetch_all(f"country/{';'.join(codes)}/indicator/{indicator}",
                                          date=f"{start}:{end}", **extra)
        return self._cache[key]

    def forecast_edition(self) -> str:
        """Date of the current Global Economic Prospects data, e.g. '2026-06-11'."""
        if "edition" not in self._cache:
            # any covered economy works; "WLD" isn't in this source (meta comes back empty)
            r = _session.get(f"{API}/country/USA/indicator/{FORECAST}",
                             params={"format": "json", "source": 27, "per_page": 1}, timeout=TIMEOUT)
            r.raise_for_status()
            self._cache["edition"] = r.json()[0]["lastupdated"]
        return self._cache["edition"]

    # ---- tools -----------------------------------------------------------
    def search(self, query: str, n: int = 8) -> list[dict]:
        scores = self._bm25.scores(query)
        for idx, ind in enumerate(self.indicators):
            if ind["id"] in CORE:
                scores[idx] *= 1.5
        top = [i for i in scores.argsort()[::-1][:n] if scores[i] > 0]
        return [{"id": self.indicators[i]["id"], "name": self.indicators[i]["name"]} for i in top]

    def get(self, indicator: str, countries: list[str], start: int | None = None, end: int | None = None,
            order: str = "highest", n: int = 10) -> dict:
        if indicator not in self.by_id:
            raise ValueError(f"unknown World Bank indicator {indicator!r}: use search_data to find its id")
        meta = self.by_id[indicator]
        out = {"indicator": indicator, "name": meta["name"], "definition": meta["note"][:300],
               "notes": self._notes(indicator) + ["'latest' = most recent year with data for that "
                                                  "economy; years can differ across economies."]}
        if [c.lower() for c in countries] == ["all"]:
            return {**out, **self._rank(indicator, order, n)}
        codes = list(dict.fromkeys(self.country(c) for c in countries))[:20]
        end = end or date.today().year + (5 if indicator == FORECAST else 0)
        rows = self._observations(indicator, codes, start or 1960, end)
        series: dict[str, list[tuple[int, float]]] = {c: [] for c in codes}
        for r in rows:
            if r["value"] is not None and r["countryiso3code"] in series:
                series[r["countryiso3code"]].append((int(r["date"]), float(r["value"])))
        detailed = len(codes) <= 4
        out["economies"] = []
        for code in codes:
            obs = sorted(series[code])
            entry = {"economy": self.countries[code]["name"], "code": code}
            if not obs:
                entry["data"] = "no observations in this period"
            elif not detailed:
                entry["latest"] = {"year": obs[-1][0], "value": round(obs[-1][1], 3)}
                if start:
                    entry["first"] = {"year": obs[0][0], "value": round(obs[0][1], 3)}
            else:
                years, values = zip(*obs)
                hi = max(range(len(obs)), key=values.__getitem__)
                lo = min(range(len(obs)), key=values.__getitem__)
                step = max(1, len(obs) // 12)
                label = "series [year, value]" if step == 1 else "sampled [year, value] (subsample: may miss peaks)"
                entry.update({
                    "latest": {"year": years[-1], "value": round(values[-1], 3)},
                    "first": {"year": years[0], "value": round(values[0], 3)},
                    "max": {"year": years[hi], "value": round(values[hi], 3)},
                    "min": {"year": years[lo], "value": round(values[lo], 3)},
                    "observations": len(obs), "years_with_data": f"{years[0]}-{years[-1]}",
                    label: [[y, round(v, 3)] for y, v in obs[::step]][-13:],
                    "sparkline": sparkline(list(values)) if len(obs) > 2 else "",
                })
            out["economies"].append(entry)
        return out

    def _rank(self, indicator: str, order: str, n: int) -> dict:
        if indicator == FORECAST:  # rank this year's forecast, not each economy's furthest-out year
            rows = _fetch_all(f"country/all/indicator/{indicator}", source=27,
                              date=self.forecast_edition()[:4])
        else:
            rows = _fetch_all(f"country/all/indicator/{indicator}", mrnev=1)
        latest = [(r["countryiso3code"], int(r["date"]), float(r["value"])) for r in rows
                  if r["value"] is not None and r["countryiso3code"] in self.countries
                  and not self.countries[r["countryiso3code"]]["aggregate"]]
        latest.sort(key=lambda x: x[2], reverse=(order == "highest"))
        n = max(1, min(int(n), 25))
        world = next((r for r in rows if r["countryiso3code"] == "WLD" and r["value"] is not None), None)
        return {"order": order, "economies_with_data": len(latest),
                "world": {"year": int(world["date"]), "value": round(float(world["value"]), 3)} if world else None,
                "ranked": [{"economy": self.countries[c]["name"], "year": y, "value": round(v, 3)}
                           for c, y, v in latest[:n]]}


if __name__ == "__main__":
    wb = WorldBank()
    query = sys.argv[1] if len(sys.argv) > 1 else "GDP per capita PPP"
    hits = wb.search(query)
    print(json.dumps(hits, indent=1))
    if len(sys.argv) > 2:
        print(json.dumps(wb.get(hits[0]["id"], sys.argv[2:]), indent=1, ensure_ascii=False))
