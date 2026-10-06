"""
Long-run growth data for analyst.py: the Maddison Project Database (GDP per
capita and population back to year 1 where estimates exist) and the Penn World
Table (output, inputs and productivity since 1950). Local files, no network.

  search(query)                              -> variable ids, keyword-ranked
  get(variable, countries, start, end)       -> per-country stats computed here
  compare(variable, countries, start, end)   -> ratios over time, overtaking, divergence
  growth_accounting(country, start, end)     -> output-per-worker growth split into
                                                capital deepening, human capital, TFP
  catalog()                                  -> every variable with units + citations

Setup (once): download the two official Excel files by hand in a browser (their
host, dataverse.nl, turns away scripted downloads) into data/longrun/:
  Maddison Project Database 2023: https://dataverse.nl/api/access/datafile/421302
      (mpd2023_web.xlsx; landing page rug.nl/ggdc/historicaldevelopment/maddison)
  Penn World Table 11.0:          https://dataverse.nl/api/access/datafile/554105
      (pwt110.xlsx; landing page rug.nl/ggdc/productivity/pwt)
then run `python longrun.py --import`, which converts them once into compact
CSVs (maddison.csv, pwt.csv, meta.json) with the standard library — no
openpyxl or pandas needed. Afterwards `python longrun.py "South Korea" Ghana
1960 2022` prints a lookup, a comparison and a growth decomposition.
"""

import csv
import difflib
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from xml.etree.ElementTree import iterparse, parse

from bm25 import BM25
from fred import sparkline

DATA_DIR = Path("data/longrun")
# Fallback when mpd*.xlsx can't be fetched (dataverse.nl challenges scripts):
# https://ourworldindata.org/grapher/gdp-per-capita-maddison-project-database.csv?v=1&csvType=full&useColumnShortNames=true
OWID_MPD = "owid_maddison_gdppc.csv"

CITATIONS = {
    "mpd": "Maddison Project Database, version 2023: Bolt, Jutta and Jan Luiten van Zanden (2024), "
           "\"Maddison style estimates of the evolution of the world economy: A new 2023 update\", "
           "Journal of Economic Surveys. doi:10.1111/joes.12618. Data doi:10.34894/INZBF2 (CC BY 4.0).",
    "pwt": "Penn World Table, version 11.0: Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer "
           "(2015), \"The Next Generation of the Penn World Table\", American Economic Review 105(10), "
           "3150-3182; www.ggdc.net/pwt. Data doi:10.34894/FABVLR (CC BY 4.0).",
}

# id -> (column in the source file, short name, units, extra search words).
# PWT units are refined at import from the file's own Legend sheet (base years
# change between versions, so they are not hard-coded here).
MPD_VARS = {
    "mpd.gdppc": ("gdppc", "Real GDP per capita (Maddison)", "2011 international dollars (PPP)",
                  "income per person living standards long run history historical"),
    "mpd.pop": ("pop", "Population (Maddison)", "thousands of people", "population history"),
}
PWT_VARS = {
    "pwt.rgdpe": ("rgdpe", "Real GDP, expenditure side, at chained PPPs", "million US$ at chained PPPs",
                  "living standards cross country comparison level"),
    "pwt.rgdpo": ("rgdpo", "Real GDP, output side, at chained PPPs", "million US$ at chained PPPs",
                  "productive capacity production"),
    "pwt.rgdpna": ("rgdpna", "Real GDP at constant national prices", "million US$ at constant prices",
                   "growth over time national accounts"),
    "pwt.pop": ("pop", "Population (PWT)", "millions", "population"),
    "pwt.emp": ("emp", "Persons engaged (employment)", "millions", "workers employment labor force"),
    "pwt.avh": ("avh", "Average annual hours worked per person engaged", "hours", "hours worked"),
    "pwt.hc": ("hc", "Human capital index (years of schooling and returns to education)", "index",
               "education schooling human capital"),
    "pwt.cn": ("cn", "Capital stock at current PPPs", "million US$ at current PPPs", "capital stock"),
    "pwt.rnna": ("rnna", "Capital stock at constant national prices", "million US$ at constant prices",
                 "capital stock growth"),
    "pwt.ctfp": ("ctfp", "TFP level at current PPPs", "index, USA = 1",
                 "total factor productivity tfp level"),
    "pwt.rtfpna": ("rtfpna", "TFP at constant national prices", "index, reference year = 1",
                   "total factor productivity tfp growth"),
    "pwt.labsh": ("labsh", "Share of labour compensation in GDP", "share (0-1)", "labor share wages"),
    "pwt.csh_i": ("csh_i", "Share of gross capital formation (investment) in GDP", "share (0-1)",
                  "investment rate savings"),
}
# Ratios computed at load (both columns from PWT).
PWT_DERIVED = {
    "pwt.rgdpe_pc": ("rgdpe", "pop", "Real GDP per capita, expenditure side, at chained PPPs",
                     "US$ per person at chained PPPs", "income per person living standards"),
    "pwt.rgdpo_pw": ("rgdpo", "emp", "Real GDP per worker, output side, at chained PPPs",
                     "US$ per worker at chained PPPs", "labor productivity output per worker"),
    "pwt.rgdpna_pc": ("rgdpna", "pop", "Real GDP per capita at constant national prices",
                      "US$ per person at constant prices", "income per person growth"),
    "pwt.rgdpna_pw": ("rgdpna", "emp", "Real GDP per worker at constant national prices",
                      "US$ per worker at constant prices", "labor productivity growth"),
}

