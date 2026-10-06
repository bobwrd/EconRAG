"""
Tests for longrun.py (Maddison Project + Penn World Table). No network, no LLM.

    .venv/bin/python tests/test_longrun.py            # all (~1s)
    .venv/bin/python tests/test_longrun.py compare    # only tests whose name contains "compare"

Two kinds of test:
  - fixture tests build a tiny xlsx in the official files' layout (shared strings,
    several sheets, a Legend sheet) with values whose answers are known exactly,
    so the importer and every computation are checked without the real files;
  - real-data tests read data/longrun/ and SKIP until the official files are
    downloaded and imported (see longrun.py's docstring).
"""

import math
import shutil
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)

import longrun  # noqa: E402
from longrun import LongRun  # noqa: E402


class Skip(Exception):
    pass


# ------------------------------------------------------------ fixture builder
def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path: Path, sheets: dict[str, list[list]]):
    """Minimal xlsx writer: strings go to sharedStrings (as Excel saves them)."""
    strings: list[str] = []
    index: dict[str, int] = {}
    sheet_xml = []
    for rows in sheets.values():
        out = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, v in enumerate(row):
                ref = f"{_col(c)}{r}"
                if v is None:
                    continue
                if isinstance(v, str):
                    if v not in index:
                        index[v] = len(strings)
                        strings.append(v)
                    cells.append(f'<c r="{ref}" t="s"><v>{index[v]}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{v!r}</v></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_xml.append('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.'
                         'openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(out) +
                         "</sheetData></worksheet>")
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    names = list(sheets)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>' + "".join(
            f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
            + "</sheets></workbook>")
        # reversed file numbering so the sheet -> file mapping is really exercised
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships">' + "".join(
                       f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{len(names) - i}.xml"/>'
                       for i in range(len(names))) + "</Relationships>")
        for i, xml in enumerate(sheet_xml):
            z.writestr(f"xl/worksheets/sheet{len(names) - i}.xml", xml)
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{main}">' + "".join(
            f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")


def build_fixture(directory: Path):
    # Maddison: Korea 1,000 in 1960 growing 6%/yr; Ghana 1,100 growing 1%/yr;
    # Korea also has two pre-1950 benchmark years and a gap.
    mpd = [["countrycode", "country", "region", "year", "gdppc", "pop"]]
    mpd += [["KOR", "Republic of Korea", "East Asia", 1820, 600.0, 9000.0],
            ["KOR", "Republic of Korea", "East Asia", 1900, 700.0, 10000.0]]
    for y in range(1960, 2021):
        mpd.append(["KOR", "Republic of Korea", "East Asia", y, 1000 * 1.06 ** (y - 1960), 25000.0])
        mpd.append(["GHA", "Ghana", "Sub Saharan Africa", y, 1100 * 1.01 ** (y - 1960), 6000.0])
    write_xlsx(directory / "mpd2023_web.xlsx", {"Sources": [["source"], ["x"]], "Full data": mpd})
    # PWT: constant employment; log growth Y 5%, K 6%, hc 1%; labour share 0.6.
    head = ["countrycode", "country", "currency_unit", "year", "rgdpe", "rgdpo", "pop", "emp", "avh", "hc",
            "cn", "rnna", "ctfp", "rtfpna", "labsh", "csh_i", "rgdpna"]
    pwt = [head]
    for y in range(1960, 2020):
        t = y - 1960
        pwt.append(["KOR", "Republic of Korea", "Won", y, 100 * math.exp(.05 * t), 100 * math.exp(.05 * t),
                    25.0, 10.0, 2000.0, 1.5 * math.exp(.01 * t), 300.0, 300 * math.exp(.06 * t), 0.5,
                    math.exp(.02 * t), 0.6, 0.3, 100 * math.exp(.05 * t)])
        pwt.append(["GHA", "Ghana", "Cedi", y, 50.0, 50.0, 6.0, 2.0, None, None, 80.0, 80.0, 0.3, 1.0,
                    None, 0.2, 50.0])
    legend = [["Variable name", "Variable definition"],
              ["rgdpe", "Expenditure-side real GDP at chained PPPs (in mil. 2021US$)"],
              ["rgdpna", "Real GDP at constant 2021 national prices (in mil. 2021US$)"],
              ["hc", "Human capital index, see note hc"]]
    write_xlsx(directory / "pwt110.xlsx", {"Info": [["PWT 11.0"]], "Legend": legend, "Data": pwt})


