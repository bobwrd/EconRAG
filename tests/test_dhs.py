"""
Tests for dhs.py (DHS Program API). Live API, no LLM tokens, ~10s:

    .venv/bin/python tests/test_dhs.py           # all
    .venv/bin/python tests/test_dhs.py regions   # only tests whose name contains "regions"

Plain asserts, like tests/test_tools.py. Expected values are from completed
historical surveys (published Oct 2026); DHS doesn't revise released survey
estimates, so a change here means the API or the selection logic changed.
Network failures are reported as SKIP.
"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)

import requests  # noqa: E402

from dhs import DHS  # noqa: E402

D = DHS()
SURVEYS = "surveys [year, value, survey_id, type]"


def _raises(fn, *args, contains=""):
    try:
        fn(*args)
    except ValueError as e:
        assert contains.lower() in str(e).lower(), e
        return
    raise AssertionError(f"expected ValueError from {args}")


# ------------------------------------------------------------------ search
def test_search_finds_core_indicators_first():
    cases = {
        "under-5 mortality": "CM_ECMR_C_U5M", "child mortality": "CM_ECMR_C_U5M",
        "infant mortality": "CM_ECMR_C_IMR", "neonatal mortality": "CM_ECMR_C_NNR",
        "stunting": "CN_NUTS_C_HA2", "wasting": "CN_NUTS_C_WH2", "total fertility rate": "FE_FRTR_W_TFR",
        "modern contraceptive use": "FP_CUSM_W_MOD", "skilled birth attendance": "RH_DELA_C_SKP",
        "institutional delivery": "RH_DELP_C_DHF", "vaccination coverage": "CH_VACC_C_BAS",
        "women literacy": "ED_LITR_W_LIT", "electricity access": "HC_ELEC_P_ELC",
        "HIV prevalence": "HA_HIVP_B_HIV", "intimate partner violence": "DV_SPVL_W_POS",
        "primary school attendance": "ED_NARP_B_BTH", "women decision making": "WE_WEMP_W_DMK",
    }
    misses = {q: D.search(q, n=1)[0]["id"] for q, want in cases.items() if D.search(q, n=1)[0]["id"] != want}
    assert not misses, misses


def test_search_returns_definitions_and_only_real_indicators():
    hits = D.search("under-five mortality")
    assert all(set(h) == {"id", "name", "definition"} for h in hits)
    # standard errors / CI bounds / denominators are filtered out of the catalog
    assert not any("standard error" in h["name"].lower() or "ci lower" in h["name"].lower() for h in hits)
    assert D.search("zzzz qqqq") == []


# ------------------------------------------------------------------ countries
def test_country_resolution():
    assert D.country("Kenya") == "KE"
    assert D.country("KEN") == "KE"
    assert D.country("India") == "IA"         # DHS code, not ISO2
    assert D.country("IN") == "IA"            # 2-letter input is ISO2
    assert D.country("NE") == "NI"            # ISO2 Niger -> DHS NI
    assert D.country("DRC") == "CD"
    assert D.country("Ivory Coast") == "CI"
    assert D.country("Kyrgyzstan") == "KY"
    assert D.country("Congo, Rep.") == "CG"
    _raises(D.country, "Congo", contains="ambiguous")
    _raises(D.country, "France", contains="no DHS surveys")
    _raises(D.country, "Atlantis", contains="unknown")


# ------------------------------------------------------------------ get
def test_kenya_under5_history_exact():
    r = D.get("CM_ECMR_C_U5M", ["Kenya"])
    k = r["countries"][0]
    assert k["iso3"] == "KEN"
    first, *_, last = k[SURVEYS]
    assert first == ["1989", 90, "KE1989DHS", "DHS"], first
    assert last[2] == "KE2022DHS" and last[1] == 41, last
    assert k["first"]["value"] == 90 and k["latest"]["value"] == 41
    assert k["latest"]["ci"] == [37, 45]
    assert k["change_first_to_latest"] == -49
    assert k["change_per_year"] == round(-49 / 33, 3)
    # headline = the preferred five-year period, not the ten-year one (1989: 91)
    assert r["reference_period"] == "Five years preceding the survey"
    assert any("never call a value current" in n for n in r["notes"])


def test_year_filter_and_survey_types():
    r = D.get("ML_NETC_C_ITN", ["Nigeria"], start=2010, end=2018)
    surveys = r["countries"][0][SURVEYS]
    assert [s[2] for s in surveys] == ["NG2010MIS", "NG2013DHS", "NG2015MIS", "NG2018DHS"], surveys
    assert any("MIS = Malaria Indicator Survey" in n for n in r["notes"])


def test_multiple_countries_and_missing_data():
    r = D.get("FE_FRTR_W_TFR", ["Kenya", "Nigeria"], start=1989, end=1990)
    k, n = r["countries"]
    assert k[SURVEYS] == [["1989", 6.7, "KE1989DHS", "DHS"]]
    assert "change_per_year" not in k            # one survey: no trend
    assert n[SURVEYS][0][2] == "NG1990DHS"
    r = D.get("FE_FRTR_W_TFR", ["Kenya"], start=1960, end=1980)
    assert "no DHS survey" in r["countries"][0]["data"]


def test_regions_kenya_2022_counties():
    r = D.get("CM_ECMR_C_U5M", ["Kenya"], regions=True)
    g = r["countries"][0]["regions"]
    assert g["survey"] == "KE2022DHS" and g["n_regions"] == 47
    assert g["highest"] == {"region": "Migori", "value": 73}
    assert g["lowest"] == {"region": "Marsabit", "value": 15}
    assert g["spread_max_minus_min"] == 58 and g["ratio_max_to_min"] == round(73 / 15, 2)
    assert g["national"] == 43                   # ten-year national, matching the regional period
    assert "Migori" in g["clearly_above_national"]
    rows = g["regions [name, value, ci_low, ci_high] (highest first)"]
    assert rows[0] == ["Migori", 73, 51, 96] and [x[1] for x in rows] == sorted((x[1] for x in rows), reverse=True)
    assert ["Nairobi", 44] in g["larger_regions [name, value]"]
    assert any("ten years preceding the survey" in n for n in r["notes"])
    assert any("confidence intervals" in n for n in r["notes"])


def test_regions_nigeria_states():
    g = D.get("CN_NUTS_C_HA2", ["Nigeria"], regions=True)["countries"][0]["regions"]
    assert g["n_regions"] == 37                  # 36 states + FCT; zones listed separately
    assert len(g["larger_regions [name, value]"]) == 6
    assert g["lowest"]["value"] <= g["median_region"] <= g["highest"]["value"]
    assert g["spread_max_minus_min"] == round(g["highest"]["value"] - g["lowest"]["value"], 2)


def test_get_errors():
    _raises(D.get, "NOT_AN_INDICATOR", ["Kenya"], contains="unknown DHS indicator")
    _raises(D.get, "CM_ECMR_C_U5M", ["Germany"], contains="no DHS surveys")


# ------------------------------------------------------------------ runner
def test_analyst_routes_dhs_and_trims_regions():
    import analyst
    bot = analyst.Analyst(lambda q, seen: ("", [], []), atlas=object(), wb=object())
    assert bot._call("search_data", {"source": "dhs", "query": "under-5 mortality"})["results"][0]["id"] == \
        "CM_ECMR_C_U5M"
    r = bot._call("get_data", {"source": "dhs", "series": "CM_ECMR_C_U5M", "countries": ["Kenya"], "regions": True})
    assert analyst._summary("get_data", r) == "CM_ECMR_C_U5M: Kenya 41 (2022)", analyst._summary("get_data", r)
    reg = r["countries"][0]["regions"]
    rows = next(v for k, v in reg.items() if k.startswith("regions ["))
    assert len(rows) == 2 * analyst.DHS_REGIONS_SHOWN + 1 and rows[analyst.DHS_REGIONS_SHOWN][0] == "...", rows
    assert reg["n_regions"] == 47 and "Migori" in str(reg["highest"]), reg  # computed before trimming


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and pattern in n]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except requests.ConnectionError:
            skipped += 1
            print(f"  SKIP  {name} (no network)")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{len(tests) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
