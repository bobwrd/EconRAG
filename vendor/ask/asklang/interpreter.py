"""Interpreter: one visit_<Clause> per verb, threading a Table through."""

import math
import os
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import context, stats
from .astnodes import (Add, Bin, Chart, Compare, Estimate, Explain, FlagOutliers,
                       FromTable, ImportRecipes, Limit, Load, Predict, RecipeDef,
                       RecordDef, Relate, Resample, Reshape, SaveNamed, SetSeed,
                       Show, Split, StrictMode, TypeDef, Where, FunctionDef,
                       DropDuplicates, GroupFilter, Window)
from .errors import AskError
from .expressions import (_EXPR_LOCALS, _unknown_column_error, eval_condition,
                          eval_expr)
from .parser import (ADVANCED_CHART_TYPES, CORE_CHART_TYPES, Parser, verb_word)
from .runtime import (Table, load_table, resolve_read_path, resolve_write_path)
from .tokens import tokenize

_MAX_RECIPE_DEPTH = 20


class Env:
    def __init__(self):
        self.types = {}      # name -> TypeDef (variants + ordered flag)
        self.records = {}    # name -> field dict
        self.recipes = {}    # name -> RecipeDef
        self.functions = {}  # name -> FunctionDef (expression macros)
        self.tables = {}     # named tables for from/combine/stack
        self.rng = np.random.default_rng()   # replaced by `set seed`
        self.strict = False                  # `use strict types` raises on drops


@dataclass
class TraceStep:
    line: int
    verb: str
    text: str
    before: Optional[int]
    after: Optional[int]
    cols: Optional[int]


