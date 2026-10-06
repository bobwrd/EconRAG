"""
The analyst's run_python tool (ROADMAP Phase 2): calculations the other tools
don't do — growth needed to hit a target, projections, regressions,
population-weighted averages, convergence across many countries.

The model lists the data it needs; this process fetches it with the existing
modules (network allowed here), then runs the model's script in a separate
Python process under macOS's sandbox-exec with no network, writes only to its
own temp folder, a CPU-time limit and a wall-clock timeout, and an output cap.
Inside, the script sees:
    DATA[name][code] = {year: value}   (code = ISO3; FRED series under "US")
    NAMES[code] = country name
    devecon (tested formulas), numpy as np, scipy.stats, math, statistics

Fine for personal use. Not a security boundary for untrusted users: before a
public web UI (Phase 6), run it in a container or VM instead.
"""

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TIMEOUT = 15        # seconds, wall clock
CPU_SECONDS = 10
MAX_OUTPUT = 3000   # characters of stdout returned to the model
MAX_SPECS = 6
MAX_POINTS = 20000  # data points across all specs (keeps the JSON handed over small)
SOURCES = ("worldbank", "longrun", "gdl", "fred")
SANDBOX = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{tmp}") (subpath "/private{tmp}") (literal "/dev/null"))
"""
PRELUDE = """\
import json, math, statistics, sys
sys.path.insert(0, {root!r})
import numpy as np
from scipy import stats
import devecon
_raw = json.load(open({data!r}))
DATA = {{name: {{c: {{int(y) if y.lstrip("-").isdigit() else y: v for y, v in s.items()}}
                for c, s in d.items()}} for name, d in _raw["data"].items()}}
NAMES = _raw["names"]
del _raw
"""


def fetch(spec: dict, wb=None, longrun=None, gdl=None) -> tuple[dict[str, dict], dict[str, str]]:
    """One data spec -> ({code: {year: value}}, {code: name}) from the matching module."""
    source = spec.get("source", "worldbank")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    series, countries = spec["series"], spec.get("countries") or []
    start, end = spec.get("start"), spec.get("end")
    year = lambda v: int(str(v)[:4]) if v else None  # noqa: E731
    out, names = {}, {}
    if source == "worldbank":
        all_ = [c.lower() for c in countries] == ["all"]
        codes = ["all"] if all_ else [wb.country(c) for c in countries]
        if not codes:
            raise ValueError("worldbank data needs countries (names, ISO3, or [\"all\"])")
        for r in wb._observations(series, codes, year(start) or 1960, year(end) or 2100):
            code = wb._code(r)
            if r["value"] is None or not code or (all_ and wb.countries.get(code, {}).get("aggregate", True)):
                continue
            out.setdefault(code, {})[int(r["date"])] = float(r["value"])
            names[code] = r["country"]["value"]
    elif source == "longrun":
        vid = longrun._var(series)
        codes = list(longrun.series[vid]) if [c.lower() for c in countries] == ["all"] else \
            [longrun.country(c) for c in countries]
        for code in codes:
            obs = longrun._obs(vid, code, year(start), year(end))
            if obs:
                out[code] = dict(obs)
                names[code] = longrun.names[code]
    elif source == "gdl":
        metric, sex = gdl._metric(series)
        codes = list(gdl.national_code) if [c.lower() for c in countries] == ["all"] else \
            [gdl.country(c) for c in countries]
        for iso3 in codes:
            keys = [(iso3, gdl.national_code.get(iso3, ""))]
            if spec.get("regions"):
                keys += [k for k in gdl.rows if k[0] == iso3 and k[1] != gdl.national_code.get(iso3)]
            for key in keys:
                s = {y: v for y, v in gdl._series(key, metric, sex).items()
                     if (year(start) is None or y >= year(start)) and (year(end) is None or y <= year(end))}
                if s:
                    label = iso3 if key[1] == gdl.national_code.get(iso3) else f"{iso3}:{gdl.region_name[key[1]]}"
                    out[label] = s
                    names[label] = gdl.country_name[iso3] + ("" if label == iso3 else f": {gdl.region_name[key[1]]}")
    else:  # fred: one US series, keyed by date
        import fred
        params = {"series_id": series, "units": spec.get("units") or "lin"}
        if start:
            params["observation_start"] = str(start)
        if end:
            params["observation_end"] = str(end)
        obs = fred._get(fred.FRED_URL, **params)["observations"]
        out["US"] = {o["date"]: float(o["value"]) for o in obs if o["value"] != "."}
        names["US"] = "United States"
    if not out:
        raise ValueError(f"no data for {series} ({source}) for {countries} in {start}-{end}")
    return out, names


def run(code: str, data: dict[str, dict], names: dict[str, str]) -> dict:
    """Runs `code` in the sandbox with DATA and NAMES defined; returns its printed output."""
    with tempfile.TemporaryDirectory(prefix="run_python_") as tmp:
        tmp = str(Path(tmp).resolve())
        data_file = Path(tmp) / "data.json"
        data_file.write_text(json.dumps({"data": {k: {c: {str(y): v for y, v in s.items()} for c, s in d.items()}
                                                  for k, d in data.items()}, "names": names}))
        script = PRELUDE.format(root=str(ROOT), data=str(data_file)) + code
        profile = SANDBOX.format(tmp=tmp.removeprefix("/private"))

        def limits():
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))

        try:
            proc = subprocess.run(["sandbox-exec", "-p", profile, sys.executable, "-B", "-c", script],
                                  capture_output=True, text=True, timeout=TIMEOUT, cwd=tmp,
                                  preexec_fn=limits, env={"PATH": "/usr/bin:/bin", "HOME": tmp})
        except subprocess.TimeoutExpired:
            return {"error": f"timed out after {TIMEOUT}s — simplify the calculation"}
    output = proc.stdout
    result = {"output": output[:MAX_OUTPUT] + ("\n... (output cut)" if len(output) > MAX_OUTPUT else "")}
    if proc.returncode != 0:
        result["error"] = "\n".join(proc.stderr.strip().splitlines()[-6:])[-800:]
    elif not output.strip():
        result["note"] = "the script printed nothing: print() every number you want to use"
    return result


def run_python(code: str, specs: list[dict], wb=None, longrun=None, gdl=None) -> dict:
    """The tool: fetch each spec, then run the script on them."""
    if len(specs) > MAX_SPECS:
        return {"error": f"at most {MAX_SPECS} data specs per call"}
    data, names, used = {}, {}, []
    for spec in specs:
        series, n = fetch(spec, wb, longrun, gdl)
        name = spec.get("name") or spec["series"]
        data[name] = series
        names.update(n)
        years = sorted({y for s in series.values() for y in s}, key=str)
        used.append(f"DATA[{name!r}]: {spec.get('source', 'worldbank')} {spec['series']}, {len(series)} "
                    f"countries/series, {years[0]}-{years[-1]}" if years else f"DATA[{name!r}]: empty")
    if sum(len(s) for d in data.values() for s in d.values()) > MAX_POINTS:
        return {"error": f"more than {MAX_POINTS} data points: narrow the years or countries"}
    return {"data": used, **run(code, data, names)}


if __name__ == "__main__":  # quick manual check: python compute.py
    print(json.dumps(run("print(round(devecon.cagr(100, 200, 10), 3))", {}, {}), indent=1))
    print(json.dumps(run("import urllib.request\nurllib.request.urlopen('https://example.com', timeout=5)", {}, {}),
                     indent=1)[:400])
    print(json.dumps(run(f"open({str(Path.home() / 'sandbox_test.txt')!r}, 'w').write('x')", {}, {}), indent=1)[:400])
