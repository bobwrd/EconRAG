"""
Tests for jpal.py ("what works" evidence) and its analyst tool. No network:
a fixture page and fixture records. Runs in about a second:

    .venv/bin/python tests/test_jpal.py
"""

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)

import analyst  # noqa: E402
import jpal  # noqa: E402
import verify  # noqa: E402

PAGE = """<html><head><title>Cash in Kenya</title></head><body>
<nav>Breadcrumb Home Evaluations Social Protection</nav><h1 class="t">Cash Transfers in Kenya</h1>
<div>Researchers: Johannes Haushofer Jeremy Shapiro Print Sample: 1,372 households Timeline: 2011 - 2013
Target group: Rural poor Outcome of interest: Consumption Intervention type: Unconditional cash transfers
Research papers: The Short-term Impact of Unconditional Cash Transfers Partners: GiveDirectly</div>
<h2>Policy issue</h2><p>Do cash transfers help?</p>
<h2>Context of the evaluation</h2><p>Rural western Kenya has high poverty.</p>
<h2>Results and policy lessons</h2><p>Transfers raised monthly consumption by USD 36 PPP and
assets by 58 percent.</p><h3>Footer</h3>
</body></html>"""


def _fixture_jpal() -> jpal.JPAL:
    path = Path(tempfile.mkdtemp(prefix="jpal_test_")) / "evaluations.json"
    records = [jpal.parse(PAGE, "https://example.org/evaluation/cash-kenya"),
               {"title": "Teaching at the Right Level in India", "url": "https://example.org/evaluation/tarl",
                "researchers": "Abhijit Banerjee", "timeline": "2013 - 2015", "sectors": "Education",
                "context_of_the_evaluation": "In India, many children cannot read.",
                "results_and_policy_lessons": "Learning camps raised reading by 0.7 standard deviations."}]
    path.write_text(json.dumps(records))
    return jpal.JPAL(path)


def test_parse_reads_fields_and_sections():
    e = jpal.parse(PAGE, "u")
    assert e["title"] == "Cash Transfers in Kenya"
    assert e["researchers"] == "Johannes Haushofer Jeremy Shapiro"
    assert e["sample"] == "1,372 households" and e["timeline"] == "2011 - 2013", e
    assert e["intervention_type"] == "Unconditional cash transfers"
    assert e["results_and_policy_lessons"].startswith("Transfers raised monthly consumption by USD 36"), e
    assert e["context_of_the_evaluation"] == "Rural western Kenya has high poverty."


def test_countries_detected_from_text():
    names = jpal._country_names()
    assert jpal.countries_in("Democratic Republic of the Congo and Kenya", names) == ["Congo, Dem. Rep.", "Kenya"]
    assert jpal.countries_in("Niger, not Nigeria", names) == ["Niger", "Nigeria"]
    assert jpal.countries_in("a study of savings groups", names) == []


def test_search_ranks_and_trims():
    lr = _fixture_jpal()
    r = lr.search("unconditional cash transfers consumption")
    assert r["evaluations"][0]["title"] == "Cash Transfers in Kenya", r
    assert r["evaluations"][0]["countries"] == ["Kenya"]
    assert lr.search("reading learning India")["evaluations"][0]["countries"] == ["India"]
    assert lr.search("zzzz")["evaluations"] == []


def test_search_drops_footer_and_ongoing_results():
    footer = " J-PAL 400 Main Street E19-201 Cambridge, MA 02142 USA"
    assert jpal.results_text({"results_and_policy_lessons": "Scores rose." + footer}) == "Scores rose."
    lr = _fixture_jpal()
    lr.evals[1]["results_and_policy_lessons"] = "Study ongoing; results forthcoming." + footer
    r = lr.search("reading learning India")["evaluations"][0]
    assert r["results"].startswith("no results reported yet"), r


def test_analyst_tool_results_count_as_evidence():
    bot = analyst.Analyst(lambda q, seen: ("", [], []), atlas=object(), wb=object())
    bot._jpal = _fixture_jpal()
    r = bot._call("search_evaluations", {"query": "cash transfers Kenya"})
    assert "Cash Transfers in Kenya" in analyst._summary("search_evaluations", r)
    passages = "\n".join(bot._passages)
    answer = "Cash raised consumption by USD 36 and assets by 58 percent (J-PAL: Cash Transfers in Kenya)."
    assert verify.unsupported_numbers(answer, "", passages) == [], verify.unsupported_numbers(answer, "", passages)
    assert verify.unsupported_numbers("Assets rose 85 percent.", "", passages) == ["85"]


def test_tool_is_listed_and_schemas_stay_small():
    assert "search_evaluations" in [t["function"]["name"] for t in analyst.TOOLS]
    assert len(json.dumps(analyst.TOOLS)) / analyst.CHARS_PER_TOKEN < 2000


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and pattern in n]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