NOTES = {
    "mpd.": "Maddison values are in 2011 international dollars (PPP): not comparable with WDI's "
            "current-US$ figures, and not identical to WDI's PPP series (different base year and "
            "sources). Say 'in 2011 international dollars'.",
    "pwt.rgdpe": "rgdpe compares living standards ACROSS countries in a given year; for growth over time "
                 "within a country use rgdpna (national-accounts growth rates).",
    "pwt.rgdpo": "rgdpo compares productive capacity ACROSS countries in a given year; for growth over "
                 "time within a country use rgdpna.",
    "pwt.rgdpna": "rgdpna is for growth over time within a country; its levels are not meant for "
                  "cross-country comparison (use rgdpe or rgdpo for levels).",
    "pwt.ctfp": "ctfp is a cross-country level comparison (USA = 1) in each year; use rtfpna for TFP "
                "growth over time.",
    "pwt.labsh": "Labour shares for many low- and middle-income countries are imputed in PWT, not measured.",
}
PRE_1950_NOTE = ("Before 1950 Maddison estimates are sparse and uncertain (often benchmark years, "
                 "interpolated in between, with wide error margins): state the year, call them estimates.")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z ]", "", name.lower().replace("&", "and").replace("-", " ")).strip()


def _r(v: float) -> float:
    """Round for display: 1 decimal for large numbers, 4 significant digits otherwise."""
    if v == 0 or abs(v) >= 100:
        return round(v, 1)
    return round(v, max(0, 3 - int(math.floor(math.log10(abs(v))))))


def _cagr(v0: float, v1: float, years: int) -> float | None:
    if years <= 0 or v0 <= 0 or v1 <= 0:
        return None
    return round(((v1 / v0) ** (1 / years) - 1) * 100, 2)


