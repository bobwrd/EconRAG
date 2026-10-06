# Ask

**A tiny, beginner-friendly language for asking your data questions — with a built-in editor, table, and charts.**

Ask is a data-analysis language designed to be *guessable*. You read a program top to bottom and it does exactly that, in order: load a file, clean it, filter it, group it, chart it. No query planner, no hidden magic, no Python to learn. Under the hood it runs on pandas + matplotlib, but you never see them — you write Ask.

```
load "sales.csv"
  where region is "West"
  show total revenue as sales
  chart sales by region as bar
```

Press **Run**. You get a table and a chart.

![Ordered bar chart from an algebraic type](docs/sample3_ordered_axis.png)

Ask is built to be *better than R for beginners* - not deeper, but clearer. Every run explains itself step by step, statistics come back in plain English with honest uncertainty, errors teach and offer one-click fixes, and a **Show code** button reveals the equivalent pandas/R so you can graduate when you're ready.

---

## Why Ask

Most data tools ask beginners to learn either a spreadsheet's mouse-maze or a programming language's syntax before they can answer a single question. Ask takes a third path, built on five principles:

- **Guessability.** You should be able to predict what a line does before you learn it.
- **One obvious way.** One way to load, one to filter, one to group. No synonyms.
- **Errors teach.** Every error says *what* went wrong, *where* (line + column + the offending value), and gives a paste-able *fix*.
- **Read order = run order.** Clauses execute top to bottom, exactly as written. Nothing silently reorders.
- **The grammar never grows; the vocabulary does.** New capability is a new verb in an existing slot — never a new sentence shape.

The mental model is a **pipeline, not a query**: a program is a stack of clauses, each takes a table and returns a table, and a trailing `chart` renders it.

---

## Install & run

Requires **Python 3.10+**, **pandas ≥ 2.2**, **matplotlib ≥ 3.8**. `tkinter` ships with the python.org installers on macOS and Windows (on Debian/Ubuntu: `sudo apt install python3-tk`).

```bash
python3 -m pip install -r requirements.txt
python3 ask.py                 # launch the UI
```

or install it as a package and get the `ask` command:

```bash
python3 -m pip install -e .
ask                            # UI
ask run analysis.ask           # headless: prints the step trace + final table
ask run analysis.ask --out result.csv --chart chart.png --report report.html
ask run analysis.ask --code pandas    # print the equivalent pandas code
ask selftest                   # run every bundled sample + error case
```

> **Apple-silicon note:** if Python dies importing numpy with an "incompatible
> architecture" error, you have an Intel-built numpy on an arm64 Mac. Fix it with
> `python3 -m pip install --force-reinstall --no-cache-dir numpy pandas matplotlib`.
> Ask detects this and prints the same advice instead of a traceback.

Two optional extras, neither required for anything else in the language: `pip install -e ".[excel]"` for XLSX export, `pip install -e ".[geo]"` for `chart ... as map` (a plain install still teaches you the install command if you hit `map` without it).

The window opens with a code editor (line numbers + highlighting) on the left, a **Run** button, **Table / Chart / Steps** tabs on the right, and a message strip for errors and "here's what I did" notes. Programs run on a background thread, so the window never freezes. **Open…/Save** manage `.ask` files, **Open data…** inserts a `load` line for any file on disk, **Save chart…** exports PNG/SVG/PDF, and **Export table…** writes CSV/XLSX.

Relative paths in `load`/`save` resolve against your **current working directory** first (falling back to the bundled samples), and `save` always writes to your working directory.

---

## A two-minute tour

```
# 1. Load a file. Types (Money, Number, Date...) are auto-detected.
load "sales.csv"

# 2. Add a computed column, vectorized over every row.
add margin = (revenue - cost) / revenue

# 3. Bucket a number into named ranges -> creates a `margin_tier` column.
bin margin into low, medium, high

# 4. Group, then summarize. `as` names the output column.
group by region, margin_tier
show total revenue as sales

# 5. Chart it. Indented lines modify the chart.
chart sales by region as bar
  color by margin_tier
  label "Revenue by region and margin tier"
```

