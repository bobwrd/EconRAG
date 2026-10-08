"""
Tool tests. Zero LLM tokens — the analyst's loop runs against
a scripted fake Groq. Run after every change:

    .venv/bin/python tests/test_tools.py          # all (~10s; FRED tests need network)
    .venv/bin/python tests/test_tools.py atlas    # only tests whose name contains "atlas"

Plain asserts, no pytest needed (pytest also collects these if installed).
Expected values come from the data files as downloaded Oct 2026 and FRED
history that no longer revises (2007-2010); if the Atlas files are re-downloaded
or FRED revises old data, update them deliberately.
"""

import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import os  # noqa: E402

os.chdir(ROOT)

import requests  # noqa: E402

import analyst  # noqa: E402
import fred  # noqa: E402
import verify  # noqa: E402
from atlas import Atlas  # noqa: E402
from groq_client import GroqUnavailable  # noqa: E402
from worldbank import WorldBank  # noqa: E402

ATLAS = Atlas()
WB = WorldBank()


def _outcomes(profile):
    return next(v for k, v in profile.items() if k.startswith("outcomes"))


def _characteristics(profile):
    return next(v for k, v in profile.items() if k.startswith("characteristics ["))


# ------------------------------------------------------------------ atlas
def test_atlas_cook_profile_values():
    p = ATLAS.county_profile("Cook", "IL")
    assert p["county"] == "Cook County, IL"
    o = _outcomes(p)
    assert o["upward_mobility"][:3] == [38.5, 40.0, 40.8], o["upward_mobility"]
    assert o["upward_mobility_black"][0] == 30.9
    assert p["county_vs_national_mobility_gap"] == -2.3


def test_atlas_cook_explanations_are_computed_not_guessed():
    p = ATLAS.county_profile("Cook", "IL")
    c = _characteristics(p)
    # the two "explanations" the model invented before the fix are average
    assert c["poverty_rate_2010"][3] == "about average"
    assert c["single_parent_share_2010"][3] == "about average"
    assert p["characteristics_consistent_with_gap"] == ["math_scores_3rd_grade_2013"]


def test_atlas_racial_shares_only_on_request():
    assert not any(k.startswith("share_") for k in _characteristics(ATLAS.county_profile("Cook", "IL")))
    assert "share_black_2010" in _characteristics(ATLAS.county_profile("Cook", "IL", demographics=True))


def test_atlas_name_matching():
    assert "ambiguous" in ATLAS.county_profile("Washington")
    assert ATLAS.county_profile("cook county", "Illinois")["county"] == "Cook County, IL"
    assert {ATLAS._label(i) for i in ATLAS.find("St. Louis")} == {"St. Louis County, MN", "St. Louis County, MO"}
    assert "error" in ATLAS.county_profile("Nowhere", "IL")


def test_atlas_rank_order_and_limits():
    r = ATLAS.rank_counties("upward_mobility", state="UT", n=100)
    values = [c["value"] for c in r["counties"]]
    assert values == sorted(values, reverse=True)
    assert len(values) <= 25
    assert all(c["children"] >= 1000 for c in r["counties"])
    assert r["counties"][0]["county"] == "Summit County, UT"
    low = ATLAS.rank_counties("upward_mobility", state="GA", order="lowest", n=3)["counties"]
    assert [c["value"] for c in low] == sorted(c["value"] for c in low)


def test_atlas_correlation_direction_in_words():
    sp = ATLAS.correlate("single_parent_share_2010", "upward_mobility")
    assert sp["weighted_correlation"] == -0.669 and "LOWER" in sp["interpretation"]
    math = ATLAS.correlate("math_scores_3rd_grade_2013", "upward_mobility")
    assert math["weighted_correlation"] > 0 and "HIGHER" in math["interpretation"]


def test_atlas_rejects_bad_arguments():
    for call in (lambda: ATLAS.correlate("share_black_2010", "upward_mobility"),
                 lambda: ATLAS.rank_counties("not_a_metric"),
                 lambda: ATLAS.rank_counties("poverty_rate_2010", race="black")):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


