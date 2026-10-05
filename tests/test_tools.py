"""
ROADMAP Phase 0a: tool tests. Zero LLM tokens — the analyst's loop runs against
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


def test_worldbank_rejects_unknown_indicator():
    try:
        WB.get("NOT.AN.ID", ["KEN"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ------------------------------------------------------------------ openalex (network)
def test_openalex_search_returns_citable_papers():
    import openalex
    papers = openalex.search("microfinance randomized evaluation", n=4)
    assert papers and all(re.match(r".+ \((19|20)\d\d\)$", p["cite_as"]) for p in papers)
    titles = [p["title"] for p in papers]
    assert len(titles) == len(set(titles)), "working-paper and journal versions should be merged"
    assert any("Banerjee" in p["cite_as"] for p in papers)


def test_openalex_spots_invented_citations():
    import openalex
    assert openalex.check_citation("Okonjo and Whitfield (2019)") == {"exists": False, "match": None}
    assert openalex.check_citation("Smithson and Okafor (2021)")["exists"] is False
    assert openalex.check_citation("Miguel and Kremer (2004)")["exists"] is True
    assert openalex.check_citation("no citation here") is None


def test_analyst_classifies_unsupported_citations():
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


def test_verify_citations():
    passages = "Chetty, Raj, and Nathaniel Hendren. 2018 b. The Impacts of Neighborhoods"
    answer = ("Chetty & Hendren 2018 (paper: CreditAccess_Paper.pdf); Chetty et al. (2016); "
              "peak in Feb 2020 (FRED: EMRATIO, 2020-02-01); see fake.pdf")
    assert verify.unsupported_citations(answer, passages, {"CreditAccess_Paper.pdf"}) == \
        ["Chetty et al. (2016", "fake.pdf"]


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
    # re-sent with every request against an 8K tokens/minute limit (NOTES.md gotcha #7)
    assert len(json.dumps(analyst.TOOLS)) / analyst.CHARS_PER_TOKEN < 2000


# ------------------------------------------------------------------ benchmark scorer
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
