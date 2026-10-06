"""Line-by-line recursive descent parser + clause-order enforcement.

Every token access is bounds-checked so a half-typed line yields a teaching
AskError, never an IndexError.
"""

import difflib
import os
import re

from .astnodes import (Add, Agg, Bin, Chart, ChartMod, Clean, Combine, Compare,
                       DropCols, DropDuplicates, Estimate, Expand, Explain,
                       FillMissing, FlagOutliers, ForEachGroup, FromTable,
                       FunctionDef, GroupBy, GroupFilter, ImportRecipes, Keep,
                       Limit, Load, Predict, RecipeCall, RecipeDef, RecordDef,
                       Relate, Rename, Replace, Resample, Reshape, Save,
                       SaveNamed, SaveReport, SetSeed, Show, SortBy, Split,
                       Stack, Standardize, StrictMode, TypeDef, Understand,
                       Where, Window)
from .errors import AskError, replace_first_word
from .expressions import ExprParser
from .runtime import resolve_read_path

CORE_CHART_TYPES = {"bar", "line", "area", "scatter", "histogram", "box", "pie", "heatmap",
                    "bubble", "distribution", "change", "flow", "tree", "network", "map",
                    "story"}
ADVANCED_CHART_TYPES = set()

# clause-order ranks (sort + limit share a rank so they may interleave).
# The window stage (rank 4) sits between group-by and summarize on purpose:
# window/rolling verbs and group-wise filters need to see the group context
# that GroupBy attaches, but must run before Show collapses it into one row.
VERB_RANK = {
    "Load": 0, "FromTable": 0, "SetSeed": 0, "StrictMode": 0,
    "Understand": 1, "Clean": 1, "Standardize": 1, "FillMissing": 1,
    "DropDuplicates": 1, "FlagOutliers": 1, "Keep": 1, "DropCols": 1,
    "Rename": 1, "Add": 1, "Bin": 1, "Split": 1, "Expand": 1, "Replace": 1,
    "Where": 1, "RecipeCall": 1,
    "Combine": 2, "Stack": 2, "Reshape": 2,
    "GroupBy": 3, "Resample": 3,
    "Window": 4, "GroupFilter": 4,
    "Show": 5, "Compare": 5, "Relate": 5, "Predict": 5, "Estimate": 5,
    "ForEachGroup": 5,
    "SortBy": 6, "Limit": 6,
    "Chart": 7,
    "Save": 8, "SaveReport": 8, "SaveNamed": 8,
    # Explain and ImportRecipes are handled specially in _check_order.
    "Explain": 99, "ImportRecipes": 0,
}
RANK_NAME = {
    0: "the load step", 1: "the transform steps",
    2: "the combine / stack / reshape step", 3: "the group by step",
    4: "the window step", 5: "the summarize step",
    6: "the sort / top step", 7: "the chart step",
    8: "the save step",
}

MODIFIER_STARTS = {"color", "size", "split", "label", "explain", "animate"}

# `reshape wide ... aggregate <agg>` - same vocabulary as `show`'s summary
# functions, restricted to the ones that make sense for a single pivot cell.
RESHAPE_AGGS = {"total", "average", "count", "min", "max", "median", "spread"}

KNOWN_VERBS = {
    "load", "from", "save", "understand", "clean", "standardize", "fill",
    "drop", "flag", "keep", "rename", "add", "bin", "split", "replace",
    "where", "combine", "stack", "group", "show", "reshape", "resample",
    "sort", "top", "bottom", "first", "last", "chart", "type", "define",
    "explain", "compare", "relate", "predict", "import",
    "use", "set", "call", "for", "estimate", "expand",
    "running", "moving", "rank", "lag", "lead",
}


def _too_short(ln, what, example):
    return AskError(f"This line ends too soon - {what} is missing.",
                    f"line {ln}", example, ln)


