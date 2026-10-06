"""Charts: Chart spec -> matplotlib Figure.

Rewritten so every chart kind honours its modifiers:
  * `color by` groups bars, draws one line/area/scatter series per group,
    and gives heatmap its second dimension
  * `box` draws one box per category of x
  * `histogram` picks bins by the Freedman-Diaconis rule
  * rows with a missing category are dropped before plotting (previously a
    NaN in a categorical axis crashed matplotlib — bug A12)
  * a numeric x (e.g. a year column) gets a real continuous axis with
    matplotlib's own tick spacing, instead of one forced label per distinct
    value — a multi-decade time series used to render as an unreadable wall
    of overlapping year labels
  * more `color by` groups than the base palette holds get extra,
    still-distinguishable colors instead of silently repeating a color
    across two different groups

Styling: Okabe-Ito colorblind-safe palette (small N), light y-gridlines, no
top/right spines, thousands separators on the value axis.
"""

import numpy as np
import pandas as pd

# Okabe-Ito palette (colorblind-safe) - used as-is for up to 8 groups.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00",
           "#CC79A7", "#56B4E9", "#F0E442", "#8C8C8C"]
ACCENT = "#D55E00"
MAX_TICK_LABELS = 15   # thin categorical tick labels beyond this many


def palette(n):
    """n visually distinct colors: the base 8 for small N, extra hues sampled
    from a wide qualitative colormap beyond that so groups never repeat."""
    if n <= len(PALETTE):
        return PALETTE[:n]
    import matplotlib as mpl
    name = "tab20" if n <= 20 else "gist_rainbow"
    cmap = mpl.colormaps[name]
    denom = 20 if n <= 20 else n
    return [cmap(i / denom) for i in range(n)]


