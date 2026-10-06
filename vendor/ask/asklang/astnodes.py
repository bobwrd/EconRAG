"""AST: one Clause class per verb; the interpreter dispatches on class name."""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Clause:
    line: int = 0


@dataclass
class Load(Clause):
    path: str = ""


@dataclass
class FromTable(Clause):
    name: str = ""


@dataclass
class Save(Clause):
    path: str = ""


@dataclass
class Understand(Clause):
    pass


@dataclass
class Clean(Clause):
    col: str = ""


@dataclass
class Standardize(Clause):
    col: str = ""


@dataclass
class FillMissing(Clause):
    col: str = ""
    method: Any = None      # "average"/"previous"/"next"/"interpolate" or a literal value


@dataclass
class DropDuplicates(Clause):
    cols: list = field(default_factory=list)


@dataclass
class FlagOutliers(Clause):
    col: str = ""


@dataclass
class Keep(Clause):
    cols: list = field(default_factory=list)


@dataclass
class DropCols(Clause):
    cols: list = field(default_factory=list)


@dataclass
class Rename(Clause):
    old: str = ""
    new: str = ""


@dataclass
class Add(Clause):
    name: str = ""
    expr: Any = None              # expression AST (compute mode)
    astype: Optional[str] = None  # type name (astype mode)
    per_group: bool = False       # grouped transform (per/within group)


@dataclass
class Bin(Clause):
    col: str = ""
    labels: list = field(default_factory=list)


@dataclass
class Split(Clause):
    col: str = ""
    sep: str = ""
    into: list = field(default_factory=list)


@dataclass
class Expand(Clause):
    """`expand <col> by ","` - one row per delimited piece of `col`, the
    rest of that row duplicated (tidyr::separate_rows)."""
    col: str = ""
    sep: str = ""


@dataclass
class Replace(Clause):
    old: str = ""
    new: str = ""
    col: str = ""


@dataclass
class Where(Clause):
    condition: Any = None


@dataclass
class Combine(Clause):
    other: str = ""
    key: str = ""
    fuzzy: bool = False
    how: str = "left"       # inner | left | right | outer


@dataclass
class Stack(Clause):
    other: str = ""


@dataclass
class GroupBy(Clause):
    cols: list = field(default_factory=list)


@dataclass
class Agg:
    func: str
    col: Optional[str]
    name: str
    q: float = 0.5          # quantile level (for func == "quantile")
    method: str = "type7"   # quantile method: type7 (R default) | linear | nearest


@dataclass
class Show(Clause):
    aggs: list = field(default_factory=list)


@dataclass
class Reshape(Clause):
    mode: str = "wide"      # wide | long
    by: Optional[str] = None
    using: Optional[str] = None
    keeping: list = field(default_factory=list)
    aggregate: Optional[str] = None   # total/average/count/min/max/median/spread


@dataclass
class Resample(Clause):
    period: str = "month"


@dataclass
class SortBy(Clause):
    col: str = ""
    descending: bool = False


@dataclass
class Limit(Clause):
    mode: str = "top"       # top | bottom | first | last
    n: int = 5
    col: Optional[str] = None


@dataclass
class ChartMod:
    kind: str               # color | size | split | label | trendline | explain
    value: Any = None


@dataclass
class Chart(Clause):
    y: str = ""
    x: str = ""
    kind: str = "bar"
    mods: list = field(default_factory=list)


@dataclass
class RecipeCall(Clause):
    name: str = ""
    args: list = field(default_factory=list)


@dataclass
class TypeDef(Clause):
    name: str = ""
    variants: list = field(default_factory=list)   # sum type variants
    ordered: bool = True                            # A < B < C vs A | B | C


@dataclass
class RecordDef(Clause):
    name: str = ""
    fields: dict = field(default_factory=dict)


@dataclass
class RecipeDef(Clause):
    name: str = ""
    body: list = field(default_factory=list)
    params: list = field(default_factory=list)


@dataclass
class Explain(Clause):
    pass


@dataclass
class Compare(Clause):
    value: str = ""
    group: str = ""
    paired: bool = False   # `compare <value> and <group> paired` - both hold numbers


@dataclass
class Relate(Clause):
    a: str = ""
    b: str = ""
    controlling: Optional[str] = None      # partial correlation covariate
    matrix_cols: list = field(default_factory=list)   # `relate all of a, b, c`


@dataclass
class Predict(Clause):
    target: str = ""
    inputs: list = field(default_factory=list)
    interactions: list = field(default_factory=list)  # pairs (a, b) for a times b
    engine: Optional[str] = None                       # r package name (rbridge.py)
    polynomial: Optional[int] = None                   # `with polynomial n`


@dataclass
class SaveReport(Clause):
    path: str = ""


@dataclass
class ImportRecipes(Clause):
    path: str = ""


@dataclass
class SetSeed(Clause):
    seed: int = 0


@dataclass
class StrictMode(Clause):
    pass


@dataclass
class SaveNamed(Clause):
    # `call it NAME` - snapshot the current table into a named slot mid-pipeline.
    name: str = ""


@dataclass
class FunctionDef(Clause):
    # `define function name(a, b): <expr>` - a pure expression macro.
    name: str = ""
    params: list = field(default_factory=list)
    expr: Any = None


@dataclass
class Window(Clause):
    # A per-row window calculation that keeps group context (never collapses).
    op: str = ""            # running_total | moving_average | rank | lag | lead
    col: str = ""
    n: int = 1
    name: str = ""


@dataclass
class GroupFilter(Clause):
    # `keep top N within group` - a group-wise row filter.
    mode: str = "top"       # top | bottom
    n: int = 1
    col: Optional[str] = None


@dataclass
class Estimate(Clause):
    # `estimate average sales with bootstrap` - a resampled point + interval.
    func: str = "average"
    col: str = ""
    method: str = "bootstrap"


@dataclass
class ForEachGroup(Clause):
    # `for each region do recipe name` - apply a recipe per group, union results.
    group: str = ""
    recipe: str = ""