class Parser:
    def __init__(self, lines):
        self.lines = lines
        self.i = 0
        self.recipes = set()

    def peek(self):
        return self.lines[self.i] if self.i < len(self.lines) else None

    def parse_program(self):
        clauses = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            first = line.tokens[0]
            if first.type == "WORD" and first.value == "type":
                clauses.append(self._parse_type_def(line))
                self.i += 1
            elif first.type == "WORD" and first.value == "define" \
                    and len(line.tokens) > 1 and line.tokens[1].value == "function":
                clauses.append(self._parse_function_def(line))
                self.i += 1
            elif first.type == "WORD" and first.value == "define":
                clauses.append(self._parse_recipe_def(line))
            else:
                clause = self._parse_statement(line)
                self.i += 1
                if isinstance(clause, Chart):
                    self._attach_modifiers(clause)
                clauses.append(clause)
        self._check_order(clauses)
        return clauses

    # ---- definitions -------------------------------------------------------
    def _parse_type_def(self, line):
        t = line.tokens
        # type NAME = ...
        if len(t) < 4 or t[1].type != "WORD" or not (t[2].type == "OP" and t[2].value == "="):
            raise AskError("A type needs a name and an '='.",
                           f"line {line.lineno}",
                           'type Rating = Poor | Fair | Good | Great', line.lineno)
        name = t[1].value
        rest = t[3:]
        if rest and rest[0].type == "PUNCT" and rest[0].value == "{":
            fields = {}
            j = 1
            while j < len(rest) and not (rest[j].type == "PUNCT" and rest[j].value == "}"):
                fname = rest[j].value
                # expect : Type
                if j + 2 < len(rest) and rest[j + 1].value == ":":
                    fields[fname] = rest[j + 2].value
                    j += 3
                else:
                    j += 1
                if j < len(rest) and rest[j].type == "PUNCT" and rest[j].value == ",":
                    j += 1
            return RecordDef(line=line.lineno, name=name, fields=fields)
        # `ordered A < B < C` keeps the ranking; `A | B | C` is an unordered set.
        ordered = True
        if rest and rest[0].type == "WORD" and rest[0].value == "ordered":
            rest = rest[1:]
        elif any(tk.type == "PUNCT" and tk.value == "|" for tk in rest):
            ordered = False
        variants = [tk.value for tk in rest if tk.type == "WORD"]
        return TypeDef(line=line.lineno, name=name, variants=variants, ordered=ordered)

    def _parse_function_def(self, line):
        # define function name(a, b): <expr>
        t = line.tokens
        if len(t) < 3 or t[2].type != "WORD":
            raise AskError("A function looks like: define function name(a, b): a + b",
                           f"line {line.lineno}",
                           "define function margin(rev, cost): (rev - cost) / rev",
                           line.lineno)
        name = t[2].value
        params = []
        # collect names inside ( ... )
        j = 3
        if j < len(t) and t[j].type == "PUNCT" and t[j].value == "(":
            j += 1
            while j < len(t) and not (t[j].type == "PUNCT" and t[j].value == ")"):
                if t[j].type == "WORD":
                    params.append(t[j].value)
                j += 1
            if j < len(t):
                j += 1  # skip )
        if j < len(t) and t[j].type == "PUNCT" and t[j].value == ":":
            j += 1
        if j >= len(t):
            raise _too_short(line.lineno, "the function body",
                            "define function margin(rev, cost): (rev - cost) / rev")
        expr = ExprParser(t[j:], line.lineno).parse()
        return FunctionDef(line=line.lineno, name=name, params=params, expr=expr)

    def _parse_recipe_def(self, line):
        t = line.tokens
        # define recipe NAME from data:
        if len(t) < 3 or t[1].value != "recipe":
            raise AskError("A recipe definition looks like: define recipe NAME from data:",
                           f"line {line.lineno}",
                           "define recipe top_regions from data:", line.lineno)
        name = t[2].value
        self.recipes.add(name)
        params = []
        # optional parameter list: define recipe name(col, n) from data:
        if len(t) > 3 and t[3].type == "PUNCT" and t[3].value == "(":
            j = 4
            while j < len(t) and not (t[j].type == "PUNCT" and t[j].value == ")"):
                if t[j].type == "WORD":
                    params.append(t[j].value)
                j += 1
        base_indent = line.indent
        self.i += 1
        body_lines = []
        while self.i < len(self.lines) and self.lines[self.i].indent > base_indent:
            body_lines.append(self.lines[self.i])
            self.i += 1
        sub = Parser(body_lines)
        sub.recipes = self.recipes
        body = []
        k = 0
        while k < len(body_lines):
            ln = body_lines[k]
            sub.i = k
            clause = sub._parse_statement(ln)
            k = sub.i + 1
            if isinstance(clause, Chart):
                # attach modifiers within recipe body
                while k < len(body_lines) and _is_modifier(body_lines[k]):
                    clause.mods.append(sub._make_modifier(body_lines[k]))
                    k += 1
            body.append(clause)
        self._check_order(body)
        return RecipeDef(line=line.lineno, name=name, body=body, params=params)

    # ---- chart modifiers ---------------------------------------------------
    def _attach_modifiers(self, chart):
        while self.i < len(self.lines) and _is_modifier(self.lines[self.i]):
            chart.mods.append(self._make_modifier(self.lines[self.i]))
            self.i += 1

    def _make_modifier(self, line):
        t = line.tokens
        w = t[0].value
        if w == "label":
            return ChartMod("label", t[1].value if len(t) > 1 else "")
        if w == "explain":
            return ChartMod("explain")
        if w == "add" and len(t) > 1 and t[1].value == "trendline":
            return ChartMod("trendline")
        if w in ("color", "size", "split", "animate"):
            col = t[2].value if len(t) > 2 else None   # skip "by"
            return ChartMod(w, col)
        return ChartMod(w)

    # ---- statements --------------------------------------------------------
    def _parse_statement(self, line):
        t = line.tokens
        w = t[0].value
        ln = line.lineno
        if t[0].type != "WORD":
            raise AskError("A line should start with a command word.",
                           f"line {ln}", 'For example: where region is "West"', ln)

        if w == "load":
            return Load(line=ln, path=self._one_string(t, ln, "load"))
        if w == "from":
            return FromTable(line=ln, name=self._one_word(t, ln, "from"))
        if w == "save":
            # save as "file"   OR   save report as "file.html"
            if len(t) >= 2 and t[1].value == "report":
                path = self._one_string(t, ln, "save report")
                return SaveReport(line=ln, path=path)
            if len(t) >= 3 and t[1].value == "as":
                return Save(line=ln, path=t[2].value)
            raise AskError('save needs a file name.', f"line {ln}",
                           'save as "out.csv"  (or  save report as "out.html")', ln)
        if w == "import":
            path = self._one_string(t, ln, "import")
            self._prescan_import(path)   # learn recipe names so later calls parse
            return ImportRecipes(line=ln, path=path)
        if w == "explain":
            return Explain(line=ln)
        if w == "use":
            # use strict types
            if [x.value for x in t[1:3]] == ["strict", "types"]:
                return StrictMode(line=ln)
            raise AskError("The only setting is: use strict types",
                           f"line {ln}", "use strict types", ln)
        if w == "set":
            # set seed <int>
            if len(t) >= 3 and t[1].value == "seed" and t[2].type == "NUM":
                return SetSeed(line=ln, seed=int(t[2].value))
            raise AskError("set looks like: set seed 42",
                           f"line {ln}", "set seed 42", ln)
        if w == "call":
            # call it NAME
            if len(t) >= 3 and t[1].value == "it":
                return SaveNamed(line=ln, name=t[2].value)
            raise AskError("call looks like: call it snapshot_name",
                           f"line {ln}", "call it clean_data", ln)
        if w == "for":
            return self._parse_for_each(t, ln)
        if w == "estimate":
            return self._parse_estimate(t, ln)
        if w in ("running", "moving", "rank", "lag", "lead"):
            return self._parse_window(w, t, ln)
        if w == "compare":
            return self._parse_compare(t, ln)
        if w == "relate":
            return self._parse_relate(t, ln)
        if w == "predict":
            return self._parse_predict(t, ln)
        if w == "understand":
            return Understand(line=ln)
        if w == "clean":
            return Clean(line=ln, col=self._one_word(t, ln, "clean"))
        if w == "standardize":
            return Standardize(line=ln, col=self._one_word(t, ln, "standardize"))
        if w == "fill":
            return self._parse_fill(t, ln)
        if w == "drop":
            if len(t) > 1 and t[1].value == "duplicates":
                cols = self._cols_after(t, 2, skip_by=True)
                return DropDuplicates(line=ln, cols=cols)
            return DropCols(line=ln, cols=self._cols_after(t, 1))
        if w == "flag":
            # flag outliers in <col>
            if len(t) >= 4 and t[1].value == "outliers" and t[2].value == "in":
                return FlagOutliers(line=ln, col=t[3].value)
            if len(t) == 3 and t[1].value == "outliers":
                return FlagOutliers(line=ln, col=t[2].value)
            raise AskError("flag looks like: flag outliers in <column>",
                           f"line {ln}", "flag outliers in revenue", ln)
        if w == "keep":
            # `keep top N within group` is a group-wise row filter, not a column pick.
            if len(t) > 1 and t[1].value in ("top", "bottom"):
                return self._parse_group_filter(t, ln)
            return Keep(line=ln, cols=self._cols_after(t, 1))
        if w == "rename":
            # rename a to b
            if len(t) >= 4 and t[2].value == "to":
                return Rename(line=ln, old=t[1].value, new=t[3].value)
            raise AskError("rename looks like: rename old to new", f"line {ln}",
                           "rename revenue to sales", ln)
        if w == "add":
            return self._parse_add(t, ln)
        if w == "bin":
            return self._parse_bin(t, ln)
        if w == "split":
            return self._parse_split(t, ln)
        if w == "expand":
            return self._parse_expand(t, ln)
        if w == "replace":
            return self._parse_replace(t, ln)
        if w == "where":
            if len(t) < 2:
                raise _too_short(ln, "the condition", 'where region is "West"')
            expr = ExprParser(t[1:], ln).parse()
            return Where(line=ln, condition=expr)
        if w == "combine":
            return self._parse_combine(t, ln)
        if w == "stack":
            return Stack(line=ln, other=self._one_word(t, ln, "stack"))
        if w == "group":
            if len(t) < 3 or t[1].value != "by":
                raise AskError("group by needs at least one column.", f"line {ln}",
                               "group by region", ln)
            return GroupBy(line=ln, cols=self._cols_after(t, 2))
        if w == "show":
            return self._parse_show(t, ln)
        if w == "reshape":
            return self._parse_reshape(t, ln)
        if w == "resample":
            period = t[-1].value if len(t) > 1 else "month"
            return Resample(line=ln, period=period)
        if w == "sort":
            if len(t) < 3 or t[1].value != "by":
                raise AskError("sort by needs a column.", f"line {ln}",
                               "sort by revenue descending", ln)
            # The column is the token right after `by`; only tokens AFTER the
            # column can flip the direction (so a column literally named
            # `desc` still sorts ascending — bug F1).
            desc = any(x.type == "WORD" and x.value in ("descending", "desc")
                       for x in t[3:])
            return SortBy(line=ln, col=t[2].value, descending=desc)
        if w in ("top", "bottom", "first", "last"):
            return self._parse_limit(w, t, ln)
        if w == "chart":
            return self._parse_chart(t, ln)

        if w in self.recipes:
            arg_tokens = t[1:]
            if arg_tokens and arg_tokens[0].type == "PUNCT" and arg_tokens[0].value == "(":
                arg_tokens = arg_tokens[1:]
                if arg_tokens and arg_tokens[-1].type == "PUNCT" \
                        and arg_tokens[-1].value == ")":
                    arg_tokens = arg_tokens[:-1]
            args, seg = [], []
            for tk in arg_tokens:
                if tk.type == "PUNCT" and tk.value == ",":
                    if seg:
                        args.append(ExprParser(seg, ln).parse())
                        seg = []
                else:
                    seg.append(tk)
            if seg:
                args.append(ExprParser(seg, ln).parse())
            return RecipeCall(line=ln, name=w, args=args)

        guess = difflib.get_close_matches(w, sorted(KNOWN_VERBS), n=1, cutoff=0.6)
        apply = None
        fix = ("Check the spelling, or see the command list. Commands include: "
               "load, where, add, group by, show, compare, relate, predict, chart.")
        if guess:
            corrected = replace_first_word(line, w, guess[0])
            fix = f"Did you mean '{guess[0]}'?"
            apply = {"mode": "replace", "line": ln, "text": corrected}
        raise AskError(
            what=f"I don't know the command {w!r}.",
            where=f"line {ln}",
            fix=fix,
            line=ln,
            apply=apply,
        )

    # ---- small statement helpers ------------------------------------------
    def _one_string(self, t, ln, verb):
        for tk in t[1:]:
            if tk.type == "STR":
                return tk.value
        raise AskError(f'{verb} needs a file name in quotes.', f"line {ln}",
                       f'{verb} "data.csv"', ln)

    def _one_word(self, t, ln, verb):
        if len(t) < 2:
            raise AskError(f"{verb} needs a name.", f"line {ln}", f"{verb} something", ln)
        return t[1].value

    def _cols_after(self, t, start, skip_by=False):
        # A column-list position never has a value/column-reference ambiguity
        # (unlike `where`/`add` expressions), so accept a bare word, a
        # `backtick-quoted` name, or a "double-quoted" name interchangeably -
        # whichever way someone reaches for a name with a dot or space in it.
        cols = []
        for tk in t[start:]:
            if tk.type == "WORD":
                if skip_by and tk.value == "by":
                    continue
                cols.append(tk.value)
            elif tk.type in ("STR", "COLREF"):
                cols.append(tk.value)
        return cols

    def _word_after(self, t, ln, keyword, example):
        """The token right after the first WORD token `keyword`, bounds-checked."""
        for k, tk in enumerate(t):
            if tk.type == "WORD" and tk.value == keyword:
                if k + 1 >= len(t):
                    raise _too_short(ln, f"the part after '{keyword}'", example)
                return t[k + 1].value
        return None

    def _parse_fill(self, t, ln):
        # fill missing <col> with X
        if len(t) < 3 or t[1].value != "missing":
            raise AskError("fill looks like: fill missing <col> with average",
                           f"line {ln}", "fill missing price with average", ln)
        col = t[2].value
        method = None
        if "with" in [x.value for x in t]:
            idx = [x.value for x in t].index("with")
            if idx + 1 < len(t):
                method = t[idx + 1].value
        return FillMissing(line=ln, col=col, method=method)

    def _parse_add(self, t, ln):
        # add NAME = <expr>   OR   add NAME as TypeName
        if len(t) < 2:
            raise AskError("add needs a column name.", f"line {ln}",
                           "add margin = revenue - cost", ln)
        name = t[1].value
        vals = t[2:]
        # `per group` / `within group` marks a grouped (non-collapsing) transform.
        per_group = False
        words = [x.value for x in vals]
        for marker in ("per", "within"):
            if marker in words:
                mi = words.index(marker)
                if mi + 1 < len(words) and words[mi + 1] == "group":
                    per_group = True
                    vals = vals[:mi]
                    break
        if vals and vals[0].type == "OP" and vals[0].value == "=":
            if len(vals) < 2:
                raise _too_short(ln, "the expression after '='",
                                "add margin = (revenue - cost) / revenue")
            expr = ExprParser(vals[1:], ln).parse()
            return Add(line=ln, name=name, expr=expr, per_group=per_group)
        if vals and vals[0].type == "WORD" and vals[0].value == "as":
            if len(vals) < 2:
                raise _too_short(ln, "the type name after 'as'",
                                "add score as Rating")
            return Add(line=ln, name=name, astype=vals[1].value)
        raise AskError("add looks like: add NAME = <expr>  (or  add NAME as Type)",
                       f"line {ln}", 'add margin = (revenue - cost) / revenue', ln)

    def _parse_bin(self, t, ln):
        # bin <col> into a, b, c
        example = "bin margin into low, medium, high"
        if len(t) < 2 or t[1].type != "WORD":
            raise AskError("bin needs a column name.", f"line {ln}", example, ln)
        col = t[1].value
        vals = [x.value for x in t]
        if "into" not in vals:
            raise AskError("bin needs 'into' and some names.", f"line {ln}",
                           example, ln)
        idx = vals.index("into")
        labels = [x.value for x in t[idx + 1:] if x.type == "WORD"]
        if not labels:
            raise AskError("bin needs at least two bucket names after 'into'.",
                           f"line {ln}", example, ln)
        return Bin(line=ln, col=col, labels=labels)

    def _parse_split(self, t, ln):
        # split <col> by "x" into a, b
        example = 'split full_name by " " into first, last'
        if len(t) < 2 or t[1].type != "WORD":
            raise AskError("split needs a column name.", f"line {ln}", example, ln)
        col = t[1].value
        sep = ""
        into = []
        vals = [x.value for x in t]
        if "by" in vals:
            bi = vals.index("by")
            if bi + 1 < len(t) and t[bi + 1].type == "STR":
                sep = t[bi + 1].value
        if "into" in vals:
            ii = vals.index("into")
            into = [x.value for x in t[ii + 1:] if x.type == "WORD"]
        if not sep or not into:
            raise AskError('split looks like: split <col> by "x" into a, b',
                           f"line {ln}", example, ln)
        return Split(line=ln, col=col, sep=sep, into=into)

    def _parse_expand(self, t, ln):
        # expand <col> by ","
        example = 'expand tags by ","'
        if len(t) < 2 or t[1].type != "WORD":
            raise AskError("expand needs a column name.", f"line {ln}", example, ln)
        col = t[1].value
        vals = [x.value for x in t]
        sep = None
        if "by" in vals:
            bi = vals.index("by")
            if bi + 1 < len(t) and t[bi + 1].type == "STR":
                sep = t[bi + 1].value
        if sep is None:
            raise AskError('expand looks like: expand <col> by ","',
                           f"line {ln}", example, ln)
        return Expand(line=ln, col=col, sep=sep)

    def _parse_replace(self, t, ln):
        # replace "a" with "b" in <col>
        example = 'replace "St." with "Street" in address'
        # find the WORD token `in` (not a string literal "in") first, so the
        # old/new STR tokens are only gathered from BEFORE it — otherwise a
        # quoted column name after `in` (e.g. "ref_area.label") would get
        # swept into `strs` and thrown off old/new.
        in_at = None
        for k in range(len(t) - 1, -1, -1):
            if t[k].type == "WORD" and t[k].value == "in":
                in_at = k
                break
        before = t[:in_at] if in_at is not None else t
        strs = [x.value for x in before if x.type == "STR"]
        if len(strs) < 2:
            raise AskError('replace looks like: replace "a" with "b" in <col>',
                           f"line {ln}", example, ln)
        # bug F2: previously the last token was assumed to be the column, so
        # `replace "a" with "b"` (with no `in`) used "b" as the column.
        col = None
        if in_at is not None and in_at + 1 < len(t) \
                and t[in_at + 1].type in ("WORD", "STR", "COLREF"):
            col = t[in_at + 1].value
        if col is None:
            raise AskError("replace needs 'in <column>' at the end.",
                           f"line {ln}", example, ln)
        return Replace(line=ln, old=strs[0], new=strs[1], col=col)

    def _parse_combine(self, t, ln):
        # combine with <t> on <key> [fuzzy] [keeping all left|all right|all|matches only]
        example = "combine with customers on name"
        keeping_example = ("combine with t on key keeping matches only "
                           "(or: keeping all left / keeping all right / keeping all)")
        if len(t) > 1 and t[1].value in ("inner", "left", "right", "outer"):
            raise AskError(
                what=f"combine no longer takes {t[1].value!r} directly after it.",
                where=f"line {ln}",
                fix="Say what to keep instead: " + keeping_example, line=ln)
        other = self._word_after(t, ln, "with", example)
        key = self._word_after(t, ln, "on", example)
        if not other:
            raise AskError("combine needs 'with <table>'.", f"line {ln}", example, ln)
        if not key:
            raise AskError("combine needs 'on <key column>'.", f"line {ln}",
                           example, ln)
        vals = [x.value for x in t]
        fuzzy = "fuzzy" in vals
        how = "left"
        if "keeping" in vals:
            ki = vals.index("keeping")
            rest = [v for v in vals[ki + 1:] if v != "fuzzy"]
            if rest[:2] == ["matches", "only"]:
                how = "inner"
            elif rest[:2] == ["all", "left"]:
                how = "left"
            elif rest[:2] == ["all", "right"]:
                how = "right"
            elif rest == ["all"]:
                how = "outer"
            else:
                raise AskError(
                    what="I don't understand that 'keeping' phrase.",
                    where=f"line {ln}", fix=keeping_example, line=ln)
        return Combine(line=ln, other=other, key=key, fuzzy=fuzzy, how=how)

    def _parse_show(self, t, ln):
        aggs = []
        funcs = {"total", "average", "count", "min", "max", "median", "spread",
                 "share", "mode", "skew", "kurtosis", "quantile"}
        i = 1
        while i < len(t):
            tk = t[i]
            if tk.type == "WORD" and tk.value in funcs:
                func = tk.value
                col = None
                name = func
                if i + 1 < len(t) and t[i + 1].type == "WORD" \
                        and t[i + 1].value not in funcs and t[i + 1].value != "as":
                    col = t[i + 1].value
                    name = col
                    i += 1
                q, method = 0.5, "type7"
                # quantile revenue at 0.9 method linear
                if func == "quantile":
                    if i + 2 < len(t) and t[i + 1].value == "at" and t[i + 2].type == "NUM":
                        q = float(t[i + 2].value)
                        i += 2
                    if i + 2 < len(t) and t[i + 1].value in ("method", "using") \
                            and t[i + 2].type == "WORD":
                        method = t[i + 2].value
                        i += 2
                    if name in ("quantile", col):
                        name = (col or "quantile") + f"_p{int(round(q * 100))}"
                if i + 2 < len(t) and t[i + 1].value == "as":
                    name = t[i + 2].value
                    i += 2
                aggs.append(Agg(func, col, name, q=q, method=method))
            i += 1
        if not aggs:
            raise AskError("show needs at least one summary, like: show count",
                           f"line {ln}", "show total revenue as sales", ln)
        return Show(line=ln, aggs=aggs)

    def _parse_reshape(self, t, ln):
        vals = [x.value for x in t]
        if "long" in vals:
            keeping = []
            if "keeping" in vals:
                ki = vals.index("keeping")
                keeping = [x.value for x in t[ki + 1:]
                          if x.type in ("WORD", "STR", "COLREF")]
            return Reshape(line=ln, mode="long", keeping=keeping)
        example = "reshape wide by month using sales"
        by = self._word_after(t, ln, "by", example)
        using = self._word_after(t, ln, "using", example)
        aggregate = None
        if "aggregate" in vals:
            ai = vals.index("aggregate")
            if ai + 1 >= len(t):
                raise _too_short(ln, "the aggregate name",
                                 "reshape wide by month using sales aggregate average")
            aggregate = t[ai + 1].value
            if aggregate not in RESHAPE_AGGS:
                raise AskError(
                    what=f"I don't know the aggregate {aggregate!r}.",
                    where=f"line {ln}",
                    fix="Try one of: " + ", ".join(sorted(RESHAPE_AGGS)), line=ln)
        return Reshape(line=ln, mode="wide", by=by, using=using, aggregate=aggregate)

    def _parse_limit(self, w, t, ln):
        # top <n> [by <col>] ; first <n>
        n = 5
        col = None
        for k, tk in enumerate(t[1:], start=1):
            if tk.type == "NUM":
                n = int(tk.value)
            if tk.type == "WORD" and tk.value == "by" and k + 1 < len(t):
                col = t[k + 1].value
        return Limit(line=ln, mode=w, n=n, col=col)

    def _parse_chart(self, t, ln):
        # chart <y> by <x> as <type>
        example = "chart revenue by region as bar"
        vals = [x.value for x in t]
        if len(t) < 2 or "by" not in vals or "as" not in vals:
            raise AskError("chart looks like: chart <value> by <category> as bar",
                           f"line {ln}", example, ln)
        bi = vals.index("by")
        ai = vals.index("as")
        if bi + 1 >= len(t) or ai + 1 >= len(t) or bi == 1:
            raise AskError("chart looks like: chart <value> by <category> as bar",
                           f"line {ln}", example, ln)
        y = t[1].value
        x = t[bi + 1].value
        kind = t[ai + 1].value
        return Chart(line=ln, y=y, x=x, kind=kind)

    def _prescan_import(self, path):
        """Peek at an imported .ask file to learn its recipe names at parse time.

        Missing/unreadable files are ignored here on purpose: the interpreter's
        visit_ImportRecipes raises the real teaching error with the line number."""
        try:
            resolved = resolve_read_path(path)
        except AskError:
            return
        try:
            with open(resolved, "r", encoding="utf-8") as f:
                for raw in f:
                    parts = raw.strip().split()
                    if len(parts) >= 3 and parts[0] == "define" and parts[1] == "recipe":
                        self.recipes.add(parts[2])
        except OSError:
            return

    def _parse_compare(self, t, ln):
        example = "compare revenue between region"
        vals = [x.value for x in t]
        if "paired" in vals and "between" not in vals:
            # compare <a> and <b> paired - two number columns, matched row by row
            paired_example = "compare before and after paired"
            if len(t) < 2 or "and" not in vals:
                raise AskError("compare paired looks like: compare <a> and <b> "
                               "paired", f"line {ln}", paired_example, ln)
            ai = vals.index("and")
            if ai + 1 >= len(t) or ai == 1 or t[ai + 1].value == "paired":
                raise AskError("compare paired looks like: compare <a> and <b> "
                               "paired", f"line {ln}", paired_example, ln)
            return Compare(line=ln, value=t[1].value, group=t[ai + 1].value,
                          paired=True)
        if len(t) < 2 or "between" not in vals:
            raise AskError("compare looks like: compare <number column> between "
                           "<group column>", f"line {ln}", example, ln)
        bi = vals.index("between")
        if bi + 1 >= len(t) or bi == 1:
            raise AskError("compare looks like: compare <number column> between "
                           "<group column>", f"line {ln}", example, ln)
        return Compare(line=ln, value=t[1].value, group=t[bi + 1].value)

    def _parse_relate(self, t, ln):
        example = "relate spend and sales"
        vals = [x.value for x in t]
        if len(t) > 2 and t[1].value == "all" and t[2].value == "of":
            cols = [x.value for x in t[3:] if x.type == "WORD"]
            if len(cols) < 2:
                raise AskError(
                    "relate all of needs two or more columns.", f"line {ln}",
                    "relate all of revenue, cost, margin", ln)
            return Relate(line=ln, matrix_cols=cols)
        if len(t) < 2 or "and" not in vals:
            raise AskError("relate looks like: relate <column> and <column>",
                           f"line {ln}", example, ln)
        ai = vals.index("and")
        if ai + 1 >= len(t) or ai == 1:
            raise AskError("relate looks like: relate <column> and <column>",
                           f"line {ln}", example, ln)
        a, b = t[1].value, t[ai + 1].value
        controlling = None
        if "controlling" in vals:
            ci = vals.index("controlling")
            if ci + 1 < len(t) and t[ci + 1].value == "for" and ci + 2 < len(t):
                controlling = t[ci + 2].value
            else:
                raise AskError(
                    "relate ... controlling for looks like: relate a and b "
                    "controlling for c", f"line {ln}",
                    "relate revenue and cost controlling for region_size", ln)
        return Relate(line=ln, a=a, b=b, controlling=controlling)

    def _parse_predict(self, t, ln):
        vals = [x.value for x in t]
        if len(t) < 2 or "from" not in vals:
            raise AskError("predict looks like: predict <target> from "
                           "<column>, <column>", f"line {ln}",
                           "predict sales from spend, visits", ln)
        fi = vals.index("from")
        # A trailing `using r package "X"` selects an external engine (the R
        # bridge in rbridge.py); see Interpreter._predict_via_r.
        engine = None
        rhs = t[fi + 1:]
        if "using" in [x.value for x in rhs]:
            ui = [x.value for x in rhs].index("using")
            for tk in rhs[ui:]:
                if tk.type == "STR":
                    engine = tk.value
            rhs = rhs[:ui]
        # `with polynomial N` adds x^2..x^N terms for every number input.
        polynomial = None
        rhs_vals = [x.value for x in rhs]
        if "with" in rhs_vals and "polynomial" in rhs_vals:
            wi = rhs_vals.index("with")
            poly_example = "predict y from x with polynomial 2"
            if wi + 1 >= len(rhs) or rhs[wi + 1].value != "polynomial":
                raise AskError("predict ... with polynomial looks like: "
                               + poly_example, f"line {ln}", poly_example, ln)
            if wi + 2 >= len(rhs) or rhs[wi + 2].type != "NUM":
                raise AskError("predict ... with polynomial needs a number "
                               "after it.", f"line {ln}", poly_example, ln)
            polynomial = int(rhs[wi + 2].value)
            rhs = rhs[:wi]
        # Formula notation: inputs joined by "and", interactions by "times".
        inputs, interactions = [], []
        segment = []
        groups = []
        for tk in rhs:
            if tk.type == "WORD" and tk.value == "and":
                if segment:
                    groups.append(segment)
                    segment = []
            elif tk.type in ("WORD", "STR"):
                segment.append(tk.value)
        if segment:
            groups.append(segment)
        for seg in groups:
            if "times" in seg:
                ti = seg.index("times")
                a = seg[ti - 1] if ti > 0 else ""
                b = seg[ti + 1] if ti + 1 < len(seg) else ""
                if a and b:
                    interactions.append((a, b))
                    for nm in (a, b):
                        if nm not in inputs:
                            inputs.append(nm)
            else:
                for nm in seg:
                    if nm not in inputs:
                        inputs.append(nm)
        return Predict(line=ln, target=t[1].value, inputs=inputs,
                       interactions=interactions, engine=engine,
                       polynomial=polynomial)

    def _parse_for_each(self, t, ln):
        # for each <group column> do recipe <name>
        example = "for each region do recipe summary"
        vals = [x.value for x in t]
        if len(t) < 3 or t[1].value != "each" or "do" not in vals:
            raise AskError("for each looks like: for each region do recipe summary",
                           f"line {ln}", example, ln)
        di = vals.index("do")
        if di + 1 >= len(t) or t[-1].value in ("do", "recipe"):
            raise AskError("for each needs a recipe name after 'do recipe'.",
                           f"line {ln}", example, ln)
        group = t[2].value
        recipe = t[-1].value
        return ForEachGroup(line=ln, group=group, recipe=recipe)

    def _parse_estimate(self, t, ln):
        # estimate <func> <col> with bootstrap
        funcs = {"average", "total", "median", "min", "max", "spread"}
        example = "estimate average revenue with bootstrap"
        if len(t) < 3 or t[1].value not in funcs:
            raise AskError("estimate looks like: estimate average sales with bootstrap",
                           f"line {ln}", example, ln)
        func = t[1].value
        col = t[2].value
        return Estimate(line=ln, func=func, col=col, method="bootstrap")

    def _parse_window(self, w, t, ln):
        vals = [x.value for x in t]
        name = None
        if "as" in vals:
            ai = vals.index("as")
            if ai + 1 < len(t):
                name = t[ai + 1].value
        if w == "running":
            # running total of <col>
            col = self._word_after(t, ln, "of", "running total of sales")
            if col is None:
                col = t[2].value if len(t) > 2 else None
            if not col:
                raise _too_short(ln, "the column", "running total of sales")
            return Window(line=ln, op="running_total", col=col,
                          name=name or (col + "_running"))
        if w == "moving":
            # moving average of <col> over N
            col = self._word_after(t, ln, "of", "moving average of sales over 3")
            if not col:
                raise _too_short(ln, "the column", "moving average of sales over 3")
            n = 3
            if "over" in vals:
                vi = vals.index("over")
                if vi + 1 < len(t) and t[vi + 1].type == "NUM":
                    n = int(t[vi + 1].value)
            return Window(line=ln, op="moving_average", col=col, n=n,
                          name=name or (col + "_moving"))
        if w == "rank":
            # rank <col> within group
            if len(t) < 2 or t[1].type != "WORD":
                raise _too_short(ln, "the column", "rank revenue within group")
            col = t[1].value
            return Window(line=ln, op="rank", col=col, name=name or (col + "_rank"))
        if w in ("lag", "lead"):
            # lag <col> by N
            if len(t) < 2 or t[1].type != "WORD":
                raise _too_short(ln, "the column", f"{w} revenue by 1")
            col = t[1].value
            n = 1
            if "by" in vals:
                bi = vals.index("by")
                if bi + 1 < len(t) and t[bi + 1].type == "NUM":
                    n = int(t[bi + 1].value)
            return Window(line=ln, op=w, col=col, n=n, name=name or (col + "_" + w))
        raise AskError(f"I don't understand the window verb {w!r}.", f"line {ln}",
                       "Try: running total of sales", ln)

    def _parse_group_filter(self, t, ln):
        # keep top N within group [by <col>]
        mode = t[1].value
        n = 1
        col = None
        for k, tk in enumerate(t):
            if tk.type == "NUM":
                n = int(tk.value)
            if tk.type == "WORD" and tk.value == "by" and k + 1 < len(t):
                col = t[k + 1].value
        return GroupFilter(line=ln, mode=mode, n=n, col=col)

    # ---- clause order ------------------------------------------------------
    def _check_order(self, clauses):
        # A program is an ordered sequence of blocks; each block is one linear
        # pipeline starting from a data source (load / from). Order is enforced
        # WITHIN each block, so a `call it NAME` + later `from NAME` workflow can
        # legitimately reset the pipeline without tripping the ordering rules.
        pipeline = [c for c in clauses
                    if not isinstance(c, (TypeDef, RecordDef, RecipeDef,
                                          FunctionDef, ImportRecipes, Explain))]
        blocks, cur = [], []
        for c in pipeline:
            if isinstance(c, (Load, FromTable)) and any(
                    isinstance(p, (Load, FromTable)) for p in cur):
                blocks.append(cur)
                cur = []
            cur.append(c)
        if cur:
            blocks.append(cur)
        for block in blocks:
            self._check_block_order(block)

    def _check_block_order(self, pipeline):
        max_rank = -1
        for idx, c in enumerate(pipeline):
            rank = VERB_RANK.get(type(c).__name__, 1)
            # a grouped `add ... per group` runs in the window stage, after group by
            if isinstance(c, Add) and getattr(c, "per_group", False):
                rank = 4
            # fill down/up (previous/next) after a group by carries within
            # each group, like a window op - only bump it into the window
            # stage once a group by has actually happened, so the common
            # ungrouped `fill missing X with previous` still mixes freely
            # with ordinary transforms before/after it
            if isinstance(c, FillMissing) and c.method in ("previous", "next") \
                    and any(isinstance(p, GroupBy) for p in pipeline[:idx]):
                rank = 4
            if isinstance(c, (Load, FromTable)):
                if idx != 0 and any(VERB_RANK.get(type(p).__name__, 1) > 0
                                    for p in pipeline[:idx]):
                    raise AskError(
                        "load must be the first step.",
                        f"line {c.line}",
                        "Move the load line to the top of the program.",
                        c.line)
            if rank < max_rank:
                higher = RANK_NAME[max_rank]
                raise AskError(
                    what=f"'{verb_word(c)}' is out of order - it must come "
                         f"before {higher}.",
                    where=f"line {c.line}",
                    fix=f"Move line {c.line} up, above {higher}.",
                    line=c.line)
            max_rank = max(max_rank, rank)


def verb_word(clause):
    return {
        "Show": "show", "GroupBy": "group by", "Where": "where", "Chart": "chart",
        "SortBy": "sort by", "Limit": "top", "Add": "add", "Load": "load",
    }.get(type(clause).__name__, type(clause).__name__.lower())


def _is_modifier(line):
    t = line.tokens
    if not t or t[0].type != "WORD":
        return False
    w = t[0].value
    if w in MODIFIER_STARTS:
        return True
    if w == "add" and len(t) > 1 and t[1].value == "trendline":
        return True
    return False
