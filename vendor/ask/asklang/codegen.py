"""Code export (graduation bridge: "here's what Ask did in real code").

A teaching aid, not a compiler: core verbs translate faithfully; anything
without a clean one-liner is left as a clearly-labelled TODO comment, and the
header warns when the output is incomplete so it never masquerades as a
runnable translation (bug F6).
"""

import re

from . import context
from .astnodes import (Add, Bin, Chart, Combine, Compare, DropCols, Estimate,
                       Expand, GroupBy, Keep, Limit, Load, Predict, Relate,
                       Rename, SaveNamed, SetSeed, Show, SortBy, Where, Window)
from .errors import AskError
from .expressions import E
from .interpreter import Interpreter
from .parser import Parser
from .tokens import tokenize

_PANDAS_DATE_ATTR = {"year": "year", "quarter": "quarter", "month": "month",
                     "week": "isocalendar().week", "day": "day"}
_R_DATE_FUNC = {"year": "year", "quarter": "quarter", "month": "month",
                "week": "isoweek", "day": "day"}

_AGG_PANDAS = {"total": "sum", "average": "mean", "count": "count", "min": "min",
               "max": "max", "median": "median", "spread": "std"}
_AGG_R = {"total": "sum", "average": "mean", "min": "min", "max": "max",
          "median": "median", "spread": "sd"}

_INCOMPLETE_NOTE = (
    "# NOTE: lines marked 'TODO' had no direct one-liner and were left as\n"
    "# comments - fill them in before running this code.")


def generate_code(src, dialect="pandas"):
    """Emit equivalent pandas or R (dplyr) code for an Ask program."""
    lines = tokenize(src)
    try:
        clauses = Parser(lines).parse_program()
    except AskError as e:
        return f"# Could not translate - fix this first:\n# {e.what}"
    saved = context.interp
    shim = Interpreter()
    shim.raw_lines = {ln.lineno: ln.raw for ln in lines}
    context.interp = shim
    try:
        out, incomplete = (_gen_pandas(clauses) if dialect == "pandas"
                           else _gen_r(clauses))
        if incomplete:
            out.insert(0, _INCOMPLETE_NOTE)
        return "\n".join(out)
    finally:
        context.interp = saved


def _cond_to_query(raw_after):
    q = raw_after
    q = re.sub(r"\bis not\b", "!=", q)
    q = re.sub(r"\bis\b", "==", q)
    return q.strip()


def _raw(c):
    return (context.interp.raw_lines.get(getattr(c, "line", 0), "")
            if context.interp else "")


def _chart_color_by(c):
    for m in c.mods:
        if m.kind == "color":
            return m.value
    return None


def _chart_pandas(c):
    """pandas/matplotlib translation for a Chart clause. The five advanced
    kinds (distribution/change/flow/tree/network) have no pandas .plot(kind=)
    equivalent, so each gets its own hand-written snippet; every other kind
    keeps the original generic one-liner."""
    color_by = _chart_color_by(c)
    if c.kind == "distribution":
        if color_by:
            return ([f'for _g, _sub in df.groupby("{color_by}"):',
                     f'    _sub["{c.y}"].plot(kind="density", alpha=0.4, label=str(_g))',
                     'plt.legend(); plt.show()'], False)
        return ([f'df["{c.y}"].plot(kind="density"); plt.show()'], False)
    if c.kind == "change":
        if color_by:
            return ([f'for _g, _sub in df.groupby("{color_by}"):',
                     f'    _sub.plot(x="{c.x}", y="{c.y}", marker="o", '
                     f'label=str(_g), ax=plt.gca())',
                     'plt.show()'], False)
        return ([f'df.plot(x="{c.x}", y="{c.y}", marker="o"); plt.show()'], False)
    if c.kind == "flow":
        if not color_by:
            return ([f"# TODO flow chart {c.x} -> ? needs a 'color by' target column"],
                    True)
        return ([f'# a Sankey needs a plotting library (e.g. matplotlib.sankey, '
                 f'or plotly); shown here as the underlying flow table:',
                 f'flows = df.groupby(["{c.x}", "{color_by}"])["{c.y}"]'
                 f'.sum().reset_index()'], True)
    if c.kind == "tree":
        extra = f', "{color_by}"' if color_by else ""
        return ([f'# no built-in pandas treemap; squarify.plot(sizes=..., label=...) '
                 f'is a common add-on',
                 f'sizes = df.groupby(["{c.x}"{extra}])["{c.y}"].sum()'], True)
    if c.kind == "network":
        if not color_by:
            return ([f"# TODO network chart {c.x} -> ? needs a 'color by' target column"],
                    True)
        return ([f'# a node-link layout needs networkx; shown here as the edge list:',
                 f'edges = df.groupby(["{c.x}", "{color_by}"])["{c.y}"].sum().reset_index()'],
                True)
    if c.kind == "map":
        return ([f'# needs geopandas + the world_countries.geojson bundled with Ask',
                 f'import geopandas as gpd',
                 f'world = gpd.read_file("sample_data/world_countries.geojson")',
                 f'world.merge(df, left_on="name", right_on="{c.x}", how="left")'
                 f'.plot(column="{c.y}", legend=True)'], True)
    if c.kind == "story":
        return ([f'# TODO story chart - replay each earlier chart step as its own '
                 f'subplot (plt.subplots(...))'], True)
    return ([f'df.plot(kind="{c.kind}", x="{c.x}", y="{c.y}"); plt.show()'], False)