# ------------------------------------------------------------------ fred (network)
def test_fred_exact_peak_not_sampled():
    s = fred.series_stats("UNRATE", "2007-01-01", "2010-12-31")
    assert s["max"] == {"date": "2009-10-01", "value": 10.0}, s["max"]


def test_fred_units_and_search():
    try:
        fred.series_stats("UNRATE", units="bogus")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert fred.search_series("employment population ratio")[0]["series_id"] == "EMRATIO"


def test_fred_sparkline():
    line = fred.sparkline(list(range(100)))
    assert len(line) == 40 and line[0] == "▁" and line[-1] == "█"
    assert fred.sparkline([5, 5, 5]) == "▁▁▁"


# ------------------------------------------------------------------ world bank
def test_worldbank_country_names():
    cases = {"Ivory Coast": "CIV", "Egypt": "EGY", "South Korea": "KOR", "Vietnam": "VNM", "DRC": "COD",
             "sub-Saharan Africa": "SSF", "Turkiye": "TUR", "ken": "KEN", "Nigera": "NGA", "World": "WLD"}
    assert {name: WB.country(name) for name in cases} == cases
    for bad in ("Congo", "Atlantis"):
        try:
            WB.country(bad)
            raise AssertionError(f"{bad} should not resolve")
        except ValueError:
            pass


def test_worldbank_search_finds_core_indicators():
    cases = {"GDP per capita current US dollars": "NY.GDP.PCAP.CD", "GDP per capita PPP": "NY.GDP.PCAP.PP.CD",
             "extreme poverty rate": "SI.POV.DDAY", "life expectancy": "SP.DYN.LE00.IN",
             "under-5 mortality": "SH.DYN.MORT", "inequality Gini": "SI.POV.GINI",
             "access to electricity": "EG.ELC.ACCS.ZS", "remittances % of GDP": "BX.TRF.PWKR.DT.GD.ZS",
             "real GDP growth": "NY.GDP.MKTP.KD.ZG", "fertility rate": "SP.DYN.TFRT.IN"}
    assert {q: WB.search(q)[0]["id"] for q in cases} == cases


def test_worldbank_poverty_carries_survey_year_and_caveat():
    r = WB.get("SI.POV.DDAY", ["Ethiopia"], end=2024)
    e = r["economies"][0]
    assert e["latest"] == {"year": 2021, "value": 38.6}, e["latest"]
    assert "$3.00" in r["name"] and any("Survey-based" in n for n in r["notes"])


def test_worldbank_current_price_note():
    assert any("constant-price" in n for n in WB.get("NY.GDP.PCAP.CD", ["KEN"], 2020, 2024)["notes"])
    assert not any("constant-price" in n for n in WB.get("SP.DYN.LE00.IN", ["KEN"], 2020, 2024)["notes"])


def test_worldbank_ranking_excludes_aggregates():
    r = WB.get("SP.DYN.TFRT.IN", ["all"], order="highest", n=5)
    names = [e["economy"] for e in r["ranked"]]
    assert len(names) == 5 and not any(n in ("World", "Sub-Saharan Africa", "Low income") for n in names)
    values = [e["value"] for e in r["ranked"]]
    assert values == sorted(values, reverse=True) and r["world"]["value"] < values[-1]


def test_worldbank_forecasts_are_labeled():
    assert WB.search("GDP growth forecast")[0]["id"] == "NYGDPMKTPKDZ"
    edition = WB.forecast_edition()
    r = WB.get("NYGDPMKTPKDZ", ["Kenya"], start=2023)
    years = [y for y, _ in r["economies"][0]["series [year, value]"]]
    assert max(years) > int(edition[:4]), "forecasts should extend past the edition year"
    assert "FORECASTS" in r["notes"][0] and edition in r["notes"][0]
    ranked = WB.get("NYGDPMKTPKDZ", ["all"], n=3)["ranked"]
    assert all(e["year"] == int(edition[:4]) for e in ranked)  # this year's forecast, not 2028's