# ---------------------------------------------------------------- xlsx import
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def xlsx_rows(path: Path, sheet: str):
    """Yields each row of one sheet as a list of str/float/None (stdlib only)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        strings = []
        if "xl/sharedStrings.xml" in names:
            with z.open("xl/sharedStrings.xml") as f:
                for _, el in iterparse(f):
                    if el.tag == _NS + "si":
                        strings.append("".join(t.text or "" for t in el.iter(_NS + "t")))
                        el.clear()
        with z.open("xl/workbook.xml") as f:
            sheets = {s.get("name"): s.get(_REL) for s in parse(f).getroot().iter(_NS + "sheet")}
        if sheet not in sheets:
            raise ValueError(f"{path.name} has no sheet {sheet!r} (sheets: {list(sheets)})")
        with z.open("xl/_rels/workbook.xml.rels") as f:
            rels = {r.get("Id"): r.get("Target") for r in parse(f).getroot()}
        target = rels[sheets[sheet]].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        with z.open(target) as f:
            for _, el in iterparse(f):
                if el.tag != _NS + "row":
                    continue
                row: list = []
                for c in el.iter(_NS + "c"):
                    i = _col_index(c.get("r", "")) if c.get("r") else len(row)
                    t, v = c.get("t"), c.find(_NS + "v")
                    if t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(_NS + "t"))
                    elif v is None or v.text is None or t == "e":
                        val = None
                    elif t == "s":
                        val = strings[int(v.text)]
                    elif t in ("str", "b"):
                        val = v.text
                    else:
                        val = float(v.text)
                    row.extend([None] * (i + 1 - len(row)))
                    row[i] = val
                el.clear()
                yield row


def _table(path: Path, sheet: str) -> tuple[list[str], list[list]]:
    rows = xlsx_rows(path, sheet)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    return header, [r + [None] * (len(header) - len(r)) for r in rows]


def _latest(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def import_raw(directory: Path = DATA_DIR) -> dict:
    """Converts the official xlsx files in `directory` into maddison.csv, pwt.csv
    and meta.json. Run once after downloading (or re-downloading) them."""
    old = json.loads((directory / "meta.json").read_text()) if (directory / "meta.json").exists() else {}
    meta = {"sources": {}}
    mpd = _latest(directory, "mpd*.xlsx")
    if mpd:
        header, rows = _table(mpd, "Full data")
        idx = {h: header.index(h) for h in ("countrycode", "country", "region", "year", "gdppc", "pop")}
        with open(directory / "maddison.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "country", "region", "year", "gdppc", "pop"])
            for r in rows:
                if r[idx["countrycode"]] and r[idx["year"]] is not None:
                    w.writerow([r[idx["countrycode"]], r[idx["country"]], r[idx["region"]],
                                int(r[idx["year"]]), *("" if r[idx[k]] is None else r[idx[k]]
                                                       for k in ("gdppc", "pop"))])
        edition = re.search(r"(\d{4})", mpd.name)
        meta["sources"]["mpd"] = {"file": mpd.name, "edition": edition.group(1) if edition else "?"}
    elif (directory / OWID_MPD).exists():
        # Our World in Data's republication of the same 2023 series: GDP per capita
        # only (no population or region); rows without an ISO3 code are regional sums.
        with open(directory / OWID_MPD, newline="") as src, open(directory / "maddison.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "country", "region", "year", "gdppc"])
            for r in csv.DictReader(src):
                if r["code"] and r["gdp_per_capita"]:
                    w.writerow([r["code"], r["entity"], "", int(r["year"]), r["gdp_per_capita"]])
        meta["sources"]["mpd"] = {"file": OWID_MPD, "edition": "2023", "via": "Our World in Data "
                                  "(ourworldindata.org/grapher/gdp-per-capita-maddison-project-database)"}
    pwt = _latest(directory, "pwt*.xlsx")
    if pwt:
        header, rows = _table(pwt, "Data")
        cols = ["countrycode", "country", "year"] + [c for c, *_ in PWT_VARS.values() if c in header]
        idx = [header.index(c) for c in cols]
        with open(directory / "pwt.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "country", "year"] + cols[3:])
            for r in rows:
                if r[idx[0]] and r[idx[2]] is not None:
                    vals = [r[i] for i in idx]
                    vals[2] = int(vals[2])
                    w.writerow(["" if v is None else v for v in vals])
        legend = {}
        try:
            lh, lrows = _table(pwt, "Legend")
            for r in lrows:
                if r[0] and len(r) > 1 and r[1]:
                    legend[str(r[0]).strip()] = " ".join(str(r[1]).split())
        except (ValueError, StopIteration):
            pass
        version = re.search(r"pwt(\d+)", pwt.name)
        v = version.group(1) if version else "?"
        meta["sources"]["pwt"] = {"file": pwt.name, "version": f"{v[:-1]}.{v[-1]}" if len(v) > 1 else v,
                                  "legend": legend}
    if "pwt" not in meta["sources"] and old.get("sources", {}).get("pwt", {}).get("via"):
        meta["sources"]["pwt"] = old["sources"]["pwt"]  # pwt.csv from import_fred_pwt: keep it
    if not meta["sources"]:
        raise FileNotFoundError(f"no mpd*.xlsx, {OWID_MPD} or pwt*.xlsx in {directory}/ — see longrun.py's docstring")
    (directory / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


# PWT column -> FRED series-id prefix. FRED republishes the PWT release as one
# series per country and variable, e.g. RGDPNAKRA666NRUG = rgdpna for KOR
# ("NRUG" = from the University of Groningen); the 3-digit unit code varies.
FRED_PWT = {"rgdpe": "RGDPES", "rgdpo": "RGDPOS", "rgdpna": "RGDPNA", "pop": "POPTTL", "emp": "EMPENG",
            "avh": "AVHWPE", "hc": "HCIYIS", "cn": "CKSPPP", "rnna": "RKNANP", "ctfp": "CTFPPP",
            "rtfpna": "RTFPNA", "labsh": "LABSHP", "csh_i": "CSHICP"}
FRED_PWT_PAUSE = 0.55  # seconds between requests: FRED allows ~120 a minute


def _iso2_to_iso3() -> dict[str, str]:
    """FRED's country code is ISO2 + "A" (annual): KRA = Korea, not ISO3. One World
    Bank request maps them; Taiwan isn't in the World Bank's list."""
    import requests
    r = requests.get("https://api.worldbank.org/v2/country", params={"format": "json", "per_page": 400}, timeout=30)
    r.raise_for_status()
    return {c["iso2Code"]: c["id"] for c in r.json()[1]} | {"TW": "TWN"}


def fred_pwt_id(series_id: str, iso3: dict[str, str]) -> tuple[str, str] | None:
    """(ISO3, PWT column) for a FRED PWT series id, e.g. RGDPNAKRA666NRUG ->
    ("KOR", "rgdpna"); None if it isn't one of FRED_PWT's variables. Raises
    KeyError for an unknown country code."""
    m = re.fullmatch(r"([A-Z]{6})([A-Z]{2})A\d{3}NRUG", series_id)
    prefixes = {v: k for k, v in FRED_PWT.items()}
    if not m or m.group(1) not in prefixes:
        return None
    return iso3[m.group(2)], prefixes[m.group(1)]


def import_fred_pwt(directory: Path = DATA_DIR, countries: list[str] | None = None, verbose: bool = True) -> dict:
    """Builds pwt.csv from FRED's copy of the Penn World Table (for when the
    official xlsx can't be downloaded: dataverse.nl challenges scripts and, at
    times, browsers). One request per country and variable — ~1,900 for all
    167 countries FRED carries, ~20 minutes at FRED's rate limit."""
    import time
    import fred
    base = "https://api.stlouisfed.org/fred"
    release = fred._get(f"{base}/series/release", series_id="RGDPNAKRA666NRUG")["releases"][0]
    wanted, names, offset = {}, {}, 0
    iso3, unmapped = _iso2_to_iso3(), set()
    while True:
        page = fred._get(f"{base}/release/series", release_id=release["id"], limit=1000, offset=offset)
        for s in page["seriess"]:
            try:
                parsed = fred_pwt_id(s["id"], iso3)
            except KeyError as e:
                unmapped.add(e.args[0])
                continue
            if parsed and (countries is None or parsed[0] in countries):
                code = parsed[0]
                wanted[parsed] = s["id"]
                names.setdefault(code, s["title"].rsplit(" for ", 1)[-1])
        offset += 1000
        if offset >= page["count"]:
            break
    data: dict[tuple[str, int], dict[str, float]] = {}
    for i, ((code, col), sid) in enumerate(sorted(wanted.items())):
        for obs in fred._get(fred.FRED_URL, series_id=sid)["observations"]:
            if obs["value"] not in (".", ""):
                data.setdefault((code, int(obs["date"][:4])), {})[col] = float(obs["value"])
        if verbose and i % 100 == 0:
            print(f"  {i}/{len(wanted)} series", flush=True)
        time.sleep(FRED_PWT_PAUSE)
    cols = list(FRED_PWT)
    with open(directory / "pwt.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "country", "year"] + cols)
        for (code, year), row in sorted(data.items()):
            w.writerow([code, names[code], year] + [row.get(c, "") for c in cols])
    meta = json.loads((directory / "meta.json").read_text()) if (directory / "meta.json").exists() else {"sources": {}}
    version = re.search(r"\d+(\.\d+)?", release["name"])
    meta["sources"]["pwt"] = {"file": "pwt.csv", "version": version.group() if version else "?",
                              "via": f"FRED, St. Louis Fed (release \"{release['name']}\", {len(wanted)} series, "
                                     f"{len(names)} countries)", "legend": {}}
    (directory / "meta.json").write_text(json.dumps(meta, indent=1))
    return {"series": len(wanted), "countries": len(names), "rows": len(data), "release": release["name"],
            "skipped_unknown_codes": sorted(unmapped)}


# ---------------------------------------------------------------- data access
def _num(s: str) -> float | None:
    return float(s) if s not in ("", None) else None


class LongRun:
    def __init__(self, directory: Path = DATA_DIR):
        directory = Path(directory)
        if not (directory / "meta.json").exists():
            if _latest(directory, "mpd*.xlsx") or _latest(directory, "pwt*.xlsx") or (directory / OWID_MPD).exists():
                import_raw(directory)
            else:
                raise FileNotFoundError(f"long-run data not set up: download the Maddison and PWT Excel "
                                        f"files into {directory}/ (see longrun.py's docstring)")
        self.meta = json.loads((directory / "meta.json").read_text())
        # series[var_id][code] = sorted [(year, value)]
        self.series: dict[str, dict[str, list[tuple[int, float]]]] = {}
        self.names: dict[str, str] = {}  # code -> country name
        self.regions: dict[str, str] = {}
        if (directory / "maddison.csv").exists():
            self._load(directory / "maddison.csv", {k: v[0] for k, v in MPD_VARS.items()}, region=True)
        if (directory / "pwt.csv").exists():
            self._load(directory / "pwt.csv", {k: v[0] for k, v in PWT_VARS.items()})
            for vid, (num, den, *_rest) in PWT_DERIVED.items():
                a, b = self.series.get(f"pwt.{num}", {}), dict(
                    (c, dict(s)) for c, s in self.series.get(f"pwt.{den}", {}).items())
                # million US$ / million people = US$ per person
                self.series[vid] = {c: [(y, v / b[c][y]) for y, v in s if b.get(c, {}).get(y)]
                                    for c, s in a.items()}
        self.vars = self._catalog()
        self._bm25 = BM25([f"{v['id']} {v['name']} {v['name']} {v['search']}" for v in self.vars.values()])
        self._name_index = self._build_names()

    def _load(self, path: Path, columns: dict[str, str], region: bool = False):
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            present = {vid: col for vid, col in columns.items() if col in reader.fieldnames}
            for vid in present:
                self.series.setdefault(vid, {})
            for row in reader:
                code, year = row["code"], int(row["year"])
                self.names.setdefault(code, row["country"])
                if region and row.get("region"):
                    self.regions.setdefault(code, row["region"])
                for vid, col in present.items():
                    v = _num(row[col])
                    if v is not None:
                        self.series[vid].setdefault(code, []).append((year, v))
        for vid in columns:
            for s in self.series.get(vid, {}).values():
                s.sort()

    def _catalog(self) -> dict[str, dict]:
        legend = self.meta["sources"].get("pwt", {}).get("legend", {})
        out = {}
        for vid, (col, name, units, words) in {**MPD_VARS, **PWT_VARS}.items():
            if vid in self.series:
                entry = {"id": vid, "name": name, "units": units, "source": vid.split(".")[0], "search": words}
                if col in legend and vid.startswith("pwt."):
                    entry["definition"] = legend[col][:200]
                    m = re.search(r"\(in ([^)]+)\)", legend[col])
                    if m:
                        entry["units"] = m.group(1)
                out[vid] = entry
        for vid, (num, den, name, units, words) in PWT_DERIVED.items():
            if vid in self.series:
                base = out.get(f"pwt.{num}", {}).get("units", "")
                m = re.search(r"(\d{4})\s*US\$", base)  # carry the base year over from the legend
                if m:
                    units = units.replace("US$", f"{m.group(1)} US$")
                out[vid] = {"id": vid, "name": name, "units": units, "source": "pwt",
                            "search": words, "definition": f"pwt.{num} / pwt.{den}, computed here"}
        return out

    def _build_names(self) -> dict[str, str]:
        index = {}
        for code, name in self.names.items():
            index[_norm(name)] = code
            index.setdefault(_norm(name.split(",")[0]), code)
        try:  # reuse the World Bank module's aliases and its cached country list (no network)
            import worldbank
            for alias, code in worldbank.ALIASES.items():
                if code in self.names:
                    index.setdefault(alias, code)
            countries = worldbank.CATALOG_DIR / "countries.json"
            if countries.exists():
                for c in json.loads(countries.read_text()):
                    if c["id"] in self.names:
                        index.setdefault(_norm(c["name"]), c["id"])
                        index.setdefault(_norm(c["name"].split(",")[0]), c["id"])
        except ImportError:
            pass
        for alias, code in {"south korea": "KOR", "korea": "KOR", "north korea": "PRK", "russia": "RUS",
                            "ussr": "SUN", "soviet union": "SUN", "uk": "GBR", "britain": "GBR",
                            "us": "USA", "usa": "USA", "america": "USA", "united states": "USA",
                            "vietnam": "VNM", "iran": "IRN", "taiwan": "TWN"}.items():
            if code in self.names:
                index.setdefault(alias, code)
        return index

    def country(self, name: str) -> str:
        s = name.strip()
        if s.upper() in self.names:
            return s.upper()
        key = _norm(s)
        if key == "congo":
            raise ValueError("'Congo' is ambiguous: Congo, Dem. Rep. (COD) or Congo, Rep. (COG)")
        if key in self._name_index:
            return self._name_index[key]
        close = difflib.get_close_matches(key, self._name_index, n=1, cutoff=0.85)
        if close:
            return self._name_index[close[0]]
        raise ValueError(f"no long-run data for {name!r}")

    def _var(self, variable: str) -> str:
        v = variable.strip().lower()
        if v in self.vars:
            return v
        matches = [vid for vid in self.vars if vid.split(".", 1)[1] == v]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError(f"{variable!r} is ambiguous: {matches}")
        raise ValueError(f"unknown long-run variable {variable!r}: use search to find its id")

    def _notes(self, vid: str, start: int | None) -> list[str]:
        notes = [n for key, n in NOTES.items() if vid.startswith(key)]
        if vid.startswith("mpd.") and (start is None or start < 1950):
            notes.append(PRE_1950_NOTE)
        return notes

    def _source(self, vid: str) -> str:
        src = vid.split(".")[0]
        info = self.meta["sources"].get(src, {})
        via = f" Via {info['via']}." if info.get("via") else ""
        return f"{CITATIONS[src]}{via} File: {info.get('file', '?')}."

    def _obs(self, vid: str, code: str, start: int | None, end: int | None) -> list[tuple[int, float]]:
        return [(y, v) for y, v in self.series[vid].get(code, [])
                if (start is None or y >= start) and (end is None or y <= end)]

    # ---- tools -----------------------------------------------------------
    def catalog(self) -> dict:
        return {"variables": [{k: v[k] for k in ("id", "name", "units")} for v in self.vars.values()],
                "sources": {src: self._source(f"{src}.") for src in self.meta["sources"]}}

    def search(self, query: str, n: int = 6) -> list[dict]:
        scores = self._bm25.scores(query)
        ids = list(self.vars)
        top = [i for i in scores.argsort()[::-1][:n] if scores[i] > 0]
        return [{"id": ids[i], "name": self.vars[ids[i]]["name"], "units": self.vars[ids[i]]["units"]}
                for i in top]

    def get(self, variable: str, countries: list[str], start: int | None = None,
            end: int | None = None) -> dict:
        vid = self._var(variable)
        meta = self.vars[vid]
        out = {"variable": vid, "name": meta["name"], "units": meta["units"],
               "notes": self._notes(vid, start), "source": self._source(vid)}
        codes = list(dict.fromkeys(self.country(c) for c in countries))[:20]
        detailed = len(codes) <= 4
        out["countries"] = []
        for code in codes:
            obs = self._obs(vid, code, start, end)
            entry = {"country": self.names[code], "code": code}
            if not obs:
                entry["data"] = "no observations in this period"
                out["countries"].append(entry)
                continue
            (y0, v0), (y1, v1) = obs[0], obs[-1]
            entry.update({"first": {"year": y0, "value": _r(v0)}, "last": {"year": y1, "value": _r(v1)},
                          "growth_per_year_pct": _cagr(v0, v1, y1 - y0),
                          "growth_label": f"compound annual growth {y0}-{y1}"})
            if start is not None and y0 > start:
                entry["no_data_before"] = y0
            if detailed:
                years, values = zip(*obs)
                hi = max(range(len(obs)), key=values.__getitem__)
                lo = min(range(len(obs)), key=values.__getitem__)
                gaps = [(a, b) for a, b in zip(years, years[1:]) if b - a > 1]
                step = max(1, len(obs) // 12)
                label = "series [year, value]" if step == 1 else "sampled [year, value]"
                entry.update({
                    "peak": {"year": years[hi], "value": _r(values[hi])},
                    "trough": {"year": years[lo], "value": _r(values[lo])},
                    "observations": len(obs),
                    "years_with_data": f"{years[0]}-{years[-1]}" + (f", {len(gaps)} gaps (e.g. "
                                       f"{gaps[0][0]}->{gaps[0][1]})" if gaps else ""),
                    label: [[y, _r(v)] for y, v in obs[::step]][-13:] + (
                        [[years[-1], _r(values[-1])]] if (len(obs) - 1) % step else []),
                    "sparkline": sparkline(list(values)) if len(obs) > 2 else "",
                })
                if vid.startswith("mpd."):
                    entry["observations_before_1950"] = sum(1 for y in years if y < 1950)
            out["countries"].append(entry)
        return out

    def compare(self, variable: str, countries: list[str], start: int | None = None,
                end: int | None = None) -> dict:
        """Each country vs the FIRST one: ratio over time, overtaking years, divergence."""
        vid = self._var(variable)
        codes = list(dict.fromkeys(self.country(c) for c in countries))[:6]
        if len(codes) < 2:
            raise ValueError("compare needs at least two different countries")
        base = dict(self._obs(vid, codes[0], start, end))
        out = {"variable": vid, "name": self.vars[vid]["name"], "units": self.vars[vid]["units"],
               "base": self.names[codes[0]], "notes": self._notes(vid, start), "source": self._source(vid),
               "comparisons": []}
        if vid in ("pwt.rgdpna", "pwt.rgdpna_pc", "pwt.rgdpna_pw", "pwt.rnna", "pwt.rtfpna"):
            out["notes"].insert(0, "Constant-national-price levels are not comparable across countries: "
                                   "compare growth rates, not ratios (use rgdpe/rgdpo/mpd.gdppc for levels).")
        for code in codes[1:]:
            other = dict(self._obs(vid, code, start, end))
            years = sorted(set(base) & set(other))
            name, bname = self.names[code], self.names[codes[0]]
            comp = {"country": name}
            if not years:
                comp["data"] = "no common years with data"
                out["comparisons"].append(comp)
                continue
            ratio = [(y, other[y] / base[y]) for y in years if base[y] > 0]
            if not ratio:
                comp["data"] = "no common years with data"
                out["comparisons"].append(comp)
                continue
            y0, y1 = years[0], years[-1]
            hi = max(ratio, key=lambda x: x[1])
            lo = min(ratio, key=lambda x: x[1])
            step = max(1, len(ratio) // 10)
            crossings = []
            for (ya, ra), (yb, rb) in zip(ratio, ratio[1:]):
                if (ra - 1) * (rb - 1) < 0 or (ra == 1 and rb != 1):
                    leader, lagger = (name, bname) if rb > 1 else (bname, name)
                    crossings.append(f"{yb}: {leader} passed {lagger}" + (f" (no data {ya + 1}-{yb - 1})"
                                                                          if yb - ya > 1 else ""))
            within = [y for y, r in ratio if 0.8 <= r <= 1.25]
            comp.update({
                "ratio_label": f"{name} / {bname}",
                "common_years": f"{y0}-{y1} ({len(years)} years)",
                "ratio_first": {"year": ratio[0][0], "value": round(ratio[0][1], 3)},
                "ratio_last": {"year": ratio[-1][0], "value": round(ratio[-1][1], 3)},
                "ratio_max": {"year": hi[0], "value": round(hi[1], 3)},
                "ratio_min": {"year": lo[0], "value": round(lo[1], 3)},
                "ratio_sampled [year, ratio]": [[y, round(r, 3)] for y, r in ratio[::step]][-11:],
                "crossings": crossings or ["none: the same country led in every common year"],
                "growth_per_year_pct": {bname: _cagr(base[y0], base[y1], y1 - y0),
                                        name: _cagr(other[y0], other[y1], y1 - y0)},
            })
            last_r = ratio[-1][1]
            if within and not 0.8 <= last_r <= 1.25:
                comp["divergence"] = (f"last year within 25% of each other: {within[-1]}; "
                                      f"ratio since then moved to {round(last_r, 2)} by {ratio[-1][0]}")
            elif not within:
                comp["divergence"] = "never within 25% of each other in the common years"
            else:
                comp["divergence"] = f"still within 25% of each other in {ratio[-1][0]}"
            out["comparisons"].append(comp)
        return out

    def growth_accounting(self, country: str, start: int | None = None, end: int | None = None) -> dict:
        """Splits growth of real GDP per worker (rgdpna/emp) into capital deepening
        (alpha * growth of rnna/emp), human capital ((1-alpha) * growth of hc) and a
        TFP residual, with alpha = 1 - average labour share. Rates are % per year
        (log points); contributions add up to output-per-worker growth exactly."""
        needed = ("pwt.rgdpna", "pwt.rnna", "pwt.emp", "pwt.hc", "pwt.labsh")
        if any(v not in self.series for v in needed):
            raise ValueError("growth accounting needs the Penn World Table (pwt*.xlsx) — not set up")
        code = self.country(country)
        data = {v: dict(self._obs(v, code, start, end)) for v in needed}
        years = sorted(set.intersection(*(set(d) for d in data.values())))
        if len(years) < 2:
            raise ValueError(f"PWT has no complete output/capital/employment/schooling/labour-share data "
                             f"for {self.names[code]} in this period")
        y0, y1 = years[0], years[-1]
        t = y1 - y0
        Y, K, L, H = (data[v] for v in needed[:4])

        def g(a, b):  # % per year, log points
            return math.log(b / a) / t * 100

        g_y = g(Y[y0] / L[y0], Y[y1] / L[y1])
        g_k = g(K[y0] / L[y0], K[y1] / L[y1])
        g_h = g(H[y0], H[y1])
        alpha = 1 - sum(data["pwt.labsh"][y] for y in years) / len(years)
        cap, hum = alpha * g_k, (1 - alpha) * g_h
        tfp = g_y - cap - hum
        out = {
            "country": self.names[code], "period": f"{y0}-{y1}",
            "method": "y = k^a (h*A)^(1-a) per worker; a = 1 - average PWT labour share; national-accounts "
                      "constant prices (rgdpna, rnna, emp, hc); TFP is the residual",
            "capital_share_alpha": round(alpha, 3),
            "growth_pct_per_year": {
                "output_per_worker": round(g_y, 2),
                "capital_deepening_contribution": round(cap, 2),
                "human_capital_contribution": round(hum, 2),
                "tfp_contribution": round(tfp, 2),
            },
            "share_of_output_per_worker_growth": ({
                "capital_deepening": round(cap / g_y, 2), "human_capital": round(hum / g_y, 2),
                "tfp": round(tfp / g_y, 2)} if abs(g_y) > 0.1 else "output per worker barely grew: shares "
                                                                   "not meaningful"),
            "inputs_growth_pct_per_year": {"capital_per_worker": round(g_k, 2), "human_capital_index": round(g_h, 2)},
            "notes": ["Rates are log-point growth per year (close to % per year); contributions add up.",
                      "TFP is a residual: it absorbs measurement error in capital and schooling, not only "
                      "technology. PWT capital stocks are built from investment flows with an assumed "
                      "starting stock, so the first decades are least reliable.",
                      NOTES["pwt.labsh"]],
            "source": self._source("pwt."),
        }
        if start is not None and y0 > start or end is not None and y1 < end:
            out["notes"].insert(0, f"Complete data only for {y0}-{y1}; the requested period was "
                                   f"{start or 'start'}-{end or 'end'}.")
        tf = dict(self._obs("pwt.rtfpna", code, y0, y1)) if "pwt.rtfpna" in self.series else {}
        if y0 in tf and y1 in tf and tf[y0] > 0 and tf[y1] > 0:
            out["pwt_own_tfp_growth_pct_per_year"] = round(g(tf[y0], tf[y1]), 2)
            out["notes"].append("pwt_own_tfp_growth is PWT's rtfpna (capital services, Tornqvist weights, "
                                "total not per worker) — a cross-check; it need not match the residual.")
        return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["--import"]:
        print(json.dumps(import_raw(), indent=1)[:2000])
        sys.exit()
    if sys.argv[1:2] == ["--import-fred-pwt"]:  # optional ISO3 codes after it: a partial import
        print(import_fred_pwt(countries=sys.argv[2:] or None))
        sys.exit()
    years = [int(a) for a in sys.argv[1:] if re.fullmatch(r"-?\d{1,4}", a)]
    names = [a for a in sys.argv[1:] if not re.fullmatch(r"-?\d{1,4}", a)] or ["South Korea", "Ghana"]
    start, end = (years + [None, None])[:2] if years else (1950, None)
    try:
        lr = LongRun()
    except FileNotFoundError as e:
        sys.exit(str(e))
    print(json.dumps(lr.get("mpd.gdppc", names, start, end), indent=1, ensure_ascii=False))
    if len(names) > 1:
        print(json.dumps(lr.compare("mpd.gdppc", names, start, end), indent=1, ensure_ascii=False))
    if "pwt.rgdpna" in lr.series:
        for n in names:
            try:
                print(json.dumps(lr.growth_accounting(n, start, end), indent=1, ensure_ascii=False))
            except ValueError as e:
                print(f"{n}: {e}")