def render_chart(chart, figsize=(7.0, 4.4), dpi=100):
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter
    if chart.kind == "story":
        return _render_story(chart, dpi=dpi)
    fig = Figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(111)
    table = chart._table
    df = table.df.copy()
    x, y, kind = chart.x, chart.y, chart.kind

    order = getattr(chart, "_order", None)
    color_by = None
    size_by = None
    animate_by = None
    label = None
    trendline = False
    for m in chart.mods:
        if m.kind == "color":
            color_by = m.value
        elif m.kind == "size":
            size_by = m.value
        elif m.kind == "animate":
            animate_by = m.value
        elif m.kind == "label":
            label = m.value
        elif m.kind == "trendline":
            trendline = True

    # Drop rows with a missing category (matplotlib rejects NaN on a
    # categorical axis) — for numeric-x kinds the numeric mask handles it.
    if kind in ("bar", "line", "area", "pie", "box", "heatmap", "bubble",
                "change", "flow", "tree", "network", "map"):
        df = df[df[x].notna()]
    if color_by and color_by in df.columns and kind != "heatmap":
        df = df[df[color_by].notna()]

    if order is not None:
        cat = pd.Categorical(df[x], categories=order, ordered=True)
        df = df.assign(**{x: cat}).sort_values(x)
        df = df[df[x].notna()]

    yvals = pd.to_numeric(df[y], errors="coerce")
    # A numeric, non-categorical x (a year, a count, ...) gets a real
    # continuous axis with matplotlib's own tick spacing; forcing one
    # explicit label per distinct value is what turned an 80-year time
    # series into an unreadable wall of overlapping text.
    x_numeric = (order is None and kind != "bar"
                and pd.api.types.is_numeric_dtype(df[x]))

    if kind in ("bar", "line", "area"):
        if color_by and color_by in df.columns:
            _grouped_series(ax, df, x, y, color_by, kind, x_numeric)
        elif x_numeric:
            xnum = pd.to_numeric(df[x], errors="coerce")
            order_idx = xnum.argsort()
            xs, ys = xnum.to_numpy()[order_idx], yvals.to_numpy()[order_idx]
            if kind == "bar":
                ax.bar(xs, ys, color=PALETTE[0])
            elif kind == "line":
                ax.plot(xs, ys, marker="o", color=PALETTE[0])
            else:
                ax.fill_between(xs, ys, color=PALETTE[0], alpha=0.6)
                ax.plot(xs, ys, color=PALETTE[0], linewidth=1)
        else:
            xs = df[x].astype(str)
            if kind == "bar":
                ax.bar(xs, yvals, color=PALETTE[0])
            elif kind == "line":
                ax.plot(range(len(xs)), yvals, marker="o", color=PALETTE[0])
                _set_thinned_ticks(ax, range(len(xs)), xs)
            else:
                ax.fill_between(range(len(xs)), yvals, color=PALETTE[0], alpha=0.6)
                ax.plot(range(len(xs)), yvals, color=PALETTE[0], linewidth=1)
                _set_thinned_ticks(ax, range(len(xs)), xs)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if not x_numeric:
            _rotate_if_needed(ax)
        if trendline:
            _add_trendline(ax, yvals)
    elif kind == "scatter":
        xnum = pd.to_numeric(df[x], errors="coerce")
        if color_by and color_by in df.columns:
            groups = list(pd.unique(df[color_by].astype(str)))
            colors = palette(len(groups))
            for gi, (g, sub) in enumerate(df.groupby(color_by, observed=True,
                                                     dropna=True)):
                ax.scatter(pd.to_numeric(sub[x], errors="coerce"),
                           pd.to_numeric(sub[y], errors="coerce"),
                           color=colors[gi], label=str(g),
                           alpha=0.8, edgecolors="white", linewidths=0.4)
            ax.legend(title=color_by, fontsize=8)
        else:
            ax.scatter(xnum, yvals, color=PALETTE[0], alpha=0.8,
                       edgecolors="white", linewidths=0.4)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if trendline:
            _add_trendline(ax, yvals, xnum)
    elif kind == "bubble":
        if not (size_by and size_by in df.columns):
            from .errors import AskError
            raise AskError(
                what="A bubble chart needs a 'size by' column to size the bubbles.",
                where=f"line {chart.line}",
                fix="Add an indented line under chart: size by <column>")
        frame_df = df[df[y].notna() & df[size_by].notna()]
        frame_note = None
        if animate_by and animate_by in frame_df.columns:
            frame_df = frame_df[frame_df[animate_by].notna()]
            frames_all = sorted(pd.unique(frame_df[animate_by]))
            if frames_all:
                frame_note = frames_all[-1]
                frame_df = frame_df[frame_df[animate_by] == frame_note]
        x_categories = (None if pd.api.types.is_numeric_dtype(frame_df[x])
                        else list(pd.unique(frame_df[x].astype(str))))
        _draw_bubbles(ax, frame_df, x, y, size_by, color_by,
                     x_categories=x_categories)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        _rotate_if_needed(ax)
        if frame_note is not None:
            label = (label or f"{y} by {x}") + (
                f"  — {animate_by} = {frame_note} (latest frame; "
                f"use an animated export for the full motion chart)")
    elif kind == "histogram":
        data = yvals.dropna().to_numpy(float)
        ax.hist(data, bins=_bin_count(data), color=PALETTE[0],
                edgecolor="white", linewidth=0.5)
        ax.set_xlabel(y)
        ax.set_ylabel("count")
    elif kind == "box":
        groups = []
        labels = []
        if order is not None:
            iterator = [(lv, df[df[x].astype(str) == str(lv)]) for lv in order]
        else:
            iterator = list(df.groupby(df[x].astype(str), dropna=True))
        for g, sub in iterator:
            vals = pd.to_numeric(sub[y], errors="coerce").dropna()
            if len(vals):
                groups.append(vals.to_numpy(float))
                labels.append(str(g))
        if not groups:
            groups = [yvals.dropna().to_numpy(float)]
            labels = [y]
        ax.boxplot(groups)
        _set_thinned_ticks(ax, range(1, len(labels) + 1), labels)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        _rotate_if_needed(ax)
    elif kind == "pie":
        vals = yvals.clip(lower=0).fillna(0)
        keep = vals > 0
        ax.pie(vals[keep], labels=df[x].astype(str)[keep], autopct="%1.0f%%",
               colors=palette(int(keep.sum())))
    elif kind == "heatmap":
        if not (color_by and color_by in df.columns):
            from .errors import AskError
            raise AskError(
                what="A heatmap needs two category columns: the x axis and a "
                     "'color by' column.",
                where=f"line {chart.line}",
                fix="Add an indented line under chart: color by <column>")
        pivot = df.pivot_table(index=color_by, columns=x, values=y,
                               aggfunc="sum", observed=False)
        # A pandas nullable dtype (Float64/Int64 - common since pandas 2/3's
        # default backends) makes `.values` come back `object`-dtype, which
        # imshow rejects outright ("Image data of dtype object cannot be
        # converted to float"). A plain numpy float array always works.
        im = ax.imshow(pivot.to_numpy(dtype=float), aspect="auto", cmap="viridis")
        _set_thinned_ticks(ax, range(len(pivot.columns)),
                          [str(v) for v in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([str(v) for v in pivot.index])
        ax.set_xlabel(x)
        ax.set_ylabel(color_by)
        fig.colorbar(im, ax=ax, label=y)
        _rotate_if_needed(ax)
    elif kind == "distribution":
        _draw_distribution(ax, df, y, color_by)
    elif kind == "change":
        _draw_change(ax, df, x, y, color_by, order, x_numeric)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if not x_numeric:
            _rotate_if_needed(ax)
    elif kind == "flow":
        _draw_flow(ax, df, x, y, color_by, chart)
    elif kind == "tree":
        _draw_tree(ax, df, x, y, color_by, order, chart)
    elif kind == "network":
        _draw_network(ax, df, x, y, color_by, chart)
    elif kind == "map":
        _draw_map(ax, df, x, y, chart)

    if kind not in ("pie", "heatmap", "flow", "tree", "network", "map"):
        ax.yaxis.set_major_formatter(FuncFormatter(_axis_fmt))
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.7, alpha=0.7)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    ax.set_title(label or f"{y} by {x}")
    fig.tight_layout()
    return fig


def _render_story(chart, dpi=100):
    """A `story` replays every chart drawn earlier in the same program as a
    grid of panels (scope choice: "the pipeline's key charts in sequence",
    not a scrollable narrated report - Ask's execution model already keeps
    exactly this list on the Chart spec via `_history`, and a grid of the
    real, already-correct panels is more honest than re-describing them in
    prose). Each earlier chart is rendered independently and rasterized
    rather than sharing this figure's Axes, since every chart kind here
    already assumes it owns a fresh Figure (e.g. `map`'s colorbar, `pie`'s
    equal aspect)."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from .errors import AskError
    history = list(getattr(chart, "_history", None) or [])
    if not history:
        raise AskError(
            what="A story needs at least one chart drawn earlier in the "
                 "program to narrate - this is the first chart.",
            where=f"line {chart.line}",
            fix="Add a chart step above this one, then chart ... as story.")
    label = None
    for m in chart.mods:
        if m.kind == "label":
            label = m.value

    n = len(history)
    cols = 1 if n == 1 else 2
    rows = -(-n // cols)
    fig = Figure(figsize=(6.5 * cols, 4.2 * rows), dpi=dpi)
    for i, hist in enumerate(history, start=1):
        panel_fig = render_chart(hist)
        canvas = FigureCanvasAgg(panel_fig)
        canvas.draw()
        img = np.asarray(canvas.buffer_rgba())
        pax = fig.add_subplot(rows, cols, i)
        pax.imshow(img)
        pax.axis("off")
        pax.set_title(f"Step {i}: {hist.kind} of {hist.y} by {hist.x}", fontsize=9)
    fig.suptitle(label or "The story so far", fontsize=13)
    fig.tight_layout()
    return fig


def save_chart_image(chart, path, dpi=150):
    """Render a chart spec straight to an image file (png/svg/pdf by extension)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = render_chart(chart)
    FigureCanvasAgg(fig)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")


def _bubble_sizes(series, lo=80, hi=2400, global_lo=None, global_hi=None):
    """Marker sizes (points^2) scaled by sqrt of value, so bubble *area* -
    not radius - is proportional to the value (the correct perceptual
    encoding). `global_lo`/`global_hi` let an animation size every frame
    against the whole series' range, so a bubble's size means the same thing
    from frame to frame instead of re-normalizing each year."""
    vals = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    vals = np.clip(np.nan_to_num(vals, nan=0.0), 0, None)
    root = np.sqrt(vals)
    rmin = np.sqrt(max(global_lo, 0)) if global_lo is not None else root.min()
    rmax = np.sqrt(max(global_hi, 0)) if global_hi is not None else root.max()
    span = rmax - rmin
    if span <= 0:
        return np.full(len(vals), (lo + hi) / 2)
    return lo + np.clip((root - rmin) / span, 0, 1) * (hi - lo)


def _draw_bubbles(ax, df, x, y, size_by, color_by, size_range=None,
                  x_categories=None, colors_map=None):
    """One scatter call per `color by` group so the legend and colors stay
    consistent; shared by the static bubble chart and each animation frame."""
    if x_categories is not None:
        pos = {c: i for i, c in enumerate(x_categories)}
        xs = df[x].astype(str).map(pos).to_numpy(dtype=float)
    else:
        xs = pd.to_numeric(df[x], errors="coerce").to_numpy(dtype=float)
    ys = pd.to_numeric(df[y], errors="coerce").to_numpy(dtype=float)
    glo, ghi = size_range if size_range else (None, None)
    sizes = _bubble_sizes(df[size_by], global_lo=glo, global_hi=ghi)
    if color_by and color_by in df.columns:
        groups = list(colors_map.keys()) if colors_map else \
            list(pd.unique(df[color_by].astype(str)))
        cmap = colors_map or dict(zip(groups, palette(len(groups))))
        cat = df[color_by].astype(str)
        for g in groups:
            mask = (cat == g).to_numpy()
            if mask.any():
                ax.scatter(xs[mask], ys[mask], s=sizes[mask], color=cmap[g],
                          alpha=0.65, edgecolors="white", linewidths=0.6,
                          label=str(g))
        ax.legend(title=color_by, fontsize=7, loc="upper left",
                  bbox_to_anchor=(1.02, 1.0), borderaxespad=0)
    else:
        ax.scatter(xs, ys, s=sizes, color=PALETTE[0], alpha=0.65,
                   edgecolors="white", linewidths=0.6)
    if x_categories is not None:
        ax.set_xticks(range(len(x_categories)))
        ax.set_xticklabels(x_categories)
        ax.set_xlim(-0.6, len(x_categories) - 1 + 0.6)


def _chart_mods(chart):
    mods = {"color": None, "size": None, "animate": None, "label": None}
    for m in chart.mods:
        if m.kind in mods:
            mods[m.kind] = m.value
    return mods


def render_chart_animation(chart, figsize=(7.5, 5.0), dpi=100, interval=900):
    """A `chart ... as bubble` with `animate by <column>` becomes a
    Gapminder-style motion chart: one frame per distinct value of that
    column (typically a year), redrawn with fixed axes/sizes/colors so
    bubbles visibly move rather than the plot rescaling under them.

    Returns (figure, FuncAnimation). Callers must keep a reference to the
    animation object alive for as long as it should keep playing/exporting -
    matplotlib stops (and Python may garbage-collect) an animation whose
    only reference drops out of scope."""
    from matplotlib.figure import Figure
    from matplotlib.animation import FuncAnimation
    from matplotlib.ticker import FuncFormatter
    from .errors import AskError

    if chart.kind != "bubble":
        raise AskError(
            what=f"Only a bubble chart can be animated, not {chart.kind!r}.",
            where=f"line {chart.line}",
            fix="Use: chart y by x as bubble")
    mods = _chart_mods(chart)
    color_by, size_by, animate_by, label = (
        mods["color"], mods["size"], mods["animate"], mods["label"])
    if not animate_by:
        raise AskError(
            what="This chart has no 'animate by' column to play through.",
            where=f"line {chart.line}",
            fix="Add an indented line under chart: animate by <column>")
    if not size_by:
        raise AskError(
            what="A bubble chart needs a 'size by' column to size the bubbles.",
            where=f"line {chart.line}",
            fix="Add an indented line under chart: size by <column>")

    table = chart._table
    x, y = chart.x, chart.y
    needed = [x, y, size_by, animate_by] + ([color_by] if color_by else [])
    for col in needed:
        if col not in table.df.columns:
            raise AskError(what=f"No column named {col!r} to animate with.",
                           where=f"line {chart.line}",
                           fix="Pick a column that exists in the table above.")
    df = table.df[needed].dropna(subset=[x, y, size_by, animate_by])
    if df.empty:
        raise AskError(
            what="No data left to animate once rows with missing values are dropped.",
            where=f"line {chart.line}",
            fix="Check the filters above the chart step.")

    frames = sorted(pd.unique(df[animate_by]))
    x_numeric = pd.api.types.is_numeric_dtype(df[x])
    if x_numeric:
        x_categories = None
        x_lo, x_hi = float(df[x].min()), float(df[x].max())
        pad = (x_hi - x_lo) * 0.08 or 1.0
        x_lo, x_hi = x_lo - pad, x_hi + pad
    else:
        x_categories = list(pd.unique(df[x].astype(str)))
        x_lo = x_hi = None  # fixed inside _draw_bubbles via set_xlim

    y_lo, y_hi = float(df[y].min()), float(df[y].max())
    pad = (y_hi - y_lo) * 0.12 or 1.0
    y_lo, y_hi = y_lo - pad, y_hi + pad
    size_range = (float(df[size_by].min()), float(df[size_by].max()))

    colors_map = None
    if color_by and color_by in df.columns:
        groups = list(pd.unique(df[color_by].astype(str)))
        colors_map = dict(zip(groups, palette(len(groups))))

    fig = Figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(111)

    def draw(frame_value):
        ax.clear()
        sub = df[df[animate_by] == frame_value]
        _draw_bubbles(ax, sub, x, y, size_by, color_by,
                     size_range=size_range, x_categories=x_categories,
                     colors_map=colors_map)
        if x_numeric:
            ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(y_lo, y_hi)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        ax.yaxis.set_major_formatter(FuncFormatter(_axis_fmt))
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.7, alpha=0.7)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        if not x_numeric:
            _rotate_if_needed(ax)
        ax.set_title(label or f"{y} by {x}")
        ax.text(0.98, 0.04, str(frame_value), transform=ax.transAxes,
               fontsize=30, color="#C9C9C9", ha="right", va="bottom",
               fontweight="bold", zorder=0)
        fig.tight_layout()

    draw(frames[0])
    ani = FuncAnimation(fig, lambda i: draw(frames[i]), frames=len(frames),
                        interval=interval, repeat=True)
    return fig, ani


