"""Numeric pinning against reference values (scipy/statsmodels ground truth)."""

import numpy as np
import pytest

from asklang import AskError
from asklang.stats import (chi_square_independence, check_collinearity,
                           hedges_g, linear_regression, mann_whitney_u,
                           one_way_anova, paired_t, partial_correlation,
                           pearson_r, quantile, two_proportion_z, two_sample_t,
                           t_crit, welford_var)

A = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], float)
B = np.array([2, 4, 6, 8, 10, 12, 14, 16, 18, 20], float)


def test_welch_t_matches_scipy():
    # scipy.stats.ttest_ind(A, B, equal_var=False)
    t, df, p = two_sample_t(A, B)
    assert t == pytest.approx(-2.5690, abs=1e-3)
    assert df == pytest.approx(13.2353, abs=1e-2)
    assert p == pytest.approx(0.02307, abs=1e-4)


def test_anova_matches_reference():
    # hand-computed: ssb=151.25, ssw=412.5 -> F = 151.25 / (412.5/18) = 6.6
    f, d1, d2, p, eta2 = one_way_anova([A, B])
    assert f == pytest.approx(6.6, abs=1e-6)
    assert (d1, d2) == (1, 18)
    assert p == pytest.approx(0.01931, abs=1e-4)
    assert eta2 == pytest.approx(151.25 / 563.75, abs=1e-6)


def test_chi_square_matches_scipy_uncorrected():
    # scipy.stats.chi2_contingency([[10,20],[30,40]], correction=False)
    chi, dof, p, min_exp, v = chi_square_independence([[10, 20], [30, 40]])
    assert chi == pytest.approx(0.79365, abs=1e-4)
    assert dof == 1
    assert p == pytest.approx(0.37301, abs=1e-4)
    assert min_exp == pytest.approx(12.0)
    assert 0 <= v <= 1


def test_chi_square_low_expected_flagged():
    _, _, _, min_exp, _ = chi_square_independence([[1, 2], [3, 4]])
    assert min_exp < 5


def test_two_proportion_z_matches_hand_computation():
    # hand-computed: p1=.6, p2=.4, pooled se=sqrt(.5*.5*(1/50+1/50))=.1, z=2.0
    diff, lo, hi, z, p = two_proportion_z(30, 50, 20, 50)
    assert diff == pytest.approx(0.2, abs=1e-9)
    assert z == pytest.approx(2.0, abs=1e-6)
    assert p == pytest.approx(0.04550, abs=1e-4)
    assert lo < diff < hi


def test_two_proportion_z_no_difference():
    diff, lo, hi, z, p = two_proportion_z(25, 50, 25, 50)
    assert diff == pytest.approx(0.0)
    assert p == pytest.approx(1.0, abs=1e-9)


def test_partial_correlation_matches_formula_and_shrinks_confound():
    rng = np.random.default_rng(42)
    z = rng.normal(size=200)
    a = z + rng.normal(scale=0.5, size=200)
    b = z + rng.normal(scale=0.5, size=200)
    r_ab, r_partial, n, p = partial_correlation(a, b, z)
    r_ab_ref = np.corrcoef(a, b)[0, 1]
    r_az = np.corrcoef(a, z)[0, 1]
    r_bz = np.corrcoef(b, z)[0, 1]
    expected = (r_ab_ref - r_az * r_bz) / np.sqrt((1 - r_az ** 2) * (1 - r_bz ** 2))
    assert r_ab == pytest.approx(r_ab_ref)
    assert r_partial == pytest.approx(expected)
    assert n == 200
    # a and b are only correlated through the shared cause z -> controlling
    # for it should shrink the correlation substantially
    assert abs(r_partial) < abs(r_ab) * 0.5


def test_paired_t_matches_hand_computation():
    # d = [1,1,1,1,1] -> zero variance -> t undefined by the usual formula;
    # use a case with real variance instead:
    a = np.array([10.0, 12.0, 9.0, 14.0, 11.0])
    b = np.array([8.0, 11.0, 10.0, 12.0, 9.0])
    d = a - b   # [2, 1, -1, 2, 2] -> mean 1.2, sd(ddof=1) ~1.4832
    t, dof, p = paired_t(a, b)
    assert dof == 4
    assert t == pytest.approx(d.mean() / (d.std(ddof=1) / np.sqrt(5)), abs=1e-9)


def test_quantile_type7_matches_numpy():
    x = np.arange(1, 11, dtype=float)
    for q in (0.1, 0.25, 0.5, 0.9):
        assert quantile(x, q) == pytest.approx(np.percentile(x, q * 100))


def test_ols_recovers_coefficients():
    rng = np.random.default_rng(0)
    n = 200
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    y = 2 + 3 * x1 - 1.5 * x2 + rng.normal(scale=0.5, size=n)
    X = np.column_stack([np.ones(n), x1, x2])
    r = linear_regression(y, X, ["b0", "x1", "x2"])
    assert r["coef"][0] == pytest.approx(2, abs=0.15)
    assert r["coef"][1] == pytest.approx(3, abs=0.15)
    assert r["coef"][2] == pytest.approx(-1.5, abs=0.15)
    assert r["r2"] > 0.9


def test_t_crit_matches_table():
    assert t_crit(10) == pytest.approx(2.2281, abs=1e-3)
    assert t_crit(30) == pytest.approx(2.0423, abs=1e-3)


def test_welford_matches_numpy():
    x = np.random.default_rng(1).normal(size=500) * 1e6 + 1e9
    assert welford_var(x) == pytest.approx(x.var(ddof=1), rel=1e-9)


def test_mann_whitney_reasonable():
    u, p = mann_whitney_u(A, B)
    assert 0 <= p <= 1


def test_hedges_g_smaller_than_d():
    from asklang.stats import cohens_d
    d, g = cohens_d(A, B), hedges_g(A, B)
    assert abs(g) < abs(d)


def test_collinearity_detected():
    """C1: rank-deficient design must raise, naming the culprits."""
    n = 50
    x = np.random.default_rng(2).normal(size=n)
    X = np.column_stack([np.ones(n), x, x])
    with pytest.raises(AskError, match="cost|x1|same information"):
        check_collinearity(X, ["(baseline)", "cost", "c2"])


def test_collinearity_constant_detected():
    n = 50
    x = np.random.default_rng(3).normal(size=n)
    X = np.column_stack([np.ones(n), x, np.full(n, 7.0)])
    with pytest.raises(AskError, match="constant"):
        check_collinearity(X, ["(baseline)", "x", "seven"])


def test_predict_collinear_via_language(run):
    with pytest.raises(AskError, match="same information"):
        run('load "sales.csv"\nadd c2 = cost\npredict revenue from cost and c2')


def test_compare_reports_welch_and_hedges(run):
    t, ch, notices, tr = run('load "sales.csv"\n'
                             'where region is "West" or region is "East"\n'
                             'compare revenue between region')
    text = " ".join(notices)
    assert "Welch" in text and "Hedges" in text


def test_anova_reports_eta_squared(run):
    t, ch, notices, tr = run('load "sales.csv"\ncompare revenue between region')
    assert any("eta-squared" in n for n in notices)


def test_pearson_perfect():
    r, n, p = pearson_r(A, B)
    assert r == pytest.approx(1.0)
    assert p < 1e-10
