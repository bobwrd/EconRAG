"""Statistics — numpy-only; no scipy/statsmodels/sklearn.

Design decision: the target machine has only pandas + matplotlib (which bring
numpy). scipy/statsmodels/sklearn are NOT assumed present. Every test and
p-value here is computed from numpy primitives. p-values come from the
regularized incomplete beta / gamma functions (Numerical-Recipes style), which
give the t, F and chi-square tail probabilities we need. The core results
(OLS, Welch t, ANOVA, chi-square, type-7 quantiles) are pinned against
reference values in tests/test_stats.py.
"""

import math

import numpy as np
import pandas as pd

from .errors import AskError


def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3.0e-12, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _gammq(a, x):
    """Upper regularized incomplete gamma Q(a, x) = 1 - P(a, x)."""
    if x <= 0.0:
        return 1.0
    if x < a + 1.0:                       # series representation for P
        ap, s, term = a, 1.0 / a, 1.0 / a
        for _ in range(500):
            ap += 1.0
            term *= x / ap
            s += term
            if abs(term) < abs(s) * 1.0e-12:
                break
        p = s * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return 1.0 - p
    # continued fraction for Q
    FPMIN = 1.0e-300
    b = x + 1.0 - a
    c = 1.0 / FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < FPMIN:
            d = FPMIN
        c = b + an / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1.0e-12:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _t_two_sided_p(t, df):
    if df <= 0:
        return float("nan")
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def _f_p(f, d1, d2):
    if f <= 0 or d1 <= 0 or d2 <= 0:
        return 1.0
    return _betai(d2 / 2.0, d1 / 2.0, d2 / (d2 + d1 * f))


def _chi2_p(x, k):
    if k <= 0:
        return float("nan")
    return _gammq(k / 2.0, x / 2.0)


def p_phrase(p):
    """Turn a p-value into beginner-friendly words."""
    if p != p:  # NaN
        return "not enough data to judge if this is chance"
    if p < 0.001:
        return "very unlikely to be chance (p < 0.001)"
    if p < 0.01:
        return f"unlikely to be chance (p = {p:.3f})"
    if p < 0.05:
        return f"probably not just chance (p = {p:.3f})"
    if p < 0.1:
        return f"could easily be chance (p = {p:.2f})"
    return f"likely just chance (p = {p:.2f})"


def numeric(series):
    """Coerce to float, dropping NaN. Returns numpy array."""
    return pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)


def series_mode(s):
    """The most common value (first, if tied). Works for any column type."""
    m = pd.Series(s).dropna().mode()
    return m.iloc[0] if len(m) else pd.NA


def quantile_interp(method):
    return {"type7": "linear", "linear": "linear", "nearest": "nearest"}.get(
        method, "linear")


def quantile(arr, q, method="type7"):
    """A quantile from numpy primitives. type7 is R's (and numpy's) default; we
    also offer nearest and linear. Kept explicit so the maths is auditable."""
    a = np.sort(np.asarray(arr, dtype=float))
    n = len(a)
    if n == 0:
        return float("nan")
    if n == 1:
        return float(a[0])
    q = min(max(q, 0.0), 1.0)
    if method == "nearest":
        return float(a[int(round(q * (n - 1)))])
    # type7 == linear interpolation between order statistics
    h = (n - 1) * q
    lo = int(math.floor(h))
    hi = min(lo + 1, n - 1)
    return float(a[lo] + (h - lo) * (a[hi] - a[lo]))


def welford_var(values):
    """Streaming, numerically stable sample variance (ddof=1) via Welford."""
    n = 0
    mean = 0.0
    m2 = 0.0
    for x in values:
        if x != x:      # skip NaN
            continue
        n += 1
        delta = x - mean
        mean += delta / n
        m2 += delta * (x - mean)
    if n < 2:
        return float("nan")
    return m2 / (n - 1)


def two_sample_t(a, b):
    """Welch's t-test. Returns (t, df, p)."""
    na, nb = len(a), len(b)
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return 0.0, na + nb - 2, 1.0
    t = (ma - mb) / se
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return t, df, _t_two_sided_p(t, df)


