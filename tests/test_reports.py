"""
Report tests (workflows.py, report_export.py, the web endpoints). Zero LLM
tokens: the summary request goes to a scripted fake. World Bank data is real
(network), like tests/test_tools.py. Run after changing any of those files:

    .venv/bin/python tests/test_reports.py
"""

import csv
import datetime
import io
import json
import sys
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import test_tools as tt  # noqa: E402  (sets cwd and sys.path; shares the fake Groq and the World Bank client)

import charts  # noqa: E402
import compute  # noqa: E402
import groq_client  # noqa: E402
import report_export  # noqa: E402
import report_recipes as rr  # noqa: E402
import workflows  # noqa: E402

_cache = {}


def _report(summary="**Kenya** grew faster.", script=None):
    """One real compare report (built once per summary script), with a fake model."""
    key = json.dumps(script) if script else summary
    if key not in _cache:
        original, keys = workflows.groq_post, (groq_client.GROQ_API_KEY, groq_client.OPENROUTER_API_KEY)
        workflows.groq_post, sent = tt._fake_groq(script or [{"content": summary}])
        groq_client.GROQ_API_KEY = keys[0] or "test"
        try:
            report = workflows.compare(tt.WB, ["Kenya", "Ghana"], ["income", "growth", "poverty"])
        finally:
            workflows.groq_post = original
            groq_client.GROQ_API_KEY = keys[0]
        _cache[key] = (report, sent)
    return _cache[key]


def test_compare_tables_come_from_the_data():
    report, sent = _report()
    income = next(s for s in report["sections"] if s["heading"] == "Income")
    assert income["table"]["columns"] == ["Indicator", "Kenya", "Ghana"]
    ppp = next(r for r in income["table"]["rows"] if "PPP" in r[0])
    kenya = next(e for e in report["results"][0][2]["economies"] if e["code"] == "KEN")["latest"]
    assert ppp[1] == f"{workflows.fmt(kenya['value'])} ({kenya['year']})"
    growth = next(s for s in report["sections"] if s["heading"] == "Growth")
    assert growth["table"]["rows"][0][0].startswith("Average annual growth")  # computed in Python
    assert any("Survey-based" in n for s in report["sections"] for n in s["notes"])
    # one request, no tools, a small fact sheet
    assert len(sent) == 1 and "tools" not in sent[0] and len(json.dumps(sent[0])) < 12000


def test_summary_is_fact_checked_and_revised_once():
    report, _ = _report(summary="**Kenya** grew faster.")
    assert report["summary"]["status"] == "ok"
    bad = [{"content": "Kenya's poverty rate is 99.9%."}, {"content": "Kenya's poverty rate is still 99.9%."}]
    report, sent = _report(script=bad)
    assert len(sent) == 2 and "99.9" in sent[1]["messages"][-1]["content"]
    assert report["summary"]["status"] == "unverified" and report["summary"]["unverified"] == ["99.9"]


def test_report_without_groq_still_builds():
    keys = groq_client.GROQ_API_KEY, groq_client.OPENROUTER_API_KEY
    groq_client.GROQ_API_KEY = groq_client.OPENROUTER_API_KEY = None
    try:
        report = workflows.compare(tt.WB, ["KEN", "GHA"], ["health"])
    finally:
        groq_client.GROQ_API_KEY, groq_client.OPENROUTER_API_KEY = keys
    # the free Python summary stands in, saying why
    assert report["summary"]["status"] == "python" and "GROQ_API_KEY" in report["summary"]["reason"]
    assert report["sections"][0]["table"]["rows"]


def test_unticked_summary_is_written_by_python_for_free():
    original = workflows.groq_post
    workflows.groq_post, sent = tt._fake_groq([])
    try:
        report = workflows.compare(tt.WB, ["Kenya", "Ghana"], ["income", "growth", "health"], write=False)
    finally:
        workflows.groq_post = original
    s = report["summary"]
    assert sent == [] and s["status"] == "python" and s["tokens"] == 0
    lines = s["text"].splitlines()
    assert len(lines) >= 5 and all(l.startswith("- **") for l in lines)
    assert any("Average annual growth" in l for l in lines) and any("times as high" in l for l in lines)
    # every number in it comes from the fact sheet
    assert workflows.verify.unsupported_numbers(s["text"], json.dumps(report["facts"]), "") == []
    assert "listed by Python" in report_export.summary_status(report)


def test_compare_rejects_bad_input():
    for countries, topics in ((["Kenya"], None), (["Kenya", "Congo"], None), (["Kenya", "Ghana"], ["weather"])):
        try:
            workflows.compare(tt.WB, countries, topics)
        except ValueError:
            continue
        raise AssertionError(f"accepted {countries} {topics}")