![Grouped bar chart](docs/sample2_grouped_bar.png)

### Types can drive the chart

Define an ordered type and the axis orders itself — `Poor → Fair → Good → Great`, not alphabetically:

```
type Rating = Poor | Fair | Good | Great
load "reviews.csv"
  add score as Rating
  group by score
  show count
  chart count by score as bar
```

### Reusable recipes

Save a sequence of steps and call it as a single verb:

```
define recipe top_regions from data:
  where revenue is not missing
  group by region
  show total revenue as sales
  top 5 by sales

load "q1.csv"
  top_regions
  chart sales by region as bar
```

---

### Statistics, in plain English

R makes you know which test to run. Ask picks the right one and answers in words, with uncertainty by default:

```
load "sales.csv"
  compare revenue between region      # Welch t / ANOVA / chi-square, chosen automatically
  relate cost and revenue             # correlation + interpretation
  predict revenue from cost           # linear regression, in plain language
```

You get findings like *"cost and revenue have a strong relationship (r = 0.88) - they tend to rise together. This is very unlikely to be chance (p < 0.001)"* and *"each 1 more cost is linked to +1.66 in revenue. The model explains 78% of the variation."* All computed in numpy — no scipy/statsmodels/sklearn to install — and pinned against reference values in the test suite.

The rigor is built in: two-group comparisons report Welch's t, a 95% CI, **Hedges' g**, and a rank-test cross-check; ANOVA reports **eta-squared** and Bonferroni-corrected pairwise notes; chi-square reports **Cramér's V** and warns when expected cell counts drop below 5; regressions **refuse to run on collinear inputs** (naming the overlapping columns) instead of reporting meaningless split coefficients, and say how many rows they were fitted on.

### Every run shows its work

The **Steps** tab prints a plain-English audit trail of the whole pipeline (*"kept 340 of 1,200 rows where region = West"*), a live status line shows the table's shape at your cursor, and `explain` on its own line spells out the step above it. When something coerces or drops values, Ask says so instead of doing it silently.

### Errors that fix themselves

Typos come back with a **did-you-mean** and an **Apply fix** button that rewrites the offending line for you. Nothing ever surfaces a Python traceback — half-typed lines, self-referencing recipes, text-math mistakes all come back as teaching errors.

### Share and graduate

`save report as "name.html"` writes a single self-contained HTML file (step trace + table + chart, no external assets). `import "shared.ask"` pulls recipe and type definitions from another file so a team can share a library. And **Show code** emits the equivalent pandas and R for your program — with untranslatable lines clearly marked `TODO` so the output never pretends to be more complete than it is.

## Language reference

A program is `(definition | statement)+`. Statements run in this canonical order (out-of-order clauses raise a teaching error):

```
load | from                         # source (required first)
clean | add | extract | where ...   # transforms (repeatable)
combine | stack
group by
show
reshape
sort by
top | bottom | first | last
chart  (+ indented chart modifiers)
save
```

### Load / save
| Command | Behavior |
|---|---|
| `load "file"` | Read CSV/Excel/JSON; auto-detect column types |
| `from <table>` | Continue on a named/loaded table |
| `save as "file"` | Write the current table to disk |

### Clean / understand
| Command | Behavior |
|---|---|
| `understand` | Profile columns: type, missing %, distinct count; for numbers, also mean/median/spread, the middle-50% range, and whether the shape is symmetric or skewed, in words |
| `clean <col>` | Fix type/locale: `"$1,200"` → Money, mixed dates → Date, trim |
| `standardize <col>` | Cluster near-identical values to one canonical form |
| `fill missing <col> with X` | `value` / `average` / `previous` (fill down) / `next` (fill up) / `interpolate`; `previous`/`next` carry within each `group by` group separately if one is active |
| `drop duplicates [by <cols>]` | Remove repeated rows |
| `flag outliers in <col>` | Mark unusual values (1.5×IQR) without removing them |