def save_chart_animation(chart, path, fps=2, dpi=120):
    """Render an animated `bubble` chart (see render_chart_animation) straight
    to a .gif (or .mp4, if ffmpeg is installed) file."""
    fig, ani = render_chart_animation(chart, dpi=dpi)
    if str(path).lower().endswith(".mp4"):
        from matplotlib.animation import FFMpegWriter
        writer = FFMpegWriter(fps=fps)
    else:
        from matplotlib.animation import PillowWriter
        writer = PillowWriter(fps=fps)
    ani.save(path, writer=writer)


def _axis_fmt(v, _pos=None):
    if v == int(v) and abs(v) >= 1000:
        return f"{int(v):,}"
    if v == int(v):
        return f"{int(v)}"
    return f"{v:g}"


def _bin_count(data):
    """Freedman-Diaconis bins, clamped to [5, 50]; falls back for tiny data."""
    n = len(data)
    if n < 4:
        return max(n, 1)
    q75, q25 = np.percentile(data, [75, 25])
    iqr = q75 - q25
    if iqr <= 0:
        return 10
    width = 2 * iqr / (n ** (1 / 3))
    span = data.max() - data.min()
    if width <= 0 or span <= 0:
        return 10
    return int(np.clip(np.ceil(span / width), 5, 50))


