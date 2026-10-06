"""Expression / condition parser + evaluator, shared by `add` and `where`.

Grammar (low -> high precedence):
  or -> and -> not -> comparison -> additive -> multiplicative -> unary -> primary
primary: NUM | STR | WORD(col) | ( expr ) | if COND then EXPR else EXPR
         | extract after/before/between ... from COL | extract number from COL
         | let x = EXPR in EXPR | name(args...)
"""

import difflib
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import context
from .errors import AskError, replace_word_in_line

# Guard for runaway self-referencing function macros (A10).
_MAX_CALL_DEPTH = 64

# `<part> of <date column>` (lubridate-style), e.g. `add q = quarter of date`.
DATE_PARTS = {"year", "quarter", "month", "week", "weekday", "day"}


class E:  # expression node namespace
    @dataclass
    class Num:
        v: Any

    @dataclass
    class Str:
        v: str

    @dataclass
    class Col:
        name: str

    @dataclass
    class Bin:
        op: str
        left: Any
        right: Any

    @dataclass
    class Unary:
        op: str
        operand: Any

    @dataclass
    class IfElse:
        cond: Any
        then: Any
        other: Any

    @dataclass
    class Compare:
        op: str
        left: Any
        right: Any

    @dataclass
    class Logic:
        op: str
        left: Any
        right: Any

    @dataclass
    class Not:
        operand: Any

    @dataclass
    class Between:
        target: Any
        low: Any
        high: Any

    @dataclass
    class In:
        target: Any
        items: list

    @dataclass
    class StrPred:
        op: str          # contains | starts | ends
        target: Any
        arg: Any

    @dataclass
    class Missing:
        target: Any
        negate: bool

    @dataclass
    class DatePart:
        part: str        # year | quarter | month | week | weekday | day
        col: str

    @dataclass
    class Extract:
        mode: str        # after | before | between | number
        a: Any
        b: Any
        col: str

    @dataclass
    class Let:
        name: str
        value: Any
        body: Any

    @dataclass
    class Call:
        name: str
        args: list