def test_worldbank_rejects_unknown_indicator():
    try:
        WB.get("NOT.AN.ID", ["KEN"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_worldbank_flags_newer_data_than_requested_end():
    # benchmark: the model passed end=2023 on its own and reported Niger's 2023 fertility, not 2024's
    r = WB.get("SP.DYN.TFRT.IN", ["Niger"], end=2023)
    e = r["economies"][0]
    assert e["latest"]["year"] == 2023
    year, value = re.match(r"newest available: (\d{4}) = ([\d.]+)", e["newer_data"]).groups()
    assert int(year) > 2023 and float(value) < e["latest"]["value"]  # fertility falling
    assert any("newer data exists" in n for n in r["notes"])
    assert "newer_data" not in WB.get("SP.DYN.TFRT.IN", ["Niger"], end=2015)["economies"][0]  # deliberate history
    assert "newer_data" not in WB.get("SP.DYN.TFRT.IN", ["Niger"])["economies"][0]


def test_worldbank_market_rate_income_carries_ppp():
    # benchmark: "Is India's GDP per capita above or below $3,000?" got only the market-rate figure
    r = WB.get("NY.GDP.PCAP.CD", ["India", "Kenya"])
    for e in r["economies"]:
        assert e["ppp"]["year"] == e["latest"]["year"] and e["ppp"]["value"] > e["latest"]["value"]
    assert "NY.GDP.PCAP.PP.CD" in r["notes"][0] and "both" in r["notes"][0]
    assert "ppp" in WB.get("NY.GNP.PCAP.CD", ["India"])["economies"][0]
    assert "ppp" not in WB.get("SP.DYN.LE00.IN", ["India"])["economies"][0]


def test_worldbank_income_group_aggregates_have_data():
    # the API returns income groups with an empty ISO3 code; they used to come back empty
    for name in ("Low income", "lower middle income countries"):
        assert WB.get("SP.DYN.TFRT.IN", [name], 2020)["economies"][0]["latest"]["year"] >= 2020


def test_worldbank_peer_comparison():
    r = WB.get("NY.GDP.PCAP.CD", ["Kenya"], peers=True)
    e = r["economies"][0]
    income, region = e["peers"]["income_group"], e["peers"]["region"]
    assert (income["group"], region["group"]) == ("Lower middle income", "Sub-Saharan Africa")
    lmc = WB.get("NY.GDP.PCAP.CD", ["Lower middle income"])["economies"][0]["latest"]
    assert income["aggregate"] == lmc
    for g in (income, region):
        assert 1 <= g["rank"] <= g["of"] and g["of"] >= 30 and 0 <= g["percentile"] <= 100
        assert g["percentile"] == round(100 * (g["of"] - g["rank"]) / g["of"])  # no ties in GDP per capita
    assert any("peers" in n for n in r["notes"])
    assert "peers" not in WB.get("NY.GDP.PCAP.CD", ["Kenya"])["economies"][0]


# ------------------------------------------------------------------ openalex (network)
def test_openalex_search_returns_citable_papers():
    import openalex
    _require_openalex()
    papers = openalex.search("microfinance randomized evaluation", n=4)
    assert papers and all(re.match(r".+ \((19|20)\d\d\)$", p["cite_as"]) for p in papers)
    titles = [p["title"] for p in papers]
    assert len(titles) == len(set(titles)), "working-paper and journal versions should be merged"
    assert any("Banerjee" in p["cite_as"] for p in papers)


def _require_openalex():
    """OpenAlex's anonymous budget (~100 searches a day) runs out on busy days: then
    these tests SKIP (like being offline) instead of failing."""
    r = requests.get("https://api.openalex.org/works", params={"search": "poverty", "per-page": 1}, timeout=20)
    if r.status_code == 429:
        raise requests.ConnectionError("OpenAlex daily budget used up (HTTP 429)")


def test_openalex_spots_invented_citations():
    import openalex
    _require_openalex()
    assert openalex.check_citation("Okonjo and Whitfield (2019)") == {"exists": False, "match": None}
    assert openalex.check_citation("Smithson and Okafor (2021)")["exists"] is False
    assert openalex.check_citation("Miguel and Kremer (2004)")["exists"] is True
    assert openalex.check_citation("no citation here") is None


def test_analyst_classifies_unsupported_citations():
    _require_openalex()
    bot = _bot()
    script = [_tool_call("search_literature", {"query": "microfinance randomized evaluation"}),
              {"content": "Banerjee et al. (2015) find modest effects; Okonjo and Whitfield (2019) disagree."},
              {"content": "Banerjee et al. (2015) find modest effects."}]
    (answer, log), sent = _with_fake(script, lambda: bot.run("microfinance?"))
    feedback = sent[2]["messages"][-1]["content"]
    assert "Okonjo and Whitfield (2019" in feedback and "likely invented" in feedback
    assert "Banerjee et al. (2015" not in feedback  # returned by the tool, so supported
    assert answer == "Banerjee et al. (2015) find modest effects." and "Not verified" not in log


# ------------------------------------------------------------------ verify
def test_verify_numbers():
    unrate = json.dumps(fred.series_stats("UNRATE", "2024-01-01", "2026-09-30"))
    # "100" isn't from a tool either, so the invented arithmetic fails on both ends
    assert verify.unsupported_numbers("employment rate = 100 − 4.2 = 95.8 %", unrate, "") == ["100", "95.8"]
    profile = json.dumps(ATLAS.county_profile("Cook", "IL"))
    ok = "38.5 vs 40.8: a gap of 2.3 points; born 1978-83, measured 2014-15, as of 2026-09-01"
    assert verify.unsupported_numbers(ok, profile, "") == []


def test_verify_rejects_random_numbers():
    # regression: any-pair arithmetic once let 100% of these through
    import random
    profile = json.dumps(ATLAS.county_profile("Cook", "IL"))
    rng = random.Random(0)
    xs = [round(rng.uniform(13, 100), 1) for _ in range(300)]
    passed = sum(not verify.unsupported_numbers(f"{x:.1f}", profile, "") for x in xs)
    assert passed / len(xs) < 0.15, f"{passed / len(xs):.0%} of random numbers passed"


def test_verify_accepts_scale_words():
    # benchmark false alarm: "≈ 237.5 million" against 237,527,782
    pop = json.dumps({"latest": {"year": 2025, "value": 237527782}})
    assert verify.unsupported_numbers("≈ 237.5 million people (237,527,782)", pop, "") == []
    assert verify.unsupported_numbers("about 0.24 billion", pop, "") == []
    assert verify.unsupported_numbers("≈ 239.5 million", pop, "") == ["239.5 million"]
    assert verify.unsupported_numbers("$2.7B", json.dumps({"v": 2.7e9}), "") == []


def test_verify_accepts_shown_arithmetic_with_rounding_and_years():
    # benchmark false alarms: 22.99 (exact ratio 22.980) and 65 (= 2025 - 1960)
    kenya = json.dumps({"first": {"year": 1960, "value": 102.824}, "latest": {"year": 2025, "value": 2362.861}})
    answer = "2,362.861 ÷ 102.824 ≈ 22.99, about 23 times; 2025 − 1960 = 65 years"
    assert verify.unsupported_numbers(answer, kenya, "") == []
    # the operands must still be shown: neither the ratio nor the span stands alone
    assert verify.unsupported_numbers("about 22.99 times over 65 years", kenya, "") == ["22.99", "65"]
    assert verify.unsupported_numbers("2,362.861 ÷ 102.824 ≈ 24.5", kenya, "") == ["24.5"]


def test_verify_ignores_numbers_in_names_and_unicode_dates():
    # benchmark false alarm: "19" from "COVID‑19" (U+2011 hyphen)
    emratio = json.dumps({"min": {"date": "2020-04-01", "value": 51.2}})
    answer = "51.2 % (April 2020, the COVID‑19 low; Covid-19; G20; CO2) (FRED: EMRATIO, 2026‑09‑15)"
    assert verify.unsupported_numbers(answer, emratio, "") == []
    assert verify.unsupported_numbers("under-5 mortality of 30.5", "{}", "") == ["30.5"]


def test_verify_citations_skip_source_tags_and_places():
    # benchmark false alarm: "Nigeria (2024" labeled a likely-invented paper
    answer = ("Life expectancy in Nigeria (2024): 54.6 years (World Bank: SP.DYN.LE00.IN, 2024); "
              "Kenya and Uganda (2023); South Africa (2022); Cook County (2015); (FRED: UNRATE, 2026-09-01); "
              "Okonjo and Whitfield (2019); Lundberg (2017)")
    assert verify.unsupported_citations(answer, "", set()) == ["Okonjo and Whitfield (2019", "Lundberg (2017"]


def test_verify_citations():
    passages = "Chetty, Raj, and Nathaniel Hendren. 2018 b. The Impacts of Neighborhoods"
    answer = ("Chetty & Hendren 2018 (paper: CreditAccess_Paper.pdf); Chetty et al. (2016); "
              "peak in Feb 2020 (FRED: EMRATIO, 2020-02-01); see fake.pdf")
    assert verify.unsupported_citations(answer, passages, {"CreditAccess_Paper.pdf"}) == \
        ["Chetty et al. (2016", "fake.pdf"]


def test_library_passages_are_labeled_with_real_citations():
    papers = json.loads((ROOT / "papers.json").read_text())
    assert set(papers) >= {p.name for p in (ROOT / "docs").glob("*.pdf")}, "a PDF in docs/ has no papers.json entry"
    label = analyst.paper_label("Banerjee_Karlan_Zinman_2015_microcredit.pdf")
    assert label.startswith('Banerjee, Karlan and Zinman (2015), "Six Randomized'), label
    passages = f"(from {label}) Microcredit has modestly positive, but not transformative, effects."
    answer = "Effects were modest (Banerjee et al. (2015)); (Banerjee, Karlan and Zinman (2015)); (Karlan (2015))"
    assert verify.unsupported_citations(answer, passages, set()) == [], answer
    assert verify.unsupported_citations("(Duflo (2012))", passages, set()) == ["Duflo (2012"]


# ------------------------------------------------------------------ analyst loop (fake Groq)
class _Response:
    def __init__(self, message):
        self._message = message

    def json(self):
        return {"choices": [{"message": self._message}]}


def _tool_call(name, args, call_id="c1"):
    return {"content": "", "tool_calls": [{"id": call_id, "type": "function",
                                            "function": {"name": name, "arguments": json.dumps(args)}}]}


def _fake_groq(script):
    """Replaces analyst.groq_post: returns scripted messages, records payloads."""
    sent = []

    def post(payload, stream=False):
        sent.append(json.loads(json.dumps(payload)))
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(item)
    return post, sent


def _bot():
    return analyst.Analyst(lambda query, exclude: ("", [], []), ATLAS, WB)


def _quietly(fn):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn()
    return result, out.getvalue()


def _with_fake(script, fn):
    original = analyst.groq_post
    analyst.groq_post, sent = _fake_groq(script)
    try:
        return _quietly(fn), sent
    finally:
        analyst.groq_post = original


def test_analyst_revises_unsupported_numbers():
    bot = _bot()
    script = [_tool_call("county_profile", {"county": "Cook", "state": "IL"}),
              {"content": "Cook is at 38.5, far below 99.9."},
              {"content": "Cook is at 38.5 vs 40.8 nationally."}]
    (answer, log), sent = _with_fake(script, lambda: bot.run("Cook?"))
    assert answer == "Cook is at 38.5 vs 40.8 nationally."
    assert "99.9" in sent[2]["messages"][-1]["content"]  # the fact-check feedback
    assert "Not verified" not in log
    assert bot.history[-1]["content"] == answer


def test_analyst_flags_what_revision_did_not_fix():
    script = [_tool_call("county_profile", {"county": "Cook", "state": "IL"}),
              {"content": "Cook: 99.9."}, {"content": "Cook: 99.9 still."}]
    (_, log), _ = _with_fake(script, lambda: _bot().run("Cook?"))
    assert "Not verified" in log and "99.9" in log


def test_analyst_last_round_sends_no_tools():
    script = [_tool_call("search_data", {"query": "x", "source": "fred"}, f"c{i}")
              for i in range(analyst.MAX_ROUNDS - 1)]
    script.append({"content": "Couldn't find it."})
    original = fred.search_series
    fred.search_series = lambda text, limit=8: []
    try:
        (answer, _), sent = _with_fake(script, lambda: _bot().run("?"))
    finally:
        fred.search_series = original
    assert answer == "Couldn't find it."
    assert "tools" in sent[0] and "tools" not in sent[-1]
    tool_msgs = [m for m in sent[-1]["messages"] if m["role"] == "tool"]
    assert "No series matched" in tool_msgs[0]["content"]


def test_analyst_routes_get_data():
    bot = _bot()
    assert "error" in bot._call("get_data", {"series": "SP.POP.TOTL"})  # worldbank needs countries
    r = bot._call("get_data", {"series": "SP.POP.TOTL", "countries": ["Nigeria"], "start": "2020", "end": "2024"})
    assert r["economies"][0]["code"] == "NGA" and r["economies"][0]["latest"]["year"] == 2024
    assert bot._call("get_data", {"series": "UNRATE", "source": "fred", "start": "2020-01-01",
                                  "end": "2020-12-31"})["max"]["value"] == 14.8


def test_analyst_routes_peers_and_fred_newer_data():
    import datetime
    bot = _bot()
    r = bot._call("get_data", {"series": "NY.GDP.PCAP.CD", "countries": ["Kenya"], "peers": True})
    assert r["economies"][0]["peers"]["income_group"]["group"] == "Lower middle income"
    # FRED: a recent end date (model assuming it's the newest) gets the newest observation attached
    recent = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
    r = bot._call("get_data", {"series": "UNRATE", "source": "fred", "start": "2024-01-01", "end": recent})
    newest = re.match(r"newest available: (\d{4}-\d{2}-\d{2}) = ", r["newer_data"]).group(1)
    assert newest > r["latest"]["date"]
    old = bot._call("get_data", {"series": "UNRATE", "source": "fred", "start": "2020-01-01", "end": "2020-12-31"})
    assert "newer_data" not in old  # a historical episode: no extra request, no distraction


def test_analyst_prompt_asks_for_explicit_threshold_answers():
    assert "above/below" in analyst.SYSTEM_PROMPT and "peers" in analyst.SYSTEM_PROMPT
    props = next(t for t in analyst.TOOLS if t["function"]["name"] == "get_data")["function"]["parameters"]
    assert props["properties"]["peers"]["type"] == "boolean"


def test_analyst_waits_out_short_rate_limits():
    script = [GroqUnavailable("429", retry_after=0.01), {"content": "Done."}]
    (answer, log), _ = _with_fake(script, lambda: _bot().run("?"))
    assert answer == "Done." and "rate limit" in log


def test_analyst_gives_up_on_long_rate_limits():
    script = [GroqUnavailable("429", retry_after=3600)]
    try:
        _with_fake(script, lambda: _bot().run("?"))
    except GroqUnavailable:
        return
    raise AssertionError("expected GroqUnavailable")


def test_analyst_trims_requests_to_fit():
    bot = _bot()
    messages = [{"role": "user", "content": "q"},
                *({"role": "tool", "tool_call_id": str(i), "content": "x" * 20000} for i in range(3))]
    bot._fit(messages)
    size = (len(json.dumps(messages)) + bot._tools_chars) / analyst.CHARS_PER_TOKEN
    assert size <= analyst.REQUEST_TOKEN_LIMIT


def test_tool_schemas_stay_small():
    # re-sent with every request against Groq's free 8K tokens/minute limit
    assert len(json.dumps(analyst.TOOLS)) / analyst.CHARS_PER_TOKEN < 2000


def test_openrouter_backup_only_for_long_groq_outages():
    import groq_client as gc

    class Resp:
        def __init__(self, status, body, retry=None):
            self.status_code, self._body, self.headers = status, body, ({"retry-after": retry} if retry else {})
            self.text = json.dumps(body)

        def json(self):
            return self._body

    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw["json"]["model"]))
        if "groq" in url:
            return replies.pop(0)
        return Resp(200, {"choices": [{"message": {"content": "ok"}}]})

    saved = (gc._session.post, gc.OPENROUTER_API_KEY, gc._groq_blocked_until, gc.USAGE_DIR)
    gc._session.post, gc.OPENROUTER_API_KEY, gc._groq_blocked_until = fake_post, "test-key", 0.0
    gc.USAGE_DIR = Path(tempfile.mkdtemp())  # token counts go to a temporary folder, not data/usage
    try:
        replies = [Resp(429, {"error": {"message": "Rate limit ... tokens per minute"}}, "6")]
        try:
            gc.post({"messages": []})
            raise AssertionError("a short (<=10s) per-minute wait should stay on Groq")
        except gc.GroqUnavailable:
            pass
        replies = [Resp(429, {"error": {"message": "Rate limit ... tokens per minute"}}, "25")]
        assert gc.post({"messages": []}).json()["choices"][0]["message"]["content"] == "ok"  # longer: switch
        gc._groq_blocked_until = 0.0
        replies = [Resp(429, {"error": {"message": "Rate limit ... tokens per day (TPD)"}}, "700")]
        assert gc.post({"messages": []}).json()["choices"][0]["message"]["content"] == "ok"
        assert gc.last_provider == "openrouter" and calls[-1] == (gc.OPENROUTER_URL, gc.OPENROUTER_MODEL)
        gc.post({"messages": []})  # Groq is skipped while its daily cap lasts
        assert [u for u, _ in calls].count(gc.GROQ_URL) == 3, calls
        assert gc.usage()["requests"] == 3  # each answered request is counted, failed ones aren't
    finally:
        gc._session.post, gc.OPENROUTER_API_KEY, gc._groq_blocked_until, gc.USAGE_DIR = saved
        gc.last_provider = "groq"