def test_every_export_format():
    report, _ = _report()
    report = {**report, "summary": {**report["summary"], "text": "Costs 5% & $3 for **Côte d'Ivoire** ≥ 2.\n\n- a list item"}}
    pdf = report_export.export(report, "pdf")
    assert pdf[:5] == b"%PDF-" and len(pdf) > 5000
    word = zipfile.ZipFile(io.BytesIO(report_export.export(report, "docx")))
    assert "Côte" in word.read("word/document.xml").decode()
    md = zipfile.ZipFile(io.BytesIO(report_export.export(report, "md")))
    text = md.read("report.md").decode()
    assert "| Indicator | Kenya | Ghana |" in text and "references.bib" in md.namelist()
    assert all(n in md.namelist() for n in text.split("](")[1:] and [l.split(")")[0] for l in text.split("](")[1:]])
    tex = zipfile.ZipFile(io.BytesIO(report_export.export(report, "tex"))).read("report.tex").decode()
    assert r"5\% \& \$3" in tex and r"\textbf{Côte d'Ivoire} $\geq$" in tex and r"\begin{itemize}" in tex
    assert report_export.export(report, "bib").decode().startswith("@misc{worldbank_wdi,")
    as_json = json.loads(report_export.export(report, "json"))
    assert as_json["title"] == report["title"] and "results" not in as_json and "facts" in as_json
    assert as_json["sections"][0]["table"] == report["sections"][0]["table"]
    assert all("/" not in c for sec in as_json["sections"] for c in sec["charts"])
    tables = zipfile.ZipFile(io.BytesIO(report_export.export(report, "csv")))
    first = tables.namelist()[0]
    rows = list(csv.reader(io.StringIO(tables.read(first).decode("utf-8-sig"))))
    assert first.startswith("01_") and rows[0] == report["sections"][0]["table"]["columns"]
    assert rows[1:] == [[str(c) for c in r] for r in report["sections"][0]["table"]["rows"]]
    nb = json.loads(report_export.export(report, "ipynb"))
    assert nb["nbformat"] == 4 and nb["cells"][0]["cell_type"] == "markdown" and report["title"] in nb["cells"][0]["source"]
    _run_notebook(nb)
    data = zipfile.ZipFile(io.BytesIO(report_export.export(report, "data", tt.WB)))
    assert {"full_series.csv", "what_the_model_saw.csv", "README.txt"} <= set(data.namelist())


def _run_notebook(nb):
    """Runs the notebook's code with Ask's Python (it has pandas and matplotlib; this project's
    .venv doesn't). Skipped when Ask isn't set up."""
    import subprocess
    ask_python = ROOT / "vendor/ask/.venv/bin/python"
    if not ask_python.exists():
        print("  (notebook not run: vendor/ask/.venv missing)")
        return
    code = "\n".join(c["source"] for c in nb["cells"] if c["cell_type"] == "code")
    run = subprocess.run([str(ask_python), "-c", code], capture_output=True, text=True, timeout=120,
                         env={"MPLBACKEND": "Agg", "PATH": "/usr/bin:/bin"})
    assert run.returncode == 0, run.stderr[-800:]