def _grouped_series(ax, df, x, y, color_by, kind, x_numeric=False):
    """One series per `color by` group, honouring the chart kind (bug B1:
    previously every kind fell back to bars). A numeric x (line/area only -
    `render_chart` never sets x_numeric for bar) uses real numeric positions
    with matplotlib's own tick spacing instead of one label per value."""
    groups = list(pd.unique(df[color_by].astype(str)))
    colors = palette(len(groups))

    if x_numeric:
        cats = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
        idx = np.asarray(cats, dtype=float)
        match = pd.to_numeric(df[x], errors="coerce")
    else:
        cats = list(pd.unique(df[x].astype(str)))
        idx = np.arange(len(cats))
        match = df[x].astype(str)

    def group_vals(g):
        sub_mask = (df[color_by].astype(str) == g)
        sub_match = match[sub_mask]
        sub_y = pd.to_numeric(df.loc[sub_mask, y], errors="coerce")
        return np.array([
            sub_y[sub_match == cat].sum() if (sub_match == cat).any() else np.nan
            for cat in cats], dtype=float)

    if kind == "bar":
        width = 0.8 / max(len(groups), 1)
        for gi, g in enumerate(groups):
            ax.bar(idx + gi * width, np.nan_to_num(group_vals(g)), width=width,
                   label=str(g), color=colors[gi])
        ax.set_xticks(idx + width * (len(groups) - 1) / 2)
        ax.set_xticklabels(cats)
    elif kind == "line":
        for gi, g in enumerate(groups):
            ax.plot(idx, group_vals(g), marker="o", label=str(g),
                    color=colors[gi])
        if not x_numeric:
            _set_thinned_ticks(ax, idx, cats)
    else:  # area -> stacked
        stack = [np.nan_to_num(group_vals(g)) for g in groups]
        ax.stackplot(idx, *stack, labels=[str(g) for g in groups],
                     colors=colors, alpha=0.8)
        if not x_numeric:
            _set_thinned_ticks(ax, idx, cats)
    ax.legend(title=color_by, fontsize=8)


