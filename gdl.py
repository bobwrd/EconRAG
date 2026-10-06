"""
Subnational human development for analyst.py: Global Data Lab's Subnational
HDI database (1,805 regions in 188 countries, 1990-2023) — HDI and its health,
education, and income components, by sex, with population. Local file, no network.

  search(query)                                  -> metric ids
  get(metric, countries, start, end, order, n)   -> per country: national value and every
                                                    region (highest/lowest, spread, change
                                                    since `start`); countries ["all"] ranks
                                                    regions worldwide

Setup: downloads need a free Global Data Lab account, so save the CSV by hand
(globaldatalab.org/shdi/download/ -> all countries, years, indicators -> CSV)
into data/gdl/. Any file name; the newest "*.csv" there is used.
"""

import csv
import json
import math
import re
import sys
from pathlib import Path

from bm25 import BM25

DATA_DIR = Path("data/gdl")
REGIONS_SHOWN = 6  # top and bottom, when a country has more than 2x this many regions
# id -> (column, name, units, extra search words)
METRICS = {
    "shdi": ("shdi", "Subnational Human Development Index", "index 0-1", "hdi human development"),
    "healthindex": ("healthindex", "Health index (HDI component)", "index 0-1", "health"),
    "edindex": ("edindex", "Education index (HDI component)", "index 0-1", "education"),
    "incindex": ("incindex", "Income index (HDI component)", "index 0-1", "income"),
    "lifexp": ("lifexp", "Life expectancy at birth", "years", "life expectancy mortality health"),
    "esch": ("esch", "Expected years of schooling", "years", "education schooling enrollment children"),
    "msch": ("msch", "Mean years of schooling (adults 25+)", "years", "education schooling attainment adults"),
    "gnic": ("lgnic", "GNI per capita", "2021 PPP dollars", "income per person gni gdp poverty rich poor"),
    "pop": ("pop", "Population", "thousands", "population people"),
    "sgdi": ("sgdi", "Subnational Gender Development Index (female HDI / male HDI)", "ratio",
             "gender women female equality"),
}
SEXES = {"f": "female", "m": "male"}
ALIASES = {"hdi": "shdi", "gni": "gnic", "income": "gnic", "population": "pop", "life_expectancy": "lifexp",
           "gdi": "sgdi"}
NOTE = ("Global Data Lab estimates: regional values are modeled from household surveys (mostly DHS) and "
        "interpolated between survey years, scaled to match national UNDP figures; regions follow survey "
        "boundaries (e.g. Kenya's 8 former provinces, not its 47 counties). Income = GNI per capita in 2021 "
        "PPP dollars. State the year.")


def _num(v: str) -> float | None:
    return float(v) if v not in ("", None) else None