TMP = Path(tempfile.mkdtemp(prefix="longrun_test_"))
build_fixture(TMP)
LR = LongRun(TMP)  # imports the fixture xlsx -> CSVs on first load


def _entry(result, code):
    return next(c for c in result["countries"] if c["code"] == code)


# ------------------------------------------------------------ fixture tests
def test_import_reads_sheets_and_writes_csvs():
    assert (TMP / "maddison.csv").exists() and (TMP / "pwt.csv").exists()
    assert LR.meta["sources"]["mpd"]["edition"] == "2023"
    assert LR.meta["sources"]["pwt"]["version"] == "11.0"
    assert LR.names["KOR"] == "Republic of Korea"
    assert LR.series["mpd.gdppc"]["KOR"][0] == (1820, 600.0)
    # a second load reads the CSVs, not the xlsx
    again = LongRun(TMP)
    assert again.series["pwt.rgdpe"]["GHA"][0] == (1960, 50.0)


def test_units_come_from_pwt_legend():
    assert LR.vars["pwt.rgdpe"]["units"] == "mil. 2021US$"
    assert LR.vars["pwt.rgdpe_pc"]["units"].startswith("2021 US$ per person")
    assert LR.vars["mpd.gdppc"]["units"] == "2011 international dollars (PPP)"


def test_country_resolution():
    assert LR.country("South Korea") == "KOR"
    assert LR.country("korea") == "KOR"
    assert LR.country("gha") == "GHA"
    assert LR.country("Republic of Korea") == "KOR"
    try:
        LR.country("Atlantis")
        raise AssertionError("should have raised")
    except ValueError:
        pass


def test_variable_ids_and_ambiguity():
    assert LR._var("gdppc") == "mpd.gdppc"
    assert LR._var("PWT.HC") == "pwt.hc"
    try:
        LR._var("pop")  # in both sources
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "ambiguous" in str(e)


def test_search_finds_core_variables():
    assert LR.search("GDP per capita history")[0]["id"] == "mpd.gdppc"
    assert "pwt.ctfp" in [h["id"] for h in LR.search("total factor productivity")[:2]]
    assert LR.search("schooling")[0]["id"] == "pwt.hc"


def test_get_stats_cagr_peak_and_pre1950():
    e = _entry(LR.get("mpd.gdppc", ["South Korea"], 1960, 2020), "KOR")
    assert e["first"] == {"year": 1960, "value": 1000.0}
    assert e["growth_per_year_pct"] == 6.0
    assert e["peak"]["year"] == 2020 and e["trough"]["year"] == 1960
    assert e["observations"] == 61
    full = LR.get("mpd.gdppc", ["KOR"])
    assert _entry(full, "KOR")["observations_before_1950"] == 2
    assert "gaps" in _entry(full, "KOR")["years_with_data"]
    assert any("1950" in n for n in full["notes"]) and any("2011 international" in n for n in full["notes"])
    assert "Bolt" in full["source"] and "mpd2023_web.xlsx" in full["source"]


def test_get_reports_missing_early_years():
    e = _entry(LR.get("pwt.rgdpe", ["Ghana"], 1950, 1970), "GHA")
    assert e["no_data_before"] == 1960
    assert "no observations" in _entry(LR.get("pwt.rgdpe", ["Ghana"], 1900, 1940), "GHA")["data"]


def test_derived_per_capita():
    e = _entry(LR.get("pwt.rgdpe_pc", ["GHA"], 1960, 1960), "GHA")
    assert e["first"]["value"] == round(50 / 6, 3)


def test_compare_overtaking_and_divergence():
    c = LR.compare("mpd.gdppc", ["Ghana", "South Korea"], 1960, 2020)["comparisons"][0]
    assert c["ratio_first"] == {"year": 1960, "value": round(1000 / 1100, 3)}
    # Korea passes Ghana when 1.06^t / 1.01^t > 1.1 -> t = 2 (1962)
    assert c["crossings"] == ["1962: Republic of Korea passed Ghana"], c["crossings"]
    # within 25% while (1.06/1.01)^t / 1.1 <= 1.25 -> t <= 6.6 -> last such year 1966
    assert c["divergence"].startswith("last year within 25% of each other: 1966"), c["divergence"]
    assert c["growth_per_year_pct"] == {"Ghana": 1.0, "Republic of Korea": 6.0}