def _set_thinned_ticks(ax, positions, labels):
    """Explicit tick labels for a categorical axis - but thinned to at most
    MAX_TICK_LABELS entries so a many-category axis stays readable instead of
    becoming a wall of overlapping text."""
    positions = list(positions)
    labels = list(labels)
    if len(labels) > MAX_TICK_LABELS:
        step = -(-len(labels) // MAX_TICK_LABELS)  # ceil div
        positions, labels = positions[::step], labels[::step]
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)


def _rotate_if_needed(ax):
    labels = [lbl.get_text() for lbl in ax.get_xticklabels()]
    if labels and max((len(s) for s in labels), default=0) > 6:
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(35)
            lbl.set_ha("right")


def _add_trendline(ax, yvals, xvals=None):
    y = pd.to_numeric(pd.Series(yvals), errors="coerce").to_numpy(dtype=float)
    if xvals is None:
        x = np.arange(len(y), dtype=float)
    else:
        x = pd.to_numeric(pd.Series(xvals), errors="coerce").to_numpy(dtype=float)
    mask = ~np.isnan(x) & ~np.isnan(y)
    if mask.sum() >= 2:
        m, b = np.polyfit(x[mask], y[mask], 1)
        xs = np.sort(x[mask])
        ax.plot(xs, m * xs + b, color=ACCENT, linestyle="--", linewidth=1.5)


# ---- distribution --------------------------------------------------------

def _kde(values, npoints=200):
    """Gaussian KDE, hand-rolled with numpy only (Silverman's rule of thumb
    for bandwidth - no scipy.stats.gaussian_kde). Returns (grid, density)."""
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    n = len(values)
    if n == 0:
        return np.array([0.0, 1.0]), np.array([0.0, 0.0])
    lo, hi = values.min(), values.max()
    if n == 1 or hi == lo:
        pad = abs(lo) * 0.1 or 1.0
        grid = np.linspace(lo - pad, hi + pad, npoints)
        return grid, np.zeros_like(grid)
    std = values.std(ddof=1)
    q75, q25 = np.percentile(values, [75, 25])
    iqr = q75 - q25
    sigma = min(std, iqr / 1.34) if iqr > 0 else std
    bw = 0.9 * sigma * n ** (-1 / 5) if sigma > 0 else (hi - lo) / 10 or 1.0
    pad = (hi - lo) * 0.15 or 1.0
    grid = np.linspace(lo - pad, hi + pad, npoints)
    diffs = (grid[:, None] - values[None, :]) / bw
    dens = np.exp(-0.5 * diffs ** 2).sum(axis=1) / (n * bw * np.sqrt(2 * np.pi))
    return grid, dens


def _draw_distribution(ax, df, y, color_by):
    """Overlaid Gaussian-KDE density curves rather than violins: a violin
    needs a discrete x position per group, but the natural split key here
    (`color by`) can have many groups, and ridgeline-style overlaid curves
    read cleanly at any group count without having to pick x positions for
    them. `x` is unused (same convention as `histogram`: pass the same
    column for x and y, or split with `color by` instead)."""
    from .errors import AskError
    if color_by and color_by in df.columns:
        groups = list(pd.unique(df[color_by].astype(str)))
        colors = palette(len(groups))
        cat = df[color_by].astype(str)
        drawn = 0
        for gi, g in enumerate(groups):
            vals = pd.to_numeric(df.loc[cat == g, y], errors="coerce").dropna()
            if len(vals) < 2:
                continue
            grid, dens = _kde(vals.to_numpy(float))
            ax.plot(grid, dens, color=colors[gi], linewidth=1.5)
            ax.fill_between(grid, dens, color=colors[gi], alpha=0.25, label=str(g))
            drawn += 1
        if drawn == 0:
            raise AskError(
                what=f"Not enough numeric values in {y!r} (need 2+ per "
                     f"{color_by!r} group) to draw a distribution.",
                where="the chart step",
                fix=f"Check the data above, or drop 'color by {color_by}'.")
        ax.legend(title=color_by, fontsize=8)
    else:
        vals = pd.to_numeric(df[y], errors="coerce").dropna()
        if len(vals) < 2:
            raise AskError(
                what=f"Not enough numeric values in {y!r} (need at least 2) "
                     "to draw a distribution.",
                where="the chart step",
                fix="Check the data above the chart step.")
        grid, dens = _kde(vals.to_numpy(float))
        ax.plot(grid, dens, color=PALETTE[0], linewidth=1.5)
        ax.fill_between(grid, dens, color=PALETTE[0], alpha=0.35)
    ax.set_xlabel(y)
    ax.set_ylabel("density")


# ---- change ---------------------------------------------------------------

