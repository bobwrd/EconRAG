"""
Demographic and Health Surveys (DHS Program STATcompiler API) for analyst.py:
~3,300 survey indicators (child mortality, nutrition, fertility, family
planning, maternal care, vaccination, education, household services, HIV,
women's empowerment) for ~90 low- and middle-income countries, one value per
survey (every ~5 years), plus regional values. Free, no key.

  search(query)                                   -> indicator ids, keyword-ranked
  get(indicator, countries, start, end)           -> per-country survey history, computed here
  get(indicator, [country], regions=True)         -> + latest survey's regional values, spread

Headline values: the API's default (no `breakdown`) returns only national
totals (IsTotal=1); where an indicator has several reference periods (e.g.
mortality over the five or ten years before the survey) exactly one row per
survey has IsPreferred=1, and that is the headline. Regional rows come from
breakdown=subnational, again IsPreferred=1 (for mortality that is the
ten-year period, so the national value shown beside them is the ten-year one).

The indicator and country lists are fetched once into data/dhs/ (~1.5 MB);
delete that folder to refresh them. Values are fetched live and cached in
memory for the session. `python dhs.py "under-5 mortality" Kenya [--regions]`
prints a search and a lookup.
"""

import difflib
import json
import re
import sys
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bm25 import BM25
from worldbank import ALIASES, _norm

API = "https://api.dhsprogram.com/rest/dhs"
CATALOG_DIR = Path("data/dhs")
WB_COUNTRIES = Path("data/worldbank/countries.json")
TIMEOUT = 30
MAX_COUNTRIES = 10

_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))))

# The indicators most questions need; ranked above lookalikes in search (the
# catalog has e.g. 25 "married women currently using ..." variants).
CORE = {
    "CM_ECMR_C_U5M", "CM_ECMR_C_IMR", "CM_ECMR_C_NNR", "MM_MMRO_W_MMR",
    "CN_NUTS_C_HA2", "CN_NUTS_C_WH2", "CN_NUTS_C_WA2", "CN_ANMC_C_ANY", "AN_ANEM_W_ANY",
    "FE_FRTR_W_TFR", "FP_CUSM_W_MOD", "FP_CUSM_W_ANY", "FP_NADM_W_UNT",
    "RH_ANCN_W_N4P", "RH_ANCP_W_SKP", "RH_DELA_C_SKP", "RH_DELP_C_DHF",
    "CH_VACC_C_BAS", "CH_VACC_C_NON", "ML_NETC_C_ITN",
    "ED_NARP_B_BTH", "ED_NARS_B_BTH", "ED_LITR_W_LIT", "ED_LITR_M_LIT", "ED_EDUC_W_MYR", "ED_EDUC_M_MYR", "ED_EDUC_W_SEH",
    "HC_ELEC_P_ELC", "WS_SRCE_P_BAS", "WS_TLET_P_BAS", "WS_SRCE_P_IMP", "WS_TLET_P_IMP",
    "HC_WIXQ_P_GNI", "HA_HIVP_B_HIV", "HA_HIVP_W_HIV", "HA_HIVP_M_HIV",
    "WE_WEMP_W_DMK", "DV_SPVL_W_POS", "CO_MOBB_W_BNK",
}