### Text extraction (usable on the right of `add`)
| Command | Behavior |
|---|---|
| `extract after "x" from <col>` | Text after the first `x` |
| `extract before "x" from <col>` | Text before the first `x` |
| `extract between "a" and "b" from <col>` | Text between two markers |
| `extract number from <col>` | First numeric value in a string |
| `year of <col>` / `quarter of` / `month of` / `week of` / `day of` | The numbered date part |
| `weekday of <col>` | The day name (`"Monday"`, ...), not a number - no lookup table to memorize |
| `split <col> by "x" into a, b` | Break one column into several |
| `expand <col> by "x"` | One row per delimited piece of `col` (e.g. `"a,b,c"` → 3 rows), everything else duplicated |
| `replace "a" with "b" in <col>` | Substitute text |

### Columns / rows
| Command | Behavior |
|---|---|
| `keep <cols>` / `drop <cols>` | Select / remove columns |
| `rename a to b` | Rename a column |
| `add x = <expr>` | Computed column, vectorized over rows |
| `add t = if C then A else B` | Conditional column |
| `add x as <Type>` | Assign an algebraic type to a column |
| `bin <col> into ...` | Bucket a number into named ranges |
| `where <condition>` | Keep matching rows |

`where` operators: `= > < >= <=`, `is`, `is not`, `and`, `or`, `not`, `between x and y`, `in [...]`, `contains`, `starts with`, `ends with`, `is missing`, `is not missing`.

Column names that aren't a plain word — dots, spaces, punctuation, common in raw exports like ILOStat/Eurostat's `ref_area.label` style columns — need backticks inside an expression: `` where `sex.label` is "Total" ``. Double-quoting one there instead (`where "sex.label" is "Total"`) compares two constant strings, not a column, and Ask will say so rather than silently doing nothing. Outside expressions — `group by`, `keep`, `drop`, `rename`, `reshape long keeping` — a plain word, `` `backtick` ``, or `"double-quoted"` name all work interchangeably, since there's no column-vs-value ambiguity in those positions.

### Group / summarize / combine
| Command | Behavior |
|---|---|
| `group by <cols>` | Partition rows |
| `show <aggs>` | `total average count min max median spread share`, name with `as` |
| `combine with <t> on <key> [fuzzy]` | Join, keeping all rows from this table by default (add `keeping matches only` for an inner join, `keeping all right`/`keeping all` to keep the other table's or both tables' unmatched rows too); `fuzzy` approximate-matches near-miss keys and lists them for review |
| `stack <table>` | Append rows |
| `reshape wide by <c> using <v> [aggregate <agg>]` | Pivot rows into columns; when several rows share a cell, combine them with `total` (default) / `average` / `count` / `min` / `max` / `median` / `spread` |
| `reshape long keeping <ids>` | Unpivot columns into `name`/`value` rows |
| `resample by day/week/month/quarter/year` | Roll a date column up into periods |

Note: `show count` counts rows; `show count <col>` counts **non-missing** values of that column.

### Sort / limit
| Command | Behavior |
|---|---|
| `sort by <col> [descending]` | Order rows |
| `top <n> by <col>` / `bottom <n>` | Highest / lowest n |
| `first <n>` / `last <n>` | By current order |

### Chart
Core types: `bar line area scatter bubble histogram box pie heatmap distribution change flow tree network map`.
Modifiers (indented under `chart`): `color by`, `size by`, `animate by`, `add trendline`, `label "..."`, `explain chart`.

`color by` groups bars, draws one line per group, stacks areas, colors scatter points, and gives heatmap/flow/tree/network their second category axis (see the table below). `box` draws one box per category. Histograms pick their bin count automatically (Freedman–Diaconis). Colors come from the Okabe–Ito colorblind-safe palette. The Chart tab has a zoom/pan toolbar, and **Save chart…** exports PNG/SVG/PDF (and GIF/MP4 for an animated `bubble` chart).