def _predict_r_bridge_lines(c):
    """The actual call `predict ... using r package "X"` makes in R (see
    rbridge.generate_r_script) - shown verbatim, since it's already R,
    regardless of which dialect (pandas/R) code export was asked for."""
    terms = list(c.inputs) + [f"{a}:{b}" for a, b in c.interactions]
    formula = f"{c.target} ~ {' + '.join(terms)}" if terms else f"{c.target} ~ 1"
    return [
        f'# the R bridge runs this for real, via Rscript (needs R installed):',
        f'library({c.engine})',
        f"model <- lm({formula!r}, data = df)  "
        f"# or glm(..., family = binomial) for a yes/no target",
    ]


_R_GEOM = {"bar": "geom_col()", "line": "geom_line()", "area": "geom_area()",
          "scatter": "geom_point()", "histogram": "geom_histogram()",
          "box": "geom_boxplot()"}


def _chart_r(c):
    """R (ggplot2) translation for a Chart clause - same split as
    `_chart_pandas`: the five advanced kinds get a hand-written geom/TODO,
    everything else keeps the original generic aes()+geom mapping."""
    color_by = _chart_color_by(c)
    if c.kind == "distribution":
        fill = f', fill = {color_by}' if color_by else ""
        return ([f'ggplot(df, aes(x = {c.y}{fill})) + '
                 f'geom_density(alpha = 0.4)'], False)
    if c.kind == "change":
        group = f', color = {color_by}, group = {color_by}' if color_by else ""
        return ([f'ggplot(df, aes(x = {c.x}, y = {c.y}{group})) + '
                 f'geom_line() + geom_point()'], False)
    if c.kind == "flow":
        if not color_by:
            return ([f"# TODO flow chart {c.x} -> ? needs a 'color by' target column"],
                    True)
        return ([f'# install.packages("ggalluvial") for a real Sankey/alluvial geom',
                 f'ggplot(df, aes(axis1 = {c.x}, axis2 = {color_by}, y = {c.y})) + '
                 f'ggalluvial::geom_alluvium()'], True)
    if c.kind == "tree":
        fill = f', fill = {color_by}' if color_by else ""
        return ([f'# install.packages("treemapify") for a real treemap geom',
                 f'ggplot(df, aes(area = {c.y}, label = {c.x}{fill})) + '
                 f'treemapify::geom_treemap()'], True)
    if c.kind == "network":
        if not color_by:
            return ([f"# TODO network chart {c.x} -> ? needs a 'color by' target column"],
                    True)
        return ([f'# install.packages(c("igraph", "ggraph")) for a real node-link plot',
                 f'g <- igraph::graph_from_data_frame(df[, c("{c.x}", "{color_by}", '
                 f'"{c.y}")])'], True)
    if c.kind == "map":
        return ([f'# install.packages(c("sf", "rnaturalearth")) for a real choropleth',
                 f'world <- rnaturalearth::ne_countries(scale = "small", returnclass = "sf")',
                 f'merge(world, df, by.x = "name", by.y = "{c.x}", all.x = TRUE)'], True)
    if c.kind == "story":
        return ([f'# TODO story chart - replay each earlier chart step as its own '
                 f'facet/panel (patchwork::wrap_plots(...))'], True)
    geom = _R_GEOM.get(c.kind, "geom_col()")
    return ([f'ggplot(df, aes({c.x}, {c.y})) + {geom}'], False)


