"""
Tests for gdl.py (Global Data Lab subnational HDI) and its analyst routing.
Needs the CSV in data/gdl/ (tests SKIP without it); no network. About a second:

    .venv/bin/python tests/test_gdl.py
"""

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)

import analyst  # noqa: E402
import gdl  # noqa: E402


class Skip(Exception):
    pass


def _gdl() -> gdl.GDL:
    try:
        return gdl.GDL()
    except FileNotFoundError:
        raise Skip("Global Data Lab CSV not in data/gdl/")


# Exact values: Subnational HDI Data v10.2, downloaded Oct 2026.
def test_country_regions_and_spread():
    c = _gdl().get("shdi", ["Kenya"], start=2000)["countries"][0]
    assert (c["year"], c["national"], c["n_regions"]) == (2023, 0.628, 8), c
    assert c["highest"] == {"region": "Nairobi", "value": 0.694} and c["lowest"]["region"] == "North Eastern", c
    assert c["national_first"] == {"year": 2000, "value": 0.5}
    assert c["change_2000_2023"]["largest_gain"] == ["North Eastern", 0.184], c


def test_sex_specific_metrics_and_aliases():
    g = _gdl()
    c = g.get("lifexpf", ["India"])["countries"][0]
    assert c["highest"] == {"region": "Kerala", "value": 80.828} and c["lowest"]["region"] == "Uttar Pradesh", c
    assert g.get("hdi", ["Kenya"])["metric"] == "shdi"
    assert g.get("life_expectancy_female", ["IND"])["metric"] == "lifexpf"


def test_income_is_unlogged_2021_ppp():
    c = _gdl().get("gnic", ["USA"])["countries"][0]
    assert c["national"] == 73644, c  # UNDP HDR 2025: USA GNI per capita 2023 = $73,650 (2021 PPP)


def test_worldwide_ranking_and_trimming():
    g = _gdl()
    r = g.get("lifexp", ["all"], order="highest", n=3)
    assert r["ranked"][0]["region"] == "Comunidad de Madrid" and r["regions_with_data"] == 1805, r
    rows = g.get("shdi", ["India"])["countries"][0]["regions [name, value] (highest first)"]
    assert len(rows) == 2 * gdl.REGIONS_SHOWN + 1 and rows[gdl.REGIONS_SHOWN][0] == "...", rows


def test_errors():
    g = _gdl()
    for bad, call in [("metric", lambda: g.get("happiness", ["Kenya"])),
                      ("country", lambda: g.get("shdi", ["Atlantis"]))]:
        try:
            call()
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for unknown {bad}")


def test_analyst_routes_gdl():
    _gdl()
    bot = analyst.Analyst(lambda q, seen: ("", [], []), atlas=object(), wb=object())
    assert bot._call("search_data", {"source": "gdl", "query": "human development index"})["results"][0]["id"] == "shdi"
    r = bot._call("get_data", {"source": "gdl", "series": "shdi", "countries": ["Kenya"]})
    assert analyst._summary("get_data", r) == "shdi: Kenya 0.628 (2023), regions 0.487-0.694", analyst._summary("get_data", r)
    r = bot._call("get_data", {"source": "gdl", "series": "lifexp", "countries": ["all"], "n": 2})
    assert analyst._summary("get_data", r).startswith("lifexp: 2 of 1805 regions, #1 Comunidad de Madrid"), r
    assert "needs `countries`" in bot._call("get_data", {"source": "gdl", "series": "shdi"})["error"]
    assert len(json.dumps(r)) < 3000


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and pattern in n]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Skip as e:
            skipped += 1
            print(f"  SKIP  {name} ({e})")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{len(tests) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