# Everyday words missing from the official labels, added to the search index.
SYNONYMS = {
    "CM_ECMR_C_U5M": "under 5 child mortality deaths children dying before age five",
    "CM_ECMR_C_IMR": "infant deaths babies dying first year",
    "CM_ECMR_C_NNR": "newborn deaths first month",
    "MM_MMRO_W_MMR": "maternal deaths mothers dying childbirth pregnancy",
    "CN_NUTS_C_HA2": "stunting chronic malnutrition undernutrition short height for age",
    "CN_NUTS_C_WH2": "wasting acute malnutrition thin weight for height",
    "CN_NUTS_C_WA2": "underweight malnutrition weight for age",
    "CN_ANMC_C_ANY": "anaemia anemia children iron",
    "AN_ANEM_W_ANY": "anaemia anemia women iron",
    "FE_FRTR_W_TFR": "fertility births per woman children per woman tfr",
    "FP_CUSM_W_MOD": "contraceptive prevalence rate cpr modern family planning use",
    "FP_CUSM_W_ANY": "contraceptive prevalence rate cpr family planning use",
    "FP_NADM_W_UNT": "unmet need family planning contraception",
    "RH_ANCN_W_N4P": "antenatal care prenatal visits anc4 four",
    "RH_ANCP_W_SKP": "antenatal care prenatal skilled",
    "RH_DELA_C_SKP": "skilled birth attendance attendant births doctor nurse midwife",
    "RH_DELP_C_DHF": "institutional delivery facility births hospital clinic",
    "CH_VACC_C_BAS": "vaccination coverage immunization fully vaccinated children",
    "CH_VACC_C_NON": "zero dose unvaccinated children immunization",
    "ML_NETC_C_ITN": "malaria bed nets mosquito",
    "ED_NARP_B_BTH": "school attendance enrollment primary children",
    "ED_NARS_B_BTH": "school attendance enrollment secondary children",
    "ED_LITR_W_LIT": "literacy women female read write",
    "ED_LITR_M_LIT": "literacy men male read write",
    "ED_EDUC_W_MYR": "years of schooling education women female",
    "ED_EDUC_M_MYR": "years of schooling education men male",
    "ED_EDUC_W_SEH": "secondary schooling education women girls",
    "HC_ELEC_P_ELC": "electricity access power households",
    "WS_SRCE_P_BAS": "drinking water access clean safe",
    "WS_SRCE_P_IMP": "drinking water access clean safe",
    "WS_TLET_P_BAS": "sanitation toilets latrines",
    "WS_TLET_P_IMP": "sanitation toilets latrines",
    "HC_WIXQ_P_GNI": "wealth inequality gini assets",
    "HA_HIVP_B_HIV": "hiv aids prevalence adults",
    "WE_WEMP_W_DMK": "women empowerment decision making autonomy household",
    "DV_SPVL_W_POS": "intimate partner violence ipv domestic violence wife beating",
    "CO_MOBB_W_BNK": "financial inclusion bank account women",
}

# Conventions attached to results, by indicator-id prefix.
NOTES = {
    "CM_": "Mortality rates describe the period before each survey (see 'reference_period'), not the "
           "survey year itself.",
    "MM_": "Maternal mortality from sibling histories: the period is ~7 years before the survey and "
           "confidence intervals are very wide.",
    "CN_NUTS": "Children under 5 measured against WHO 2006 growth standards.",
    "HA_HIVP": "From blood tests of those interviewed and tested; not all surveys test.",
}
SURVEY_NOTE = ("Household surveys, run every ~5 years: values exist only for survey years, which differ across "
               "countries and are often several years old. Always give the survey year; never call a value "
               "current or today's.")
TYPE_NOTE = ("Survey types: DHS = full Demographic and Health Survey; MIS = Malaria Indicator Survey and AIS = "
             "AIDS Indicator Survey (smaller, focused questionnaires, same definitions).")
CHANGE_NOTE = "'change_per_year' = (latest - first) / years between those surveys; it says nothing about the path between."
REGION_NOTE = ("Regional estimates rest on much smaller samples than the national one: confidence intervals "
               "(ci) are wide, so a gap between two regions can be noise. 'clearly_above/below_national' = "
               "regions whose whole ci lies above/below the national value.")
REGION_NOTE_NO_CI = ("Regional estimates rest on much smaller samples than the national one (the API gives no "
                     "confidence intervals for this indicator): small gaps between regions can be noise.")
REGION_PERIOD_NOTE = ("Regional values refer to the {period}; 'national' beside them is for the same period, so it "
                      "can differ from the headline series above.")

_PERIOD = re.compile(r"(?:the )?(?:\w+ (?:or \w+ )?)?(?:years?|months?) (?:preceding|before) the survey", re.I)