def _gen_pandas(clauses):
    out = ["import pandas as pd", "import matplotlib.pyplot as plt", ""]
    pending_group = None
    incomplete = False
    for c in clauses:
        if isinstance(c, Load):
            out.append(f'df = pd.read_csv("{c.path}")')
        elif isinstance(c, Where):
            after = re.sub(r"^\s*where\s+", "", _raw(c)).strip()
            out.append(f'df = df.query({_cond_to_query(after)!r})'
                       if after else "# TODO where ...")
            incomplete = incomplete or not after
        elif isinstance(c, Add) and isinstance(c.expr, E.DatePart):
            col, part = c.expr.col, c.expr.part
            if part in _PANDAS_DATE_ATTR:
                out.append(f'df["{c.name}"] = pd.to_datetime(df["{col}"])'
                           f'.dt.{_PANDAS_DATE_ATTR[part]}')
            else:
                out.append(f'df["{c.name}"] = pd.to_datetime(df["{col}"]).dt.day_name()')
        elif isinstance(c, Add) and c.expr is not None:
            raw = _raw(c)
            rhs = raw.split("=", 1)[1].strip() if "=" in raw else ""
            if rhs and "extract" not in rhs and "if " not in rhs:
                out.append(f'df["{c.name}"] = df.eval({rhs!r})')
            else:
                out.append(f'# TODO add {c.name} = {rhs}   (text/conditional - '
                           f'do by hand)')
                incomplete = True
        elif isinstance(c, GroupBy):
            pending_group = c.cols
        elif isinstance(c, Show):
            if pending_group:
                specs = []
                for a in c.aggs:
                    if a.func == "count" and a.col is None:
                        specs.append(f'{a.name}=("{pending_group[0]}", "size")')
                    else:
                        specs.append(f'{a.name}=("{a.col}", '
                                     f'"{_AGG_PANDAS.get(a.func, "sum")}")')
                keys = ", ".join(f'"{k}"' for k in pending_group)
                out.append(f'df = df.groupby([{keys}]).agg({", ".join(specs)})'
                           f'.reset_index()')
                pending_group = None
            else:
                specs = ", ".join(f'"{a.col}": "{_AGG_PANDAS.get(a.func, "sum")}"'
                                  for a in c.aggs if a.col)
                out.append(f'df = df.agg({{{specs}}})' if specs
                           else 'print(len(df))  # show count')
        elif isinstance(c, SortBy):
            out.append(f'df = df.sort_values("{c.col}", ascending={not c.descending})')
        elif isinstance(c, Limit):
            if c.mode == "top" and c.col:
                out.append(f'df = df.nlargest({c.n}, "{c.col}")')
            elif c.mode == "bottom" and c.col:
                out.append(f'df = df.nsmallest({c.n}, "{c.col}")')
            elif c.mode in ("top", "first"):
                out.append(f'df = df.head({c.n})')
            else:
                out.append(f'df = df.tail({c.n})')
        elif isinstance(c, Keep):
            out.append(f'df = df[{c.cols}]')
        elif isinstance(c, DropCols):
            out.append(f'df = df.drop(columns={c.cols})')
        elif isinstance(c, Rename):
            out.append(f'df = df.rename(columns={{"{c.old}": "{c.new}"}})')
        elif isinstance(c, Bin):
            out.append(f'df["{c.col}_tier"] = pd.qcut(df["{c.col}"], q={len(c.labels)}, '
                       f'labels={c.labels})')
        elif isinstance(c, Expand):
            out.append(f'df["{c.col}"] = df["{c.col}"].astype(str).str.split("{c.sep}")')
            out.append(f'df = df.explode("{c.col}", ignore_index=True)')
            out.append(f'df["{c.col}"] = df["{c.col}"].str.strip()')
        elif isinstance(c, Combine):
            out.append(f'{c.other} = pd.read_csv("{c.other}.csv")  '
                       f'# or your own DataFrame')
            out.append(f'df = df.merge({c.other}, on="{c.key}", how="{c.how}")')
        elif isinstance(c, SaveNamed):
            out.append(f'{c.name} = df.copy()')
        elif isinstance(c, SetSeed):
            out.insert(2, "import numpy as np")
            out.append(f'rng = np.random.default_rng({c.seed})')
        elif isinstance(c, Window):
            op = {"running_total": ".cumsum()", "rank": ".rank()",
                  "lag": f".shift({c.n})", "lead": f".shift(-{c.n})",
                  "moving_average": f".rolling({c.n}, min_periods=1).mean()"}.get(c.op, "")
            out.append(f'df["{c.name}"] = df["{c.col}"]{op}  '
                       f'# add .groupby(...) first for per-group windows')
        elif isinstance(c, Chart):
            lines, chart_incomplete = _chart_pandas(c)
            out.extend(lines)
            incomplete = incomplete or chart_incomplete
        elif isinstance(c, Predict) and c.engine is not None:
            out.extend(_predict_r_bridge_lines(c))
            incomplete = True
        elif isinstance(c, (Compare, Relate, Predict, Estimate)):
            out.append(f"# TODO {type(c).__name__.lower()} - use scipy/statsmodels "
                       f"for this in Python")
            incomplete = True
        else:
            raw = _raw(c)
            out.append(f"# TODO {raw.strip() or type(c).__name__}   "
                       f"(no direct one-liner)")
            incomplete = True
    return out, incomplete


