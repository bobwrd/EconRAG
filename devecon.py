"""
Development-economics computations (ROADMAP Phase 2), pure Python + NumPy.

Every function returns plain numbers, lists, or dicts so tools can pass results
straight to the model and to verify.py. Conventions:
  - A series is a dict {year: value} or a list of (year, value) pairs; None/NaN
    values are treated as missing and dropped.
  - Growth rates and shares are in percent unless a name says otherwise;
    fgt() and the Lorenz functions return fractions (0-1).
  - Bad input (empty, mismatched lengths, non-positive values under a log)
    raises ValueError rather than returning a misleading number.

`python devecon.py` runs a few worked examples.
"""

import datetime
import math

import numpy as np


# ------------------------------------------------------------------ helpers
def _missing(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def _series(s):
    """Normalize a series to a sorted list of (int year, float value), dropping missing."""
    items = s.items() if isinstance(s, dict) else s
    out = sorted((int(y), float(v)) for y, v in items if not _missing(v))
    if len({y for y, _ in out}) != len(out):
        raise ValueError("duplicate years in series")
    return out


def _paired(values, weights):
    """Align values and weights (lists of equal length, or dicts by key); keep pairs where both exist."""
    if isinstance(values, dict):
        if weights is None:
            weights = {k: 1.0 for k in values}
        if not isinstance(weights, dict):
            raise ValueError("values is a dict, so weights must be a dict too")
        keys = list(values) + [k for k in weights if k not in values]
        pairs = [(values.get(k), weights.get(k)) for k in keys]
    else:
        values = list(values)
        weights = [1.0] * len(values) if weights is None else list(weights)
        if len(values) != len(weights):
            raise ValueError(f"values ({len(values)}) and weights ({len(weights)}) differ in length")
        pairs = list(zip(values, weights))
    return pairs


def _clean(values, weights=None):
    """Arrays of values and weights with any missing pair dropped; weights must be >= 0."""
    pairs = [(float(v), float(w)) for v, w in _paired(values, weights) if not _missing(v) and not _missing(w)]
    if not pairs:
        raise ValueError("no non-missing values")
    x, w = np.array(pairs).T
    if (w < 0).any():
        raise ValueError("weights must be non-negative")
    if w.sum() <= 0:
        raise ValueError("weights sum to zero")
    return x, w


def _positive(*vals):
    for v in vals:
        if _missing(v) or v <= 0:
            raise ValueError(f"needs positive values, got {v}")


# ------------------------------------------------------------------ growth
def cagr(start_value, end_value, years):
    """Compound annual growth rate, % = ((end/start)^(1/years) - 1) * 100."""
    _positive(start_value, end_value, years)
    return ((end_value / start_value) ** (1 / years) - 1) * 100


def growth_between(start, end):
    """Total and compound annual growth between two (year, value) points."""
    (y0, v0), (y1, v1) = start, end
    if y1 <= y0:
        raise ValueError("end year must be after start year")
    _positive(v0, v1)
    return {"start_year": y0, "end_year": y1, "years": y1 - y0,
            "total_growth_pct": (v1 / v0 - 1) * 100, "annual_growth_pct": cagr(v0, v1, y1 - y0)}


def annualized_growth(series, start_year=None, end_year=None, method="endpoints"):
    """Annual growth % over a gappy series: 'endpoints' = CAGR between first/last available
    years in range; 'least_squares' = exp(b) - 1 from OLS of ln(value) on year (World Bank method)."""
    pts = [(y, v) for y, v in _series(series)
           if (start_year is None or y >= start_year) and (end_year is None or y <= end_year)]
    if len(pts) < 2:
        raise ValueError("need at least two years with data")
    years = [y for y, _ in pts]
    out = {"first_year": years[0], "last_year": years[-1], "n_years_with_data": len(pts),
           "missing_years": sorted(set(range(years[0], years[-1] + 1)) - set(years))}
    if method == "endpoints":
        out["annual_growth_pct"] = growth_between(pts[0], pts[-1])["annual_growth_pct"]
    elif method == "least_squares":
        _positive(*(v for _, v in pts))
        b = np.polyfit(years, np.log([v for _, v in pts]), 1)[0]
        out["annual_growth_pct"] = (math.exp(b) - 1) * 100
    else:
        raise ValueError("method must be 'endpoints' or 'least_squares'")
    return out


def doubling_time(rate_pct):
    """Years to double at a constant annual rate: ln 2 / ln(1 + r) (exact, not the rule of 70)."""
    _positive(rate_pct)
    return math.log(2) / math.log(1 + rate_pct / 100)


# ------------------------------------------------------------------ prices and units
def rebase(series, base_year):
    """Index with base_year = 100: index_t = 100 * value_t / value_base."""
    s = dict(_series(series))
    if base_year not in s:
        raise ValueError(f"no value for base year {base_year}")
    if s[base_year] == 0:
        raise ValueError("base-year value is zero")
    return {y: 100 * v / s[base_year] for y, v in s.items()}


def real_from_nominal(nominal, deflator, base_year=None):
    """Constant-price series: nominal_t * deflator_base / deflator_t (in base_year prices);
    without base_year, the deflator is taken as already = 100 in its own base year."""
    n, d = dict(_series(nominal)), dict(_series(deflator))
    if base_year is not None:
        if base_year not in d:
            raise ValueError(f"no deflator value for base year {base_year}")
        d = rebase(d, base_year)
    years = sorted(set(n) & set(d))
    if not years:
        raise ValueError("nominal and deflator series share no years")
    _positive(*(d[y] for y in years))
    return {y: n[y] * 100 / d[y] for y in years}


def per_capita(total, population):
    """total / population, for two numbers or two series (years present in both)."""
    if isinstance(total, (dict, list, tuple)):
        t, p = dict(_series(total)), dict(_series(population))
        years = sorted(set(t) & set(p))
        if not years:
            raise ValueError("total and population share no years")
        return {y: per_capita(t[y], p[y]) for y in years}
    _positive(population)
    return total / population


def ppp_convert(value_lcu, ppp_lcu_per_intl_dollar):
    """Local currency -> PPP international dollars: value_lcu / PPP factor (LCU per international $)."""
    _positive(ppp_lcu_per_intl_dollar)
    return value_lcu / ppp_lcu_per_intl_dollar


def market_rate_convert(value_lcu, lcu_per_usd):
    """Local currency -> US dollars at the market exchange rate: value_lcu / (LCU per US$)."""
    _positive(lcu_per_usd)
    return value_lcu / lcu_per_usd


# ------------------------------------------------------------------ averages and coverage
def weighted_mean(values, weights):
    """sum(w * x) / sum(w), over pairs where both value and weight exist."""
    x, w = _clean(values, weights)
    return float((x * w).sum() / w.sum())


def group_averages(values, weights):
    """Simple vs weighted (e.g. population-weighted) mean over the same members, with coverage:
    members missing a value or weight are dropped from both means."""
    pairs = _paired(values, weights)
    known_w = sum(float(w) for _, w in pairs if not _missing(w))
    use = [(float(v), float(w)) for v, w in pairs if not _missing(v) and not _missing(w)]
    out = {"n_members": len(pairs), "n_used": len(use),
           "n_missing_value": sum(_missing(v) for v, _ in pairs),
           "n_missing_weight": sum(_missing(w) for _, w in pairs),
           "simple_mean": None, "weighted_mean": None, "weight_coverage_pct": 0.0}
    if use and known_w > 0:
        x, w = np.array(use).T
        out["simple_mean"] = float(x.mean())
        out["weighted_mean"] = float((x * w).sum() / w.sum()) if w.sum() > 0 else None
        out["weight_coverage_pct"] = float(w.sum() / known_w * 100)
    return out


# ------------------------------------------------------------------ latest available data
def latest_available(series, max_age=None, as_of=None):
    """Most recent non-missing (year, value) and its age in years (as_of defaults to this year);
    None if nothing, or nothing within max_age years."""
    s = _series(series)
    if not s:
        return None
    as_of = datetime.date.today().year if as_of is None else as_of
    pts = [(y, v) for y, v in s if y <= as_of]
    if not pts:
        return None
    year, value = pts[-1]
    if max_age is not None and as_of - year > max_age:
        return None
    return {"year": year, "value": value, "age_years": as_of - year}


def common_latest_year(series_by_country, min_countries=None):
    """Latest year with data for every country (or at least min_countries), for like-for-like comparisons."""
    data = {c: dict(_series(s)) for c, s in series_by_country.items()}
    if not data:
        raise ValueError("no countries")
    need = len(data) if min_countries is None else min_countries
    for year in sorted({y for s in data.values() for y in s}, reverse=True):
        have = [c for c, s in data.items() if year in s]
        if len(have) >= need:
            return {"year": year, "countries": have, "missing": [c for c in data if c not in have],
                    "values": {c: data[c][year] for c in have}}
    return None


# ------------------------------------------------------------------ poverty (Foster-Greer-Thorbecke)
def fgt(incomes, poverty_line, alpha, weights=None):
    """FGT index (fraction): sum over the poor (income < line) of w * ((z - y) / z)^alpha, / sum(w).
    alpha 0 = headcount ratio, 1 = poverty gap index, 2 = squared poverty gap."""
    _positive(poverty_line)
    if alpha < 0:
        raise ValueError("alpha must be >= 0")
    y, w = _clean(incomes, weights)
    poor = y < poverty_line
    gap = np.where(poor, (poverty_line - y) / poverty_line, 0.0)
    return float((w * poor * gap ** alpha).sum() / w.sum())


def poverty_measures(incomes, poverty_line, weights=None, population=None):
    """Headcount ratio, poverty gap, squared poverty gap (all %) and, given population, number of poor."""
    out = {f"{k}_pct": fgt(incomes, poverty_line, a, weights) * 100
           for k, a in (("headcount_ratio", 0), ("poverty_gap", 1), ("squared_poverty_gap", 2))}
    if population is not None:
        out["number_poor"] = number_poor(out["headcount_ratio_pct"], population)
    return out


def number_poor(headcount_ratio_pct, population):
    """Number of poor people = headcount ratio (%) / 100 * population."""
    if not 0 <= headcount_ratio_pct <= 100:
        raise ValueError("headcount ratio must be 0-100%")
    return headcount_ratio_pct / 100 * population


# ------------------------------------------------------------------ inequality
def lorenz_curve(incomes, weights=None):
    """Lorenz points [(cumulative population share, cumulative income share)], from (0, 0) to (1, 1)."""
    y, w = _clean(incomes, weights)
    if (y < 0).any():
        raise ValueError("incomes must be non-negative")
    if (y * w).sum() <= 0:
        raise ValueError("total income is zero")
    order = np.argsort(y, kind="stable")
    y, w = y[order], w[order]
    p = np.concatenate([[0], np.cumsum(w) / w.sum()])
    l = np.concatenate([[0], np.cumsum(y * w) / (y * w).sum()])
    return [(float(a), float(b)) for a, b in zip(p, l)]


def gini_from_lorenz(points):
    """Gini = 1 - sum (p_k - p_k-1)(L_k + L_k-1), i.e. 1 - 2 * area under the piecewise-linear Lorenz curve."""
    pts = sorted(points)
    if pts[0] != (0.0, 0.0):
        pts = [(0.0, 0.0)] + pts
    if abs(pts[-1][0] - 1) > 1e-9 or abs(pts[-1][1] - 1) > 1e-9:
        raise ValueError("Lorenz curve must end at (1, 1)")
    return 1 - sum((p1 - p0) * (l1 + l0) for (p0, l0), (p1, l1) in zip(pts, pts[1:]))


def gini(incomes, weights=None):
    """Gini (0-1) from (weighted) microdata, population convention: sum_ij w_i w_j |y_i - y_j| / (2 W^2 mean).
    Equals 1 - 2 * Lorenz area; maximum for n equal-weight people is (n-1)/n, e.g. [0,0,0,1] -> 0.75."""
    return gini_from_lorenz(lorenz_curve(incomes, weights))


def _cumulate_shares(shares, pop_shares=None):
    shares = [float(s) for s in shares]
    if not shares or any(_missing(s) or s < 0 for s in shares):
        raise ValueError("shares must be non-negative numbers")
    pop = [1 / len(shares)] * len(shares) if pop_shares is None else [float(p) for p in pop_shares]
    if len(pop) != len(shares):
        raise ValueError("shares and pop_shares differ in length")
    s, p = sum(shares), sum(pop)
    if abs(s - 100) < 0.5:  # accept percentages
        shares = [x / 100 for x in shares]
    elif abs(s - 1) > 0.005:
        raise ValueError(f"income shares sum to {s}, not 1 or 100")
    if abs(p - 100) < 0.5:
        pop = [x / 100 for x in pop]
    elif abs(p - 1) > 0.005:
        raise ValueError(f"population shares sum to {p}, not 1 or 100")
    ps, ls = np.cumsum(pop), np.cumsum(shares)
    return [(0.0, 0.0)] + [(float(a) / ps[-1], float(b) / ls[-1]) for a, b in zip(ps, ls)]


def gini_from_shares(shares, pop_shares=None):
    """Gini from grouped income shares (poorest group first; quintiles/deciles by default, % or fractions).
    Assumes equality within groups, so it is a lower bound on the microdata Gini."""
    return gini_from_lorenz(_cumulate_shares(shares, pop_shares))


def lorenz_at(points, p):
    """Cumulative income share held by the poorest fraction p, interpolating linearly along the Lorenz curve."""
    if not 0 <= p <= 1:
        raise ValueError("p must be in [0, 1]")
    xs, ys = zip(*sorted(points))
    return float(np.interp(p, xs, ys))


def top_share(incomes, weights=None, top=0.10):
    """Share (%) of total income held by the richest `top` fraction of people (1 - L(1 - top))."""
    return (1 - lorenz_at(lorenz_curve(incomes, weights), 1 - top)) * 100


def palma(incomes, weights=None):
    """Palma ratio = income share of the top 10% / income share of the bottom 40%."""
    pts = lorenz_curve(incomes, weights)
    bottom = lorenz_at(pts, 0.4)
    if bottom <= 0:
        raise ValueError("bottom 40% holds no income")
    return (1 - lorenz_at(pts, 0.9)) / bottom


def palma_from_deciles(decile_shares):
    """Palma ratio from 10 decile shares (poorest first): D10 / (D1 + D2 + D3 + D4)."""
    if len(decile_shares) != 10:
        raise ValueError("need exactly 10 decile shares")
    bottom = sum(decile_shares[:4])
    if bottom <= 0:
        raise ValueError("bottom 40% holds no income")
    return decile_shares[9] / bottom


# ------------------------------------------------------------------ growth decomposition
def growth_decomposition(start, end, years):
    """Annual log growth (100 * ln(x1/x0) / years) of GDP per capita = labor productivity (GDP per worker)
    + employment-to-population ratio, exact in logs. start/end: dicts with gdp, population, employment."""
    _positive(years)
    for d in (start, end):
        _positive(d["gdp"], d["population"], d["employment"])

    def g(f):
        return 100 * math.log(f(end) / f(start)) / years

    total = g(lambda d: d["gdp"] / d["population"])
    prod = g(lambda d: d["gdp"] / d["employment"])
    emp = g(lambda d: d["employment"] / d["population"])
    return {"gdp_per_capita_growth_pct": total, "labor_productivity_growth_pct": prod,
            "employment_ratio_growth_pct": emp,
            "productivity_share_pct": prod / total * 100 if abs(total) > 1e-12 else None,
            "note": "annualized log growth rates; they add up exactly (CAGRs would not)"}


# ------------------------------------------------------------------ convergence
def beta_convergence(initial, final, years):
    """OLS of annual log growth (100 * ln(y_T/y_0) / T) on ln(y_0) across countries. Slope < 0 = beta-convergence;
    speed lambda solves slope/100 = -(1 - e^(-lambda T)) / T (Barro & Sala-i-Martin), half-life = ln 2 / lambda."""
    _positive(years)
    if isinstance(initial, dict):
        keys = [k for k in initial if k in final and not _missing(initial[k]) and not _missing(final[k])]
        y0, y1 = [initial[k] for k in keys], [final[k] for k in keys]
    else:
        if len(initial) != len(final):
            raise ValueError("initial and final differ in length")
        pairs = [(a, b) for a, b in zip(initial, final) if not _missing(a) and not _missing(b)]
        y0, y1 = [a for a, _ in pairs], [b for _, b in pairs]
    if len(y0) < 3:
        raise ValueError("need at least 3 countries")
    _positive(*y0, *y1)
    x = np.log(y0)
    g = 100 * np.log(np.array(y1) / np.array(y0)) / years
    if np.ptp(x) == 0:
        raise ValueError("initial incomes are all equal")
    slope, intercept = np.polyfit(x, g, 1)
    r = float(np.corrcoef(x, g)[0, 1]) if np.ptp(g) > 0 else 0.0
    out = {"slope": float(slope), "intercept": float(intercept), "r": r, "n": len(y0),
           "speed_pct": None, "half_life_years": None,
           "slope_meaning": "percentage points of annual growth per 1-unit higher ln(initial income)"}
    inside = 1 + slope / 100 * years
    if slope < 0 and inside > 0:
        lam = -math.log(inside) / years
        out["speed_pct"] = lam * 100
        out["half_life_years"] = math.log(2) / lam
    return out


def sigma_convergence(series_by_country, years=None, balanced=True):
    """Cross-country standard deviation (ddof=0) of ln(income) by year; falling = sigma-convergence.
    balanced=True keeps only countries with data in every requested year, so the sample is fixed."""
    data = {c: dict(_series(s)) for c, s in series_by_country.items()}
    if years is None:
        years = sorted({y for s in data.values() for y in s})
    if balanced:
        data = {c: s for c, s in data.items() if all(y in s for y in years)}
    out = {}
    for y in years:
        vals = [s[y] for s in data.values() if y in s]
        if len(vals) < 2:
            continue
        _positive(*vals)
        out[y] = {"sd_log": float(np.std(np.log(vals))), "n": len(vals)}
    return out


if __name__ == "__main__":
    print("CAGR 100 -> 200 over 10 years:", round(cagr(100, 200, 10), 3), "%")
    print("Doubling time at 7%:", round(doubling_time(7), 2), "years")
    print("FGT, incomes [1,2,3,4], line 3:", poverty_measures([1, 2, 3, 4], 3))
    print("Gini [0,0,0,1]:", gini([0, 0, 0, 1]))
    print("Gini from quintiles [5,10,15,25,45]:", round(gini_from_shares([5, 10, 15, 25, 45]), 3))