def _draw_change(ax, df, x, y, color_by, order, x_numeric):
    """Slope chart, not waterfall: a waterfall needs a running-total/"total"
    bar convention that doesn't fall out of the generic (value by category
    [color by group]) chart shape used everywhere else in this file. A slope
    chart is just "one line per group across the ordered x categories" -
    the existing grouped-line drawing code already does exactly that, and it
    generalizes past two points into a bump chart instead of being locked to
    a single before/after pair."""
    from .errors import AskError
    n_categories = len(order) if order is not None else df[x].nunique(dropna=True)
    if n_categories < 2:
        raise AskError(
            what=f"A change chart needs at least two points along {x!r} to "
                 "show a before/after or over-time delta "
                 f"(only {n_categories} found).",
            where="the chart step",
            fix="Use a category or date column with 2 or more distinct values.")
    yvals = pd.to_numeric(df[y], errors="coerce")
    if color_by and color_by in df.columns:
        _grouped_series(ax, df, x, y, color_by, "line", x_numeric)
    elif x_numeric:
        xnum = pd.to_numeric(df[x], errors="coerce")
        order_idx = xnum.argsort()
        ax.plot(xnum.to_numpy()[order_idx], yvals.to_numpy()[order_idx],
                marker="o", color=PALETTE[0], linewidth=2)
    else:
        xs = df[x].astype(str)
        ax.plot(range(len(xs)), yvals, marker="o", color=PALETTE[0], linewidth=2)
        _set_thinned_ticks(ax, range(len(xs)), xs)
    # annotate the first -> last delta on every line drawn (one per group,
    # or the single line when there's no `color by`)
    for line in ax.lines:
        xd = np.asarray(line.get_xdata(), dtype=float)
        yd = np.asarray(line.get_ydata(), dtype=float)
        valid = np.where(~np.isnan(yd))[0]
        if len(valid) >= 2:
            first, last = yd[valid[0]], yd[valid[-1]]
            delta = last - first
            tag = f"{delta / first * 100:+.0f}%" if first else f"{delta:+.0f}"
            ax.annotate(tag, (xd[valid[-1]], last), textcoords="offset points",
                        xytext=(8, 0), fontsize=8, color=line.get_color(),
                        fontweight="bold", va="center")


# ---- flow (Sankey) ---------------------------------------------------------

def _node_spans(names, totals, gap=0.02):
    """Positions along [0, 1] proportional to each name's total, with a
    small fixed gap between consecutive nodes for readability."""
    total_all = sum(totals.get(n, 0) for n in names)
    usable = max(1 - gap * max(len(names) - 1, 0), 0.05)
    unit = usable / total_all if total_all else 0
    spans = {}
    cursor = 0.0
    for n in names:
        h = totals.get(n, 0) * unit
        spans[n] = (cursor, cursor + h)
        cursor += h + gap
    return spans


def _draw_flow(ax, df, x, y, color_by, chart):
    """Two-column Sankey diagram: straight-edged ribbons (not curved
    Beziers) between a left column of `x` nodes and a right column of
    `color by` (target) nodes, widths proportional to flow. Straight edges
    keep the geometry simple/robust while still reading clearly as a flow
    diagram once colored and made semi-transparent."""
    from .errors import AskError
    if not (color_by and color_by in df.columns):
        raise AskError(
            what="A flow chart needs two category columns: the source (x) "
                 "and target ('color by' column).",
            where=f"line {chart.line}",
            fix="Add an indented line under chart: color by <target column>")
    agg = df.groupby([x, color_by], observed=True)[y].sum().reset_index()
    agg = agg[pd.to_numeric(agg[y], errors="coerce") > 0]
    if agg.empty:
        raise AskError(
            what=f"No positive {y!r} values to draw as flow between {x!r} "
                 f"and {color_by!r}.",
            where=f"line {chart.line}",
            fix="Check the data above the chart step - flow needs positive numbers.")
    sources = list(agg.groupby(x)[y].sum().sort_values(ascending=False).index)
    targets = list(agg.groupby(color_by)[y].sum().sort_values(ascending=False).index)
    src_tot = agg.groupby(x)[y].sum().to_dict()
    tgt_tot = agg.groupby(color_by)[y].sum().to_dict()
    src_spans = _node_spans(sources, src_tot)
    tgt_spans = _node_spans(targets, tgt_tot)
    src_cursor = {n: src_spans[n][0] for n in sources}
    tgt_cursor = {n: tgt_spans[n][0] for n in targets}
    src_colors = dict(zip(sources, palette(len(sources))))

    node_w = 0.05
    for _, row in agg.sort_values([x, color_by]).iterrows():
        s, t, w = row[x], row[color_by], float(row[y])
        s0, s1 = src_spans[s]
        t0, t1 = tgt_spans[t]
        lh = w / src_tot[s] * (s1 - s0) if src_tot[s] else 0
        rh = w / tgt_tot[t] * (t1 - t0) if tgt_tot[t] else 0
        ly0 = src_cursor[s]
        ry0 = tgt_cursor[t]
        src_cursor[s] += lh
        tgt_cursor[t] += rh
        poly = plt_polygon(
            [(node_w, ly0), (node_w, ly0 + lh),
             (1 - node_w, ry0 + rh), (1 - node_w, ry0)],
            closed=True, facecolor=src_colors[s], edgecolor="none", alpha=0.55)
        ax.add_patch(poly)

    for n in sources:
        s0, s1 = src_spans[n]
        ax.add_patch(plt_rectangle((0, s0), node_w, s1 - s0,
                                   facecolor=src_colors[n], edgecolor="white"))
        ax.text(-0.01, (s0 + s1) / 2, f"{n} ({_axis_fmt(src_tot[n])})",
                ha="right", va="center", fontsize=8)
    for n in targets:
        t0, t1 = tgt_spans[n]
        ax.add_patch(plt_rectangle((1 - node_w, t0), node_w, t1 - t0,
                                   facecolor="#8C8C8C", edgecolor="white"))
        ax.text(1.01, (t0 + t1) / 2, f"{n} ({_axis_fmt(tgt_tot[n])})",
                ha="left", va="center", fontsize=8)

    ax.set_xlim(-0.55, 1.55)
    ax.set_ylim(-0.02, 1.02)
    ax.axis("off")


