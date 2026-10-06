"""
Report tests (workflows.py, report_export.py, the web endpoints). Zero LLM
tokens: the summary request goes to a scripted fake. World Bank data is real
(network), like tests/test_tools.py. Run after changing any of those files:

    .venv/bin/python tests/test_reports.py
"""

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
import groq_client  # noqa: E402
import report_export  # noqa: E402
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
    data = zipfile.ZipFile(io.BytesIO(report_export.export(report, "data", tt.WB)))
    assert {"full_series.csv", "what_the_model_saw.csv", "README.txt"} <= set(data.namelist())


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
        assert test_web._request(app, "POST", "/api/report", {"kind": "compare"}, origin="https://evil.example")[0] == 403
    finally:
        workflows.compare = original
        server.shutdown()


def test_world_bank_charts_use_the_whole_series():
    result = {"indicator": "X", "name": "x", "economies": [{"economy": "A", "code": "AAA", "latest": {"year": 2024, "value": 3},
              "first": {"year": 2020, "value": 1}, "series [year, value]": [[2020, 1], [2021, 5], [2022, 2], [2024, 3]]}]}
    line = charts.specs_for("get_data", {}, result)[0]
    assert line["lines"]["A"] == [(2020, 1), (2021, 5), (2022, 2), (2024, 3)]


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