def test_each_number_in_an_answer_is_traced_to_where_it_came_from():
    result = {"economies": [{"economy": "Kenya", "latest": {"year": 2022, "value": 36.13}},
                            {"economy": "Ghana", "latest": {"year": 2022, "value": 34.1}}],
              "note": "share 0.254 of people"}
    evidence = {"results": [("get_data", {"series": "SI.POV.DDAY", "countries": ["KEN", "GHA"]}, result)],
                "structured": [json.dumps(result)], "sources": set(),
                "passages": ["[Banerjee et al. (2015)] Consumption rose by 41.7 percent among borrowers."]}
    answer = ("Kenya's rate was 36.1% in 2022, 2.0 points above Ghana's 34.1; a quarter (25.4%) of people. "
              "A study found 41.7 percent. Somewhere it is 77.7.")
    found = {n["number"]: n for n in verify.locate_numbers(answer, evidence)}
    kenya = found["36.1"]
    assert kenya["status"] == "found" and kenya["sources"][0]["tool"] == "get_data"
    assert kenya["sources"][0]["path"] == "economies[0].latest.value"
    assert kenya["sources"][0]["context"] == {"year": 2022, "value": 36.13}
    assert found["25.4"]["status"] == "found" and found["25.4"]["sources"][0]["path"] == "note"  # 0.254 = 25.4%
    assert found["2.0"]["status"] == "computed"   # 36.1 - 34.1, both in the answer
    assert found["41.7"]["status"] == "found" and "Banerjee" in found["41.7"]["quotes"][0]
    assert found["77.7"]["status"] == "not found" and not found["77.7"]["sources"]
    assert "2022" not in found  # years aren't numbers to trace