class Interpreter:
    def __init__(self, env=None):
        self.env = env or Env()
        self.notices = []
        self.trace = []
        self.last_chart = None
        self.chart_history = []
        self.last_stat = None
        self.raw_lines = {}
        self.source = ""
        self._recipe_depth = 0

    def run(self, clauses):
        context.interp = self
        _EXPR_LOCALS.clear()
        table = None       # the working table, threaded through the pipeline
        display = None     # what to SHOW (a stats summary doesn't replace `table`)
        chart = None
        try:
            for clause in clauses:                   # read order == run order
                name = type(clause).__name__
                method = getattr(self, "visit_" + name, None)
                if method is None:
                    raise AskError(f"I can't run a {name} yet.")
                before = len(table.df) if isinstance(table, Table) else None
                result = method(clause, table)
                if isinstance(clause, (Compare, Relate, Predict, Estimate)):
                    display = self.last_stat          # terminal, non-destructive
                    after, cols = before, _cols_of(table)
                elif isinstance(result, Chart):
                    chart = result
                    self.last_chart = result
                    after, cols = before, _cols_of(table)
                elif isinstance(result, Table):
                    table = result
                    display = result
                    after, cols = len(table.df), _cols_of(table)
                else:
                    after, cols = before, _cols_of(table)
                desc = self._describe(clause, before, after, cols)
                if desc:
                    self.trace.append(TraceStep(getattr(clause, "line", 0),
                                                name, desc, before, after, cols))
        finally:
            context.interp = None
        return (display if display is not None else table), chart, \
            self.notices, self.trace

    # ---- plain-English trace ----------------------------------------------
    def _describe(self, clause, before, after, cols):
        raw = self.raw_lines.get(getattr(clause, "line", 0), "").strip()
        if not raw:
            raw = type(clause).__name__
        if isinstance(clause, Explain):
            return None
        if isinstance(clause, (TypeDef, RecordDef, RecipeDef, FunctionDef,
                               ImportRecipes)):
            return f"{raw}   (setup)"
        if isinstance(clause, (SetSeed, StrictMode)):
            return f"{raw}   (setting)"
        if isinstance(clause, SaveNamed):
            return f"{raw}   ->   snapshot saved"
        if isinstance(clause, Chart):
            return f"{raw}   ->   drew a {clause.kind} chart"
        if isinstance(clause, (Compare, Relate, Predict, Estimate)):
            return f"{raw}   ->   see the finding above"
        if isinstance(clause, Window):
            return f"{raw}   ->   added {clause.name}"
        if isinstance(clause, GroupFilter) and before is not None and after is not None:
            return f"{raw}   ->   kept {after:,} of {before:,} rows"
        if isinstance(clause, Load) and after is not None:
            return f"{raw}   ->   {after:,} rows, {cols} columns"
        if isinstance(clause, Where) and before is not None and after is not None:
            return f"{raw}   ->   kept {after:,} of {before:,} rows"
        if isinstance(clause, DropDuplicates) and before is not None and after is not None:
            return f"{raw}   ->   {after:,} rows left (was {before:,})"
        if isinstance(clause, Limit) and after is not None:
            return f"{raw}   ->   {after:,} rows"
        if isinstance(clause, (Show, Resample, Reshape)) and after is not None:
            return f"{raw}   ->   summarized to {after:,} rows, {cols} columns"
        if isinstance(clause, (Add, Bin, Split, FlagOutliers)):
            return f"{raw}   ->   now {cols} columns"
        if before is not None and after is not None and after != before:
            return f"{raw}   ->   {after:,} of {before:,} rows"
        if after is not None:
            return f"{raw}   ->   {after:,} rows, {cols} columns"
        return raw

    # ---- definitions -------------------------------------------------------
    def visit_TypeDef(self, c, table):
        self.env.types[c.name] = c
        joiner = " < " if c.ordered else " | "
        kind = "ordered" if c.ordered else "unordered"
        self.notices.append(f"Defined {kind} type {c.name}: "
                            + joiner.join(c.variants))
        return None

    def visit_FunctionDef(self, c, table):
        self.env.functions[c.name] = c
        self.notices.append(f"Defined function {c.name}"
                            f"({', '.join(c.params)}).")
        return None

    def visit_SetSeed(self, c, table):
        self.env.rng = np.random.default_rng(c.seed)
        self.notices.append(f"set the random seed to {c.seed} "
                            f"(so resampling is repeatable).")
        return table

    def visit_StrictMode(self, c, table):
        self.env.strict = True
        self.notices.append("strict types on: silent number drops now stop the run.")
        return table

    def visit_SaveNamed(self, c, table):
        self._need(table, c)
        self.env.tables[c.name] = table.copy()
        self.notices.append(f"saved a snapshot as {c.name!r} "
                            f"({len(table.df)} rows). Reuse it with: from {c.name}")
        return table

    def visit_RecordDef(self, c, table):
        self.env.records[c.name] = c.fields
        return None

    def visit_RecipeDef(self, c, table):
        self.env.recipes[c.name] = c
        self.notices.append(f"Defined recipe {c.name} ({len(c.body)} steps).")
        return None

    def visit_RecipeCall(self, c, table):
        recipe = self.env.recipes.get(c.name)
        if recipe is None:
            raise AskError(f"I don't know a recipe named {c.name!r}.",
                           f"line {c.line}", "Define it first with: define recipe ...")
        base = table.df if isinstance(table, Table) else pd.DataFrame()
        arg_values = [eval_expr(a, base, c.line) for a in c.args]
        return self._run_recipe(recipe, table, arg_values, c.line)

    def _run_recipe(self, recipe, table, arg_values, ln):
        """Run a recipe body, binding any parameters as local expression values.
        Recipe composition works because a body may itself contain RecipeCalls."""
        if recipe.params and len(arg_values) != len(recipe.params):
            raise AskError(
                what=f"The recipe {recipe.name!r} expects {len(recipe.params)} "
                     f"value(s), but got {len(arg_values)}.",
                where=f"line {ln}",
                fix=f"Call it as {recipe.name}({', '.join(recipe.params)})", line=ln)
        if self._recipe_depth >= _MAX_RECIPE_DEPTH:
            raise AskError(
                what=f"The recipe {recipe.name!r} keeps calling itself and "
                     f"never finishes.",
                where=f"line {ln}",
                fix="A recipe can't call itself. Remove the self-reference.",
                line=ln)
        scope = {p: v for p, v in zip(recipe.params, arg_values)}
        _EXPR_LOCALS.append(scope)
        self._recipe_depth += 1
        try:
            for step in recipe.body:
                method = getattr(self, "visit_" + type(step).__name__)
                res = method(step, table)
                if isinstance(res, Table):
                    table = res
            return table
        finally:
            self._recipe_depth -= 1
            _EXPR_LOCALS.pop()

    # ---- source ------------------------------------------------------------
    def visit_Load(self, c, table):
        t = load_table(c.path)
        self.env.tables["data"] = t
        return t

    def visit_FromTable(self, c, table):
        if c.name in self.env.tables:
            return self.env.tables[c.name].copy()
        # fall back to a same-named CSV only if it actually exists (bug F4:
        # a typo'd snapshot name used to silently load a surprise file).
        csv_path = resolve_read_path(c.name + ".csv")
        if os.path.exists(csv_path):
            return load_table(c.name + ".csv")
        known = ", ".join(sorted(self.env.tables)) or "(none yet)"
        raise AskError(
            what=f"I don't know a table named {c.name!r}.",
            where=f"line {c.line}",
            fix=f"Snapshot one first with: call it {c.name}. "
                f"Known tables: {known}.", line=c.line)

    def visit_Save(self, c, table):
        self._need(table, c)
        out = resolve_write_path(c.path)
        try:
            table.df.to_csv(out, index=False)
        except OSError:
            raise AskError(what=f"I couldn't write the file {c.path!r}.",
                           where=f"line {c.line}",
                           fix="Pick a writable location or a different name.")
        self.notices.append(f"Saved {len(table.df)} rows to {out}.")
        return table

    # ---- clean / understand ------------------------------------------------
    def visit_Understand(self, c, table):
        self._need(table, c)
        df = table.df
        rows = []
        for col in df.columns:
            s = df[col]
            missing = f"{s.isna().mean() * 100:.0f}%"
            line = (f"  {col}: {table.coltypes.get(col, 'Text')}, "
                    f"missing {missing}, {s.nunique()} distinct")
            if pd.api.types.is_numeric_dtype(s):
                vals = pd.to_numeric(s, errors="coerce").dropna().to_numpy(float)
                if len(vals) >= 2:
                    mean = vals.mean()
                    median = stats.quantile(vals, 0.5)
                    sd = math.sqrt(stats.welford_var(vals))
                    q1 = stats.quantile(vals, 0.25)
                    q3 = stats.quantile(vals, 0.75)
                    skew = float(pd.Series(vals).skew()) if len(vals) >= 3 else float("nan")
                    if skew != skew:
                        shape = "not enough data to judge shape"
                    elif abs(skew) < 0.5:
                        shape = "roughly symmetric"
                    elif skew > 0:
                        shape = ("right-skewed (a few unusually high values "
                                "pull the average up)")
                    else:
                        shape = ("left-skewed (a few unusually low values "
                                "pull the average down)")
                    line += (f" -- averages {mean:,.2f} (median {median:,.2f}, "
                            f"spread {sd:,.2f}); the middle half falls between "
                            f"{q1:,.2f} and {q3:,.2f}; {shape}.")
            rows.append(line)
        self.notices.append("understand - column profile:\n" + "\n".join(rows))
        return table  # pass-through so the pipeline continues

    def visit_Clean(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        s = df[col].astype("string")
        # currency / number
        stripped = s.str.replace(r"[,$£€\s]", "", regex=True)
        num = pd.to_numeric(stripped, errors="coerce")
        coltypes = dict(table.coltypes)
        if num.notna().mean() > 0.6:
            df[col] = num
            had_currency = s.str.contains(r"[$£€]", na=False).any()
            coltypes[col] = "Money" if had_currency else "Number"
            self.notices.append(f"cleaned {col} -> {coltypes[col]}.")
            return Table(df, coltypes)
        # dates
        dt = pd.to_datetime(s, errors="coerce")
        if dt.notna().mean() > 0.6:
            df[col] = dt
            coltypes[col] = "Date"
            self.notices.append(f"cleaned {col} -> Date.")
            return Table(df, coltypes)
        df[col] = s.str.strip()
        self.notices.append(f"cleaned {col} (trimmed spaces).")
        return Table(df, coltypes)

    def visit_Standardize(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        canon = {}
        result = []
        for v in df[col].astype("string"):
            if v is pd.NA:
                result.append(v)
                continue
            key = re.sub(r"\s+", " ", str(v).strip().lower())
            canon.setdefault(key, str(v).strip())
            result.append(canon[key])
        df[col] = result
        self.notices.append(f"standardized {col} to {len(canon)} canonical values.")
        return Table(df, table.coltypes)

    def visit_FillMissing(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        m = c.method
        n_missing = int(df[col].isna().sum())
        # "previous"/"next" (fill down/up) carry the last-seen value forward
        # or the next one back; within a preceding `group by`, each group is
        # carried separately rather than leaking into the next group's rows.
        keys = getattr(table, "_group_keys", None)
        if m == "average":
            df[col] = df[col].fillna(pd.to_numeric(df[col], errors="coerce").mean())
        elif m == "previous":
            df[col] = df.groupby(keys, observed=False)[col].ffill() if keys \
                else df[col].ffill()
        elif m == "next":
            df[col] = df.groupby(keys, observed=False)[col].bfill() if keys \
                else df[col].bfill()
        elif m == "interpolate":
            df[col] = pd.to_numeric(df[col], errors="coerce").interpolate()
        elif m in (None, "value"):
            df[col] = df[col].fillna(0)
        else:
            df[col] = df[col].fillna(m)
        grouped_note = (f" within each {', '.join(keys)} group"
                        if keys and m in ("previous", "next") else "")
        self.notices.append(f"filled {n_missing} missing value(s) in {col}"
                            f"{grouped_note}.")
        return Table(df, table.coltypes)

    def visit_DropDuplicates(self, c, table):
        self._need(table, c)
        for col in c.cols:
            self._col(table, col, c)
        df = table.df.drop_duplicates(subset=c.cols or None)
        return Table(df, table.coltypes)

    def visit_FlagOutliers(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        s = pd.to_numeric(df[col], errors="coerce")
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        flag = (s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)
        df[col + "_outlier"] = flag.fillna(False)
        coltypes = dict(table.coltypes)
        coltypes[col + "_outlier"] = "Boolean"
        self.notices.append(f"flagged {int(flag.sum())} outliers in {col} "
                            f"(1.5 x IQR rule).")
        return Table(df, coltypes)

    # ---- columns / rows ----------------------------------------------------
    def visit_Keep(self, c, table):
        self._need(table, c)
        for col in c.cols:
            self._col(table, col, c)
        df = table.df[c.cols]
        coltypes = {k: v for k, v in table.coltypes.items() if k in c.cols}
        return Table(df, coltypes)

    def visit_DropCols(self, c, table):
        self._need(table, c)
        df = table.df.drop(columns=[x for x in c.cols if x in table.df.columns])
        coltypes = {k: v for k, v in table.coltypes.items() if k in df.columns}
        return Table(df, coltypes)

    def visit_Rename(self, c, table):
        self._need(table, c)
        self._col(table, c.old, c)
        df = table.df.rename(columns={c.old: c.new})
        coltypes = dict(table.coltypes)
        if c.old in coltypes:
            coltypes[c.new] = coltypes.pop(c.old)
        return Table(df, coltypes)

    def visit_Add(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        coltypes = dict(table.coltypes)
        if c.astype is not None:
            self._col(table, c.name, c)
            builtin = ("Number", "Text", "Date", "Category", "Boolean", "Money",
                       "Percent", "Integer", "Duration")
            if c.astype not in self.env.types and c.astype not in self.env.records \
                    and c.astype not in builtin:
                raise AskError(f"I don't know a type named {c.astype!r}.",
                               f"line {c.line}",
                               "Define it first, e.g. type Rating = Poor | Fair | Good | Great")
            if c.astype in self.env.types:
                td = self.env.types[c.astype]
                df[c.name] = pd.Categorical(df[c.name].astype("string"),
                                            categories=td.variants,
                                            ordered=td.ordered)
                coltypes[c.name] = c.astype
            elif c.astype in builtin:
                df[c.name] = _coerce_to_builtin(df[c.name], c.astype, c.name, c.line)
                coltypes[c.name] = c.astype
            else:
                coltypes[c.name] = c.astype
            return Table(df, coltypes)
        # grouped (per-group) transform: keep every row, compute within the group.
        keys = getattr(table, "_group_keys", None)
        if c.per_group:
            if not keys:
                raise AskError(
                    what="'per group' needs a group by step first.",
                    where=f"line {c.line}",
                    fix="Add a line like: group by region")
            value = _grouped_transform(c.expr, df, keys, c.line)
        else:
            value = eval_expr(c.expr, df, c.line)
        df[c.name] = value
        coltypes[c.name] = _infer_value_type(value)
        out = Table(df, coltypes)
        if keys:
            out._group_keys = keys   # keep group context flowing
        return out

    def visit_Bin(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        s = pd.to_numeric(df[col], errors="coerce")
        labels = c.labels
        try:
            binned = pd.qcut(s, q=len(labels), labels=labels, duplicates="drop")
            if len(binned.cat.categories) != len(labels):
                binned = pd.cut(s, bins=len(labels), labels=labels)
        except Exception:
            binned = pd.cut(s, bins=len(labels), labels=labels)
        new_col = col + "_tier"
        # keep the tier order (low < medium < high) so sort + axes line up
        df[new_col] = pd.Categorical(binned, categories=labels, ordered=True)
        coltypes = dict(table.coltypes)
        coltypes[new_col] = "Category"
        self.notices.append(f"bin {col} -> new column {new_col} ({', '.join(labels)}).")
        return Table(df, coltypes)

    def visit_Split(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        parts = df[col].astype("string").str.split(re.escape(c.sep), expand=True)
        coltypes = dict(table.coltypes)
        for idx, name in enumerate(c.into):
            df[name] = parts[idx] if idx in parts.columns else pd.NA
            coltypes[name] = "Text"
        return Table(df, coltypes)

    def visit_Expand(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        before = len(df)
        pieces = df[col].astype("string").str.split(re.escape(c.sep))
        df[col] = pieces
        df = df.explode(col, ignore_index=True)
        df[col] = df[col].astype("string").str.strip()
        self.notices.append(f"expand: {before} row(s) became {len(df)} by "
                            f"splitting {col!r} on {c.sep!r}.")
        return Table(df, table.coltypes)

    def visit_Replace(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        col = self._col(table, c.col, c)
        df[col] = df[col].astype("string").str.replace(c.old, c.new, regex=False)
        return Table(df, table.coltypes)

    def visit_Where(self, c, table):
        self._need(table, c)
        mask = eval_condition(c.condition, table.df, c.line)
        if not isinstance(mask, pd.Series):
            # A condition that never touches a column (e.g. two literal
            # values compared to each other) evaluates to one plain bool
            # instead of one-per-row - almost always a sign the writer meant
            # to reference a column. Catch it here with a teaching error
            # rather than let a raw AttributeError through.
            raise AskError(
                what="This condition doesn't refer to any column, so there's "
                     "nothing to filter row by row.",
                where=f"line {c.line}",
                fix="If you meant a column with a dot or space in its name, "
                    "use backticks: where `sex.label` is \"Total\"",
                line=c.line)
        mask = mask.fillna(False).astype(bool)
        df = table.df[mask]
        return Table(df, table.coltypes)

    # ---- combine / stack ---------------------------------------------------
    def _other_table(self, name, ln):
        if name in self.env.tables:
            return self.env.tables[name]
        return load_table(name + ".csv")

    def visit_Combine(self, c, table):
        self._need(table, c)
        other = self._other_table(c.other, c.line)
        left, right = table.df.copy(), other.df.copy()
        key = c.key
        if key not in left.columns:
            raise _unknown_column_error(key, left.columns, c.line)
        if key not in right.columns:
            raise AskError(
                what=f"The other table has no column named {key!r} to match on.",
                where=f"line {c.line}",
                fix="Pick a key both tables share. The other table has: "
                    + ", ".join(map(str, right.columns)))
        if c.fuzzy:
            import difflib
            right_keys = right[key].astype(str).dropna().unique().tolist()
            mapping, matches_made = {}, []
            for lv in left[key].astype(str).dropna().unique():
                if lv in right_keys:
                    mapping[lv] = lv
                    continue
                m = difflib.get_close_matches(lv, right_keys, n=1, cutoff=0.8)
                mapping[lv] = m[0] if m else lv
                if m:
                    matches_made.append(f"{lv!r}->{m[0]!r}")
            left["__matchkey__"] = left[key].astype(str).map(mapping)
            merged = left.merge(right, left_on="__matchkey__", right_on=key,
                                how=c.how, suffixes=("", "_other")).drop(
                columns="__matchkey__")
            if matches_made:
                shown = "; ".join(matches_made[:8])
                more = "" if len(matches_made) <= 8 else \
                    f" (+{len(matches_made) - 8} more)"
                context.record_notice(f"fuzzy match linked {len(matches_made)} "
                                      f"near-miss key(s): {shown}{more}. Please review.")
            else:
                context.record_notice("fuzzy match: every key already matched exactly, "
                                      "so no guesses were needed.")
        else:
            merged = left.merge(right, on=key, how=c.how, suffixes=("", "_other"))
        # Report rows on each side that found no partner (data you might be losing).
        left_keys = set(left[key].astype(str).dropna())
        right_keys = set(right[key].astype(str).dropna())
        left_unmatched = len(left_keys - right_keys)
        right_unmatched = len(right_keys - left_keys)
        if left_unmatched or right_unmatched:
            kept = {"left": "keeping all from this table", "right": "keeping all "
                   "from the other table", "outer": "keeping all from both tables",
                   "inner": "keeping only matches"}.get(c.how, c.how)
            context.record_notice(
                f"combine on {key!r} ({kept}): {left_unmatched} key(s) in this "
                f"table had no match on the other side, and {right_unmatched} on "
                f"the other side had no match here.")
        coltypes = dict(table.coltypes)
        coltypes.update(other.coltypes)
        coltypes = {k: v for k, v in coltypes.items() if k in merged.columns}
        return Table(merged, coltypes)

    def visit_Stack(self, c, table):
        self._need(table, c)
        other = self._other_table(c.other, c.line)
        df = pd.concat([table.df, other.df], ignore_index=True)
        return Table(df, table.coltypes)

    # ---- group / show ------------------------------------------------------
    def visit_GroupBy(self, c, table):
        self._need(table, c)
        for col in c.cols:
            self._col(table, col, c)
        t = table.copy()
        t._group_keys = c.cols
        return t

    def visit_Show(self, c, table):
        self._need(table, c)
        keys = getattr(table, "_group_keys", None)
        df = table.df
        for agg in c.aggs:
            if agg.col is not None:
                self._col(table, agg.col, c)
        coltypes = {}
        if keys:
            grouped = df.groupby(keys, dropna=False, observed=False)
            out = {}
            for agg in c.aggs:
                out[agg.name] = self._apply_agg(agg, grouped, df, keys)
            result = pd.DataFrame(out).reset_index()
            for k in keys:
                if k in table.coltypes:
                    coltypes[k] = table.coltypes[k]
            for agg in c.aggs:
                coltypes[agg.name] = "Number"
            return Table(result, coltypes)
        # ungrouped -> single summary row
        row = {}
        for agg in c.aggs:
            row[agg.name] = [self._apply_agg_scalar(agg, df)]
            coltypes[agg.name] = "Number"
        return Table(pd.DataFrame(row), coltypes)

    def _apply_agg(self, agg, grouped, df, keys):
        if agg.func == "count":
            # bug C7: `show count <col>` now counts NON-MISSING values of that
            # column; a bare `show count` still counts rows.
            if agg.col is None:
                return grouped.size()
            return df[agg.col].groupby([df[k] for k in keys],
                                       dropna=False, observed=False).count()
        col = agg.col
        # `mode` works on the raw values; the rest need numbers.
        if agg.func == "mode":
            return df[col].groupby([df[k] for k in keys],
                                   dropna=False, observed=False).agg(stats.series_mode)
        g = _coerce_col(df, col).groupby([df[k] for k in keys], dropna=False,
                                         observed=False)
        mapping = {"total": g.sum, "average": g.mean, "min": g.min, "max": g.max,
                   "median": g.median, "spread": g.std, "skew": g.skew}
        if agg.func in mapping:
            return mapping[agg.func]()
        if agg.func == "kurtosis":
            return g.apply(lambda s: s.kurt())
        if agg.func == "quantile":
            return g.quantile(agg.q, interpolation=stats.quantile_interp(agg.method))
        if agg.func == "share":
            tot = g.sum()
            return tot / tot.sum() * 100
        return g.sum()

    def _apply_agg_scalar(self, agg, df):
        if agg.func == "count":
            return len(df) if agg.col is None else int(df[agg.col].notna().sum())
        if agg.func == "mode":
            return stats.series_mode(df[agg.col])
        s = _coerce_col(df, agg.col)
        if agg.func == "skew":
            return s.skew()
        if agg.func == "kurtosis":
            return s.kurt()
        if agg.func == "quantile":
            return stats.quantile(s.dropna().to_numpy(float), agg.q, agg.method)
        if agg.func == "spread":
            # streaming Welford variance for numerical stability
            var = stats.welford_var(s.dropna().to_numpy(float))
            return math.sqrt(var) if var == var else float("nan")
        return {"total": s.sum, "average": s.mean, "min": s.min, "max": s.max,
                "median": s.median, "share": s.sum}[agg.func]()

    # ---- window / group-wise (rank 4: after group by, before it collapses) --
    def visit_Window(self, c, table):
        self._need(table, c)
        col = self._col(table, c.col, c)
        df = table.df.copy()
        coltypes = dict(table.coltypes)
        keys = getattr(table, "_group_keys", None)
        s = pd.to_numeric(df[col], errors="coerce")
        gb = s.groupby([df[k] for k in keys], dropna=False,
                       observed=False) if keys else None
        if c.op == "running_total":
            result = gb.cumsum() if keys else s.cumsum()
        elif c.op == "moving_average":
            roll = (lambda x: x.rolling(c.n, min_periods=1).mean())
            result = gb.transform(roll) if keys else roll(s)
        elif c.op == "rank":
            result = gb.rank(method="min") if keys else s.rank(method="min")
        elif c.op in ("lag", "lead"):
            shift = c.n if c.op == "lag" else -c.n
            result = gb.shift(shift) if keys else s.shift(shift)
        else:
            raise AskError(f"I don't know the window verb {c.op!r}.",
                           f"line {c.line}", "Try: running total of sales")
        df[c.name] = result
        coltypes[c.name] = "Number"
        out = Table(df, coltypes)
        if keys:
            out._group_keys = keys
        self.notices.append(f"window: added {c.name} "
                            f"({c.op.replace('_', ' ')} of {col}).")
        return out

    def visit_GroupFilter(self, c, table):
        self._need(table, c)
        keys = getattr(table, "_group_keys", None)
        if not keys:
            raise AskError(
                what="'keep top N within group' needs a group by step first.",
                where=f"line {c.line}",
                fix="Add a line like: group by region")
        if c.col is None:
            raise AskError(
                what="Say which column ranks the rows inside each group.",
                where=f"line {c.line}",
                fix="keep top 3 within group by revenue")
        col = self._col(table, c.col, c)
        df = table.df
        s = pd.to_numeric(df[col], errors="coerce")
        asc = (c.mode == "bottom")
        ranks = s.groupby([df[k] for k in keys], dropna=False,
                          observed=False).rank(method="first", ascending=asc)
        out = Table(df[(ranks <= c.n).fillna(False)], table.coltypes)
        out._group_keys = keys
        return out

    # ---- estimate (bootstrap) ---------------------------------------------
    def visit_Estimate(self, c, table):
        self._need(table, c)
        col = self._col(table, c.col, c)
        vals = stats.numeric(table.df[col])
        if len(vals) < 2:
            raise AskError(
                what=f"Not enough numbers in {col!r} to estimate anything.",
                where=f"line {c.line}",
                fix="Pick a number column with at least two filled-in rows.")
        point, lo, hi = stats.bootstrap_ci(vals, stat=c.func, rng=self.env.rng)
        label = {"average": "average", "total": "total", "median": "median",
                 "min": "smallest", "max": "largest", "spread": "spread"}[c.func]
        finding = (f"The {label} of {col} is about {point:,.2f}. A bootstrap "
                   f"resample (2,000 reps, percentile method) puts the 95% "
                   f"range at {lo:,.2f} to {hi:,.2f}. Based on {len(vals):,} "
                   f"non-missing value(s).")
        self.notices.append("estimate -> " + finding)
        tbl = pd.DataFrame([{"statistic": c.func, "column": col,
                             "estimate": round(point, 4), "low": round(lo, 4),
                             "high": round(hi, 4), "n": len(vals)}])
        self.last_stat = Table(tbl, _coltypes_from_df(tbl))
        return table

    # ---- for each group do recipe (the one narrow iteration primitive) -----
    def visit_ForEachGroup(self, c, table):
        self._need(table, c)
        self._col(table, c.group, c)
        recipe = self.env.recipes.get(c.recipe)
        if recipe is None:
            raise AskError(f"I don't know a recipe named {c.recipe!r}.",
                           f"line {c.line}", "Define it first with: define recipe ...")
        results = []
        for val, sub in table.df.groupby(c.group, dropna=False, observed=False):
            subtable = Table(sub, dict(table.coltypes))
            res = self._run_recipe(recipe, subtable, [], c.line)
            if isinstance(res, Table):
                rdf = res.df.copy()
                if c.group not in rdf.columns:
                    rdf.insert(0, c.group, val)
                results.append(rdf)
        if not results:
            raise AskError(
                what=f"'for each {c.group}' produced nothing to combine.",
                where=f"line {c.line}",
                fix="Check the recipe returns a table for each group.")
        combined = pd.concat(results, ignore_index=True)
        self.notices.append(f"for each {c.group}: ran recipe {c.recipe!r} on "
                            f"{len(results)} group(s) and stacked the results.")
        return Table(combined, _coltypes_from_df(combined))

    # ---- reshape / resample -----------------------------------------------
    def visit_Reshape(self, c, table):
        self._need(table, c)
        df = table.df
        if c.mode == "long":
            ids = c.keeping or [col for col in df.columns
                                if not pd.api.types.is_numeric_dtype(df[col])]
            for col in ids:
                self._col(table, col, c)
            value_cols = [col for col in df.columns if col not in ids]
            if not value_cols:
                raise AskError(
                    what="reshape long needs some number columns to stack up.",
                    where=f"line {c.line}",
                    fix="Say which label columns to keep: "
                        "reshape long keeping region, year")
            melted = df.melt(id_vars=ids, value_vars=value_cols,
                             var_name="name", value_name="value")
            coltypes = {col: table.coltypes.get(col, "Text") for col in ids}
            coltypes["name"] = "Category"
            coltypes["value"] = "Number"
            return Table(melted, coltypes)
        if c.by and c.using:
            self._col(table, c.by, c)
            self._col(table, c.using, c)
            aggfunc = _RESHAPE_AGGFUNC.get(c.aggregate or "total", "sum")
            try:
                idx = [col for col in df.columns if col not in (c.by, c.using)]
                wide = df.pivot_table(index=idx, columns=c.by, values=c.using,
                                      aggfunc=aggfunc).reset_index()
                wide.columns = [str(x) for x in wide.columns]
                return Table(wide, {})
            except Exception:
                raise AskError("I couldn't reshape this into a wide table.",
                               f"line {c.line}",
                               "Check the 'by' and 'using' columns exist and hold "
                               "categories and numbers.")
        raise AskError("reshape wide needs a 'by' column and a 'using' column.",
                       f"line {c.line}",
                       "reshape wide by month using sales")

    def visit_Resample(self, c, table):
        self._need(table, c)
        df = table.df.copy()
        key = _normalize_period(c.period)
        if key is None:
            raise AskError(
                what=f"I don't know the period {c.period!r}.",
                where=f"line {c.line}",
                fix="Use one of: day, week, month, quarter, year.")
        date_cols = [col for col, tp in table.coltypes.items() if tp == "Date"]
        if not date_cols:
            date_cols = [col for col in df.columns
                         if pd.api.types.is_datetime64_any_dtype(df[col])]
        if not date_cols:
            raise AskError(
                what="resample needs a date column, but I don't see one.",
                where=f"line {c.line}",
                fix="Add a line first, e.g.: clean order_date")
        if len(date_cols) > 1:
            raise AskError(
                what=f"There are several date columns "
                     f"({', '.join(date_cols)}); I can't tell which to roll up.",
                where=f"line {c.line}",
                fix="keep just the date column you want before resampling.")
        date_col = date_cols[0]
        resample_freq = {"day": "D", "week": "W", "month": "ME",
                         "quarter": "QE", "year": "YE"}[key]
        period_freq = {"day": "D", "week": "W", "month": "M",
                       "quarter": "Q", "year": "Y"}[key]
        numeric_cols = [col for col in df.columns if col != date_col
                        and table.coltypes.get(col) in
                        ("Number", "Money", "Integer", "Percent", "Duration")]
        dt = pd.to_datetime(df[date_col], errors="coerce")
        g = df.assign(**{date_col: dt}).dropna(subset=[date_col]).set_index(date_col)
        if numeric_cols:
            rolled = g[numeric_cols].apply(
                lambda s: pd.to_numeric(s, errors="coerce")).resample(
                resample_freq).sum()
        else:
            rolled = pd.DataFrame(index=g.resample(resample_freq).size().index)
        rolled["count"] = g.resample(resample_freq).size()
        rolled.index = rolled.index.to_period(period_freq).astype(str)
        out = rolled.reset_index().rename(columns={"index": date_col})
        coltypes = {date_col: "Category", "count": "Number"}
        for col in numeric_cols:
            coltypes[col] = table.coltypes.get(col, "Number")
        return Table(out, coltypes)

    # ---- sort / limit ------------------------------------------------------
    def visit_SortBy(self, c, table):
        self._need(table, c)
        col = self._col(table, c.col, c)
        df = table.df.sort_values(col, ascending=not c.descending)
        return Table(df, table.coltypes)

    def visit_Limit(self, c, table):
        self._need(table, c)
        df = table.df
        if c.mode in ("first", "last"):
            df = df.head(c.n) if c.mode == "first" else df.tail(c.n)
        else:
            col = c.col
            if col is None:
                df = df.head(c.n) if c.mode == "top" else df.tail(c.n)
            else:
                self._col(table, col, c)
                s = pd.to_numeric(df[col], errors="coerce")
                order = s.sort_values(ascending=(c.mode == "bottom")).index
                df = df.loc[order].head(c.n)
        return Table(df, table.coltypes)

    # ---- chart -------------------------------------------------------------
    def visit_Chart(self, c, table):
        self._need(table, c)
        if c.kind in ADVANCED_CHART_TYPES:
            raise AskError(
                what=f"The {c.kind!r} chart is not yet implemented (stretch feature).",
                where=f"line {c.line}",
                fix="Try: bar, line, area, scatter, bubble, histogram, box, pie, "
                    "heatmap, distribution, change, flow, tree, network, map.")
        if c.kind not in CORE_CHART_TYPES:
            raise AskError(f"I don't know a chart type called {c.kind!r}.",
                           f"line {c.line}",
                           "Core types: bar, line, area, scatter, bubble, histogram, "
                           "box, pie, heatmap, distribution, change, flow, tree, "
                           "network, map, story.")
        self._col(table, c.x, c)
        self._col(table, c.y, c)
        for m in c.mods:
            if m.kind in ("color", "size", "split", "animate") and m.value is not None:
                self._col(table, m.value, c)
        if c.kind == "bubble" and not any(m.kind == "size" for m in c.mods):
            raise AskError(
                what="A bubble chart needs a 'size by' column to size the bubbles.",
                where=f"line {c.line}",
                fix="Add an indented line under chart: size by <column>")
        # y must hold some numbers for every kind except a pure category count
        yvals = pd.to_numeric(table.df[c.y], errors="coerce")
        if len(table.df) and yvals.notna().sum() == 0:
            raise AskError(
                what=f"The column {c.y!r} isn't numbers, so I can't chart it "
                     f"as {c.kind}.",
                where=f"line {c.line}",
                fix=f"Chart a number column, or add: clean {c.y}", line=c.line)
        order = None
        xtype = table.coltypes.get(c.x)
        if xtype in self.env.types and self.env.types[xtype].ordered:
            order = self.env.types[xtype].variants
        elif isinstance(table.df[c.x].dtype, pd.CategoricalDtype) \
                and table.df[c.x].dtype.ordered:
            order = list(table.df[c.x].dtype.categories)
        spec = Chart(line=c.line, y=c.y, x=c.x, kind=c.kind, mods=c.mods)
        spec._table = table
        spec._order = order
        # every chart drawn earlier in the program, for `as story` to replay
        spec._history = list(self.chart_history)
        self.chart_history.append(spec)
        for m in c.mods:
            if m.kind == "explain":
                self.notices.append(f"explain: a {c.kind} chart of {c.y} across {c.x}.")
        return spec

    # ---- explain -----------------------------------------------------------
    def visit_Explain(self, c, table):
        if not self.trace:
            raise AskError(
                what="There's no step above for explain to describe.",
                where=f"line {c.line}",
                fix="Put explain on the line right after a step you want explained.")
        last = self.trace[-1]
        detail = ""
        if last.before is not None and last.after is not None:
            detail = (f"  [rows {last.before:,} -> {last.after:,}, "
                      f"{last.cols} column(s)]")
        self.notices.append("explain -> " + last.text + detail)
        return table

    # ---- plain-English statistics -----------------------------------------
    def visit_Compare(self, c, table):
        self._need(table, c)
        df = table.df
        self._col(table, c.value, c)
        self._col(table, c.group, c)
        if c.paired:
            return self._compare_paired(c, table)
        value, group = c.value, c.group
        groups_series = df[group].astype("string")
        levels = [lv for lv in pd.unique(groups_series.dropna())]
        if len(levels) < 2:
            raise AskError(
                what=f"compare needs at least two groups in {group!r}, "
                     f"but I found {len(levels)}.",
                where=f"line {c.line}",
                fix="Pick a column that splits the rows into groups.")
        numeric_value = pd.to_numeric(df[value], errors="coerce")
        # a yes/no value is technically numeric-coercible (True/False -> 1/0),
        # but "on average bought is 0.60" reads worse than "60% bought" - and
        # it's the case a proportion test is actually for - so check for it
        # before falling into the generic numeric mean-comparison path.
        is_boolish = (table.coltypes.get(value) == "Boolean"
                     or pd.api.types.is_bool_dtype(df[value])
                     or len(pd.unique(df[value].dropna())) == 2
                     and numeric_value.notna().mean() <= 0.6)
        if is_boolish and len(levels) == 2:
            arr, pos = stats.binary_target(df[value])
            y = pd.Series(arr, index=df.index)
            g0, g1 = levels
            y0 = y[groups_series == g0].dropna()
            y1 = y[groups_series == g1].dropna()
            n0, n1 = len(y0), len(y1)
            if n0 < 1 or n1 < 1:
                raise AskError(
                    what=f"Not enough rows in each group of {group!r} to "
                         f"compare proportions of {value!r}.",
                    where=f"line {c.line}",
                    fix="Each group needs at least one row.")
            x0, x1 = float(y0.sum()), float(y1.sum())
            p0, p1 = x0 / n0, x1 / n1
            diff, lo, hi, z, p = stats.two_proportion_z(x0, n0, x1, n1)
            verb = "higher than" if p0 > p1 else "lower than"
            finding = (f"The share of {value} = {pos!r} is {p0 * 100:.0f}% "
                      f"for {g0} vs {p1 * 100:.0f}% for {g1}. {g0} is "
                      f"{verb} {g1} by {diff * 100:+.0f} percentage points "
                      f"(95% range {lo * 100:+.0f} to {hi * 100:+.0f}); "
                      f"{stats.p_phrase(p)} (two-proportion z-test).")
            stat_tbl = pd.DataFrame([
                {"group": g0, "proportion": round(p0, 4), "rows": n0},
                {"group": g1, "proportion": round(p1, 4), "rows": n1}])
        elif not is_boolish and numeric_value.notna().mean() > 0.6:
            data = [(lv, numeric_value[groups_series == lv].dropna().to_numpy(float))
                    for lv in levels]
            data = [(lv, arr) for lv, arr in data if len(arr) > 1]
            if len(data) < 2:
                raise AskError(
                    what=f"Not enough numbers in each group to compare {value!r}.",
                    where=f"line {c.line}",
                    fix="Each group needs at least two number values.")
            means = {lv: arr.mean() for lv, arr in data}
            hi, lo = max(means, key=means.get), min(means, key=means.get)
            if len(data) == 2:
                (l0, a0), (l1, a1) = data
                _, _, p = stats.two_sample_t(a0, a1)
                diff, clo, chi = stats.mean_diff_ci(a0, a1)
                g = stats.hedges_g(a0, a1)
                _, pu = stats.mann_whitney_u(a0, a1)
                verb = "higher than" if means[l0] > means[l1] else "lower than"
                finding = (f"On average {value} is {means[l0]:,.2f} for {l0} vs "
                           f"{means[l1]:,.2f} for {l1}. {l0} is {verb} {l1} by "
                           f"{diff:+,.2f} (95% range {clo:,.2f} to {chi:,.2f}); "
                           f"{stats.p_phrase(p)} (Welch's t-test). The gap is "
                           f"{stats.d_magnitude(g)} one (Hedges' g = {g:.2f}). "
                           f"A rank test agrees it is {stats.p_phrase(pu)}.")
            else:
                _, _, _, p, eta2 = stats.one_way_anova([arr for _, arr in data])
                eta_txt = (f" Group membership explains {eta2 * 100:.0f}% of the "
                           f"variation (eta-squared).") if eta2 == eta2 else ""
                finding = (f"Across {len(data)} groups, {value} is highest for {hi} "
                           f"({means[hi]:,.2f}) and lowest for {lo} "
                           f"({means[lo]:,.2f}); {stats.p_phrase(p)} (one-way "
                           f"ANOVA).{eta_txt}")
                # More than two groups: offer Bonferroni-corrected pairwise tests.
                pairs = []
                m = len(data) * (len(data) - 1) // 2
                for i in range(len(data)):
                    for j in range(i + 1, len(data)):
                        _, _, pij = stats.two_sample_t(data[i][1], data[j][1])
                        pairs.append((data[i][0], data[j][0],
                                      min(pij * m, 1.0)))
                top = sorted(pairs, key=lambda x: x[2])[:3]
                note = "; ".join(f"{a} vs {b}: {stats.p_phrase(pc)}" for a, b, pc in top)
                self.notices.append(
                    f"compare -> with {len(data)} groups, a single test can mislead. "
                    f"Bonferroni-corrected pairwise checks (closest first): {note}.")
            rows = [{"group": lv, "average": round(means[lv], 4), "rows": len(arr)}
                    for lv, arr in data]
            stat_tbl = pd.DataFrame(rows).sort_values("average", ascending=False)
        else:
            ct = pd.crosstab(df[value].astype("string"), groups_series)
            chi2, dof, p, min_exp, v = stats.chi_square_independence(ct.to_numpy())
            v_txt = f" Association strength: Cramer's V = {v:.2f}." if v == v else ""
            finding = (f"Looking at whether {value} depends on {group}: "
                      f"{stats.p_phrase(p)} (chi-square test on the counts)."
                      f"{v_txt}")
            if min_exp == min_exp and min_exp < 5:
                self.notices.append(
                    f"compare -> caution: some table cells expect fewer than "
                    f"5 counts (smallest {min_exp:.1f}), so the chi-square "
                    f"p-value is approximate. Collect more data or merge "
                    f"rare categories.")
            stat_tbl = ct.reset_index()
        self.notices.append("compare -> " + finding)
        self.last_stat = Table(stat_tbl, _coltypes_from_df(stat_tbl))
        return table   # non-destructive: keep the data so you can keep going

    def _compare_paired(self, c, table):
        """`compare <a> and <b> paired` - a paired t-test on two matched
        number columns (e.g. before/after on the same rows), not an
        independent-groups test."""
        df = table.df
        a_col, b_col = c.value, c.group
        a = pd.to_numeric(df[a_col], errors="coerce")
        b = pd.to_numeric(df[b_col], errors="coerce")
        mask = a.notna() & b.notna()
        n = int(mask.sum())
        if n < 2:
            raise AskError(
                what=f"Not enough paired numbers to compare {a_col!r} and "
                     f"{b_col!r}.",
                where=f"line {c.line}",
                fix="Both columns need to be numbers, with at least 2 shared rows.")
        av, bv = a[mask].to_numpy(float), b[mask].to_numpy(float)
        d = av - bv
        mean_diff = float(d.mean())
        t_stat, dof, p = stats.paired_t(av, bv)
        sd = d.std(ddof=1)
        se = sd / math.sqrt(n) if n else float("nan")
        tcrit = stats.t_crit(dof) if dof else float("nan")
        ci_txt = ""
        if se == se and tcrit == tcrit:
            lo, hi = mean_diff - tcrit * se, mean_diff + tcrit * se
            ci_txt = f" (95% range {lo:+,.2f} to {hi:+,.2f})"
        dz = mean_diff / sd if sd else float("nan")
        eff_txt = (f" The gap is {stats.d_magnitude(dz)} one (Cohen's d = {dz:.2f})."
                  if dz == dz else "")
        _, pw = stats.wilcoxon_signed_rank(av, bv)
        verb = "higher than" if mean_diff > 0 else "lower than"
        finding = (f"Paired comparison of {a_col} and {b_col} ({n} pairs): "
                  f"{a_col} is on average {abs(mean_diff):,.2f} {verb} "
                  f"{b_col}{ci_txt}; {stats.p_phrase(p)} (paired t-test). "
                  f"A rank-based check agrees it is {stats.p_phrase(pw)}."
                  f"{eff_txt}")
        self.notices.append("compare -> " + finding)
        tbl = pd.DataFrame([{"mean_difference": round(mean_diff, 4), "pairs": n,
                            "p_value": round(p, 4)}])
        self.last_stat = Table(tbl, _coltypes_from_df(tbl))
        return table

    def visit_Relate(self, c, table):
        self._need(table, c)
        if c.matrix_cols:
            return self._relate_matrix(c, table)
        self._col(table, c.a, c)
        self._col(table, c.b, c)
        if c.controlling:
            return self._relate_partial(c, table)
        a = pd.to_numeric(table.df[c.a], errors="coerce")
        b = pd.to_numeric(table.df[c.b], errors="coerce")
        mask = a.notna() & b.notna()
        if int(mask.sum()) < 3:
            raise AskError(
                what=f"Not enough paired numbers to relate {c.a!r} and {c.b!r}.",
                where=f"line {c.line}",
                fix="Both columns need to be numbers, with at least 3 shared rows.")
        av, bv = a[mask].to_numpy(float), b[mask].to_numpy(float)
        n_all = len(table.df)
        if int(mask.sum()) < n_all:
            self.notices.append(f"relate -> used {int(mask.sum()):,} of {n_all:,} "
                                f"rows (the rest were missing a value).")
        r, n, p = stats.pearson_r(av, bv)
        clo, chi = stats.fisher_r_ci(r, n)
        rho, _, prho = stats.spearman_r(av, bv)
        direction = "they tend to rise together" if r > 0 else \
            "when one rises the other tends to fall"
        ci_txt = (f" (95% range {clo:.2f} to {chi:.2f})"
                  if clo == clo else "")
        finding = (f"{c.a} and {c.b} have {stats.corr_strength(r)} relationship "
                   f"(r = {r:.2f}){ci_txt} - {direction}. This is {stats.p_phrase(p)}. "
                   f"By rank (Spearman) the link is {rho:.2f}, {stats.p_phrase(prho)}.")
        self.notices.append("relate -> " + finding)
        tbl = pd.DataFrame([{"relationship": stats.corr_strength(r).strip(),
                             "r": round(r, 3), "r_low": round(clo, 3),
                             "r_high": round(chi, 3), "spearman": round(rho, 3),
                             "rows": n, "p_value": round(p, 4)}])
        self.last_stat = Table(tbl, _coltypes_from_df(tbl))
        return table

    def _relate_partial(self, c, table):
        """`relate a and b controlling for z` - the correlation between a and
        b with z's shared influence removed, so a spurious link driven by a
        common cause doesn't get mistaken for a direct relationship."""
        self._col(table, c.controlling, c)
        df = table.df
        a = pd.to_numeric(df[c.a], errors="coerce")
        b = pd.to_numeric(df[c.b], errors="coerce")
        z = pd.to_numeric(df[c.controlling], errors="coerce")
        mask = a.notna() & b.notna() & z.notna()
        n = int(mask.sum())
        if n < 4:
            raise AskError(
                what=f"Not enough shared numbers to relate {c.a!r} and "
                     f"{c.b!r} controlling for {c.controlling!r}.",
                where=f"line {c.line}",
                fix="All three columns need numbers, with at least 4 shared rows.")
        av, bv, zv = (a[mask].to_numpy(float), b[mask].to_numpy(float),
                     z[mask].to_numpy(float))
        r_ab, r_partial, _, p = stats.partial_correlation(av, bv, zv)
        direction = ("they still tend to rise together" if r_partial > 0 else
                    "when one rises the other still tends to fall")
        finding = (f"{c.a} and {c.b} have {stats.corr_strength(r_partial)} "
                  f"relationship (partial r = {r_partial:.2f}) after "
                  f"controlling for {c.controlling} - {direction}. Before "
                  f"controlling for it, the raw correlation was r = "
                  f"{r_ab:.2f}. This is {stats.p_phrase(p)}.")
        self.notices.append("relate -> " + finding)
        tbl = pd.DataFrame([{"partial_r": round(r_partial, 3),
                            "raw_r": round(r_ab, 3),
                            "controlling_for": c.controlling, "rows": n,
                            "p_value": round(p, 4)}])
        self.last_stat = Table(tbl, _coltypes_from_df(tbl))
        return table

    def _relate_matrix(self, c, table):
        """`relate all of a, b, c, ...` - every pairwise correlation at
        once, on the rows where all of them have a value (so every cell in
        the matrix is comparable across the same rows)."""
        cols = c.matrix_cols
        for col in cols:
            self._col(table, col, c)
        df = table.df
        sub = pd.DataFrame({col: pd.to_numeric(df[col], errors="coerce")
                            for col in cols}).dropna()
        n = len(sub)
        if n < 3:
            raise AskError(
                what=f"Not enough shared numbers across {', '.join(cols)} to "
                     f"build a correlation matrix.",
                where=f"line {c.line}",
                fix="Every column needs numbers, with at least 3 shared rows.")
        matrix = pd.DataFrame(1.0, index=cols, columns=cols)
        pairs = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                r, _, p = stats.pearson_r(sub[cols[i]].to_numpy(float),
                                          sub[cols[j]].to_numpy(float))
                matrix.loc[cols[i], cols[j]] = round(r, 3)
                matrix.loc[cols[j], cols[i]] = round(r, 3)
                pairs.append((cols[i], cols[j], r, p))
        top = sorted(pairs, key=lambda x: -abs(x[2]))[:3]
        summary = "; ".join(f"{a} & {b}: {stats.corr_strength(r).strip()} "
                            f"(r = {r:.2f}, {stats.p_phrase(p)})"
                            for a, b, r, p in top)
        finding = (f"Correlation matrix across {len(cols)} columns "
                  f"({n:,} shared rows). Strongest relationships: "
                  f"{summary}.")
        self.notices.append("relate -> " + finding)
        tbl = matrix.reset_index().rename(columns={"index": "column"})
        self.last_stat = Table(tbl, _coltypes_from_df(tbl))
        return table

    def visit_Predict(self, c, table):
        self._need(table, c)
        if not c.inputs:
            raise AskError(what="predict needs at least one input column.",
                           where=f"line {c.line}", fix="predict sales from spend")
        self._col(table, c.target, c)
        for col in c.inputs:
            self._col(table, col, c)
        df = table.df
        # Decide OLS vs logistic from the target's shape.
        y_raw = df[c.target]
        levels = pd.unique(y_raw.dropna())
        is_binary = table.coltypes.get(c.target) == "Boolean" or len(levels) == 2
        if c.engine is not None:
            return self._predict_via_r(c, table, is_binary)
        names, columns = stats.build_design(df, c.inputs, c.interactions,
                                            table.coltypes)
        if c.polynomial:
            if c.polynomial < 2:
                raise AskError(
                    what="predict ... with polynomial needs a number of 2 or higher.",
                    where=f"line {c.line}",
                    fix="predict y from x with polynomial 2")
            added_any = False
            for col in c.inputs:
                if not (table.coltypes.get(col) in
                       ("Number", "Money", "Integer", "Percent", "Duration")
                       or pd.api.types.is_numeric_dtype(df[col])):
                    continue   # squaring a category dummy isn't meaningful
                base = pd.to_numeric(df[col], errors="coerce").to_numpy(float)
                for power in range(2, c.polynomial + 1):
                    names.append(f"{col}^{power}")
                    columns.append(base ** power)
                added_any = True
            if not added_any:
                raise AskError(
                    what="predict ... with polynomial needs at least one "
                         "number input, not just categories.",
                    where=f"line {c.line}",
                    fix="Add a number column to 'from', e.g. "
                        "predict y from x with polynomial 2")
        ncols = len(columns)
        full = np.column_stack([np.ones(len(df))] + columns) if columns \
            else np.ones((len(df), 1))
        if is_binary:
            y, pos = stats.binary_target(y_raw)
        else:
            y = pd.to_numeric(y_raw, errors="coerce").to_numpy(float)
        row_ok = ~np.isnan(y) & ~np.isnan(full).any(axis=1)
        X = full[row_ok]
        yv = y[row_ok]
        if len(yv) <= ncols + 1:
            raise AskError(
                what=f"Not enough complete rows to predict {c.target!r}.",
                where=f"line {c.line}",
                fix="You need more rows than inputs, each filled in.")
        if len(yv) < len(df):
            self.notices.append(
                f"predict -> fitted on {len(yv):,} of {len(df):,} rows "
                f"(the rest had missing values).")
        # bug C1: rank-deficient designs used to be reported as answers.
        stats.check_collinearity(X, names)
        if is_binary:
            res = stats.logistic_regression(yv, X, names)
            parts, rows = [], []
            for name, coef, p in zip(res["names"], res["coef"], res["p"]):
                if name == "(baseline)":
                    rows.append({"factor": "baseline (starting point)",
                                 "effect_log_odds": round(coef, 4),
                                 "certainty": stats.p_phrase(p)})
                    continue
                odds = math.exp(coef) if abs(coef) < 30 else float("inf")
                parts.append(f"{name} multiplies the odds of {pos} by {odds:.2f}")
                rows.append({"factor": name, "effect_log_odds": round(coef, 4),
                             "odds_multiplier": round(odds, 3),
                             "certainty": stats.p_phrase(p)})
            acc = res["accuracy"] * 100
            finding = (f"Predicting whether {c.target} is {pos!r}: "
                       + "; ".join(parts) +
                       f". The model labels {acc:.0f}% of rows correctly.")
            self.notices.append("predict -> " + finding)
            tbl = pd.DataFrame(rows)
            self.last_stat = Table(tbl, _coltypes_from_df(tbl))
            return table
        res = stats.linear_regression(yv, X, names)
        tcrit = stats.t_crit(max(res["n"] - ncols - 1, 1))
        # standardized (beta) coefficients: coef * sd(x) / sd(y), so factors
        # measured on very different scales (e.g. dollars vs. a 1-5 rating)
        # can be compared for relative importance. Only meaningful for a
        # genuine number column - a category dummy's "spread" depends on its
        # 0/1 encoding, not a real unit, so it's left blank for those.
        y_sd = yv.std(ddof=1) if len(yv) > 1 else float("nan")
        x_sds = X[:, 1:].std(ddof=1, axis=0) if X.shape[1] > 1 else np.array([])
        parts, rows = [], []
        for i, (name, coef, se, p) in enumerate(
                zip(res["names"], res["coef"], res["se"], res["p"])):
            lo = coef - tcrit * se if se == se else float("nan")
            hi = coef + tcrit * se if se == se else float("nan")
            if name == "(baseline)":
                rows.append({"factor": "baseline (starting point)",
                             "effect": round(coef, 4), "low": round(lo, 4),
                             "high": round(hi, 4), "certainty": stats.p_phrase(p)})
                continue
            parts.append(f"each 1 more {name} is linked to {coef:+,.2f} in {c.target}")
            row = {"factor": name, "effect": round(coef, 4),
                  "low": round(lo, 4), "high": round(hi, 4),
                  "certainty": stats.p_phrase(p)}
            is_plain_number = "=" not in name and " x " not in name and "^" not in name
            if is_plain_number and y_sd == y_sd and y_sd > 0:
                row["standardized"] = round(coef * x_sds[i - 1] / y_sd, 4)
            rows.append(row)
        r2 = res["r2"]
        n, k = res["n"], ncols
        adj = 1 - (1 - r2) * (n - 1) / max(n - k - 1, 1) if r2 == r2 else float("nan")
        r2txt = f"{r2 * 100:.0f}%" if r2 == r2 else "an unknown share of"
        adjtxt = f" (adjusted {adj * 100:.0f}%)" if adj == adj else ""
        finding = (f"Predicting {c.target}: " + "; ".join(parts) +
                   f". The model explains {r2txt}{adjtxt} of the variation "
                   f"(R-squared). Effects show a 95% range.")
        self.notices.append("predict -> " + finding)
        tbl = pd.DataFrame(rows)
        self.last_stat = Table(tbl, _coltypes_from_df(tbl))
        return table

    def _predict_via_r(self, c, table, is_binary):
        """The `using r package "X"` escape hatch: same target/inputs, but
        fit in real R (via the rbridge module) instead of Ask's own numpy
        regression, so results present in Ask's usual plain-English style."""
        from . import rbridge
        df = table.df
        res = rbridge.run_r_predict(df, c.target, c.inputs, c.interactions,
                                    c.engine, is_binary, c.line)
        rows_used = res["rows"] or 0
        if rows_used and rows_used < len(df):
            self.notices.append(
                f"predict (via R, package {c.engine!r}) -> fitted on "
                f"{rows_used:,} of {len(df):,} rows (the rest had missing "
                f"values).")
        parts, out_rows = [], []
        for name, coef, se, p in res["coefs"]:
            certainty = stats.p_phrase(p) if p is not None else "unknown significance"
            if name == "(Intercept)":
                out_rows.append({"factor": "baseline (starting point)",
                                 "effect": round(coef, 4), "certainty": certainty})
                continue
            if is_binary:
                odds = math.exp(coef) if abs(coef) < 30 else float("inf")
                parts.append(f"{name} multiplies the odds by {odds:.2f}")
                out_rows.append({"factor": name, "effect_log_odds": round(coef, 4),
                                 "odds_multiplier": round(odds, 3),
                                 "certainty": certainty})
            else:
                parts.append(f"each 1 more {name} is linked to {coef:+,.2f} "
                             f"in {c.target}")
                out_rows.append({"factor": name, "effect": round(coef, 4),
                                 "certainty": certainty})
        tail = ""
        if res["r_squared"] is not None:
            tail = (f". The model explains {res['r_squared'] * 100:.0f}% of "
                    f"the variation.")
        finding = (f"Predicting {c.target} (via R, package {c.engine!r}): "
                  + "; ".join(parts) + tail)
        self.notices.append("predict -> " + finding)
        tbl = pd.DataFrame(out_rows)
        self.last_stat = Table(tbl, _coltypes_from_df(tbl))
        return table

    # ---- sharing / interop -------------------------------------------------
    def visit_ImportRecipes(self, c, table):
        path = c.path
        resolved = resolve_read_path(path)
        if not os.path.exists(resolved):
            raise AskError(
                what=f"I can't find the recipe file {path!r}.",
                where=f"line {c.line}",
                fix=f"Put {path!r} next to your program, or check the name.")
        try:
            with open(resolved, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise AskError(what=f"I couldn't read {path!r}.", where=f"line {c.line}",
                           fix="Make sure it's a plain .ask text file.")
        sub_clauses = Parser(tokenize(text)).parse_program()
        count = 0
        for cl in sub_clauses:
            if isinstance(cl, (TypeDef, RecordDef, RecipeDef, FunctionDef)):
                getattr(self, "visit_" + type(cl).__name__)(cl, None)
                count += 1
        self.notices.append(f"imported {count} definition(s) from {path!r}.")
        return table

    def visit_SaveReport(self, c, table):
        from .report import build_html_report
        out = resolve_write_path(c.path)
        html = build_html_report(self.source, self.trace, table, self.last_chart)
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
        except OSError:
            raise AskError(what=f"I couldn't write the report {c.path!r}.",
                           where=f"line {c.line}",
                           fix="Pick a writable location or a different name.")
        self.notices.append(f"saved a self-contained report to {out}.")
        return table

    # ---- helpers -----------------------------------------------------------
    def _need(self, table, c):
        if table is None:
            raise AskError(
                what=f"There's no table yet for {verb_word(c)} to work on.",
                where=f"line {getattr(c, 'line', '?')}",
                fix='Start the program with a load line, e.g. load "sales.csv"')

    def _col(self, table, name, c):
        if name not in table.df.columns:
            raise _unknown_column_error(name, table.df.columns,
                                        getattr(c, "line", None))
        return name


def _normalize_period(period):
    p = str(period).lower().strip()
    aliases = {"daily": "day", "weekly": "week", "monthly": "month",
               "quarterly": "quarter", "yearly": "year", "annual": "year",
               "annually": "year"}
    p = aliases.get(p, p)
    if p.endswith("s"):
        p = p[:-1]
    return p if p in ("day", "week", "month", "quarter", "year") else None


def _coerce_col(df, col):
    """to_numeric a column, telling the user how many values it had to ignore."""
    orig = df[col]
    try:
        num = pd.to_numeric(orig, errors="coerce")
    except (TypeError, ValueError):
        num = pd.to_numeric(orig.astype("string"), errors="coerce")
    dropped = int((orig.notna() & num.isna()).sum())
    context.coerce_drop_report(dropped, col, "summarizing")
    return num


def _coltypes_from_df(df):
    return {c: ("Number" if pd.api.types.is_numeric_dtype(df[c]) else "Text")
            for c in df.columns}


# `reshape wide ... aggregate <agg>` -> a pivot_table aggfunc name. Same
# words as `show`'s summaries, restricted to ones pandas can do in one cell.
_RESHAPE_AGGFUNC = {"total": "sum", "average": "mean", "count": "count",
                    "min": "min", "max": "max", "median": "median",
                    "spread": "std"}


def _parse_duration_to_minutes(v):
    """Read a duration like '2h30m', '1:45', '90m', or a plain number of minutes."""
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
        return float("nan")
    s = str(v).strip().lower()
    if not s:
        return float("nan")
    m = re.match(r"^(\d+):(\d{1,2})$", s)          # H:MM
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms]?)", s):
        if num == "":
            continue
        found = True
        val = float(num)
        total += val * {"h": 60.0, "m": 1.0, "s": 1.0 / 60.0, "": 1.0}[unit]
    return total if found else float("nan")


def _coerce_to_builtin(series, typename, cname, ln):
    """Coerce + validate a column into a named built-in type."""
    if typename in ("Number", "Money"):
        conv = pd.to_numeric(series, errors="coerce")
        context.coerce_drop_report(int((series.notna() & conv.isna()).sum()),
                                   cname, "reading as numbers", ln)
        return conv
    if typename == "Integer":
        conv = pd.to_numeric(series, errors="coerce")
        context.coerce_drop_report(int((series.notna() & conv.isna()).sum()),
                                   cname, "reading as whole numbers", ln)
        if (conv.dropna() % 1 != 0).any():
            context.record_notice(f"rounded fractional values in {cname} "
                                  f"to whole numbers.")
        return conv.round().astype("Int64")
    if typename == "Percent":
        cleaned = series.astype("string").str.replace("%", "", regex=False)
        conv = pd.to_numeric(cleaned, errors="coerce")
        context.coerce_drop_report(int((series.notna() & conv.isna()).sum()),
                                   cname, "reading as percentages", ln)
        # values written as fractions (0-1) are scaled to 0-100 for consistency
        if conv.dropna().abs().le(1).all() and conv.notna().any():
            conv = conv * 100
            context.record_notice(f"read {cname} as fractions and scaled it "
                                  f"to percent.")
        return conv
    if typename == "Duration":
        return series.map(_parse_duration_to_minutes).astype(float)
    if typename == "Date":
        return pd.to_datetime(series, errors="coerce")
    if typename == "Boolean":
        low = series.astype("string").str.lower()
        return low.isin(["true", "yes", "1"])
    return series.astype("string")


def _grouped_transform(expr, df, keys, ln):
    """A within-group transform: each row's share of its group's total for `expr`.

    Uses groupby().transform() so every row is kept (nothing collapses)."""
    val = eval_expr(expr, df, ln)
    val = pd.to_numeric(pd.Series(val, index=df.index), errors="coerce")
    totals = val.groupby([df[k] for k in keys], dropna=False,
                         observed=False).transform("sum")
    with np.errstate(divide="ignore", invalid="ignore"):
        share = val / totals
    return share


def _infer_value_type(value):
    if isinstance(value, pd.Series):
        if pd.api.types.is_bool_dtype(value):
            return "Boolean"
        if pd.api.types.is_numeric_dtype(value):
            return "Number"
        return "Text"
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, (int, float)):
        return "Number"
    return "Text"


def _cols_of(table):
    return len(table.df.columns) if isinstance(table, Table) else None


def run_program(src, env=None):
    """tokenize -> parse -> interpret.
    Returns (Table|None, Chart|None, notices, trace)."""
    lines = tokenize(src)
    clauses = Parser(lines).parse_program()
    interp = Interpreter(env or Env())
    interp.raw_lines = {ln.lineno: ln.raw for ln in lines}
    interp.source = src
    return interp.run(clauses)