# ---- tree (icicle) ----------------------------------------------------------

def _draw_tree(ax, df, x, y, color_by, order, chart):
    """Two-level icicle (not a squarified treemap): the top band splits into
    one rectangle per `x` category, width proportional to its total `y`;
    when `color by` is given, a second band below splits each parent's
    width further by its `color by` subgroups. Slice-and-dice bands are far
    simpler to lay out correctly than a squarified treemap and still show
    the same "value across nested categories" story."""
    from .errors import AskError
    totals = df.groupby(x, observed=True)[y].sum()
    totals = totals[totals > 0]
    if totals.empty:
        raise AskError(
            what=f"No positive {y!r} values to size a tree chart with.",
            where=f"line {chart.line}",
            fix="Check the data above the chart step.")
    names = [n for n in order if n in totals.index] if order is not None \
        else list(totals.sort_values(ascending=False).index)
    top_totals = {n: float(totals[n]) for n in names}
    top_spans = _node_spans(names, top_totals, gap=0.01)
    top_colors = dict(zip(names, palette(len(names))))
    has_sub = bool(color_by and color_by in df.columns)
    top_y0, top_y1 = (0.55, 0.98) if has_sub else (0.05, 0.98)

    for n in names:
        x0, x1 = top_spans[n]
        ax.add_patch(plt_rectangle((x0, top_y0), x1 - x0, top_y1 - top_y0,
                                   facecolor=top_colors[n], edgecolor="white"))
        if x1 - x0 > 0.03:
            ax.text((x0 + x1) / 2, (top_y0 + top_y1) / 2,
                    f"{n}\n{_axis_fmt(top_totals[n])}",
                    ha="center", va="center", fontsize=8, color="white",
                    fontweight="bold")

    if has_sub:
        sub_groups = list(pd.unique(df[color_by].astype(str)))
        sub_colors = dict(zip(sub_groups, palette(len(sub_groups))))
        cat = df[color_by].astype(str)
        for n in names:
            x0, x1 = top_spans[n]
            sub = df[df[x] == n]
            sub_tot = sub.groupby(cat[sub.index], observed=True)[y].sum()
            sub_tot = sub_tot[sub_tot > 0]
            if sub_tot.empty:
                continue
            sub_names = list(sub_tot.sort_values(ascending=False).index)
            local_spans = _node_spans(sub_names,
                                      {k: float(v) for k, v in sub_tot.items()},
                                      gap=0.01)
            for sn in sub_names:
                s0, s1 = local_spans[sn]
                sx0, sx1 = x0 + s0 * (x1 - x0), x0 + s1 * (x1 - x0)
                ax.add_patch(plt_rectangle((sx0, 0.05), sx1 - sx0, 0.45,
                                          facecolor=sub_colors[sn],
                                          edgecolor="white"))
                # a box too narrow for the full name would spill its text
                # into the neighboring slice, so truncate (or drop) instead
                # of letting labels overlap - a small box with a shortened
                # label reads better than two boxes with unreadable text.
                width = sx1 - sx0
                if width > 0.09:
                    txt = sn
                elif width > 0.045 and len(sn) > 4:
                    txt = sn[:3] + "…"
                elif width > 0.045:
                    txt = sn
                else:
                    txt = None
                if txt:
                    ax.text((sx0 + sx1) / 2, 0.27, txt, ha="center",
                            va="center", fontsize=7, color="white")

    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(0, 1.02)
    ax.axis("off")


# ---- network (node-link) ---------------------------------------------------

def _draw_network(ax, df, x, y, color_by, chart):
    """Circular layout, not force-directed: it's deterministic (no physics
    iteration to tune or for a test to flake on), always spreads nodes out
    with no overlap, and needs no extra dependency (no networkx). `y` is
    the edge weight - if the data has no natural weight, `add weight = 1`
    upstream gives every edge the same width."""
    from .errors import AskError
    if not (color_by and color_by in df.columns):
        raise AskError(
            what="A network chart needs two category columns: the source "
                 "(x) and target ('color by' column).",
            where=f"line {chart.line}",
            fix="Add an indented line under chart: color by <target column>")
    edges = df.groupby([x, color_by], observed=True)[y].sum().reset_index()
    edges = edges[pd.to_numeric(edges[y], errors="coerce") > 0]
    if edges.empty:
        raise AskError(
            what=f"No positive {y!r} values to draw as edges between {x!r} "
                 f"and {color_by!r}.",
            where=f"line {chart.line}",
            fix="Check the data above the chart step - network needs positive numbers.")
    nodes = list(pd.unique(pd.concat([edges[x].astype(str),
                                      edges[color_by].astype(str)])))
    n = len(nodes)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pos = {node: (np.cos(a), np.sin(a)) for node, a in zip(nodes, angles)}
    wmax = edges[y].max()
    for _, row in edges.iterrows():
        s, t, w = str(row[x]), str(row[color_by]), float(row[y])
        x0, y0 = pos[s]
        x1, y1 = pos[t]
        lw = 0.6 + 4.4 * (w / wmax if wmax else 0)
        ax.plot([x0, x1], [y0, y1], color="#8C8C8C", linewidth=lw,
                alpha=0.55, zorder=1, solid_capstyle="round")
    deg = {}
    for _, row in edges.iterrows():
        deg[str(row[x])] = deg.get(str(row[x]), 0) + float(row[y])
        deg[str(row[color_by])] = deg.get(str(row[color_by]), 0) + float(row[y])
    dmax = max(deg.values()) if deg else 1
    colors = dict(zip(nodes, palette(n)))
    for node in nodes:
        px, py = pos[node]
        size = 180 + 1400 * (deg.get(node, 0) / dmax if dmax else 0)
        ax.scatter([px], [py], s=size, color=colors[node],
                   edgecolors="white", linewidths=1, zorder=2)
        ax.annotate(node, (px, py), textcoords="offset points", xytext=(0, 11),
                    ha="center", fontsize=8, zorder=3)
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-1.45, 1.45)
    ax.set_aspect("equal")
    ax.axis("off")