def test_token_use_is_totaled_over_the_last_24_hours():
    import groq_client as gc
    from datetime import datetime, timedelta
    folder, now = Path(tempfile.mkdtemp()), datetime(2026, 10, 9, 10, 0)
    for hours_ago, provider, total in ((30, "groq", 50_000), (20, "groq", 30_000), (1, "groq", 12_000),
                                       (1, "openrouter", 9_000)):
        gc.record_usage(provider, {"prompt_tokens": total - 100, "completion_tokens": 100, "total_tokens": total},
                        now - timedelta(hours=hours_ago), folder)
    assert sorted(p.name for p in folder.iterdir()) == ["2026-10-08.jsonl", "2026-10-09.jsonl"]
    u = gc.usage(now, folder)  # the 30-hour-old request has dropped out of Groq's rolling window
    assert (u["groq"], u["openrouter"], u["requests"], u["groq_left"]) == (42_000, 9_000, 3, 158_000), u
    assert gc.usage(now, Path(tempfile.mkdtemp()))["groq"] == 0


def test_followups_may_reuse_numbers_from_previous_answers():
    bot = _bot()
    bot.history = [{"role": "user", "content": "Which countries are poorest?"},
                   {"role": "assistant", "content": "DRC 85.3% (2020), Mozambique 81.4% (2022) (Banerjee et al. (2015))."}]
    numbers, citations = bot._check("As noted, the DRC's 85.3% rate (Banerjee et al. (2015)) reflects conflict.")
    assert numbers == [] and citations == [], (numbers, citations)
    assert bot._check("The DRC's rate is 91.2%.")[0] == ["91.2"]  # new numbers are still checked
    assert "these results" in analyst.SYSTEM_PROMPT and "Answer first, in plain prose" in analyst.SYSTEM_PROMPT
    assert analyst.clean_markers("Conflict matters【search_literature: Arndt et al. (2016)】; 10.4%【get_data】.") == \
        "Conflict matters (Arndt et al. (2016)); 10.4%."
    # Nemotron's bare markers (seen in the web UI, Oct 2026)
    assert analyst.clean_markers("only 50% (2†lines-5-9).\n* Gap: $20,950 5†L1-L4.") == "only 50%.\n* Gap: $20,950."