def one_way_anova(groups):
    """One-way ANOVA. groups: list of arrays.
    Returns (F, df1, df2, p, eta_squared)."""
    k = len(groups)
    n = sum(len(g) for g in groups)
    grand = np.concatenate(groups).mean()
    ss_between = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    ss_within = sum(((g - g.mean()) ** 2).sum() for g in groups)
    df1, df2 = k - 1, n - k
    ss_total = ss_between + ss_within
    eta2 = ss_between / ss_total if ss_total > 0 else float("nan")
    if df2 <= 0 or ss_within == 0:
        return float("nan"), df1, df2, float("nan"), eta2
    f = (ss_between / df1) / (ss_within / df2)
    return f, df1, df2, _f_p(f, df1, df2), eta2


def chi_square_independence(table):
    """Chi-square test of independence on a contingency table (numpy 2D).
    Returns (chi2, dof, p, min_expected, cramers_v)."""
    table = np.asarray(table, dtype=float)
    row = table.sum(axis=1, keepdims=True)
    col = table.sum(axis=0, keepdims=True)
    total = table.sum()
    if total == 0:
        return float("nan"), 0, float("nan"), float("nan"), float("nan")
    expected = row @ col / total
    with np.errstate(divide="ignore", invalid="ignore"):
        chi = np.nansum((table - expected) ** 2 / expected)
    dof = (table.shape[0] - 1) * (table.shape[1] - 1)
    kmin = min(table.shape[0] - 1, table.shape[1] - 1)
    v = math.sqrt(chi / (total * kmin)) if kmin > 0 and total > 0 else float("nan")
    return chi, dof, _chi2_p(chi, dof), float(expected.min()), v


def pearson_r(a, b):
    """Pearson correlation with a two-sided p-value. Returns (r, n, p)."""
    n = len(a)
    if n < 3:
        return float("nan"), n, float("nan")
    r = np.corrcoef(a, b)[0, 1]
    if abs(r) >= 1.0:
        return r, n, 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, n, _t_two_sided_p(t, n - 2)


def partial_correlation(a, b, z):
    """First-order partial correlation between a and b, controlling for one
    covariate z. Returns (raw_r_ab, partial_r, n, p)."""
    n = len(a)
    if n < 4:
        return float("nan"), float("nan"), n, float("nan")
    r_ab = np.corrcoef(a, b)[0, 1]
    r_az = np.corrcoef(a, z)[0, 1]
    r_bz = np.corrcoef(b, z)[0, 1]
    denom = math.sqrt(max((1 - r_az ** 2) * (1 - r_bz ** 2), 0.0))
    if denom <= 0:
        return r_ab, float("nan"), n, float("nan")
    r_partial = (r_ab - r_az * r_bz) / denom
    if abs(r_partial) >= 1.0:
        return r_ab, r_partial, n, 0.0
    dof = n - 3   # n - 2 - (1 control variable)
    if dof <= 0:
        return r_ab, r_partial, n, float("nan")
    t = r_partial * math.sqrt(dof / (1 - r_partial ** 2))
    return r_ab, r_partial, n, _t_two_sided_p(t, dof)


def check_collinearity(X, names):
    """Raise a teaching AskError when the design matrix is rank-deficient
    (duplicate / linearly dependent / constant inputs) — bug C1: previously
    the coefficients were silently split across the aliased columns."""
    X = np.asarray(X, dtype=float)
    if X.shape[1] < 2:
        return
    rank = np.linalg.matrix_rank(X)
    if rank >= X.shape[1]:
        return
    # name the culprits: pairwise near-perfect correlation among predictors
    culprits = []
    for i in range(1, X.shape[1]):
        si = X[:, i].std()
        if si == 0:
            culprits.append(f"{names[i]} (constant)")
            continue
        for j in range(i + 1, X.shape[1]):
            sj = X[:, j].std()
            if sj == 0:
                continue
            r = np.corrcoef(X[:, i], X[:, j])[0, 1]
            if abs(r) > 0.9999:
                culprits.append(f"{names[i]} and {names[j]}")
    detail = "; ".join(dict.fromkeys(culprits)) or "some inputs"
    raise AskError(
        what=f"Two or more inputs carry the same information ({detail}), so "
             f"their separate effects can't be told apart.",
        where="",
        fix="Remove one of the overlapping inputs and run predict again.")


