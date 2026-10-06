"""
devecon.py tests: hand-computed textbook examples, no network, well under a second.

    .venv/bin/python tests/test_devecon.py          # all
    .venv/bin/python tests/test_devecon.py gini     # only tests whose name contains "gini"

Plain asserts, no pytest needed (pytest also collects these if installed).
"""

import math
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import devecon as d  # noqa: E402


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def raises(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError:
        return True
    return False


# ------------------------------------------------------------------ growth
def test_cagr_doubling_over_ten_years():
    assert close(d.cagr(100, 200, 10), 7.177346253629313)
    assert close(d.cagr(100, 100, 5), 0)
    assert d.cagr(200, 100, 10) < 0


def test_cagr_rejects_non_positive():
    assert raises(d.cagr, 0, 100, 10)
    assert raises(d.cagr, -5, 100, 10)
    assert raises(d.cagr, 100, 200, 0)


def test_growth_between_points():
    g = d.growth_between((2000, 100), (2010, 200))
    assert g["years"] == 10 and close(g["total_growth_pct"], 100)
    assert close(g["annual_growth_pct"], d.cagr(100, 200, 10))
    assert raises(d.growth_between, (2010, 100), (2000, 200))


def test_annualized_growth_with_gaps():
    s = {2000: 100, 2001: None, 2003: float("nan"), 2005: 100 * 1.05 ** 5, 2010: 100 * 1.05 ** 10}
    e = d.annualized_growth(s)
    assert (e["first_year"], e["last_year"], e["n_years_with_data"]) == (2000, 2010, 3)
    assert close(e["annual_growth_pct"], 5)
    assert 2001 in e["missing_years"] and 2005 not in e["missing_years"]
    ls = d.annualized_growth(s, method="least_squares")
    assert close(ls["annual_growth_pct"], 5, 1e-9)
    assert close(d.annualized_growth(s, end_year=2005)["annual_growth_pct"], 5)
    assert raises(d.annualized_growth, {2000: 1})
    assert raises(d.annualized_growth, s, method="bogus")


def test_least_squares_differs_from_endpoints_on_bumpy_series():
    s = [(2000, 100), (2001, 130), (2002, 110), (2003, 121)]
    assert close(d.annualized_growth(s)["annual_growth_pct"], d.cagr(100, 121, 3))
    assert not close(d.annualized_growth(s, method="least_squares")["annual_growth_pct"], d.cagr(100, 121, 3), 0.01)


def test_doubling_time():
    assert close(d.doubling_time(7), math.log(2) / math.log(1.07))
    assert close(d.doubling_time(100), 1)
    assert raises(d.doubling_time, 0) and raises(d.doubling_time, -2)


# ------------------------------------------------------------------ prices and units
def test_rebase():
    assert d.rebase({2010: 50, 2015: 100, 2020: 150}, 2015) == {2010: 50.0, 2015: 100.0, 2020: 150.0}
    assert d.rebase([(2010, 80), (2020, 120)], 2010)[2020] == 150
    assert raises(d.rebase, {2010: 50}, 2015)
    assert raises(d.rebase, {2010: 0, 2011: 5}, 2010)


def test_real_from_nominal():
    nominal, deflator = {2010: 100, 2020: 150}, {2010: 100, 2020: 125, 2021: 130}
    assert d.real_from_nominal(nominal, deflator) == {2010: 100.0, 2020: 120.0}
    r = d.real_from_nominal(nominal, deflator, base_year=2020)
    assert close(r[2010], 125) and close(r[2020], 150)
    assert raises(d.real_from_nominal, {1990: 1}, deflator)


def test_per_capita():
    assert d.per_capita(1e9, 4e6) == 250
    assert d.per_capita({2000: 100, 2001: 120}, {2000: 10, 2001: 12, 2002: 13}) == {2000: 10.0, 2001: 10.0}
    assert raises(d.per_capita, 100, 0)


def test_ppp_and_market_conversion():
    assert d.ppp_convert(2000, 20) == 100       # 2000 rupees at 20 LCU per international $
    assert d.market_rate_convert(2000, 80) == 25  # same rupees at 80 per US$
    assert raises(d.ppp_convert, 100, 0) and raises(d.market_rate_convert, 100, -1)


# ------------------------------------------------------------------ averages
def test_weighted_mean_skips_missing_pairs():
    assert d.weighted_mean([10, 20, 30], [1, 1, 2]) == 22.5
    assert d.weighted_mean([10, None, 30, 40], [1, 5, 1, None]) == 20
    assert raises(d.weighted_mean, [1, 2], [1])
    assert raises(d.weighted_mean, [None], [1])
    assert raises(d.weighted_mean, [1, 2], [0, 0])
    assert raises(d.weighted_mean, [1, 2], [1, -1])


def test_group_averages_simple_vs_weighted_with_coverage():
    g = d.group_averages({"A": 10, "B": 20, "C": None, "D": 40}, {"A": 1, "B": 3, "C": 2, "D": None})
    assert g["n_used"] == 2 and g["n_missing_value"] == 1 and g["n_missing_weight"] == 1
    assert g["simple_mean"] == 15 and g["weighted_mean"] == 17.5
    assert close(g["weight_coverage_pct"], 4 / 6 * 100)
    empty = d.group_averages([None, None], [1, 2])
    assert empty["simple_mean"] is None and empty["weight_coverage_pct"] == 0
    assert raises(d.group_averages, [1, 2, 3], [1, 2])


# ------------------------------------------------------------------ latest available
def test_latest_available_and_max_age():
    s = {2015: 1.0, 2019: 2.0, 2021: None, 2030: 9.0}
    assert d.latest_available(s, as_of=2024) == {"year": 2019, "value": 2.0, "age_years": 5}
    assert d.latest_available(s, max_age=4, as_of=2024) is None
    assert d.latest_available({}, as_of=2024) is None
    assert d.latest_available({2023: 5}, as_of=2023)["age_years"] == 0


def test_common_latest_year():
    data = {"KEN": {2019: 1, 2021: 2}, "UGA": {2019: 3, 2020: 4}, "TZA": {2018: 5, 2019: 6, 2021: 7}}
    c = d.common_latest_year(data)
    assert c["year"] == 2019 and c["missing"] == [] and c["values"]["UGA"] == 3
    c2 = d.common_latest_year(data, min_countries=2)
    assert c2["year"] == 2021 and c2["missing"] == ["UGA"]
    assert d.common_latest_year({"A": {2000: 1}, "B": {2001: 1}}) is None


# ------------------------------------------------------------------ poverty
def test_fgt_worked_example():
    # incomes 1,2,3,4; line 3: poor are 1 and 2 (3 is not below the line)
    # gaps (3-1)/3 = 2/3 and 1/3 -> P0 = 2/4, P1 = (2/3 + 1/3)/4 = 1/4, P2 = (4/9 + 1/9)/4 = 5/36
    y = [1, 2, 3, 4]
    assert d.fgt(y, 3, 0) == 0.5
    assert close(d.fgt(y, 3, 1), 0.25)
    assert close(d.fgt(y, 3, 2), 5 / 36)
    m = d.poverty_measures(y, 3, population=1_000_000)
    assert close(m["headcount_ratio_pct"], 50) and close(m["poverty_gap_pct"], 25)
    assert close(m["squared_poverty_gap_pct"], 500 / 36) and close(m["number_poor"], 500_000)


def test_fgt_weighted_matches_expanded():
    assert close(d.fgt([1, 2, 4], 3, 2, weights=[2, 1, 1]), d.fgt([1, 1, 2, 4], 3, 2))
    assert close(d.fgt([1, None, 4], 3, 1), d.fgt([1, 4], 3, 1))


def test_fgt_edge_cases():
    assert d.fgt([5, 6], 3, 0) == 0
    assert d.fgt([0, 0], 3, 1) == 1  # zero income = full gap
    assert raises(d.fgt, [], 3, 0)
    assert raises(d.fgt, [1, 2], 0, 0)
    assert raises(d.fgt, [1, 2], 3, 1, weights=[1])
    assert raises(d.number_poor, 120, 100)


# ------------------------------------------------------------------ inequality
def test_gini_textbook_values():
    assert d.gini([5, 5, 5, 5]) == 0
    assert close(d.gini([0, 0, 0, 1]), 0.75)  # population convention: max is (n-1)/n
    assert close(d.gini([1, 2, 3, 4]), 0.25)  # sum|xi-xj| = 20 over 2 * 16 * 2.5
    assert close(d.gini([4, 1, 3, 2]), 0.25)  # order-independent


def test_gini_weighted_matches_expanded_and_mean_difference():
    assert close(d.gini([1, 2], weights=[3, 1]), 0.15)
    assert close(d.gini([1, 2], weights=[3, 1]), d.gini([1, 1, 1, 2]))
    y = [3.0, 7.0, 1.0, 12.0, 5.0]
    mean_diff = sum(abs(a - b) for a in y for b in y) / (2 * len(y) ** 2 * (sum(y) / len(y)))
    assert close(d.gini(y), mean_diff)


def test_gini_edge_cases():
    assert raises(d.gini, [])
    assert raises(d.gini, [0, 0])
    assert raises(d.gini, [-1, 5])
    assert raises(d.gini, [1, 2, 3], weights=[1, 1])


def test_lorenz_curve_points():
    assert d.lorenz_curve([3, 1]) == [(0.0, 0.0), (0.5, 0.25), (1.0, 1.0)]
    assert raises(d.gini_from_lorenz, [(0, 0), (0.5, 0.2)])


def test_gini_from_quintile_shares():
    # cumulative shares .05 .15 .30 .55 1 -> 1 - 0.2 * (.05 + .20 + .45 + .85 + 1.55) = 0.38
    assert close(d.gini_from_shares([5, 10, 15, 25, 45]), 0.38)
    assert close(d.gini_from_shares([0.05, 0.10, 0.15, 0.25, 0.45]), 0.38)
    assert close(d.gini_from_shares([10] * 10), 0)
    # grouped shares give a lower bound on the microdata Gini
    y = list(range(1, 101))
    q = [sum(y[i:i + 20]) / sum(y) for i in range(0, 100, 20)]
    assert d.gini_from_shares(q) <= d.gini(y)
    assert raises(d.gini_from_shares, [10, 20, 30])
    assert raises(d.gini_from_shares, [50, 50], pop_shares=[1.0])


def test_top_share_and_palma():
    y = list(range(1, 11))  # total 55; top person holds 10, bottom four hold 10
    assert close(d.top_share(y), 10 / 55 * 100)
    assert close(d.palma(y), 1.0)
    assert close(d.palma_from_deciles([2, 3, 4, 5, 6, 7, 8, 10, 15, 40]), 40 / 14)
    assert raises(d.palma_from_deciles, [10] * 9)
    assert raises(d.palma, [0, 0, 0, 0, 0, 0, 0, 0, 0, 1])


# ------------------------------------------------------------------ decomposition
def test_growth_decomposition_adds_up_in_logs():
    g = d.growth_decomposition({"gdp": 100, "population": 10, "employment": 5},
                               {"gdp": 200, "population": 10, "employment": 8}, 10)
    assert close(g["gdp_per_capita_growth_pct"], 10 * math.log(2))
    assert close(g["employment_ratio_growth_pct"], 10 * math.log(1.6))
    assert close(g["labor_productivity_growth_pct"], 10 * math.log(1.25))
    assert close(g["gdp_per_capita_growth_pct"],
                 g["labor_productivity_growth_pct"] + g["employment_ratio_growth_pct"])
    assert raises(d.growth_decomposition, {"gdp": 0, "population": 1, "employment": 1},
                  {"gdp": 1, "population": 1, "employment": 1}, 5)


# ------------------------------------------------------------------ convergence
def test_beta_convergence_exact_line():
    # growth = 12 - 1 * ln(y0) exactly -> slope -1, r = -1; lambda = -ln(1 - 0.01 * 20) / 20
    T, xs = 20, [7, 8, 9, 10]
    initial = {f"c{i}": math.exp(x) for i, x in enumerate(xs)}
    final = {k: v * math.exp((12 - math.log(v)) * T / 100) for k, v in initial.items()}
    b = d.beta_convergence(initial, final, T)
    assert close(b["slope"], -1, 1e-9) and close(b["intercept"], 12, 1e-8)
    assert close(b["r"], -1, 1e-9) and b["n"] == 4
    lam = -math.log(0.8) / 20
    assert close(b["speed_pct"], lam * 100, 1e-9) and close(b["half_life_years"], math.log(2) / lam, 1e-6)


def test_beta_divergence_has_no_speed():
    b = d.beta_convergence([100, 1000, 10000], [200, 4000, 80000], 10)
    assert b["slope"] > 0 and b["speed_pct"] is None and b["half_life_years"] is None


def test_beta_convergence_edge_cases():
    assert raises(d.beta_convergence, [1, 2], [2, 3], 10)
    assert raises(d.beta_convergence, [1, 2, 3], [2, 3], 10)
    assert raises(d.beta_convergence, [0, 2, 3], [2, 3, 4], 10)
    assert raises(d.beta_convergence, [5, 5, 5], [6, 7, 8], 10)


def test_sigma_convergence():
    data = {"A": {2000: 100, 2010: 200}, "B": {2000: 100 * math.e ** 2, 2010: 200 * math.e},
            "C": {2010: 1}}
    s = d.sigma_convergence(data)
    assert s[2000]["n"] == 2 and close(s[2000]["sd_log"], 1) and close(s[2010]["sd_log"], 0.5)
    unbalanced = d.sigma_convergence(data, balanced=False)
    assert unbalanced[2010]["n"] == 3
    assert raises(d.sigma_convergence, {"A": {2000: 0}, "B": {2000: 1}})


# ------------------------------------------------------------------ runner
def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and pattern in n]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