def _get(path: str, **params) -> list[dict]:
    """All rows of an API listing (most calls fit in one page)."""
    rows, page = [], 1
    while True:
        r = _session.get(f"{API}/{path}", params={"f": "json", "perpage": 1000, "page": page, **params},
                         timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
        if "Data" not in body:
            raise ValueError(f"DHS API error: {str(body)[:200]}")
        rows += body["Data"]
        if page >= (body.get("TotalPages") or 1):
            return rows
        page += 1


def build_catalog(directory: Path = CATALOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)
    # Only real indicators ("I"); the rest are standard errors, CI bounds,
    # denominators and missing-value shares.
    indicators = [{"id": i["IndicatorId"], "name": i["Label"].strip(),
                   "definition": " ".join((i.get("Definition") or "").split())[:400],
                   "topic": " / ".join(x for x in (i.get("Level1"), i.get("Level2")) if x),
                   "unit": i.get("MeasurementType") or "", "denominator": (i.get("Denominator") or "")[:120]}
                  for i in _get("indicators", returnFields="IndicatorId,Label,Definition,Level1,Level2,"
                                "MeasurementType,Denominator,IndicatorType", perpage=5000)
                  if i.get("IndicatorType") == "I"]
    countries = [{"code": c["DHS_CountryCode"], "name": c["CountryName"], "iso3": c.get("ISO3_CountryCode") or "",
                  "iso2": c.get("ISO2_CountryCode") or ""}
                 for c in _get("countries")]
    (directory / "indicators.json").write_text(json.dumps(indicators))
    (directory / "countries.json").write_text(json.dumps(countries))


def _num(v):
    return None if v in ("", None) else float(v)


def _r(v):
    return None if v is None else (int(v) if float(v).is_integer() else round(v, 2))


class DHS:
    def __init__(self, directory: Path = CATALOG_DIR):
        if not (directory / "indicators.json").exists():
            build_catalog(directory)
        self.indicators = json.loads((directory / "indicators.json").read_text())
        self.by_id = {i["id"]: i for i in self.indicators}
        # name counted three times: a name match beats a passing mention in a definition
        self._bm25 = BM25([f"{i['name']} {i['name']} {i['name']} {i['id']} {SYNONYMS.get(i['id'], '')} "
                           f"{SYNONYMS.get(i['id'], '')} {i['topic']} {i['definition'][:200]}"
                           for i in self.indicators])
        self.countries = {c["code"]: c for c in json.loads((directory / "countries.json").read_text())}
        by_iso3 = {c["iso3"]: c["code"] for c in self.countries.values() if c["iso3"]}
        self._names = {_norm(c["name"]): c["code"] for c in self.countries.values()}
        # World Bank names, ISO3 codes and aliases ("DRC", "Ivory Coast") -> DHS code,
        # plus names of economies DHS doesn't cover, for a clearer error.
        self._not_covered: dict[str, str] = {}
        self._iso3 = dict(by_iso3)
        self._iso2 = {c["iso2"]: c["code"] for c in self.countries.values() if c.get("iso2")}
        wb = json.loads(WB_COUNTRIES.read_text()) if WB_COUNTRIES.exists() else []
        for c in wb:
            if c["aggregate"]:
                continue
            for key in {_norm(c["name"]), _norm(c["name"].split(",")[0])}:
                if c["id"] in by_iso3:
                    self._names.setdefault(key, by_iso3[c["id"]])
                else:
                    self._not_covered.setdefault(key, c["name"])
            if c["id"] not in by_iso3:
                self._not_covered.setdefault(_norm(c["id"]), c["name"])
        for alias, iso3 in ALIASES.items():
            if iso3 in by_iso3:
                self._names.setdefault(alias, by_iso3[iso3])
        self._cache: dict[tuple, list] = {}

    # ---- lookup helpers ------------------------------------------------
    def country(self, name: str) -> str:
        """DHS 2-letter code from a name, ISO3 or ISO2 code. DHS codes are not
        ISO2 (India = IA, Niger = NI, which is ISO2 for Nicaragua), so a
        2-letter input is read as ISO2 only."""
        s = name.strip()
        if s.upper() in self._iso3:
            return self._iso3[s.upper()]
        if len(s) == 2 and s.upper() in self._iso2:
            return self._iso2[s.upper()]
        key = _norm(s)
        if key == "congo":
            raise ValueError("'Congo' is ambiguous: Congo Democratic Republic (COD) or Congo (Republic, COG)")
        if key in self._names:
            return self._names[key]
        if key in self._not_covered:
            raise ValueError(f"no DHS surveys for {self._not_covered[key]}: DHS covers ~90 low- and "
                             "middle-income countries")
        close = difflib.get_close_matches(key, list(self._names) + list(self._not_covered), n=1, cutoff=0.85)
        if close and close[0] in self._names:
            return self._names[close[0]]
        if close:
            raise ValueError(f"no DHS surveys for {self._not_covered[close[0]]}")
        raise ValueError(f"unknown country {name!r}")

    def _rows(self, indicator: str, codes: tuple, breakdown: str | None = None, **years) -> list[dict]:
        key = (indicator, codes, breakdown, tuple(sorted(years.items())))
        if key not in self._cache:
            params = {"countryIds": ",".join(codes), "indicatorIds": indicator,
                      "returnFields": "DHS_CountryCode,SurveyId,SurveyYear,SurveyYearLabel,SurveyType,Value,"
                                      "CILow,CIHigh,IsTotal,IsPreferred,ByVariableId,ByVariableLabel,"
                                      "CharacteristicLabel,CharacteristicOrder"}
            if breakdown:
                params["breakdown"] = breakdown
            params.update({k: v for k, v in years.items() if v is not None})
            self._cache[key] = _get("data", **params)
        return self._cache[key]

    def _notes(self, indicator: str) -> list[str]:
        return [SURVEY_NOTE] + [note for key, note in NOTES.items() if indicator.startswith(key)]

    # ---- tools -----------------------------------------------------------
    def search(self, query: str, n: int = 8) -> list[dict]:
        scores = self._bm25.scores(query)
        for idx, ind in enumerate(self.indicators):
            if ind["id"] in CORE:
                scores[idx] *= 1.5
        top = [i for i in scores.argsort()[::-1][:n] if scores[i] > 0]
        return [{"id": self.indicators[i]["id"], "name": self.indicators[i]["name"],
                 "definition": self.indicators[i]["definition"][:150]} for i in top]

    def get(self, indicator: str, countries: list[str], start: int | None = None, end: int | None = None,
            regions: bool = False) -> dict:
        if indicator not in self.by_id:
            raise ValueError(f"unknown DHS indicator {indicator!r}: use search_data with source dhs to find its id")
        meta = self.by_id[indicator]
        codes = tuple(dict.fromkeys(self.country(c) for c in countries))[:MAX_COUNTRIES]
        if not codes:
            raise ValueError("no countries given")
        rows = self._rows(indicator, codes, surveyYearStart=start, surveyYearEnd=end)
        out = {"source": "DHS Program (STATcompiler API)", "indicator": indicator, "name": meta["name"],
               "definition": meta["definition"][:300], "unit": meta["unit"],
               "denominator": meta["denominator"], "notes": self._notes(indicator), "countries": []}
        periods, types = set(), set()
        for code in codes:
            entry = {"country": self.countries[code]["name"], "iso3": self.countries[code]["iso3"]}
            surveys = self._headline(rows, code)
            if not surveys:
                entry["data"] = "no DHS survey has this indicator" + (" in this period" if start or end else "")
                out["countries"].append(entry)
                continue
            periods.update(s["period"] for s in surveys if s["period"])
            types.update(s["type"] for s in surveys)
            first, latest = surveys[0], surveys[-1]
            entry["surveys [year, value, survey_id, type]"] = [
                [s["label"], s["value"], s["survey"], s["type"]] for s in surveys]
            entry["latest"] = {"year": latest["label"], **{k: latest[k] for k in ("value", "survey", "type", "ci")
                                                           if k in latest}}
            if len(surveys) > 1:
                entry["first"] = {"year": first["label"], "value": first["value"], "survey": first["survey"]}
                change = latest["value"] - first["value"]
                years = latest["year"] - first["year"]
                entry["change_first_to_latest"] = _r(round(change, 2))
                if years > 0:
                    entry["change_per_year"] = round(change / years, 3)
                    if first["value"]:
                        entry["change_percent"] = round(100 * change / first["value"], 1)
            if regions and code == codes[0]:
                entry["regions"] = self._regions(indicator, code, rows)
            out["countries"].append(entry)
        if periods:
            out["reference_period"] = "; ".join(sorted(periods))
        elif (m := _PERIOD.search(meta["definition"])):
            out["reference_period"] = m.group(0)
        if any(len(c.get("surveys [year, value, survey_id, type]", [])) > 1 for c in out["countries"]):
            out["notes"].append(CHANGE_NOTE)
        if types - {"DHS"}:
            out["notes"].append(TYPE_NOTE)
        if regions:
            r = out["countries"][0].get("regions", {})
            period = r.pop("period", "")
            if "national" in r:
                out["notes"].append(REGION_NOTE if "clearly_above_national" in r else REGION_NOTE_NO_CI)
                if period:
                    out["notes"].append(REGION_PERIOD_NOTE.format(period=period.lower()))
            if len(codes) > 1:
                out["notes"].append("Regional values are given for the first country only.")
        return out

    @staticmethod
    def _headline(rows: list[dict], code: str) -> list[dict]:
        """One national value per survey: the total row marked preferred
        (falling back to the first total row), oldest survey first."""
        best: dict[str, dict] = {}
        for r in rows:
            if r["DHS_CountryCode"] != code or not r.get("IsTotal") or _num(r["Value"]) is None:
                continue
            if r["SurveyId"] not in best or (r.get("IsPreferred") and not best[r["SurveyId"]].get("IsPreferred")):
                best[r["SurveyId"]] = r
        out = []
        for r in sorted(best.values(), key=lambda r: (int(r["SurveyYear"]), r["SurveyId"])):
            s = {"year": int(r["SurveyYear"]), "label": r.get("SurveyYearLabel") or r["SurveyYear"],
                 "value": _r(_num(r["Value"])), "survey": r["SurveyId"], "type": r.get("SurveyType") or "",
                 "period": r.get("ByVariableLabel") or ""}
            lo, hi = _num(r.get("CILow")), _num(r.get("CIHigh"))
            if lo is not None and hi is not None:
                s["ci"] = [_r(lo), _r(hi)]
            out.append(s)
        return out

    def _regions(self, indicator: str, code: str, national_rows: list[dict]) -> dict:
        """The latest survey with regional values: regions sorted, extremes,
        spread, and the national value for the same reference period."""
        rows = [r for r in self._rows(indicator, (code,), "subnational")
                if r.get("IsPreferred") and _num(r["Value"]) is not None]
        if not rows:
            return {"data": "no regional values for this indicator"}
        latest = max(rows, key=lambda r: (int(r["SurveyYear"]), r["SurveyId"]))
        survey = latest["SurveyId"]
        rows = sorted((r for r in rows if r["SurveyId"] == survey), key=lambda r: int(r["CharacteristicOrder"] or 0))
        # Hierarchy is marked by ".." prefixes ("Coast", "..Mombasa"); report
        # the finest units (leaves) and list the larger regions separately.
        units = []
        for i, r in enumerate(rows):
            label = r["CharacteristicLabel"]
            depth = (len(label) - len(label.lstrip("."))) // 2
            nxt = rows[i + 1]["CharacteristicLabel"] if i + 1 < len(rows) else ""
            has_children = (len(nxt) - len(nxt.lstrip("."))) // 2 > depth
            unit = {"name": label.lstrip(". "), "value": _r(_num(r["Value"])), "depth": depth,
                    "leaf": not has_children}
            lo, hi = _num(r.get("CILow")), _num(r.get("CIHigh"))
            if lo is not None and hi is not None:
                unit["ci"] = [_r(lo), _r(hi)]
            units.append(unit)
        leaves = [u for u in units if u["leaf"]]
        # top-level regions, when some of them are split further (Kenya: provinces -> counties)
        parents = [u for u in units if u["depth"] == 0] if any(u["depth"] for u in units) else []
        by_var = latest.get("ByVariableId")
        national = next((r for r in national_rows if r["SurveyId"] == survey and r.get("IsTotal")
                         and r.get("ByVariableId") == by_var and _num(r["Value"]) is not None), None)
        if national is None:  # regional period may not exist nationally in the cached rows
            national = next((r for r in self._rows(indicator, (code,)) if r["SurveyId"] == survey
                             and r.get("IsTotal") and r.get("ByVariableId") == by_var), None)
        ordered = sorted(leaves, key=lambda u: u["value"], reverse=True)
        hi, lo = ordered[0], ordered[-1]
        values = [u["value"] for u in ordered]
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
        out = {"survey": survey, "year": latest.get("SurveyYearLabel") or latest["SurveyYear"],
               "period": latest.get("ByVariableLabel") or "",
               "national": _r(_num(national["Value"])) if national else None,
               "n_regions": len(ordered), "highest": {"region": hi["name"], "value": hi["value"]},
               "lowest": {"region": lo["name"], "value": lo["value"]},
               "spread_max_minus_min": _r(round(hi["value"] - lo["value"], 2)),
               "ratio_max_to_min": round(hi["value"] / lo["value"], 2) if lo["value"] else None,
               "median_region": _r(round(median, 2))}
        if out["national"] is not None:
            nat = out["national"]
            above = [u["name"] for u in ordered if "ci" in u and u["ci"][0] > nat]
            below = [u["name"] for u in ordered if "ci" in u and u["ci"][1] < nat]
            if any("ci" in u for u in ordered):
                out["clearly_above_national"], out["clearly_below_national"] = above, below
        else:
            del out["national"]
        has_ci = any("ci" in u for u in ordered)
        key = "regions [name, value, ci_low, ci_high] (highest first)" if has_ci else \
              "regions [name, value] (highest first)"
        out[key] = [[u["name"], u["value"], *u.get("ci", [])] if has_ci else [u["name"], u["value"]]
                    for u in ordered]
        if parents:
            out["larger_regions [name, value]"] = [[u["name"], u["value"]] for u in
                                                   sorted(parents, key=lambda u: u["value"], reverse=True)]
        return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--regions"]
    d = DHS()
    hits = d.search(args[0] if args else "under-5 mortality")
    print(json.dumps(hits, indent=1))
    if len(args) > 1:
        print(json.dumps(d.get(hits[0]["id"], args[1:], regions="--regions" in sys.argv), indent=1,
                         ensure_ascii=False))