def linear_regression(y, X, names):
    """Ordinary least squares. X already has an intercept column.
    Returns dict with coefs, std errors, t, p, r2."""
    coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    n, k = X.shape
    dof = max(n - k, 1)
    sigma2 = (resid @ resid) / dof
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        se = np.full(k, float("nan"))
    with np.errstate(divide="ignore", invalid="ignore"):
        tvals = coef / se
    pvals = [_t_two_sided_p(t, dof) for t in tvals]
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot > 0 else float("nan")
    return {"names": names, "coef": coef, "se": se, "t": tvals,
            "p": pvals, "r2": r2, "n": n}


def t_crit(df, conf=0.95):
    """Two-sided critical t value, found by inverting the t tail numerically."""
    if df <= 0:
        return float("nan")
    alpha = 1 - conf
    lo, hi = 0.0, 1000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def z_crit(conf=0.95):
    return {0.90: 1.6448536, 0.95: 1.959964, 0.99: 2.5758293}.get(
        round(conf, 2), 1.959964)


def two_proportion_z(x1, n1, x2, n2, conf=0.95):
    """Two-sample z-test comparing two proportions: p-value from the pooled
    (null-hypothesis: equal proportions) standard error, CI for the
    difference from the unpooled one. Returns (diff, ci_lo, ci_hi, z, p)."""
    if n1 <= 0 or n2 <= 0:
        return float("nan"), float("nan"), float("nan"), float("nan"), float("nan")
    p1, p2 = x1 / n1, x2 / n2
    diff = p1 - p2
    p_pool = (x1 + x2) / (n1 + n2)
    se_pool = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    z = diff / se_pool if se_pool > 0 else float("nan")
    p = 2 * (1 - std_normal_cdf(abs(z))) if z == z else 1.0
    se_unpooled = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    zc = z_crit(conf)
    lo, hi = diff - zc * se_unpooled, diff + zc * se_unpooled
    return diff, lo, hi, z, min(max(p, 0.0), 1.0)


def mean_diff_ci(a, b, conf=0.95):
    """Welch confidence interval for the difference in means (a - b)."""
    na, nb = len(a), len(b)
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / na + vb / nb)
    diff = a.mean() - b.mean()
    if se == 0:
        return diff, diff, diff
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    t = t_crit(df, conf)
    return diff, diff - t * se, diff + t * se


def cohens_d(a, b):
    """Cohen's d using the pooled standard deviation."""
    na, nb = len(a), len(b)
    va, vb = a.var(ddof=1), b.var(ddof=1)
    sp2 = ((na - 1) * va + (nb - 1) * vb) / max(na + nb - 2, 1)
    sp = math.sqrt(sp2) if sp2 > 0 else float("nan")
    if not sp or sp != sp:
        return float("nan")
    return (a.mean() - b.mean()) / sp


def hedges_g(a, b):
    """Hedges' g: Cohen's d with the small-sample bias correction (C5)."""
    d = cohens_d(a, b)
    if d != d:
        return d
    n = len(a) + len(b)
    corr = 1.0 - 3.0 / (4.0 * n - 9.0) if n > 2 else 1.0
    return d * corr


def d_magnitude(d):
    a = abs(d)
    if a < 0.2:
        return "a negligible"
    if a < 0.5:
        return "a small"
    if a < 0.8:
        return "a medium"
    return "a large"