def test_year_range_and_custom_peers():
    assert workflows.year_range() == (datetime.date.today().year - workflows.WINDOW, None)
    assert workflows.year_range("2000", "2010") == (2000, 2010)
    for bad in (("1900", None), ("2010", "2000"), ("x", None)):
        try:
            workflows.year_range(*bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    report = workflows.compare(tt.WB, ["Kenya", "Ghana"], ["health"], write=False, start=2005, end=2012)
    years = [int(c.split("(")[1][:4]) for row in report["sections"][0]["table"]["rows"] for c in row[1:] if "(" in c]
    assert years and all(2005 <= y <= 2012 for y in years), years
    assert any("2005-2012" in a for a in report["about"])
    options = rr.options(tt.WB)
    assert "Kenya" in options["countries"] and "Sub-Saharan Africa" in options["regions"]
    assert "Sub-Saharan Africa" not in options["countries"]  # groups aren't countries


def test_brief_with_your_indicators_and_peer_countries():
    saved = dict(rr._loaded)
    rr._loaded.update(longrun=None, gdl=None, dhs=None, jpal=None, imf=None)  # only the World Bank parts
    try:
        report = rr.country_brief(tt.WB, "Kenya", write=False, indicators=["access to electricity"],
                                  peers=["Tanzania", "Uganda", "Kenya"])
    finally:
        rr._loaded.clear()
        rr._loaded.update(saved)
    headings = [s["heading"] for s in report["sections"]]
    assert "Your indicators" in headings and "Compared with your chosen peers" in headings
    peers = next(s for s in report["sections"] if s["heading"] == "Compared with your chosen peers")
    assert peers["table"]["columns"] == ["Indicator", "Kenya", "Tanzania", "Uganda", "Peer median"]  # Kenya once
    life = next(r for r in peers["table"]["rows"] if r[0].startswith("Life expectancy"))
    values = sorted(float(c.split(" ")[0]) for c in life[2:4])
    assert float(life[4]) == round(sum(values) / 2, 1)  # median of two = their mean
    fact = next(f for f in report["facts"]["rows"] if "peer median" in f and f["indicator"].startswith("Life"))
    assert fact["vs peer median"] in ("higher", "lower", "the same")
    for bad in (["World"], ["Kenya"]):
        try:
            rr.country_brief(tt.WB, "Kenya", write=False, peers=bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted peers {bad}")


def test_web_report_endpoints():
    import test_web
    report, _ = _report()
    app, server = test_web._serve(tt._bot())
    original = workflows.compare
    workflows.compare = lambda *a, **k: (k["emit"]("step", "World Bank: x"), report)[1]
    try:
        seen = {}
        workflows.compare = lambda *a, **k: (seen.update(k), k["emit"]("step", "World Bank: x"), report)[2]
        status, body = test_web._request(app, "POST", "/api/report",
                                         {"kind": "compare", "countries": "Kenya, Ghana", "summary": False})
        assert seen["write"] is False
        events = [json.loads(l) for l in body.decode().splitlines()]
        assert status == 200 and events[0]["text"] == "World Bank: x"
        done = events[-1]
        assert done["kind"] == "done" and done["title"] == report["title"] and "results" not in done
        assert all(c.startswith("/charts/") for s in done["sections"] for c in s["charts"])
        status, body = test_web._request(app, "GET", f"/api/report?id={done['id']}&fmt=pdf")
        assert status == 200 and body[:5] == b"%PDF-"
        assert test_web._request(app, "GET", f"/api/report?id={done['id']}&fmt=exe")[0] == 404
        # saved: after a restart it's listed, reopens, downloads, and can be deleted
        import tempfile
        import sessions
        saved = sessions.Sessions(Path(tempfile.mkdtemp()) / "s.db")
        app.saved = saved
        workflows.compare = lambda *a, **k: report
        status, body = test_web._request(app, "POST", "/api/report", {"kind": "compare", "countries": "Kenya, Ghana"})
        rid = json.loads(body.decode().splitlines()[-1])["id"]
        app.reports.clear()  # as if web.py had restarted
        listed = json.loads(test_web._request(app, "GET", "/api/reports")[1])["reports"]
        assert rid.isdigit() and [r["id"] for r in listed] == [int(rid)] and listed[0]["title"] == report["title"]
        view = json.loads(test_web._request(app, "GET", f"/api/reports/view?id={rid}")[1])
        assert view["title"] == report["title"] and view["id"] == rid and "results" not in view
        assert test_web._request(app, "GET", f"/api/report?id={rid}&fmt=json")[0] == 200
        assert test_web._request(app, "POST", "/api/reports/delete", {"id": int(rid)})[0] == 200
        assert test_web._request(app, "GET", f"/api/reports/view?id={rid}")[0] == 404
        app.saved = None
        status, body = test_web._request(app, "GET", "/api/report-options")
        assert status == 200 and "Kenya" in json.loads(body)["countries"]
        assert test_web._request(app, "POST", "/api/report", {"kind": "compare"}, origin="https://evil.example")[0] == 403
    finally:
        workflows.compare = original
        server.shutdown()


def test_world_bank_charts_use_the_whole_series():
    result = {"indicator": "X", "name": "x", "economies": [{"economy": "A", "code": "AAA", "latest": {"year": 2024, "value": 3},
              "first": {"year": 2020, "value": 1}, "series [year, value]": [[2020, 1], [2021, 5], [2022, 2], [2024, 3]]}]}
    line = charts.specs_for("get_data", {}, result)[0]
    assert line["lines"]["A"] == [(2020, 1), (2021, 5), (2022, 2), (2024, 3)]


# ------------------------------------------------------------------ brief, poverty profile, what works
def _with_fake_model(script, build):
    """build() with a scripted fake model; returns (report, requests sent)."""
    original, key = workflows.groq_post, groq_client.GROQ_API_KEY
    workflows.groq_post, sent = tt._fake_groq(script)
    groq_client.GROQ_API_KEY = key or "test"
    try:
        return build(), sent
    finally:
        workflows.groq_post, groq_client.GROQ_API_KEY = original, key


def _brief():
    if "brief" not in _cache:
        _cache["brief"] = _with_fake_model([{"content": "**Kenya** is a lower-middle-income country."}],
                                           lambda: rr.country_brief(tt.WB, "Kenya"))
    return _cache["brief"]


def test_country_brief_tables_come_from_the_data():
    report, sent = _brief()
    headings = [s["heading"] for s in report["sections"]]
    for h in ("Income", "Growth", "IMF outlook", "Long-run growth", "Poverty and inequality", "Health", "Education",
              "Differences within the country", "Research about the country"):
        assert h in headings, h
    income = report["sections"][0]
    assert income["table"]["columns"][1:4] == ["Kenya", "Lower middle income (aggregate)", "Sub-Saharan Africa (aggregate)"]
    result = next(r for _, _, r in report["results"] if r.get("indicator") == "NY.GDP.PCAP.PP.CD")
    kenya = next(e for e in result["economies"] if e["code"] == "KEN")["latest"]
    assert income["table"]["rows"][0][1] == f"{workflows.fmt(kenya['value'])} ({kenya['year']})"
    growth = next(s for s in report["sections"] if s["heading"] == "Growth")
    assert growth["table"]["rows"][0][0].startswith("Average annual growth")
    assert any("FORECAST" in r[0] for r in growth["table"]["rows"])
    # comparisons with the groups are computed, not left to the model
    ppp = next(f for f in report["facts"]["rows"] if f["indicator"].startswith("GDP per capita, PPP"))
    assert ppp["vs Lower middle income"] in ("higher", "lower", "the same")
    # one request, no tools, a small fact sheet
    assert len(sent) == 1 and "tools" not in sent[0] and len(json.dumps(sent[0])) < 12000
    assert report["summary"]["status"] == "ok"


def test_missing_data_switches_sections_off():
    saved, chunks = dict(rr._loaded), rr.CHUNKS
    rr._loaded.update(longrun=None, gdl=None, dhs=None, jpal=None, imf=None)
    rr.CHUNKS = Path("no/such/chunks.json")
    try:
        report = rr.country_brief(tt.WB, "Ghana", write=False)
        profile = rr.poverty_profile(tt.WB, "Ghana", write=False)
        works = rr.what_works(tt.WB, "deworming", write=False, library=None)
    finally:
        rr._loaded.clear()
        rr._loaded.update(saved)
        rr.CHUNKS = chunks
    off = {s["heading"]: " ".join(s["notes"]) for s in report["sections"] + profile["sections"] + works["sections"]
           if not s["table"] and not s["items"]}
    for heading in ("Long-run growth", "Differences within the country", "Research about the country",
                    "Income by region", "Randomized evaluations (J-PAL)", "From the paper library"):
        assert "not set up" in off[heading], heading
    assert report["sections"][0]["table"]["rows"] and report["summary"]["status"] == "python"
    for r in (report, profile, works):  # every format still builds
        assert report_export.export(r, "pdf")[:5] == b"%PDF-" and report_export.markdown(r)


def test_poverty_profile_computes_number_of_poor_and_survey_gaps():
    report = rr.poverty_profile(tt.WB, "Kenya", write=False)
    by_year = report["sections"][0]["table"]
    rates, _ = compute.fetch({"series": "SI.POV.DDAY", "countries": ["KEN"], "start": 1960}, tt.WB)
    pop, _ = compute.fetch({"series": "SP.POP.TOTL", "countries": ["KEN"], "start": 1960}, tt.WB)
    year = max(rates["KEN"])
    last = by_year["rows"][-1]
    assert last[0] == str(year) and last[-1] == rr.people(rates["KEN"][year] * pop["KEN"][year] / 100)
    timing = dict(report["sections"][1]["table"]["rows"])
    surveys = sorted(rates["KEN"])
    longest = max(b - a for a, b in zip(surveys, surveys[1:]))
    assert timing["Longest gap between surveys"].startswith(f"{longest} years")
    regions = next(s for s in report["sections"] if s["heading"] == "Income by region")
    assert any("not a poverty rate" in n for n in regions["notes"])
    assert any("poverty_lines_KEN" in c for c in report["sections"][0]["charts"]) or not charts.ask_available()
    s = report["summary"]
    assert s["status"] == "python" and workflows.verify.unsupported_numbers(s["text"], json.dumps(report["facts"]), "") == []


REVIEWS = [{"cite_as": "Kabeer and Waddington (2015)", "title": "Economic impacts of conditional cash transfer programmes",
            "authors": "Kabeer, Waddington", "year": 2015, "venue": "Journal of Development Effectiveness",
            "cited_by": 157, "doi": "https://doi.org/10.1080/19439342.2015.1068833", "abstract": "Transfers raised income."}]


def _works(script):
    source = next(iter(rr.PAPERS))
    library = lambda query, n: [{"source": source, "text": "Cash transfers raised consumption in the treated villages."}]
    original = rr.openalex.search
    rr.openalex.search = lambda query, n=5, from_year=None: REVIEWS
    try:
        return _with_fake_model(script, lambda: rr.what_works(tt.WB, "cash transfers", "Africa", library=library))
    finally:
        rr.openalex.search = original


def test_what_works_groups_studies_and_quotes_results():
    report, _ = _works([{"content": "Evidence is mixed (Kabeer and Waddington (2015))."}])
    glance = next(s for s in report["sections"] if s["heading"] == "Evidence at a glance (J-PAL)")
    studies = next(s for s in report["sections"] if s["heading"] == "The studies")["items"]
    assert 0 < len(studies) <= rr.MAX_STUDIES and all(i["title"].startswith("J-PAL: ") for i in studies)
    counts = [int(r[1]) if r[1].isdigit() else r[0].count(",") + 1 for r in glance["table"]["rows"]]
    assert sum(counts) >= len(studies)  # a study counts once per outcome
    assert not any("400 Main Street" in i["text"] for i in studies)  # J-PAL's site footer is cut off
    assert all("quoted" in i["text"] or "No results" in i["text"] for i in studies)
    regions = next(s for s in report["sections"] if s["heading"] == "Where the studies were done")["table"]["rows"]
    assert all("Africa" in r[0] for r in regions)  # the region filter
    facts = report["facts"]
    assert len(facts["studies"]) <= rr.FACT_STUDIES and facts["reviews"][0]["cite_as"] == "Kabeer and Waddington (2015)"
    assert len(json.dumps(facts)) < 12000  # stays well under Groq's 8K-token request limit
    assert report["summary"]["status"] == "ok"
    bib = report_export.export(report, "bib").decode()
    assert "@article{oa_kabeer" in bib and "@misc{jpal_evaluations" in bib
    text = report_export.markdown(report)
    assert "- **[J-PAL: " in text and "Kabeer, Waddington" in text


def test_what_works_citations_must_come_from_the_fact_sheet():
    bad = "Transfers work (Smith and Jones (2019); J-PAL: An Invented Study)."
    report, sent = _works([{"content": bad}, {"content": bad}])
    assert len(sent) == 2 and "Smith and Jones" in sent[1]["messages"][-1]["content"]
    s = report["summary"]
    assert s["status"] == "unverified" and "J-PAL: An Invented Study" in s["unverified"]
    assert any(u.startswith("Smith and Jones") for u in s["unverified"])
    title = report["facts"]["studies"][0]["cite_as"]
    good = f"One study found gains ({title}); a review agrees (Kabeer and Waddington (2015))."
    assert rr.citations_outside(good, json.dumps(report["facts"], ensure_ascii=False, separators=(",", ":"))) == []


def test_jpal_outcome_labels():
    assert rr.outcome_labels("Earnings and income Employment") == ["Earnings and income", "Employment"]
    assert rr.outcome_labels("Electoral participation Voter Behavior") == ["Electoral participation", "Voter Behavior"]
    assert rr.outcome_labels("Corruption and Leakages") == ["Corruption and Leakages"]


def test_web_runs_every_report_kind():
    import test_web
    report, _ = _report()
    app, server = test_web._serve(tt._bot())
    calls, saved = {}, (rr.country_brief, rr.poverty_profile, rr.what_works)
    fake = lambda name: lambda *a, **k: (calls.update({name: (a[1:], k["write"])}), report)[1]  # noqa: E731
    rr.country_brief, rr.poverty_profile, rr.what_works = fake("brief"), fake("poverty"), fake("works")
    app.library = False  # no models in this test server
    try:
        for body in ({"kind": "brief", "country": "Kenya"}, {"kind": "poverty", "country": "Kenya", "summary": False},
                     {"kind": "works", "intervention": "deworming", "region": "Kenya", "outcome": ""}):
            status, out = test_web._request(app, "POST", "/api/report", body)
            assert status == 200 and json.loads(out.decode().splitlines()[-1])["kind"] == "done"
        assert calls["brief"] == (("Kenya",), True) and calls["poverty"] == (("Kenya",), False)
        assert calls["works"][0] == ("deworming", "Kenya", "")
    finally:
        rr.country_brief, rr.poverty_profile, rr.what_works = saved
        server.shutdown()


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