def plt_polygon(*args, **kwargs):
    from matplotlib.patches import Polygon
    return Polygon(*args, **kwargs)


def plt_rectangle(*args, **kwargs):
    from matplotlib.patches import Rectangle
    return Rectangle(*args, **kwargs)


# ---- map (choropleth) -------------------------------------------------------
#
# The only chart kind that isn't numpy/matplotlib-only: coloring real country
# shapes needs actual geometry, which numpy has no notion of. Rather than
# silently pulling in a heavy dependency, this is gated behind the optional
# `geo` extra (`pip install "ask-lang[geo]"`, i.e. geopandas) - a plain
# install of Ask never needs it, and every other chart type still works
# without it. The country boundaries themselves are a bundled, trimmed-down
# Natural Earth 110m file (public domain, ~180KB) in sample_data/, since
# recent geopandas releases no longer ship a built-in world dataset.

_WORLD_CACHE = None

# Common country names/abbreviations that don't match the bundled dataset's
# `name` field exactly (itself a mix of short forms, e.g. "Czechia",
# "Dem. Rep. Congo") - mapped to the exact name so ordinary data "just works"
# without forcing everyone to look up Natural Earth's naming quirks first.
_COUNTRY_ALIASES = {
    "united states": "united states of america", "usa": "united states of america",
    "us": "united states of america", "u.s.": "united states of america",
    "u.s.a.": "united states of america",
    "uk": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
    "russian federation": "russia",
    "south korea": "south korea", "korea, south": "south korea", "republic of korea": "south korea",
    "north korea": "north korea", "korea, north": "north korea",
    "democratic republic of congo": "dem. rep. congo", "dr congo": "dem. rep. congo",
    "drc": "dem. rep. congo", "congo-kinshasa": "dem. rep. congo",
    "republic of the congo": "congo", "congo-brazzaville": "congo",
    "ivory coast": "côte d'ivoire",
    "czech republic": "czechia",
    "macedonia": "north macedonia",
    "burma": "myanmar",
    "viet nam": "vietnam",
    "bosnia and herzegovina": "bosnia and herz.",
    "cape verde": "cabo verde",
}


def _load_world():
    global _WORLD_CACHE
    if _WORLD_CACHE is None:
        import geopandas as gpd
        from .runtime import SAMPLES_DIR
        import os
        path = os.path.join(SAMPLES_DIR, "world_countries.geojson")
        _WORLD_CACHE = gpd.read_file(path)
    return _WORLD_CACHE


def _draw_map(ax, df, x, y, chart):
    from .errors import AskError
    try:
        import geopandas as gpd  # noqa: F401
    except ImportError:
        raise AskError(
            what="Map charts need the optional 'geo' extra, which isn't installed.",
            where=f"line {chart.line}",
            fix='Install it with: pip install "ask-lang[geo]"  (or: pip install geopandas)')

    agg = df.groupby(x, observed=True)[y].sum().reset_index()
    agg["_key"] = agg[x].astype(str).str.strip().str.casefold()
    agg["_key"] = agg["_key"].map(lambda s: _COUNTRY_ALIASES.get(s, s))

    world = _load_world().copy()
    world["_key"] = world["name"].astype(str).str.casefold()
    merged = world.merge(agg[[x, y, "_key"]], on="_key", how="left")

    matched = int(merged[y].notna().sum())
    if matched == 0:
        raise AskError(
            what=f"None of the values in {x!r} matched a country name.",
            where=f"line {chart.line}",
            fix="Use common English country names (e.g. 'United States', "
                "'France', 'Kenya').")
    unmatched = sorted(set(agg[x].astype(str)) - set(merged.loc[merged[y].notna(), x]))

    merged.plot(column=y, ax=ax, cmap="viridis", legend=True,
                missing_kwds={"color": "#EBEBEB", "edgecolor": "white",
                             "linewidth": 0.3, "label": "no data"},
                edgecolor="white", linewidth=0.3,
                legend_kwds={"label": y, "shrink": 0.55})
    ax.set_axis_off()
    ax.set_aspect("equal")
    if unmatched:
        shown = ", ".join(unmatched[:5]) + ("…" if len(unmatched) > 5 else "")
        ax.text(0.01, 0.02,
                f"Not matched to a country: {shown}", transform=ax.transAxes,
                fontsize=7, color="#8C8C8C", va="bottom")