def fisher_r_ci(r, n, conf=0.95):
    """Confidence interval for a correlation via the Fisher z transform."""
    if n < 4 or abs(r) >= 1:
        return float("nan"), float("nan")
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1 / math.sqrt(n - 3)
    zc = z_crit(conf)
    lo, hi = z - zc * se, z + zc * se
    return math.tanh(lo), math.tanh(hi)


def spearman_r(a, b):
    """Spearman rank correlation: rank-transform, then reuse Pearson."""
    ra = pd.Series(a).rank().to_numpy(float)
    rb = pd.Series(b).rank().to_numpy(float)
    return pearson_r(ra, rb)


def rankdata(a):
    """Average ranks with ties (needed for tie-corrected rank tests)."""
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    sa = a[order]
    i = 0
    n = len(a)
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def mann_whitney_u(a, b):
    """Mann-Whitney U (rank-sum) test with a tie-corrected normal approximation.
    Returns (U, p)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    n1, n2 = len(a), len(b)
    allv = np.concatenate([a, b])
    ranks = rankdata(allv)
    r1 = ranks[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u = min(u1, n1 * n2 - u1)
    mu = n1 * n2 / 2.0
    # tie correction
    _, counts = np.unique(allv, return_counts=True)
    n = n1 + n2
    tie = (counts ** 3 - counts).sum()
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if sigma2 <= 0:
        return u, float("nan")
    z = (u - mu + 0.5) / math.sqrt(sigma2)
    p = 2 * (1 - std_normal_cdf(abs(z)))
    return u, min(max(p, 0.0), 1.0)


def wilcoxon_signed_rank(a, b):
    """Wilcoxon signed-rank test for paired samples (normal approximation).
    Returns (W, p)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    d = a - b
    d = d[d != 0]
    n = len(d)
    if n < 1:
        return float("nan"), float("nan")
    ranks = rankdata(np.abs(d))
    w_plus = ranks[d > 0].sum()
    w_minus = ranks[d < 0].sum()
    w = min(w_plus, w_minus)
    mu = n * (n + 1) / 4.0
    sigma2 = n * (n + 1) * (2 * n + 1) / 24.0
    if sigma2 <= 0:
        return w, float("nan")
    z = (w - mu + 0.5) / math.sqrt(sigma2)
    p = 2 * (1 - std_normal_cdf(abs(z)))
    return w, min(max(p, 0.0), 1.0)