def test_analyst_skips_reworded_paper_searches():
    # the five searches from one real web-UI question (Oct 2026), ~28K tokens
    queries = ["average upward mobility United States percentile low-income children Opportunity Atlas",
               "average upward mobility percentile United States Opportunity Atlas average",
               "average upward mobility percentile United States Opportunity Atlas national average",
               "38th percentile low-income children Opportunity Atlas national average",
               "national average upward mobility percentile low-income families 25th percentile"]
    searched = []
    bot = analyst.Analyst(lambda q, exclude: (searched.append(q) or "text", ["a.pdf"], [len(searched)]), ATLAS, WB)
    results = [bot._call("search_papers", {"query": q}) for q in queries]
    assert searched == [queries[0], queries[3], queries[4]]
    assert "nearly the same" in results[1]["note"] and analyst._summary("search_papers", results[1])
    assert analyst._repeated_query("microcredit impact on women", ["deworming school attendance"]) is None


# ------------------------------------------------------------------ benchmark scorer
def test_benchmark_retest_selects_fixed_and_new_questions():
    import benchmark
    ids = {q["id"] for q in benchmark.selected(benchmark.load_questions(), "retest")}
    assert len(ids) == 22 and set(benchmark.RETEST) <= ids and "lr_kor_gha" in ids, ids
    assert "wb_bra_gini" not in ids  # passed cleanly the first time
    assert [q["id"] for q in benchmark.selected(benchmark.load_questions(), "wb_bra_gini")] == ["wb_bra_gini"]


def test_benchmark_number_parsing_and_grading():
    import benchmark
    assert benchmark.numbers("232.7 million; $2,362.86; 11.3 pp") == [232.7e6, 2362.86, 11.3]
    q = {"truth": {"abs_tol": 0.5, "must_year": True}, "must": ["survey"]}
    truth = {"values": [38.6], "year": "2021", "label": "x"}
    assert benchmark.grade(q, truth, "38.6% in the 2021 survey") == []
    assert len(benchmark.grade(q, truth, "38.6% today")) == 2


# ------------------------------------------------------------------ runner
def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and pattern in n]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except requests.ConnectionError as e:
            skipped += 1
            print(f"  SKIP  {name} ({'OpenAlex budget used up' if 'OpenAlex' in str(e) else 'no network'})")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{len(tests) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