class GDL:
    def __init__(self, directory: Path = DATA_DIR):
        files = sorted(Path(directory).glob("*.csv"), key=lambda p: p.stat().st_mtime)
        if not files:
            raise FileNotFoundError(f"Global Data Lab file not set up: save the Subnational HDI CSV into "
                                    f"{directory}/ (see gdl.py's docstring)")
        self.file = files[-1].name
        version = re.search(r"v(\d+(?:\.\d+)?)", self.file)
        self.version = version.group(1) if version else "?"
        # rows[(iso3, gdlcode)] = {year: row}; national rows have level "National"
        self.rows: dict[tuple[str, str], dict[int, dict]] = {}
        self.region_name: dict[str, str] = {}
        self.country_name: dict[str, str] = {}
        self.national_code: dict[str, str] = {}
        with open(files[-1], newline="") as f:
            for r in csv.DictReader(f):
                iso3, code = r["isocode3"], r["gdlcode"]
                self.rows.setdefault((iso3, code), {})[int(r["year"])] = r
                self.region_name[code] = r["region"]
                self.country_name[iso3] = r["country"]
                if r["level"] == "National":
                    self.national_code[iso3] = code
        self._bm25 = BM25([f"{k} {name} {words}" for k, (_, name, _, words) in METRICS.items()])

    # ---- lookups ----------------------------------------------------------
    def country(self, name: str) -> str:
        key = name.strip().upper()
        if key in self.country_name:
            return key
        import worldbank
        try:
            code = worldbank.WorldBank().country(name)  # aliases, typos; cached catalog
        except ValueError as e:
            raise ValueError(f"unknown country {name!r}: {e}") from None
        if code not in self.country_name:
            raise ValueError(f"Global Data Lab has no data for {name!r}")
        return code

    def _metric(self, metric: str) -> tuple[str, str | None]:
        """'lifexp' -> ('lifexp', None); 'lifexpf' / 'lifexp_female' -> ('lifexp', 'f')."""
        m = metric.strip().lower().replace("_female", "f").replace("_male", "m")
        m = ALIASES.get(m, m)
        if m in METRICS:
            return m, None
        base = ALIASES.get(m[:-1], m[:-1])
        if base in METRICS and m[-1] in SEXES and base not in ("pop", "sgdi"):
            return base, m[-1]
        raise ValueError(f"unknown metric {metric!r}; use one of {', '.join(METRICS)} "
                         f"(append f/m for female/male, e.g. lifexpf)")

    def _value(self, row: dict, metric: str, sex: str | None) -> float | None:
        col = METRICS[metric][0] + (sex or "")
        v = _num(row.get(col, ""))
        if v is None:
            return None
        if metric == "gnic":
            return round(math.exp(v))
        return v

    def _series(self, key: tuple[str, str], metric: str, sex: str | None) -> dict[int, float]:
        out = {}
        for year, row in self.rows.get(key, {}).items():
            v = self._value(row, metric, sex)
            if v is not None:
                out[year] = v
        return out

    # ---- tools ------------------------------------------------------------
    def search(self, query: str, n: int = 6) -> list[dict]:
        ids, scores = list(METRICS), self._bm25.scores(query)
        top = sorted(range(len(ids)), key=lambda i: -scores[i])[:n]
        return [{"id": ids[i], "name": METRICS[ids[i]][1], "units": METRICS[ids[i]][2]} for i in top if scores[i] > 0]

    def get(self, metric: str, countries: list[str], start: int | None = None, end: int | None = None,
            order: str = "highest", n: int = 10) -> dict:
        metric, sex = self._metric(metric)
        out = {"metric": metric + (sex or ""), "name": METRICS[metric][1] + (f", {SEXES[sex]}" if sex else ""),
               "units": METRICS[metric][2], "notes": [NOTE],
               "source": f"Global Data Lab, Subnational HDI Database v{self.version} (globaldatalab.org/shdi)."}
        if [c.lower() for c in countries] == ["all"]:
            out.update(self._rank(metric, sex, end, order, n))
            return out
        out["countries"] = [self._country(self.country(c), metric, sex, start, end) for c in countries[:4]]
        return out

    def _country(self, iso3: str, metric: str, sex: str | None, start: int | None, end: int | None) -> dict:
        nat = self._series((iso3, self.national_code.get(iso3, "")), metric, sex)
        regions = {code: self._series((c, code), metric, sex) for (c, code) in self.rows
                   if c == iso3 and code != self.national_code.get(iso3)}
        years = sorted(set(nat) | {y for s in regions.values() for y in s})
        if not years:
            return {"country": self.country_name[iso3], "data": "no values for this metric"}
        year = max(y for y in years if end is None or y <= end) if any(end is None or y <= end for y in years) \
            else years[0]
        entry = {"country": self.country_name[iso3], "year": year,
                 "national": nat.get(year), "national_first": None}
        if nat:
            y0 = min(y for y in nat if start is None or y >= start) if any(start is None or y >= start for y in nat) \
                else min(nat)
            entry["national_first"] = {"year": y0, "value": nat[y0]}
        rows = sorted(((self.region_name[code], s[year]) for code, s in regions.items() if year in s),
                      key=lambda r: -r[1])
        if not rows:
            entry["regions"] = "no regional values for this year"
            return entry
        values = [v for _, v in rows]
        entry.update({
            "n_regions": len(rows),
            "highest": {"region": rows[0][0], "value": rows[0][1]},
            "lowest": {"region": rows[-1][0], "value": rows[-1][1]},
            "spread_max_minus_min": round(values[0] - values[-1], 3),
            "ratio_max_to_min": round(values[0] / values[-1], 2) if values[-1] > 0 else None,
        })
        shown = rows if len(rows) <= 2 * REGIONS_SHOWN else \
            rows[:REGIONS_SHOWN] + [("...", f"{len(rows) - 2 * REGIONS_SHOWN} more")] + rows[-REGIONS_SHOWN:]
        entry["regions [name, value] (highest first)"] = [list(r) for r in shown]
        if start is not None:  # who gained most since `start`
            changes = sorted(((self.region_name[code], round(s[year] - s[start], 3))
                              for code, s in regions.items() if year in s and start in s), key=lambda r: -r[1])
            if changes:
                entry[f"change_{start}_{year}"] = {"largest_gain": list(changes[0]),
                                                   "smallest_gain": list(changes[-1])}
        return entry

    def _rank(self, metric: str, sex: str | None, end: int | None, order: str, n: int) -> dict:
        latest = []
        for (iso3, code), years in self.rows.items():
            if code == self.national_code.get(iso3):
                continue
            s = {y: v for y, v in self._series((iso3, code), metric, sex).items() if end is None or y <= end}
            if s:
                y = max(s)
                latest.append((s[y], self.region_name[code], self.country_name[iso3], y))
        latest.sort(key=lambda r: -r[0] if order == "highest" else r[0])
        n = max(1, min(n, 25))
        return {"ranked": [{"region": r, "country": c, "value": v, "year": y} for v, r, c, y in latest[:n]],
                "regions_with_data": len(latest), "order": order}


if __name__ == "__main__":
    args = sys.argv[1:] or ["shdi", "Kenya"]
    print(json.dumps(GDL().get(args[0], args[1:] or ["all"]), indent=1, ensure_ascii=False))