def paired_t(a, b):
    """Paired-sample t-test. Returns (t, df, p)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    d = a - b
    n = len(d)
    if n < 2:
        return float("nan"), 0, float("nan")
    sd = d.std(ddof=1)
    if sd == 0:
        return 0.0, n - 1, 1.0
    t = d.mean() / (sd / math.sqrt(n))
    return t, n - 1, _t_two_sided_p(t, n - 1)


def std_normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bootstrap_ci(values, stat="average", rng=None, reps=2000, conf=0.95):
    """A percentile bootstrap confidence interval for a simple statistic."""
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return float("nan"), float("nan"), float("nan")
    if rng is None:
        rng = np.random.default_rng()
    fn = {"average": np.mean, "total": np.sum, "median": np.median,
          "min": np.min, "max": np.max,
          "spread": lambda x: x.std(ddof=1)}.get(stat, np.mean)
    n = len(arr)
    idx = rng.integers(0, n, (reps, n))
    resampled = arr[idx]
    stats = np.array([fn(row) for row in resampled])
    lo = (1 - conf) / 2 * 100
    hi = (1 + conf) / 2 * 100
    return float(fn(arr)), float(np.percentile(stats, lo)), \
        float(np.percentile(stats, hi))


def logistic_regression(y, X, names, max_iter=50, tol=1e-8):
    """Binary logistic regression by iteratively reweighted least squares (IRLS).
    y is 0/1. Raises AskError on separable / non-converging data. numpy only."""
    y = np.asarray(y, dtype=float)
    n, k = X.shape
    beta = np.zeros(k)
    for _ in range(max_iter):
        eta = X @ beta
        eta = np.clip(eta, -30, 30)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = mu * (1 - mu)
        if np.any(w < 1e-9) and np.all((mu > 0.999) | (mu < 0.001)):
            raise AskError(
                what="The two outcomes can be separated perfectly, so the model "
                     "can't settle on stable numbers.",
                where="",
                fix="Add more mixed examples, or remove an input that splits the "
                    "outcome cleanly.")
        w = np.clip(w, 1e-9, None)
        z = eta + (y - mu) / w
        WX = X * w[:, None]
        try:
            beta_new = np.linalg.solve(X.T @ WX, X.T @ (w * z))
        except np.linalg.LinAlgError:
            raise AskError(
                what="I couldn't fit the yes/no model on this data.",
                where="",
                fix="Check the inputs aren't copies of each other, then retry.")
        if np.max(np.abs(beta_new - beta)) < tol:
            beta = beta_new
            break
        beta = beta_new
    else:
        raise AskError(
            what="The yes/no model didn't converge on a stable answer.",
            where="",
            fix="Try fewer inputs, or more rows with a mix of both outcomes.")
    eta = np.clip(X @ beta, -30, 30)
    mu = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(mu * (1 - mu), 1e-9, None)
    try:
        cov = np.linalg.inv((X * w[:, None]).T @ X)
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        se = np.full(k, float("nan"))
    with np.errstate(divide="ignore", invalid="ignore"):
        zvals = beta / se
    pvals = [2 * (1 - std_normal_cdf(abs(z))) for z in zvals]
    pred = (mu >= 0.5).astype(float)
    accuracy = float((pred == y).mean())
    return {"names": names, "coef": beta, "se": se, "z": zvals,
            "p": pvals, "accuracy": accuracy, "n": n}


def design_columns(df, col, coltypes):
    """Columns for one predictor: numeric stays as-is; a category is one-hot
    encoded with its first level as the (dropped) reference."""
    s = df[col]
    tp = coltypes.get(col)
    if tp in ("Number", "Money", "Integer", "Percent", "Duration") \
            or pd.api.types.is_numeric_dtype(s):
        return [(col, pd.to_numeric(s, errors="coerce").to_numpy(float))]
    cats = pd.Categorical(s.astype("string"))
    levels = list(cats.categories)
    return [(f"{col}={lv}", (cats.codes == i + 1).astype(float))
            for i, lv in enumerate(levels[1:])]


def build_design(df, inputs, interactions, coltypes):
    """Assemble a design matrix (with intercept) from numeric + categorical
    inputs and any two-way interaction terms. Returns (names, columns)."""
    terms = []
    for col in inputs:
        terms.extend(design_columns(df, col, coltypes))
    for a, b in interactions:
        for an, aa in design_columns(df, a, coltypes):
            for bn, ba in design_columns(df, b, coltypes):
                terms.append((f"{an} x {bn}", aa * ba))
    names = ["(baseline)"] + [t[0] for t in terms]
    columns = [t[1] for t in terms]
    return names, columns


def binary_target(series):
    """Map a two-valued target to 0/1 floats. Returns (array, positive_label)."""
    s = series
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().any():
        vals = set(np.unique(num.dropna().to_numpy()))
        if vals <= {0.0, 1.0} and len(vals) == 2:
            return num.to_numpy(float), "1"
    if pd.api.types.is_bool_dtype(s):
        return s.astype(float).to_numpy(float), "yes"
    levels = sorted(str(x) for x in pd.unique(s.dropna()))
    truthy = {"true", "yes", "y", "t", "1"}
    pos = levels[-1]
    for lv in levels:
        if str(lv).lower() in truthy:
            pos = lv
            break
    mask = s.notna().to_numpy()
    arr = np.full(len(s), np.nan)
    arr[mask] = (s.astype("string")[s.notna()] == pos).astype(float).to_numpy()
    return arr, pos


def corr_strength(r):
    a = abs(r)
    if a < 0.1:
        return "essentially no"
    if a < 0.3:
        return "a weak"
    if a < 0.5:
        return "a moderate"
    if a < 0.7:
        return "a fairly strong"
    return "a strong"