class ExprParser:
    def __init__(self, tokens, lineno):
        self.t = tokens
        self.i = 0
        self.ln = lineno

    def parse(self):
        return self._or()

    def _peek(self, offset=0):
        idx = self.i + offset
        return self.t[idx] if idx < len(self.t) else None

    def _next(self):
        tk = self.t[self.i]
        self.i += 1
        return tk

    def _need(self, what, example):
        """Take the next token or raise a teaching error (never IndexError)."""
        if self.i >= len(self.t):
            raise AskError(f"This line ends too soon - {what} is missing.",
                           f"line {self.ln}", example, self.ln)
        return self._next()

    def _is_word(self, *words):
        tk = self._peek()
        return tk is not None and tk.type == "WORD" and tk.value in words

    def _is_op(self, *ops):
        tk = self._peek()
        return tk is not None and tk.type == "OP" and tk.value in ops

    def _or(self):
        node = self._and()
        while self._is_word("or"):
            self._next()
            node = E.Logic("or", node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._is_word("and"):
            self._next()
            node = E.Logic("and", node, self._not())
        return node

    def _not(self):
        if self._is_word("not"):
            self._next()
            return E.Not(self._not())
        return self._comparison()

    def _comparison(self):
        left = self._additive()
        tk = self._peek()
        if tk is None:
            return left
        if tk.type == "OP" and tk.value in ("=", ">", "<", ">=", "<="):
            self._next()
            op = "==" if tk.value == "=" else tk.value
            return E.Compare(op, left, self._additive())
        if tk.type == "WORD" and tk.value == "is":
            self._next()
            # is not ... | is missing | is not missing | is <value>
            if self._is_word("not"):
                self._next()
                if self._is_word("missing"):
                    self._next()
                    return E.Missing(left, negate=True)
                return E.Compare("!=", left, self._additive())
            if self._is_word("missing"):
                self._next()
                return E.Missing(left, negate=False)
            return E.Compare("==", left, self._additive())
        if tk.type == "WORD" and tk.value == "between":
            self._next()
            low = self._additive()
            if self._is_word("and"):
                self._next()
            high = self._additive()
            return E.Between(left, low, high)
        if tk.type == "WORD" and tk.value == "in":
            self._next()
            items = self._list()
            return E.In(left, items)
        if tk.type == "WORD" and tk.value == "contains":
            self._next()
            return E.StrPred("contains", left, self._additive())
        if tk.type == "WORD" and tk.value == "starts":
            self._next()
            if self._is_word("with"):
                self._next()
            return E.StrPred("starts", left, self._additive())
        if tk.type == "WORD" and tk.value == "ends":
            self._next()
            if self._is_word("with"):
                self._next()
            return E.StrPred("ends", left, self._additive())
        return left

    def _list(self):
        items = []
        if self._peek() and self._peek().type == "PUNCT" and self._peek().value == "[":
            self._next()
            while self._peek() and not (self._peek().type == "PUNCT" and self._peek().value == "]"):
                tk = self._next()
                if tk.type in ("NUM", "STR", "WORD"):
                    items.append(tk.value)
            if self._peek():
                self._next()  # ]
        return items

    def _additive(self):
        node = self._multiplicative()
        while self._is_op("+", "-"):
            op = self._next().value
            node = E.Bin(op, node, self._multiplicative())
        return node

    def _multiplicative(self):
        node = self._unary()
        while self._is_op("*", "/"):
            op = self._next().value
            node = E.Bin(op, node, self._unary())
        return node

    def _unary(self):
        if self._is_op("-"):
            self._next()
            return E.Unary("-", self._unary())
        return self._primary()

    def _primary(self):
        tk = self._peek()
        if tk is None:
            raise AskError("A value or column is missing here.",
                           f"line {self.ln}", "Add the missing part of the expression.",
                           self.ln)
        if tk.type == "NUM":
            self._next()
            return E.Num(tk.value)
        if tk.type == "STR":
            self._next()
            return E.Str(tk.value)
        if tk.type == "COLREF":
            self._next()
            return E.Col(tk.value)
        if tk.type == "PUNCT" and tk.value == "(":
            self._next()
            node = self._or()
            if self._peek() and self._peek().type == "PUNCT" and self._peek().value == ")":
                self._next()
            return node
        if tk.type == "WORD" and tk.value == "if":
            self._next()
            cond = self._or()
            if self._is_word("then"):
                self._next()
            then = self._or()
            if self._is_word("else"):
                self._next()
            other = self._or()
            return E.IfElse(cond, then, other)
        if tk.type == "WORD" and tk.value == "extract":
            return self._extract()
        if tk.type == "WORD" and tk.value in DATE_PARTS:
            nxt = self._peek(1)
            # only claim this as a date part when it's actually "<part> of
            # <col>" - otherwise a column genuinely named "year" or "month"
            # (e.g. `add x = year + 1`) must still work as a plain column
            if nxt is not None and nxt.type == "WORD" and nxt.value == "of":
                return self._date_part()
        if tk.type == "WORD" and tk.value == "let":
            # let x = <expr> in <expr>
            self._next()
            name = self._need("the name after 'let'",
                              "let base = revenue - cost in base / revenue").value
            if self._is_op("="):
                self._next()
            # value stops at `in` (parsed at the arithmetic level so the `in`
            # keyword isn't mistaken for a membership test)
            value = self._additive()
            if self._is_word("in"):
                self._next()
            body = self._or()
            return E.Let(name, value, body)
        if tk.type == "WORD":
            self._next()
            # function call: name(arg, arg)
            if self._peek() and self._peek().type == "PUNCT" and self._peek().value == "(":
                self._next()  # (
                args = []
                while self._peek() and not (self._peek().type == "PUNCT"
                                            and self._peek().value == ")"):
                    args.append(self._or())
                    if self._peek() and self._peek().type == "PUNCT" \
                            and self._peek().value == ",":
                        self._next()
                if self._peek():
                    self._next()  # )
                return E.Call(tk.value, args)
            return E.Col(tk.value)
        raise AskError(f"I didn't expect {tk.value!r} here.",
                       f"line {self.ln}", "Check the expression.", self.ln)

    def _extract(self):
        self._next()  # extract
        example = 'extract after ": " from notes'
        # extract number from col
        if self._is_word("number"):
            self._next()
            if self._is_word("from"):
                self._next()
            col = self._need("the column to extract from",
                             "extract number from price_text").value
            return E.Extract("number", None, None, col)
        # extract after/before/between ...
        mode = self._need("after, before or between", example).value
        if mode not in ("after", "before", "between"):
            raise AskError(f"extract needs 'after', 'before', 'between' or 'number', "
                           f"not {mode!r}.", f"line {self.ln}", example, self.ln)
        a = self._need('the marker text in quotes', example).value
        b = None
        if mode == "between":
            if self._is_word("and"):
                self._next()
            b = self._need('the second marker in quotes',
                           'extract between "(" and ")" from notes').value
        if self._is_word("from"):
            self._next()
        col = self._need("the column to extract from", example).value
        return E.Extract(mode, a, b, col)

    def _date_part(self):
        part = self._next().value       # already confirmed to be in DATE_PARTS
        self._next()                    # "of"
        col = self._need("the date column", f"{part} of date").value
        return E.DatePart(part, col)


def _unknown_column_error(name, columns, ln):
    cols = [str(c) for c in columns]
    guess = difflib.get_close_matches(str(name), cols, n=1, cutoff=0.6)
    apply = None
    if guess and context.interp is not None and ln in context.interp.raw_lines:
        corrected = replace_word_in_line(context.interp.raw_lines[ln],
                                         str(name), guess[0])
        fix = f"Did you mean '{guess[0]}'? Columns: " + ", ".join(cols)
        apply = {"mode": "replace", "line": ln, "text": corrected}
    else:
        fix = "Columns available: " + ", ".join(cols)
    return AskError(what=f"I don't know a column named {name!r}.",
                    where=f"line {ln}", fix=fix, line=ln, apply=apply)


def _series_for_col(name, df, ln):
    if name in df.columns:
        return df[name]
    raise _unknown_column_error(name, df.columns, ln)


# A stack of local expression scopes (for `let ... in` and function-macro args).
# Single-threaded per run, mirroring the context.interp convention.
_EXPR_LOCALS = []


def _is_texty(series):
    """True when a Series holds text-like data (object, str, string, category).

    pandas 3 stores text as the `str` dtype, not `object`, so the old
    `dtype == object` check misses every real text column (bug A11)."""
    if not isinstance(series, pd.Series):
        return False
    if isinstance(series.dtype, pd.CategoricalDtype):
        return True
    return not (pd.api.types.is_numeric_dtype(series)
                or pd.api.types.is_datetime64_any_dtype(series)
                or pd.api.types.is_bool_dtype(series)
                or pd.api.types.is_timedelta64_dtype(series))


def _to_numeric_series(series):
    """to_numeric that also survives Categorical input."""
    try:
        return pd.to_numeric(series, errors="coerce")
    except (TypeError, ValueError):
        return pd.to_numeric(series.astype("string"), errors="coerce")


def eval_expr(node, df, ln):
    """Evaluate an expression to a pandas Series (or scalar)."""
    if isinstance(node, E.Num):
        return node.v
    if isinstance(node, E.Str):
        return node.v
    if isinstance(node, E.Col):
        for scope in reversed(_EXPR_LOCALS):
            if node.name in scope:
                return scope[node.name]
        return _series_for_col(node.name, df, ln)
    if isinstance(node, E.Let):
        val = eval_expr(node.value, df, ln)
        _EXPR_LOCALS.append({node.name: val})
        try:
            return eval_expr(node.body, df, ln)
        finally:
            _EXPR_LOCALS.pop()
    if isinstance(node, E.Call):
        fn = None
        if context.interp is not None:
            fn = context.interp.env.functions.get(node.name)
        if fn is None:
            raise AskError(
                what=f"I don't know a function named {node.name!r}.",
                where=f"line {ln}",
                fix="Define it first, e.g. define function boost(x): x * 2", line=ln)
        if len(node.args) != len(fn.params):
            raise AskError(
                what=f"The function {node.name!r} expects {len(fn.params)} "
                     f"input(s), but got {len(node.args)}.",
                where=f"line {ln}",
                fix=f"Call it as {node.name}({', '.join(fn.params)})", line=ln)
        if len(_EXPR_LOCALS) > _MAX_CALL_DEPTH:
            raise AskError(
                what=f"The function {node.name!r} keeps calling itself and never "
                     f"finishes.",
                where=f"line {ln}",
                fix="A function can't call itself. Rewrite it without the "
                    "self-reference.", line=ln)
        scope = {p: eval_expr(a, df, ln) for p, a in zip(fn.params, node.args)}
        _EXPR_LOCALS.append(scope)
        try:
            return eval_expr(fn.expr, df, ln)
        finally:
            _EXPR_LOCALS.pop()
    if isinstance(node, E.Unary):
        return -eval_expr(node.operand, df, ln)
    if isinstance(node, E.Bin):
        left = eval_expr(node.left, df, ln)
        right = eval_expr(node.right, df, ln)
        left, right = _coerce_numeric(node, left, right, ln)
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            return left / right
    if isinstance(node, E.IfElse):
        cond = eval_condition(node.cond, df, ln)
        then = eval_expr(node.then, df, ln)
        other = eval_expr(node.other, df, ln)
        return pd.Series(np.where(cond, then, other), index=df.index)
    if isinstance(node, E.Extract):
        return _eval_extract(node, df, ln)
    if isinstance(node, E.DatePart):
        return _eval_date_part(node, df, ln)
    # a bare condition used as a value -> boolean series
    return eval_condition(node, df, ln)


def _coerce_numeric(node, left, right, ln):
    def fix(v, side_node):
        if _is_texty(v):
            conv = _to_numeric_series(v)
            cname = side_node.name if isinstance(side_node, E.Col) else "a column"
            if conv.notna().any():
                dropped = int((v.notna() & conv.isna()).sum())
                context.coerce_drop_report(dropped, cname, "doing math", ln)
                return conv
            apply = None
            if isinstance(side_node, E.Col) and context.interp is not None \
                    and ln in context.interp.raw_lines:
                raw = context.interp.raw_lines[ln]
                indent = raw[:len(raw) - len(raw.lstrip())]
                apply = {"mode": "insert", "line": ln,
                         "text": f"{indent}clean {cname}"}
            raise AskError(
                what=f"The column {cname!r} is text, but you're doing math on it.",
                where=f"line {ln}",
                fix=f"Add a line just before it: clean {cname}",
                line=ln, apply=apply)
        return v
    return fix(left, node.left), fix(right, node.right)


def _eval_date_part(node, df, ln):
    import warnings
    s = _series_for_col(node.col, df, ln)
    with warnings.catch_warnings():
        # non-date text (the "not a date column" case, handled just below)
        # makes pandas warn about per-element parsing being slow - noise for
        # a case we already turn into a proper teaching error
        warnings.simplefilter("ignore", UserWarning)
        dt = pd.to_datetime(s, errors="coerce")
    if s.notna().any() and dt.isna().all():
        apply = None
        if context.interp is not None and ln in context.interp.raw_lines:
            raw = context.interp.raw_lines[ln]
            indent = raw[:len(raw) - len(raw.lstrip())]
            apply = {"mode": "insert", "line": ln,
                     "text": f"{indent}clean {node.col}"}
        raise AskError(
            what=f"The column {node.col!r} doesn't look like dates, so I "
                 f"can't get the {node.part} from it.",
            where=f"line {ln}",
            fix=f"Add a line just before it: clean {node.col}",
            line=ln, apply=apply)
    if node.part == "year":
        return dt.dt.year
    if node.part == "quarter":
        return dt.dt.quarter
    if node.part == "month":
        return dt.dt.month
    if node.part == "week":
        return dt.dt.isocalendar().week.astype("Int64")
    if node.part == "day":
        return dt.dt.day
    # weekday: a name ("Monday", ...), not a number - the point of asking in
    # plain English is to skip the "which number is Monday?" lookup
    return dt.dt.day_name()


def _eval_extract(node, df, ln):
    s = _series_for_col(node.col, df, ln).astype("string")
    if node.mode == "number":
        return s.str.extract(r"([-+]?\d[\d,]*\.?\d*)", expand=False).str.replace(",", "", regex=False)
    if node.mode == "after":
        marker = node.a

        def after(x):
            if x is pd.NA or x is None:
                return x
            idx = str(x).find(marker)
            return str(x)[idx + len(marker):] if idx >= 0 else str(x)
        return s.map(after).astype("string")
    if node.mode == "before":
        marker = node.a

        def before(x):
            if x is pd.NA or x is None:
                return x
            idx = str(x).find(marker)
            return str(x)[:idx] if idx >= 0 else str(x)
        return s.map(before).astype("string")
    if node.mode == "between":
        a, b = node.a, node.b

        def between(x):
            if x is pd.NA or x is None:
                return x
            xs = str(x)
            i = xs.find(a)
            if i < 0:
                return xs
            j = xs.find(b, i + len(a))
            if j < 0:
                return xs[i + len(a):]
            return xs[i + len(a):j]
        return s.map(between).astype("string")
    return s


def eval_condition(node, df, ln):
    """Evaluate a condition to a boolean pandas Series."""
    if isinstance(node, E.Logic):
        left = eval_condition(node.left, df, ln)
        right = eval_condition(node.right, df, ln)
        return (left & right) if node.op == "and" else (left | right)
    if isinstance(node, E.Not):
        return ~eval_condition(node.operand, df, ln)
    if isinstance(node, E.Compare):
        left = eval_expr(node.left, df, ln)
        right = eval_expr(node.right, df, ln)
        left, right = _compare_coerce(left, right)
        if node.op == "==":
            return left == right
        if node.op == "!=":
            return left != right
        if node.op == ">":
            return left > right
        if node.op == "<":
            return left < right
        if node.op == ">=":
            return left >= right
        if node.op == "<=":
            return left <= right
    if isinstance(node, E.Between):
        target = eval_expr(node.target, df, ln)
        low = eval_expr(node.low, df, ln)
        high = eval_expr(node.high, df, ln)
        return (target >= low) & (target <= high)
    if isinstance(node, E.In):
        target = eval_expr(node.target, df, ln)
        return target.isin(node.items)
    if isinstance(node, E.StrPred):
        target = eval_expr(node.target, df, ln).astype("string")
        arg = node.arg.v if isinstance(node.arg, E.Str) else str(eval_expr(node.arg, df, ln))
        if node.op == "contains":
            return target.str.contains(arg, na=False, regex=False)
        if node.op == "starts":
            return target.str.startswith(arg, na=False)
        if node.op == "ends":
            return target.str.endswith(arg, na=False)
    if isinstance(node, E.Missing):
        target = eval_expr(node.target, df, ln)
        m = target.isna()
        return ~m if node.negate else m
    # fallback: truthy expression
    val = eval_expr(node, df, ln)
    if isinstance(val, pd.Series):
        return val.astype(bool)
    return pd.Series([bool(val)] * len(df), index=df.index)


def _compare_coerce(left, right):
    # if one side is a numeric-looking text column and the other is a number, coerce
    if _is_texty(left) and isinstance(right, (int, float)) \
            and not isinstance(right, bool):
        conv = _to_numeric_series(left)
        if conv.notna().any():
            left = conv
    if _is_texty(right) and isinstance(left, (int, float)) \
            and not isinstance(left, bool):
        conv = _to_numeric_series(right)
        if conv.notna().any():
            right = conv
    return left, right
