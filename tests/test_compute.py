"""
Tests for compute.py (the run_python tool): the sandbox computes, blocks the
network and writes outside its folder, reports errors, and its printed numbers
count as tool results for the fact-check. Uses the local long-run files (no
network for data). A few seconds:

    .venv/bin/python tests/test_compute.py
"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)

import analyst  # noqa: E402
import compute  # noqa: E402
import verify  # noqa: E402


def test_sandbox_computes_with_devecon_and_numpy():
    r = compute.run("print(round(devecon.cagr(100, 200, 10), 3), np.mean([1, 2, 3]))", {}, {})
    assert r == {"output": "7.177 2.0\n"}, r


def test_sandbox_blocks_network_and_outside_writes():
    r = compute.run("import socket\nsocket.create_connection(('1.1.1.1', 80), timeout=3)", {}, {})
    assert "error" in r and "rror" in r["error"], r
    target = Path.home() / "run_python_sandbox_test.txt"
    r = compute.run(f"open({str(target)!r}, 'w').write('x')", {}, {})
    assert "PermissionError" in r.get("error", "") and not target.exists(), r


def test_errors_and_silent_scripts_are_reported():
    assert "ZeroDivisionError" in compute.run("print(1 / 0)", {}, {})["error"]
    assert "print()" in compute.run("x = 1", {}, {})["note"]
    assert "at most" in compute.run_python("print(1)", [{"series": "x"}] * 7)["error"]


def test_analyst_tool_on_local_longrun_data():
    bot = analyst.Analyst(lambda q, seen: ("", [], []), atlas=object(), wb=object())
    code = ('kor, gha = DATA["mpd.gdppc"]["KOR"], DATA["mpd.gdppc"]["GHA"]\n'
            'print(f"ratio 2022: {kor[2022] / gha[2022]:.2f}")\n'
            'print(f"Korea growth 1960-2022: {devecon.cagr(kor[1960], kor[2022], 62):.2f}% a year")')
    r = bot._call("run_python", {"code": code, "data": [
        {"source": "longrun", "series": "mpd.gdppc", "countries": ["South Korea", "Ghana"]}]})
    assert r["output"] == "ratio 2022: 9.74\nKorea growth 1960-2022: 5.44% a year\n", r  # Maddison via OWID
    assert analyst._summary("run_python", r).startswith("ratio 2022: 9.74"), r
    # printed numbers are tool results, so the fact-check accepts them (and still flags others)
    bot._structured.append(str(r))  # what Analyst.run does with every non-passage tool result
    assert verify.unsupported_numbers("Korea grew 5.44% a year; 9.74 times richer.", "\n".join(bot._structured), "") == []
    assert verify.unsupported_numbers("Korea grew 6.1% a year.", "\n".join(bot._structured), "") == ["6.1"]


def test_charts_drawn_from_tool_results_without_a_model():
    import re
    import tempfile
    import charts
    wb = {"indicator": "NY.GDP.PCAP.KD", "name": "GDP per capita", "economies": [
        {"economy": "China", "first": {"year": 1990, "value": 1500}, "latest": {"year": 2023, "value": 25067},
         "max": {"year": 2023, "value": 25067}, "min": {"year": 1990, "value": 1500},
         "sampled [year, value] (subsample: may miss peaks)": [[2000, 4000], [2010, 11000]]}]}
    gdl = {"metric": "shdi", "name": "HDI", "units": "index", "source": "Global Data Lab, v10.2", "countries": [
        {"country": "Kenya", "year": 2023, "national": 0.628,
         "regions [name, value] (highest first)": [["Nairobi", 0.694], ["...", "2 more"], ["North Eastern", 0.487]]}]}
    paths = charts.auto([("get_data", {}, wb), ("get_data", {}, gdl), ("run_python", {}, {"output": "1"})],
                        Path(tempfile.mkdtemp()), use_ask=False)  # the SVG fallback
    assert [p.name.split("_", 2)[2] for p in paths] == ["NY.GDP.PCAP.KD.svg", "shdi_Kenya.svg"], paths
    line, bars = (p.read_text() for p in paths)
    assert "China 25.1k" in line and "Source: World Bank" in line
    top = max(float(m) for m in re.findall(r'>(\d+(?:\.\d+)?)k</text>', line))
    assert top >= 25.067, top  # the axis reaches the highest value
    assert "Nairobi" in bars and "North Eastern" in bars and "national 0.628" in bars and "..." not in bars


def test_ask_draws_png_line_and_map():
    import tempfile
    import charts
    if not charts.ask_available():
        print("        (vendor/ask not set up: skipped)")
        return
    econ = [{"economy": n, "code": c, "latest": {"year": 2023, "value": v}, "first": {"year": 2000, "value": v / 2},
             "max": {"year": 2023, "value": v}, "min": {"year": 2000, "value": v / 2}}
            for n, c, v in [("Kenya", "KEN", 6), ("Ghana", "GHA", 7), ("India", "IND", 9), ("Brazil", "BRA", 18),
                            ("Nigeria", "NGA", 5)]]
    paths = charts.auto([("get_data", {}, {"indicator": "X.Y", "name": "Test", "economies": econ})],
                        Path(tempfile.mkdtemp()))
    assert [p.suffix for p in paths] == [".png", ".png"] and "map" in paths[1].name, paths


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
