"""
Tests for imf.py (IMF World Economic Outlook) and its use in the analyst,
run_python and the country brief. Live IMF data (DataMapper, cached a week in
data/imf/) plus a canned reply for the fallback; no Groq. A few seconds:

    .venv/bin/python tests/test_imf.py
"""

import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import test_tools as tt  # noqa: E402  (sets cwd and sys.path; shares the World Bank client)

import analyst  # noqa: E402
import compute  # noqa: E402
import imf  # noqa: E402
import verify  # noqa: E402

IMF = imf.IMF()

SDMX_REPLY = """<message:StructureSpecificData><DataSet>
<Series COUNTRY="KEN" INDICATOR="PCPIPCH" FREQUENCY="A" COUNTRY_UPDATE_DATE="9/22/2025">
<Obs TIME_PERIOD="2025" OBS_VALUE="4.1"/><Obs TIME_PERIOD="2026" OBS_VALUE="5.2"/></Series>
<Series COUNTRY="IND" INDICATOR="PCPIPCH" FREQUENCY="A" COUNTRY_UPDATE_DATE="9/26/2025">
<Obs TIME_PERIOD="2025" OBS_VALUE="2.8"/></Series></DataSet></message:StructureSpecificData>"""


def test_search_finds_the_headline_series():
    assert IMF.search("inflation")[0]["id"] == "PCPIPCH"
    assert IMF.search("government debt")[0]["id"] == "GGXWDG_NGDP"
    assert "GGXCNL_NGDP" in [r["id"] for r in IMF.search("budget deficit")]


def test_get_labels_edition_and_projections():
    r = IMF.get("PCPIPCH", ["Kenya"], resolve=tt.WB.country, name=lambda c: tt.WB.countries[c]["name"])
    assert r["source"].startswith("IMF, World Economic Outlook (") and "PROJECTIONS" in r["notes"][0]
    kenya = r["economies"][0]
    first = imf.edition_year(r["edition"])
    assert kenya["economy"] == "Kenya" and kenya["projections_from"] == first
    years = [y for y, _ in kenya["series [year, value]"]]
    assert max(years) >= first + 3 and min(years) < first  # history and projections


def test_fallback_reads_the_imf_data_api():
    data, updated = imf.parse_sdmx(SDMX_REPLY)
    assert data == {"KEN": {2025: 4.1, 2026: 5.2}, "IND": {2025: 2.8}} and updated == "2025-09-26"
    # DataMapper refusing (as it did to Python in early Oct 2026) and nothing cached: the data API instead
    original_json, original_get = imf._json, imf._session.get
    imf._json = lambda url: (_ for _ in ()).throw(imf.IMFUnavailable("403"))
    imf._session.get = lambda url, timeout: type("R", (), {"text": SDMX_REPLY, "raise_for_status": lambda self: None})()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = imf.IMF.__new__(imf.IMF)
            fresh.dir, fresh.indicators = Path(tmp), IMF.indicators
            data, edition = fresh.series("PCPIPCH", ["KEN", "IND"])
    finally:
        imf._json, imf._session.get = original_json, original_get
    assert data["KEN"][2026] == 5.2 and "IMF data API" in edition and "2025-09-26" in edition and "older" in edition


def test_analyst_and_run_python_use_imf():
    bot = analyst.Analyst(None, None, tt.WB)
    r = bot._call("get_data", {"source": "imf", "series": "GGXWDG_NGDP", "countries": ["Kenya", "India"]})
    assert [e["code"] for e in r["economies"]] == ["KEN", "IND"] and "latest" in r["economies"][1]
    assert bot._call("search_data", {"source": "imf", "query": "inflation"})["results"][0]["id"] == "PCPIPCH"
    data, names = compute.fetch({"source": "imf", "series": "PCPIPCH", "countries": ["KEN"], "start": 2020}, tt.WB)
    assert names["KEN"] == "Kenya" and min(data["KEN"]) == 2020
    # a source tag like "(IMF, World Economic Outlook (April 2026))" isn't read as an author-year citation
    assert verify.unsupported_citations("Debt rises (IMF, World Economic Outlook April 2026).", "", set()) == []


if __name__ == "__main__":
    failed = 0
    for name, fn in [(n, f) for n, f in list(globals().items()) if n.startswith("test_")]:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