def test_compare_warns_on_constant_price_levels():
    r = LR.compare("pwt.rgdpna", ["GHA", "KOR"], 1960, 2000)
    assert "not comparable across countries" in r["notes"][0]


def test_growth_accounting_exact_decomposition():
    g = LR.growth_accounting("South Korea", 1960, 2019)
    assert g["capital_share_alpha"] == 0.4
    rates = g["growth_pct_per_year"]
    assert rates == {"output_per_worker": 5.0, "capital_deepening_contribution": 2.4,
                     "human_capital_contribution": 0.6, "tfp_contribution": 2.0}, rates
    assert g["share_of_output_per_worker_growth"]["tfp"] == 0.4
    assert g["pwt_own_tfp_growth_pct_per_year"] == 2.0


def test_analyst_routes_longrun_source():
    import analyst
    bot = analyst.Analyst(lambda q, seen: ("", [], []), atlas=object(), wb=object())
    bot._longrun = LR
    r = bot._call("get_data", {"source": "longrun", "series": "mpd.gdppc", "countries": ["Ghana", "Korea"],
                               "start": "1960", "end": "2020"})
    assert r["comparison"]["comparisons"][0]["crossings"] == ["1962: Republic of Korea passed Ghana"], r
    assert "Ghana 1960" in analyst._summary("get_data", r)
    g = bot._call("get_data", {"source": "longrun", "series": "pwt.growth_accounting", "countries": ["KOR"],
                               "start": "1960", "end": "2019"})
    assert g["growth_pct_per_year"]["tfp_contribution"] == 2.0, g
    assert "TFP 2.0" in analyst._summary("get_data", g)
    assert bot._call("search_data", {"source": "longrun", "query": "GDP per capita"})["results"]
    assert "needs `countries`" in bot._call("get_data", {"source": "longrun", "series": "mpd.gdppc"})["error"]


def test_growth_accounting_refuses_without_inputs():
    try:
        LR.growth_accounting("Ghana")  # fixture Ghana has no hc / labsh
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "no complete" in str(e)


def test_missing_data_message():
    empty = Path(tempfile.mkdtemp(prefix="longrun_empty_"))
    try:
        LongRun(empty)
        raise AssertionError("should have raised")
    except FileNotFoundError as e:
        assert "download" in str(e)
    finally:
        shutil.rmtree(empty)


# ------------------------------------------------------------ real data
def _real():
    d = longrun.DATA_DIR
    if not ((d / "meta.json").exists() or list(d.glob("mpd*.xlsx")) or list(d.glob("pwt*.xlsx"))):
        raise Skip("official files not in data/longrun/ yet")
    return LongRun()


def test_real_korea_ghana_similar_in_1960_then_diverge():
    lr = _real()
    if "mpd.gdppc" not in lr.series:
        raise Skip("Maddison file not imported")
    # exact values: Maddison 2023 (via Our World in Data), imported Oct 2026
    c = lr.compare("mpd.gdppc", ["Ghana", "South Korea"], 1960, 2022)["comparisons"][0]
    assert c["ratio_first"] == {"year": 1960, "value": round(1547.6918 / 2197, 3)}, c  # Korea poorer in 1960
    assert c["crossings"] == ["1967: South Korea passed Ghana"], c
    assert c["divergence"].startswith("last year within 25% of each other: 1968"), c
    assert c["ratio_last"]["value"] == 9.74, c


def test_real_pwt_growth_accounting_runs_for_korea():
    lr = _real()
    if "pwt.rgdpna" not in lr.series:
        raise Skip("PWT file not imported")
    g = lr.growth_accounting("KOR", 1960, 2019)
    r = g["growth_pct_per_year"]
    total = r["capital_deepening_contribution"] + r["human_capital_contribution"] + r["tfp_contribution"]
    assert abs(total - r["output_per_worker"]) < 0.02
    assert r["output_per_worker"] > 3 and 0.2 < g["capital_share_alpha"] < 0.6, g


# ------------------------------------------------------------------ runner
def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and pattern in n]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Skip as s:
            skipped += 1
            print(f"  SKIP  {name} ({s})")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
