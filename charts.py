"""
Charts drawn by plain Python from the data the tools already fetched — no model
involved, so they cost no Groq tokens and add nothing to requests. After an
answer, analyst.py calls auto(results); each chart is an SVG file in charts/
(opens in any browser), with its source written on it.

  time series (World Bank, FRED, long-run, DHS surveys) -> line chart, one line per country
  regional breakdowns (Global Data Lab, DHS regions), rankings -> horizontal bar chart

Points come from each result's sampled values plus its first/latest/max/min,
so lines pass through the true extremes.
"""

import datetime
import html
import re
from pathlib import Path

OUT_DIR = Path("charts")
W, H = 760, 420
LEFT, RIGHT, TOP, BOTTOM = 70, 150, 50, 60
COLORS = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4b5563", "#ca8a04"]
MAX_LINES, MAX_BARS = 8, 60
BAR_ROW = 18  # px per bar: bar charts grow taller with more regions


def _x(v) -> float:
    """Year (int), "2008-09", or "2020-04-01" -> decimal year."""
    s = str(v)
    m = re.match(r"(\d{4})(?:-(\d{2})-(\d{2}))?", s)
    if not m:
        raise ValueError(s)
    year = int(m.group(1))
    return year + (int(m.group(2)) - 1) / 12 if m.group(2) else float(year)


