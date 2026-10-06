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


def charts_for(name: str, args: dict, result: dict) -> list[tuple[str, str]]:
    """(slug, svg) for one tool result that has something worth drawing."""
    if name != "get_data" or not isinstance(result, dict) or "error" in result:
        return []
    source = str(result.get("source", ""))
    out = []
    if "series_id" in result:  # FRED
        pts = _points(result, "sampled_points", ("first", "latest", "max", "min"), "date")
        svg = line_chart({result["series_id"]: pts}, result.get("title", result["series_id"]),
                         result.get("units", ""), f"FRED, {result['series_id']} (St. Louis Fed)")
        out.append((result["series_id"], svg))
    elif "economies" in result:  # World Bank
        lines = {e["economy"]: _points(e, "sampled", ("first", "latest", "max", "min"), "year")
                 for e in result["economies"] if "latest" in e}
        out.append((result["indicator"], line_chart(lines, result["name"], "",
                                                    f"World Bank, World Development Indicators ({result['indicator']})")))
    elif "ranked" in result and source.startswith("Global Data Lab"):
        bars = [(f"{r['region']} ({r['country']})", r["value"]) for r in result["ranked"]]
        out.append((result["metric"] + "_ranking", bar_chart(bars, f"{result['name']}, regions ({result['order']})",
                                                             result["units"], source)))
    elif "countries" in result and source.startswith("Global Data Lab"):
        for c in result["countries"]:
            rows = next((v for k, v in c.items() if k.startswith("regions [")), None)
            if rows:
                bars = [(r, v) for r, v in rows if r != "..."]
                out.append((f"{result['metric']}_{c['country']}", bar_chart(
                    bars, f"{result['name']} by region, {c['country']} {c['year']}", result["units"], source,
                    ("national", c["national"]) if c.get("national") is not None else None)))
    elif "countries" in result and source.startswith("DHS"):
        lines = {}
        for c in result["countries"]:
            surveys = next((v for k, v in c.items() if k.startswith("surveys")), None)
            if surveys:
                lines[c["country"]] = [(_x(s[0]), float(s[1])) for s in surveys if s[1] is not None]
            reg = c.get("regions")
            rows = next((v for k, v in reg.items() if k.startswith("regions [")), None) if isinstance(reg, dict) else None
            if rows:
                bars = [(r[0], r[1]) for r in rows if r[0] != "..."]
                out.append((f"{result['indicator']}_{c['country']}_regions", bar_chart(
                    bars, f"{result['name']} by region, {c['country']} {reg.get('year', '')}", result.get("unit", ""),
                    source, ("national", reg["national"]) if isinstance(reg.get("national"), (int, float)) else None)))
        out.append((result["indicator"], line_chart(lines, result["name"], result.get("unit", ""), source)))
    elif "countries" in result and "variable" in result:  # long-run
        lines = {c["country"]: _points(c, "sampled", ("first", "last", "peak", "trough"), "year")
                 for c in result["countries"] if "last" in c}
        out.append((result["variable"], line_chart(lines, result["name"], result.get("units", ""), source)))
    return [(slug, svg) for slug, svg in out if svg]


def auto(results: list[tuple[str, dict, dict]], out_dir: Path = OUT_DIR) -> list[Path]:
    """Saves a chart for every tool result worth drawing; returns the file paths."""
    paths, stamp = [], datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for name, args, result in results:
        for slug, svg in charts_for(name, args, result):
            out_dir.mkdir(exist_ok=True)
            path = out_dir / f"{stamp}_{re.sub(r'[^A-Za-z0-9._-]+', '_', slug)[:60]}.svg"
            path.write_text(svg)
            paths.append(path)
    return paths