def _gen_r(clauses):
    out = ["library(dplyr)", "library(readr)", "library(ggplot2)", ""]
    pending_group = None
    incomplete = False
    for c in clauses:
        if isinstance(c, Load):
            out.append(f'df <- read_csv("{c.path}")')
        elif isinstance(c, Where):
            after = re.sub(r"^\s*where\s+", "", _raw(c)).strip()
            r = re.sub(r"\bis not\b", "!=", after)
            r = re.sub(r"\bis\b", "==", r)
            r = r.replace('"', "'")
            out.append(f'df <- df %>% filter({r})' if after else "# TODO where ...")
            incomplete = incomplete or not after
        elif isinstance(c, Add) and isinstance(c.expr, E.DatePart):
            col, part = c.expr.col, c.expr.part
            if part in _R_DATE_FUNC:
                out.append(f'df <- df %>% mutate({c.name} = '
                           f'lubridate::{_R_DATE_FUNC[part]}({col}))')
            else:
                out.append(f'df <- df %>% mutate({c.name} = '
                           f'lubridate::wday({col}, label = TRUE, abbr = FALSE))')
        elif isinstance(c, Add) and c.expr is not None:
            raw = _raw(c)
            rhs = raw.split("=", 1)[1].strip() if "=" in raw else ""
            if rhs and "extract" not in rhs and "if " not in rhs:
                out.append(f'df <- df %>% mutate({c.name} = {rhs})')
            else:
                out.append(f'# TODO add {c.name} = {rhs}  (text/conditional - '
                           f'do by hand)')
                incomplete = True
        elif isinstance(c, GroupBy):
            pending_group = c.cols
        elif isinstance(c, Show):
            if pending_group:
                specs = []
                for a in c.aggs:
                    if a.func == "count" and a.col is None:
                        specs.append(f"{a.name} = n()")
                    else:
                        specs.append(f"{a.name} = {_AGG_R.get(a.func, 'sum')}"
                                     f"({a.col}, na.rm=TRUE)")
                keys = ", ".join(pending_group)
                out.append(f'df <- df %>% group_by({keys}) %>% '
                           f'summarise({", ".join(specs)}, .groups="drop")')
                pending_group = None
            else:
                specs = ", ".join(f"{a.name} = {_AGG_R.get(a.func, 'sum')}"
                                  f"({a.col}, na.rm=TRUE)"
                                  for a in c.aggs if a.col)
                out.append(f'df <- df %>% summarise({specs})' if specs
                           else 'nrow(df)  # show count')
        elif isinstance(c, SortBy):
            col = f"desc({c.col})" if c.descending else c.col
            out.append(f'df <- df %>% arrange({col})')
        elif isinstance(c, Limit):
            if c.mode == "top" and c.col:
                out.append(f'df <- df %>% slice_max({c.col}, n = {c.n})')
            elif c.mode == "bottom" and c.col:
                out.append(f'df <- df %>% slice_min({c.col}, n = {c.n})')
            else:
                out.append(f'df <- df %>% head({c.n})')
        elif isinstance(c, Keep):
            out.append(f'df <- df %>% select({", ".join(c.cols)})')
        elif isinstance(c, Expand):
            out.append(f'df <- df %>% tidyr::separate_rows({c.col}, sep = "{c.sep}")')
        elif isinstance(c, Combine):
            joins = {"inner": "inner_join", "left": "left_join",
                     "right": "right_join", "outer": "full_join"}
            out.append(f'{c.other} <- read_csv("{c.other}.csv")')
            out.append(f'df <- df %>% {joins.get(c.how, "left_join")}'
                       f'({c.other}, by = "{c.key}")')
        elif isinstance(c, SaveNamed):
            out.append(f'{c.name} <- df')
        elif isinstance(c, Chart):
            lines, chart_incomplete = _chart_r(c)
            out.extend(lines)
            incomplete = incomplete or chart_incomplete
        elif isinstance(c, Predict) and c.engine is not None:
            out.extend(_predict_r_bridge_lines(c))
            incomplete = True
        else:
            raw = _raw(c)
            out.append(f"# TODO {raw.strip() or type(c).__name__}  "
                       f"(no direct one-liner)")
            incomplete = True
    return out, incomplete