def _nice(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round tick values covering [lo, hi]."""
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    start = int(lo // step) * step
    ticks, t = [start], start
    while ticks[-1] < hi - step * 1e-9:  # end on the first tick at or above hi
        t += step
        ticks.append(round(t, 10))
    return ticks


def _fmt(v: float) -> str:
    for threshold, scale, suffix in ((1e9, 1e9, "B"), (1e6, 1e6, "M"), (1e4, 1e3, "k")):
        if abs(v) >= threshold:
            return f"{v / scale:.3g}{suffix}"
    return f"{v:.4g}"


def _frame(title: str, source: str, h: int = H) -> list[str]:
    esc = html.escape
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12">',
            f'<rect width="{W}" height="{h}" fill="white"/>',
            f'<text x="{LEFT}" y="24" font-size="15" font-weight="bold">{esc(title[:95])}</text>',
            f'<text x="{LEFT}" y="{h - 12}" fill="#555" font-size="10.5">{esc(("Source: " + source)[:140])}</text>']


def line_chart(lines: dict[str, list[tuple[float, float]]], title: str, units: str, source: str) -> str:
    lines = {k: sorted(set(v)) for k, v in list(lines.items())[:MAX_LINES] if len(set(v)) >= 2}
    if not lines:
        return ""
    xs = [x for pts in lines.values() for x, _ in pts]
    ys = [y for pts in lines.values() for _, y in pts]
    yt = _nice(min(0.0, min(ys)) if min(ys) >= 0 and min(ys) < 0.35 * max(ys) else min(ys), max(ys))
    x0, x1, y0, y1 = min(xs), max(xs), yt[0], yt[-1]
    px = lambda x: LEFT + (x - x0) / ((x1 - x0) or 1) * (W - LEFT - RIGHT)  # noqa: E731
    py = lambda y: H - BOTTOM - (y - y0) / ((y1 - y0) or 1) * (H - TOP - BOTTOM)  # noqa: E731
    out = _frame(title, source)
    for t in yt:
        out.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{py(t):.1f}" y2="{py(t):.1f}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{LEFT - 6}" y="{py(t) + 4:.1f}" text-anchor="end" fill="#444">{_fmt(t)}</text>')
    for t in _nice(x0, x1, 6):
        if x0 <= t <= x1:
            out.append(f'<text x="{px(t):.1f}" y="{H - BOTTOM + 18}" text-anchor="middle" fill="#444">'
                       f'{int(t) if t == int(t) else round(t, 1)}</text>')
    out.append(f'<text x="14" y="{TOP + (H - TOP - BOTTOM) / 2:.0f}" fill="#444" '
               f'transform="rotate(-90 14 {TOP + (H - TOP - BOTTOM) / 2:.0f})" text-anchor="middle">'
               f'{html.escape(units[:60])}</text>')
    for i, (label, pts) in enumerate(lines.items()):
        color = COLORS[i % len(COLORS)]
        path = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts)
        out.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.2"/>')
        lx, ly = pts[-1]
        out.append(f'<text x="{px(lx) + 6:.1f}" y="{py(ly) + 4:.1f}" fill="{color}">'
                   f'{html.escape(label[:22])} {_fmt(round(ly, 2))}</text>')
    return "\n".join(out + ["</svg>"])


def bar_chart(bars: list[tuple[str, float]], title: str, units: str, source: str,
              reference: tuple[str, float] | None = None) -> str:
    bars = bars[:MAX_BARS]
    if not bars:
        return ""
    vals = [v for _, v in bars] + ([reference[1]] if reference else [])
    ticks = _nice(min(0.0, min(vals)), max(vals))
    lo, hi = ticks[0], ticks[-1]
    left, h = 190, TOP + BOTTOM + BAR_ROW * len(bars)
    px = lambda v: left + (v - lo) / ((hi - lo) or 1) * (W - left - 70)  # noqa: E731
    row = BAR_ROW
    out = _frame(title, source, h)
    for t in ticks:
        out.append(f'<line x1="{px(t):.1f}" x2="{px(t):.1f}" y1="{TOP}" y2="{h - BOTTOM}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{px(t):.1f}" y="{h - BOTTOM + 16}" text-anchor="middle" fill="#444">{_fmt(t)}</text>')
    for i, (label, v) in enumerate(bars):
        y = TOP + i * row
        out.append(f'<rect x="{px(min(0, v) if lo < 0 else lo):.1f}" y="{y + row * 0.15:.1f}" '
                   f'width="{abs(px(v) - px(max(lo, min(0, v)))):.1f}" height="{row * 0.7:.1f}" fill="{COLORS[0]}"/>')
        out.append(f'<text x="{left - 6}" y="{y + row * 0.62:.1f}" text-anchor="end">{html.escape(label[:28])}</text>')
        out.append(f'<text x="{px(v) + 4:.1f}" y="{y + row * 0.62:.1f}" fill="#333">{_fmt(round(v, 3))}</text>')
    if reference:
        x = px(reference[1])
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{TOP - 6}" y2="{h - BOTTOM}" stroke="#dc2626" '
                   f'stroke-dasharray="5,4"/><text x="{x + 4:.1f}" y="{TOP - 8}" fill="#dc2626">'
                   f'{html.escape(reference[0])} {_fmt(reference[1])}</text>')
    out.append(f'<text x="{W - 60}" y="{h - BOTTOM + 34}" text-anchor="end" fill="#444">{html.escape(units[:60])}</text>')
    return "\n".join(out + ["</svg>"])


def _points(entry: dict, sample_key_prefix: str, extremes: tuple[str, ...], xkey: str) -> list[tuple[float, float]]:
    pts = []
    for k, v in entry.items():
        if k.startswith(sample_key_prefix) and isinstance(v, list):
            pts += [(_x(a), float(b)) for a, b, *_ in v if b is not None]
    for k in extremes:
        if isinstance(entry.get(k), dict) and entry[k].get("value") is not None:
            pts.append((_x(entry[k][xkey]), float(entry[k]["value"])))
    return pts


ATLAS_SOURCE = "Opportunity Atlas (Opportunity Insights)"


def _atlas_specs(name: str, result: dict) -> list[dict]:
    if name == "rank_counties" and result.get("counties"):
        return [{"kind": "bar", "slug": f"{result['metric']}_counties", "category": "County",
                 "title": f"{result['metric'].replace('_', ' ').capitalize()}: {result['order']} counties, "
                          f"{result['scope']}", "units": result.get("unit", ""), "source": ATLAS_SOURCE,
                 "bars": [(c["county"], c["value"]) for c in result["counties"]],
                 "keep_order": True}]  # already ranked (highest or lowest first)
    if name == "county_profile":
        key = next((k for k in result if k.startswith("outcomes")), None)
        if not key:
            return []
        rows = [(m.removeprefix("upward_mobility").strip("_").replace("_", " ").capitalize() or "All", where, v[i])
                for m, v in result[key].items() if m.startswith("upward_mobility")
                for i, where in enumerate([result["county"].split(",")[0], "State average", "US average"])
                if v[i] is not None]
        return [{"kind": "grouped_bar", "slug": "mobility_" + result["county"], "rows": rows, "category": "Children",
                 "group": "Place", "title": f"Upward mobility, {result['county']}",
                 "units": "Income percentile (kids of 25th-pct parents)", "source": ATLAS_SOURCE}]
    if name == "correlate_counties":
        key = next((k for k in result if k.startswith("y_by_x_quintile")), None)
        bins = result.get(key) if key else None
        if isinstance(bins, list) and bins:
            x, y = result["x"], result["y"]
            return [{"kind": "bar", "slug": f"{x['metric']}_vs_{y['metric']}", "category": x["metric"].replace("_", " "),
                     "bars": [(f"{b['x_range'][0]:g}-{b['x_range'][1]:g}", b["avg_y"]) for b in bins], "keep_order": True,
                     "title": f"Average {y['metric'].replace('_', ' ')} by fifth of counties on "
                              f"{x['metric'].replace('_', ' ')} (r = {result['weighted_correlation']})",
                     "units": y.get("unit", ""), "source": ATLAS_SOURCE}]
    return []


_TABLE_ROW = re.compile(r"^\s*(.+?)\s*(?:,|\t|\||:)\s*(-?\d[\d,]*\.?\d*)\s*%?\s*$")


def _table_spec(result: dict) -> list[dict]:
    """A run_python script that printed a small table ("label: value" / "label, value"
    lines, 3+ of them) gets a chart: a line if the labels are years, else bars."""
    rows = []
    for line in (result.get("output") or "").splitlines():
        m = _TABLE_ROW.match(line)
        if m:
            rows.append((m.group(1).strip(), float(m.group(2).replace(",", ""))))
        elif rows:
            break  # only the first contiguous table
    if not 3 <= len(rows) <= MAX_BARS:
        return []
    source = "Computed with run_python from the data listed in the answer"
    if all(re.fullmatch(r"(19|20)\d\d", label) for label, _ in rows):
        return [{"kind": "line", "slug": "computed_series", "lines": {"computed": [(float(l), v) for l, v in rows]},
                 "title": "Computed series", "units": "", "source": source}]
    return [{"kind": "bar", "slug": "computed_table", "bars": rows, "keep_order": True, "title": "Computed values",
             "units": "", "source": source}]


def specs_for(name: str, args: dict, result: dict) -> list[dict]:
    """Chart specs (what to draw, not how) for one tool result worth drawing."""
    if not isinstance(result, dict) or "error" in result:
        return []
    if name in ("rank_counties", "county_profile", "correlate_counties"):
        return _atlas_specs(name, result)
    if name == "run_python":
        return _table_spec(result)
    if name != "get_data":
        return []
    source = str(result.get("source", ""))
    out = []
    if "capital_share_alpha" in result:  # long-run growth accounting
        g = result["growth_pct_per_year"]
        bars = [("Output per worker", g["output_per_worker"]), ("Capital deepening", g["capital_deepening_contribution"]),
                ("Human capital", g["human_capital_contribution"]), ("TFP", g["tfp_contribution"])]
        return [{"kind": "bar", "slug": "growth_accounting_" + result["country"], "bars": bars, "keep_order": True,
                 "category": "Component", "title": f"Sources of growth, {result['country']} {result['period']}",
                 "units": "% per year", "source": source}]
    if "series_id" in result:  # FRED
        pts = _points(result, "sampled_points", ("first", "latest", "max", "min"), "date")
        out.append({"kind": "line", "slug": result["series_id"], "lines": {result["series_id"]: pts},
                    "title": result.get("title", result["series_id"]), "units": result.get("units", ""),
                    "source": f"FRED, {result['series_id']} (St. Louis Fed)"})
    elif "economies" in result:  # World Bank: one line per economy, a map when there are many
        wb_source = f"World Bank, World Development Indicators ({result['indicator']})"
        lines = {e["economy"]: _points(e, "sampled", ("first", "latest", "max", "min"), "year")
                 for e in result["economies"] if "latest" in e}
        out.append({"kind": "line", "slug": result["indicator"], "lines": lines, "title": result["name"],
                    "units": "", "source": wb_source})
        latest = {e["code"]: (e["economy"], e["latest"]["value"], e["latest"]["year"])
                  for e in result["economies"] if "latest" in e and e.get("code")}
        for e in result["economies"]:  # peers=true: the country against its groups
            peers = e.get("peers")
            if isinstance(peers, dict) and "latest" in e:
                bars = [(e["economy"], e["latest"]["value"])]
                for key in ("income_group", "region"):
                    p = peers.get(key) or {}
                    if p.get("aggregate"):
                        bars.append((f"{p['group']} (aggregate)", p["aggregate"]["value"]))
                    if p.get("median") is not None:
                        bars.append((f"{p['group']} (median country)", p["median"]))
                out.append({"kind": "bar", "slug": f"{result['indicator']}_{e['economy']}_peers", "bars": bars,
                            "keep_order": True, "category": "Compared with", "units": "",
                            "title": f"{result['name']}: {e['economy']} vs peers", "source": wb_source})
        if len(latest) >= MAP_MIN_COUNTRIES:
            out.append({"kind": "map", "slug": result["indicator"] + "_map", "values": latest,
                        "title": result["name"] + " (latest year)", "units": "", "source": wb_source})
    elif "ranked" in result and "indicator" in result:  # World Bank ranking of every economy (names only)
        codes = _wb_codes()
        latest = {codes.get(r["economy"], r["economy"]): (r["economy"], r["value"], r["year"])
                  for r in result["ranked"]}
        title = f"{result['name']}, {result.get('order', 'highest')} {len(latest)}"
        out.append({"kind": "bar", "slug": result["indicator"] + "_ranking", "title": title, "units": "",
                    "bars": [(n, v) for n, v, _ in latest.values()],
                    "source": f"World Bank, World Development Indicators ({result['indicator']})"})
        if len(latest) >= MAP_MIN_COUNTRIES:
            out.append({"kind": "map", "slug": result["indicator"] + "_map", "values": latest, "title": title,
                        "units": "", "source": f"World Bank, World Development Indicators ({result['indicator']})"})
    elif "ranked" in result and source.startswith("Global Data Lab"):
        out.append({"kind": "bar", "slug": result["metric"] + "_ranking", "units": result["units"], "source": source,
                    "title": f"{result['name']}, regions ({result['order']})",
                    "bars": [(f"{r['region']} ({r['country']})", r["value"]) for r in result["ranked"]]})
    elif "countries" in result and source.startswith("Global Data Lab"):
        for c in result["countries"]:
            rows = next((v for k, v in c.items() if k.startswith("regions [")), None)
            if rows:
                out.append({"kind": "bar", "slug": f"{result['metric']}_{c['country']}", "units": result["units"],
                            "title": f"{result['name']} by region, {c['country']} {c['year']}", "source": source,
                            "bars": [(r, v) for r, v in rows if r != "..."],
                            "reference": ("national", c["national"]) if c.get("national") is not None else None})
    elif "countries" in result and source.startswith("DHS"):
        lines = {}
        for c in result["countries"]:
            surveys = next((v for k, v in c.items() if k.startswith("surveys")), None)
            if surveys:
                lines[c["country"]] = [(_x(s[0]), float(s[1])) for s in surveys if s[1] is not None]
            reg = c.get("regions")
            rows = next((v for k, v in reg.items() if k.startswith("regions [")), None) if isinstance(reg, dict) else None
            if rows:
                out.append({"kind": "bar", "slug": f"{result['indicator']}_{c['country']}_regions", "source": source,
                            "title": f"{result['name']} by region, {c['country']} {reg.get('year', '')}",
                            "units": result.get("unit", ""), "bars": [(r[0], r[1]) for r in rows if r[0] != "..."],
                            "reference": ("national", reg["national"])
                            if isinstance(reg.get("national"), (int, float)) else None})
        out.append({"kind": "line", "slug": result["indicator"], "lines": lines, "title": result["name"],
                    "units": result.get("unit", ""), "source": source})
    elif "countries" in result and "variable" in result:  # long-run
        lines = {c["country"]: _points(c, "sampled", ("first", "last", "peak", "trough"), "year")
                 for c in result["countries"] if "last" in c}
        out.append({"kind": "line", "slug": result["variable"], "lines": lines, "title": result["name"],
                    "units": result.get("units", ""), "source": source})
    return out


def _wb_codes() -> dict[str, str]:
    """World Bank economy name -> ISO3, from the cached catalog (rankings carry names only)."""
    import json
    path = Path("data/worldbank/countries.json")
    return {c["name"]: c["id"] for c in json.loads(path.read_text())} if path.exists() else {}


def bubble_spec(results: list[tuple[str, dict, dict]], wb) -> dict | None:
    """Gapminder-style animation when an answer used two World Bank indicators for
    the same 2+ countries: x = the income-like one, y = the other, bubbles sized by
    population, one frame per year. Full yearly series come from wb (plain data
    requests, no model)."""
    by_indicator: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for name, _, r in results:
        if name == "get_data" and isinstance(r, dict) and "economies" in r and "error" not in r:
            codes = {e["code"] for e in r["economies"] if "latest" in e and e.get("code")}
            by_indicator.setdefault(r["indicator"], set()).update(codes)
            names[r["indicator"]] = r["name"]
    pairs = [(a, b) for a in by_indicator for b in by_indicator if a < b and len(by_indicator[a] & by_indicator[b]) >= 2]
    if not pairs or wb is None:
        return None
    a, b = pairs[0]
    x, y = (a, b) if re.search(r"PCAP|GNI|GDP", a) or not re.search(r"PCAP|GNI|GDP", b) else (b, a)
    codes = sorted(by_indicator[a] & by_indicator[b])[:12]
    series = {}
    for ind in (x, y, "SP.POP.TOTL"):
        series[ind] = {}
        for row in wb._observations(ind, codes, 1960, 2100):
            if row["value"] is not None:
                series[ind][(wb._code(row), int(row["date"]))] = (row["country"]["value"], float(row["value"]))
    rows = [(series[x][k][0], k[1], series[x][k][1], series[y][k][1], series["SP.POP.TOTL"][k][1] / 1e6)
            for k in series[x] if k in series[y] and k in series["SP.POP.TOTL"]]
    years = sorted({r[1] for r in rows})
    if len(years) < 3:
        return None
    step = max(1, len(years) // 40)  # keep the GIF to ~40 frames
    keep = set(years[::step]) | {years[-1]}
    return {"kind": "bubble", "slug": f"{x}_vs_{y}", "rows": [r for r in sorted(rows, key=lambda r: r[1]) if r[1] in keep],
            "xlabel": names.get(x, x), "ylabel": names.get(y, y),
            "title": f"{re.sub(r' [(].*', '', names.get(y, y))} vs {re.sub(r' [(].*', '', names.get(x, x))}",
            "source": f"World Bank, World Development Indicators ({x}, {y}, SP.POP.TOTL)"}


# ---------------------------------------------------------------- drawing with Ask
# A copy of Ask (the user's charting language) lives in vendor/ask with its own
# environment (pandas, matplotlib, geopandas). It draws PNG charts, maps, and
# animated GIFs; without it, line and bar charts fall back to the SVG code above.
ASK_DIR = Path(__file__).resolve().parent / "vendor" / "ask"
ASK_PYTHON = ASK_DIR / ".venv" / "bin" / "python"
ASK_TIMEOUT = 60
MAP_MIN_COUNTRIES = 5
_MAP_NAMES: dict[str, str] | None = None


def ask_available() -> bool:
    return ASK_PYTHON.exists()


def _map_names() -> dict[str, str]:
    """ISO3 -> the country name Ask's world map uses (it matches by name)."""
    global _MAP_NAMES
    if _MAP_NAMES is None:
        import json
        geo = json.loads((ASK_DIR / "sample_data" / "world_countries.geojson").read_text())
        _MAP_NAMES = {f["properties"]["iso_a3"]: f["properties"]["name"] for f in geo["features"]
                      if f["properties"].get("iso_a3") not in (None, "-99")}
    return _MAP_NAMES


def _axis(text: str, default: str = "Value") -> str:
    """A readable axis name, usable as an Ask column in backticks."""
    text = re.sub(r"\s+", " ", text.replace("`", "'").replace('"', "'")).strip(" ,")
    return (text[:48].rstrip() + "...") if len(text) > 50 else (text or default)


def _label(spec: dict) -> str:
    """Short enough to fit above the chart: the title without long parentheticals,
    plus the source's first part ("World Bank", "Global Data Lab", ...)."""
    title = re.sub(r"\s*\([^)]{15,}\)", "", spec["title"]).strip()
    if len(title) > 70:
        title = title[:67].rstrip() + "..."
    source = spec["source"].split(",")[0].split(" (")[0]
    return f"{title} ({source})".replace('"', "'")


def ask_program(spec: dict, csv_path: Path) -> tuple[list[list], str] | None:
    """(CSV rows, Ask program) for a spec, or None if it has nothing to draw.
    Columns are named for their axis labels and quoted with backticks."""
    kind, label = spec["kind"], _label(spec)
    value = _axis(spec.get("units") or spec["title"])
    if kind == "line":
        rows = [["Series", "Year", value]] + [[name, round(x, 3), y] for name, pts in spec["lines"].items()
                                             for x, y in sorted(set(pts))]
        if len(rows) < 3:
            return None
        return rows, (f'load "{csv_path}"\nchart `{value}` by Year as line\n'
                      f'  color by Series\n  label "{label}"\n')
    if kind == "bar":
        cat = spec.get("category", "Name")
        rows = [[cat, value]] + [[n, v] for n, v in spec["bars"]]
        sort = "" if spec.get("keep_order") else f"sort by `{value}` descending\n"
        return rows, f'load "{csv_path}"\n{sort}chart `{value}` by `{cat}` as bar\n  label "{label}"\n'
    if kind == "grouped_bar":  # rows: (category, group, value)
        cat, group = spec.get("category", "Category"), spec.get("group", "Group")
        rows = [[cat, group, value]] + [list(r) for r in spec["rows"]]
        return rows, (f'load "{csv_path}"\nchart `{value}` by `{cat}` as bar\n'
                      f'  color by `{group}`\n  label "{label}"\n')
    if kind == "map":
        names = _map_names()
        rows = [["Country", value]] + [[names[c], v] for c, (_, v, _) in spec["values"].items() if c in names]
        if len(rows) < MAP_MIN_COUNTRIES + 1:
            return None
        return rows, f'load "{csv_path}"\nchart `{value}` by Country as map\n  label "{label}"\n'
    if kind == "bubble":
        xcol, ycol = _axis(spec["xlabel"], "x"), _axis(spec["ylabel"], "y")
        if xcol == ycol:
            ycol += " (y)"
        rows = [["Country", "Year", xcol, ycol, "Population (millions)"]] + [list(r) for r in spec["rows"]]
        return rows, (f'load "{csv_path}"\nchart `{ycol}` by `{xcol}` as bubble\n  size by `Population (millions)`\n'
                      f'  color by Country\n  animate by Year\n  label "{label}"\n')
    return None


# Runs inside Ask's own environment: draws every job in one process, so pandas
# and matplotlib load once per answer, not once per chart.
_DRIVER = """
import json, sys
import matplotlib
matplotlib.use("Agg")
from asklang.interpreter import Env, run_program
from asklang.charts import save_chart_image, save_chart_animation
done = []
for job in json.load(sys.stdin):
    try:
        _, chart, _, _ = run_program(job["program"], Env())
        if chart is None:
            continue
        if job["out"].endswith(".gif"):
            save_chart_animation(chart, job["out"])
        else:
            save_chart_image(chart, job["out"])
        done.append(job["out"])
    except Exception as e:
        print(f"chart failed: {type(e).__name__}: {e}", file=sys.stderr)
print(json.dumps(done))
"""


def draw_with_ask(jobs: list[tuple[dict, Path]]) -> set[Path]:
    """Draws (spec, path) jobs with Ask in one process; returns the paths written."""
    import csv
    import json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ask_chart_") as tmp:
        payload = []
        for i, (spec, path) in enumerate(jobs):
            csv_path = Path(tmp) / f"data{i}.csv"
            made = ask_program(spec, csv_path)
            if made is None:
                continue
            rows, program = made
            with open(csv_path, "w", newline="") as f:
                csv.writer(f).writerows(rows)
            payload.append({"program": program, "out": str(path.resolve())})
        if not payload:
            return set()
        try:
            proc = subprocess.run([str(ASK_PYTHON), "-c", _DRIVER], cwd=ASK_DIR, input=json.dumps(payload),
                                  capture_output=True, text=True, timeout=ASK_TIMEOUT)
            done = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else []
        except (subprocess.TimeoutExpired, ValueError):
            return set()
    return {Path(p) for p in done if Path(p).exists()}


def draw_svg(spec: dict) -> str:
    if spec["kind"] == "line":
        return line_chart(spec["lines"], spec["title"], spec.get("units", ""), spec["source"])
    if spec["kind"] == "bar":
        return bar_chart(spec["bars"], spec["title"], spec.get("units", ""), spec["source"], spec.get("reference"))
    return ""  # maps and animations need Ask


def auto(results: list[tuple[str, dict, dict]], out_dir: Path = OUT_DIR, wb=None, use_ask: bool | None = None) -> list[Path]:
    """Draws a chart for every tool result worth drawing; returns the file paths.
    Ask (vendor/ask) draws PNGs, maps, and the bubble animation; otherwise SVG."""
    use_ask = ask_available() if use_ask is None else use_ask
    specs = [s for name, args, result in results for s in specs_for(name, args, result)]
    if use_ask:
        try:
            bubble = bubble_spec(results, wb)
        except Exception:  # a failed data request shouldn't cost the other charts
            bubble = None
        if bubble:
            specs.append(bubble)
    if not specs:
        return []
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # string concatenation, not with_suffix: ids like NY.GDP.PCAP.KD contain dots
    bases = [str(out_dir / f"{stamp}_{i}_{re.sub(r'[^A-Za-z0-9._-]+', '_', s['slug'])[:60]}") for i, s in enumerate(specs)]
    jobs = [(s, Path(b + (".gif" if s["kind"] == "bubble" else ".png"))) for s, b in zip(specs, bases)]
    drawn = draw_with_ask(jobs) if use_ask else set()
    paths = []
    for (spec, path), base in zip(jobs, bases):
        if path.resolve() in drawn:
            paths.append(path)
            continue
        svg = draw_svg(spec)  # fallback: line and bar charts only
        if svg:
            Path(base + ".svg").write_text(svg)
            paths.append(Path(base + ".svg"))
    return paths
