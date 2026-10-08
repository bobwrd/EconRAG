"""
Web UI tests (web.py): structured results, the localhost-only guards, data
downloads, the technical rewrite. Zero LLM tokens (scripted fake Groq), no
models loaded, no network. Run after changing web.py or ui/:

    .venv/bin/python tests/test_web.py
"""

import http.client
import io
import json
import sys
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import test_tools as tt  # noqa: E402  (sets cwd and sys.path; shares the fake Groq and loaded data)

import analyst  # noqa: E402
import compute  # noqa: E402
import web  # noqa: E402

COOK = tt._tool_call("county_profile", {"county": "Cook", "state": "IL"})


def _serve(bot):
    app = web.App(analyst=bot)
    web.Handler.app = app
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    app.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return app, server


def _request(app, method, path, body=None, host=None, origin="same"):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    headers = {"Host": host or f"127.0.0.1:{app.port}", "Content-Type": "application/json"}
    if origin == "same":
        headers["Origin"] = f"http://127.0.0.1:{app.port}"
    elif origin:
        headers["Origin"] = origin
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    response = conn.getresponse()
    return response.status, response.read()


def _ask(app, question, script):
    original = analyst.groq_post
    analyst.groq_post, _ = tt._fake_groq(script)
    try:
        status, body = _request(app, "POST", "/api/ask", {"question": question})
    finally:
        analyst.groq_post = original
    return status, [json.loads(line) for line in body.decode().splitlines() if line.strip()]


def test_run_reports_events_and_structured_result():
    bot, events = tt._bot(), []
    bot.on_event = lambda kind, text, **data: events.append((kind, data))
    script = [COOK, {"content": "Cook is at 38.5, far below 99.9."}, {"content": "Cook is at 38.5, below 99.9."}]
    (answer, printed), _ = tt._with_fake(script, lambda: bot.run("Cook?"))
    assert printed.strip() == ""  # everything went to on_event, not the terminal
    kinds = [k for k, _ in events]
    assert kinds[:2] == ["tool", "revision"] and "answer" in kinds and "unverified" in kinds
    r = bot.last_result
    assert r["answer"] == answer and r["unverified"] == ["99.9"] and r["revised"]
    assert [t["name"] for t in r["tools"]] == ["county_profile"] and "Cook" in r["tools"][0]["summary"]
    assert bot.evidence()["results"][0][0] == "county_profile"


def test_server_streams_progress_then_the_result():
    app, server = _serve(tt._bot())
    try:
        status, events = _ask(app, "Cook?", [COOK, {"content": "Cook is at 38.5."}])
        assert status == 200
        assert [e["kind"] for e in events][0] == "tool" and events[-1]["kind"] == "done"
        done = events[-1]
        assert done["answer"] == "Cook is at 38.5." and done["unverified"] == [] and done["id"] == "1"
        assert done["technical"] and "Opportunity Atlas" in done["technical"][0]["source"]
        # what the model saw, as a zip with a README
        status, body = _request(app, "GET", "/api/data?id=1&kind=seen")
        z = zipfile.ZipFile(io.BytesIO(body))
        assert status == 200 and {"README.txt", "what_the_model_saw.csv", "tool_calls.json"} <= set(z.namelist())
        assert "38.5" in z.read("what_the_model_saw.csv").decode()
        status, body = _request(app, "GET", "/api/data?id=1&kind=full")
        assert status == 200 and "full_series.csv" in zipfile.ZipFile(io.BytesIO(body)).namelist()
    finally:
        server.shutdown()


def test_server_refuses_other_hosts_sites_and_paths():
    app, server = _serve(tt._bot())
    try:
        assert _request(app, "GET", "/", host="evil.example:80")[0] == 403          # DNS rebinding
        assert _request(app, "GET", "/")[0] == 200
        assert _request(app, "POST", "/api/ask", {"question": "x"}, origin=None)[0] == 403
        assert _request(app, "POST", "/api/ask", {"question": "x"}, origin="https://evil.example")[0] == 403
        assert _request(app, "GET", "/charts/..%2Fweb.py")[0] == 404
        assert _request(app, "GET", "/charts/../web.py")[0] == 404
        assert _request(app, "GET", "/api/data?id=99&kind=seen")[0] == 404
    finally:
        server.shutdown()


def test_usage_endpoint_reports_the_last_24_hours():
    import tempfile
    import groq_client
    app, server = _serve(tt._bot())
    saved = groq_client.USAGE_DIR
    groq_client.USAGE_DIR = Path(tempfile.mkdtemp())
    try:
        groq_client.record_usage("groq", {"total_tokens": 1234})
        status, body = _request(app, "GET", "/api/usage")
        u = json.loads(body)
        assert status == 200 and u["groq"] == 1234 and u["limit"] == 200_000 and u["groq_left"] == 198_766, u
        assert _request(app, "GET", "/api/usage", host="evil.example:80")[0] == 403
    finally:
        groq_client.USAGE_DIR = saved
        server.shutdown()


def test_full_series_specs():
    results = [("get_data", {"series": "SI.POV.DDAY", "countries": ["all"]}, {"ranked": []}),
               ("get_data", {"series": "lifexp", "source": "gdl", "countries": ["Kenya"]}, {}),
               ("get_data", {"series": "x", "source": "dhs", "countries": ["Kenya"]}, {}),
               ("get_data", {"series": "pwt.growth_accounting", "source": "longrun", "countries": ["KOR"]}, {}),
               ("get_data", {"series": "UNRATE", "source": "fred"}, {"error": "bad"}),
               ("run_python", {"code": "", "data": [{"series": "NY.GDP.PCAP.KD", "countries": ["GHA"]}]}, {})]
    specs = web.full_specs(results)
    assert len(specs) == 3  # dhs, growth accounting and errors are skipped
    assert specs[0]["source"] == "worldbank" and specs[0]["start"] >= 2000  # rankings: recent years
    assert specs[1]["regions"] is True


def test_technical_rewrite_is_fact_checked():
    record = {"question": "Cook?", "answer": "Cook is at 38.5.",
              "evidence": {"structured": [json.dumps({"upward_mobility": 38.5})], "passages": [], "sources": set(),
                           "results": [("county_profile", {"county": "Cook"}, {"upward_mobility": 38.5})]}}
    original = web.groq_post
    for text, flagged in (("Upward mobility is **38.5**.", []), ("It is 38.5, rank 77.7.", ["77.7"])):
        web.groq_post, sent = tt._fake_groq([{"content": text}])
        try:
            r = web.rewrite_technical(record)
        finally:
            web.groq_post = original
        assert r["text"] == text and r["unverified"] == flagged
        assert "tools" not in sent[0] and len(json.dumps(sent[0])) < 12000  # small request, no tool schemas


def test_run_python_never_runs_without_the_sandbox():
    original = compute.shutil.which
    compute.shutil.which = lambda name: None
    try:
        assert "off on this computer" in compute.run("print(1)", {}, {})["error"]
    finally:
        compute.shutil.which = original


if __name__ == "__main__":
    import traceback
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