```
chart revenue by region as bar
  color by segment
  label "Revenue by region"
```

| Type | Behavior |
|---|---|
| `distribution` | Density curve of one number (`x` is unused, same convention as `histogram`); `color by <group>` overlays one curve per group instead of a single curve |
| `change` | Slope/bump chart: one line per `color by` group across the ordered `x` categories, with the first→last percent change labeled at the line's end |
| `flow` | Sankey: flow between `x` (source) and `color by` (target), ribbon width = the summed value |
| `tree` | Icicle (two-level slice treemap): top band = `x` categories sized by total value, bottom band = each one split further by `color by` |
| `network` | Node-link graph on a circular layout: nodes = the union of `x` and `color by` values, edges weighted by value (add a constant column first if you have no real weight) |
| `map` | Choropleth by country name (`x` = country column, `y` = value); needs the optional `geo` extra - `pip install "ask-lang[geo]"` - a plain install still runs everything else |
| `story` | Replays every chart drawn earlier in the *same* program as a grid of panels - needs at least one chart step above it |

`flow`, `network`, and `heatmap` all reuse `color by` as their second category axis rather than adding new grammar for it. `map`'s only new thing is the optional dependency - install it once (`pip install "ask-lang[geo]"`) and it renders like anything else.

### Statistics, explain & sharing
| Command | Behavior |
|---|---|
| `compare <value> between <group>` | Auto-picks Welch t / ANOVA / a two-proportion z-test (yes/no value, 2 groups) / chi-square (more categories); reports direction, effect size + significance in words |
| `compare <a> and <b> paired` | Paired t-test on two matched number columns (e.g. before/after on the same rows), not independent groups |
| `relate <a> and <b>` | Pearson + Spearman correlation with a plain interpretation |
| `relate <a> and <b> controlling for <z>` | Partial correlation - the link between a and b with z's shared influence removed |
| `relate all of <a>, <b>, <c>, ...` | Correlation matrix across every pair, with the strongest relationships called out |
| `predict <target> from <a>, <b>` | Linear (or logistic, for yes/no targets) regression; each factor explained in plain language, plus a standardized coefficient for genuine number inputs so factors on different scales (dollars vs. a 1-5 rating) are comparable |
| `predict <target> from <a> with polynomial <n>` | Adds `<a>^2` .. `<a>^n` terms for every number input, for a curved (not just straight-line) fit |
| `predict <target> from <a>, <b> using r package "X"` | Same regression, delegated to a real R (`Rscript`) running package `X`, for cross-checking Ask's built-in numbers - fully optional, and a plain-English teaching error (no traceback) if R isn't installed |
| `estimate average <col> with bootstrap` | Resampled point estimate + 95% interval |
| `explain` | On its own line, describe the step above it (row counts before/after) |
| `import "file.ask"` | Load recipe, type and function definitions from another file |
| `save report as "file.html"` | Self-contained HTML report: step trace + table + chart |

---

## Errors that teach

Ask never shows a Python traceback. A mistake produces three parts — what, where, and a fix:

```
Column 'price' is text, but you're doing math on it.
line 4
Try: clean price
```

Ask a `show` before a `group by`, misspell a column, stop a line half-way through, or feed a regression two copies of the same input — you get the same friendly treatment instead of a crash.

---

## The ILOStat acceptance test

The repo bundles `ilostat.csv` (female employment in Chile, with the economic activity buried inside a `notes` field under three classification schemes) so this runs out of the box:

```
load "ilostat.csv"
  understand
  add scheme = extract between "(" and ")" from notes
  add activity = extract after ": " from notes
  add activity = extract after ". " from activity
  where scheme is "ISIC-Rev.4"
  where activity is not "Total"
  where year is 2025
  group by activity
  show total value as employment
  top 10 by employment
  sort by employment descending
  chart employment by activity as bar
    label "Female employment by sector, Chile 2025"
```

![ILOStat sector chart](docs/sample5_ilostat.png)

---

## How it works

Four stages, mirroring a classic interpreter:

```
source text
  -> tokenize()   : str            -> list[Token]
  -> parse()      : list[Token]    -> list[Clause]   (AST, clause-order checked)
  -> interpret()  : list[Clause]   -> Table | Chart  (tree-walk, one Table threaded through)
  -> render()     : Table | Chart  -> tkinter panes
```

A `Table` wraps a pandas `DataFrame` plus a column→algebraic-type map; the interpreter has one `visit_<Clause>` method per verb.

## Project layout

```
asklang/            The language + UI, as a package
  errors.py           AskError (what / where / fix / one-click apply)
  tokens.py           tokenizer
  astnodes.py         one Clause dataclass per verb
  parser.py           recursive descent + clause-order enforcement
  expressions.py      expression/condition parser + evaluator
  runtime.py          Table, type inference, cached file loading
  interpreter.py      one visit_<Clause> per verb
  stats.py            numpy-only statistics (pinned by tests)
  charts.py           Chart spec -> matplotlib Figure
  report.py           self-contained HTML reports
  codegen.py          pandas/R code export
  samples.py          the bundled sample programs
  ui.py               tkinter app
  cli.py              `ask` command-line interface
ask.py              Compatibility launcher (python3 ask.py still works)
tests/              pytest suite (113 tests: parser, language, charts, stats, codegen, uploads)
packaging/          build_app.sh - builds a self-contained macOS Ask.app (see below)
sample_data/        Bundled CSVs + shared.ask, kept out of the repo root
make_data.py        Regenerates the files in sample_data/
docs/               Screenshots used in this README
```

Run the tests with `python3 -m pytest`.

### Building the standalone macOS app

```bash
./packaging/build_app.sh
```

Produces `dist/Ask.app` — a fully self-contained bundle (Python + numpy/pandas/matplotlib/tkinter all included) that runs on any Mac with zero setup, no signing, and no Apple Developer account. It's ad-hoc signed automatically (free, built into `codesign`), so a local build just double-clicks and runs. A copy downloaded from elsewhere needs one right-click → Open the first time — that's Gatekeeper reacting to the download's quarantine flag, not something removable without a paid Developer ID. The script also zips the result to `dist/Ask-macOS-arm64.zip`, ready to attach to a GitHub release.

---

## Roadmap

Implemented: the full core path, text extraction, cleaning, recipes and user-defined types, `reshape long`/`wide` (with an `aggregate` option for many-to-one cells), `resample by`, fuzzy `combine` with explicit join kinds (`keeping matches only` / `keeping all left` / `keeping all right` / `keeping all`), grouped fill down/up, `expand` (row explode), date-part helpers (`year of`/`quarter of`/`month of`/`week of`/`day of`/`weekday of`), importable recipes, HTML reports, chart/table export, a headless CLI, pandas/R code export, every chart type including the advanced ones (`distribution`, `change`, `flow`, `tree`, `network`, `map`, `story`), and plain-English statistics: `compare` (Welch t / ANOVA / two-proportion z-test / chi-square / paired t-test), `relate` (Pearson + Spearman, partial correlation, correlation matrices), `predict` (linear/logistic, with polynomial terms and standardized coefficients), and `estimate`. Also the R bridge (`predict ... using r package "X"`, gracefully teaching if R isn't installed). `map` needs the optional `geo` extra (`pip install "ask-lang[geo]"`); the R bridge needs a real `Rscript` on PATH; nothing else in the core install requires either.

Not yet built (a much larger R-parity effort): `trend`/`forecast`/`cluster`, mixed-effects/survival/GAM/ARIMA/Bayesian models via the R bridge, and a `parameters` header block for re-runnable programs.

---

## License

MIT — see [LICENSE](LICENSE).
