"""Language semantics: samples run, keyword collisions, dtype coercion (pandas 3)."""

import os

import pandas as pd
import pytest

from asklang import AskError, Env, SAMPLES, render_chart, run_program
from asklang.runtime import SAMPLES_DIR


@pytest.mark.parametrize("name", list(SAMPLES))
def test_sample_runs_and_renders(name):
    table, chart, notices, trace = run_program(SAMPLES[name], Env())
    assert table is not None or chart is not None
    if chart is not None:
        render_chart(chart)  # must not raise (A12 regression)


def test_math_on_text_teaches(run):
    """A11: pandas 3 str dtype must still trip the 'text math' teaching error."""
    with pytest.raises(AskError, match="text, but you're doing math"):
        run('load "reviews.csv"\nadd x = score + 1\nshow count')


def test_where_compare_on_numeric_text(run):
    # numeric-looking text still compares as numbers
    t, *_ = run('load "sales.csv"\nwhere revenue > 0\nshow count')
    assert t.df.iloc[0, 0] > 0


def test_sort_by_column_named_desc(tmp_path, run):
    """F1: a column literally named `desc` must sort ASCENDING."""
    p = tmp_path / "d.csv"
    p.write_text("desc,v\n3,1\n1,2\n2,3\n")
    t, *_ = run(f'load "{p}"\nsort by desc')
    assert t.df["desc"].tolist() == [1, 2, 3]
    t, *_ = run(f'load "{p}"\nsort by desc descending')
    assert t.df["desc"].tolist() == [3, 2, 1]


def test_replace_needs_in_column(run):
    t, *_ = run('load "sales.csv"\nreplace "West" with "Best" in region\n'
                'where region is "Best"\nshow count')
    assert t.df.iloc[0, 0] > 0


def test_show_count_column_counts_nonnull(tmp_path, run):
    """C7: `show count <col>` counts non-missing values of that column."""
    p = tmp_path / "m.csv"
    p.write_text("g,v\na,1\na,\nb,3\nb,4\n")
    t, *_ = run(f'load "{p}"\nshow count v as n, count as rows')
    assert int(t.df["n"].iloc[0]) == 3
    assert int(t.df["rows"].iloc[0]) == 4
    # grouped variant
    t, *_ = run(f'load "{p}"\ngroup by g\nshow count v as n, count as rows')
    got = {r.g: (int(r.n), int(r.rows)) for r in t.df.itertuples()}
    assert got == {"a": (1, 2), "b": (2, 2)}


def test_from_unknown_table_teaches(run):
    with pytest.raises(AskError, match="don't know a table"):
        run('load "sales.csv"\ncall it snap\nfrom snapp\nshow count')


def test_from_known_snapshot(run):
    t, *_ = run('load "sales.csv"\ncall it snap\nfrom snap\nshow count')
    assert int(t.df.iloc[0, 0]) > 0


def test_load_resolves_cwd_first(tmp_path, monkeypatch):
    """E2: a file in the cwd wins over one next to the package."""
    (tmp_path / "sales.csv").write_text("region,revenue\nZZ,1\n")
    monkeypatch.chdir(tmp_path)
    t, *_ = run_program('load "sales.csv"\nshow count', Env())
    assert int(t.df.iloc[0, 0]) == 1  # got the cwd file, not the bundled one


def test_save_writes_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = ('load "' + os.path.join(SAMPLES_DIR, "sales.csv") + '"\n'
           'first 3\nsave as "out_test.csv"')
    run_program(src, Env())
    assert (tmp_path / "out_test.csv").exists()


def test_strict_mode_survives_load_cache(run):
    """Strict-mode load errors must fire even when the load is cached."""
    src = 'use strict types\nload "reviews.csv"\nshow average score'
    with pytest.raises(AskError, match="strict types is on"):
        run(src)
    with pytest.raises(AskError, match="strict types is on"):
        run(src)   # second run hits the cache — must still raise


def test_fuzzy_combine_notices(run):
    t, ch, notices, tr = run(SAMPLES["10. Fuzzy-match messy names"])
    assert any("fuzzy match" in n for n in notices)


def test_combine_default_keeps_all_left_rows(run):
    """No 'keeping' phrase = left join, same as before (40 orders either way)."""
    t, ch, n, tr = run('load "orders_messy.csv"\ncombine with customers on name')
    assert len(t.df) == 40


def test_combine_keeping_matches_only_is_inner_join(run):
    t, ch, n, tr = run('load "orders_messy.csv"\n'
                       'combine with customers on name keeping matches only')
    assert len(t.df) < 40
    assert t.df["tier"].notna().all()


def test_combine_keeping_all_is_outer_join(run):
    t, ch, n, tr = run('load "orders_messy.csv"\n'
                       'combine with customers on name keeping all')
    assert len(t.df) >= 40


def test_combine_keeping_all_right(run):
    """A right join keeps every matching left row per right key (standard
    join semantics - each of the 5 customers matches several exact-name
    orders), so this equals the inner-join count, not the customer count."""
    t, ch, n, tr = run('load "orders_messy.csv"\n'
                       'combine with customers on name keeping all right')
    t2, ch2, n2, tr2 = run('load "orders_messy.csv"\n'
                           'combine with customers on name keeping matches only')
    assert len(t.df) == len(t2.df)


def test_combine_old_join_keyword_teaches_migration(run):
    with pytest.raises(AskError, match="no longer takes"):
        run('load "orders_messy.csv"\ncombine inner with customers on name')


def test_combine_bad_keeping_phrase_teaches(run):
    with pytest.raises(AskError, match="'keeping' phrase"):
        run('load "orders_messy.csv"\ncombine with customers on name keeping bananas')


def test_fill_down_carries_within_group_not_across(run, tmp_path):
    """fill missing X with previous ('fill down') must not let group A's last
    value leak into group B's leading gap."""
    rows = [
        {"region": "East", "day": 1, "price": 10}, {"region": "East", "day": 2, "price": None},
        {"region": "West", "day": 1, "price": None}, {"region": "West", "day": 2, "price": 20},
    ]
    p = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\ngroup by region\n'
                       f'fill missing price with previous')
    got = dict(zip(t.df["region"] + t.df["day"].astype(str), t.df["price"]))
    assert got["East2"] == 10          # carried down within East
    assert pd.isna(got["West1"])       # NOT carried over from East's last row


def test_predict_polynomial_adds_squared_term(run, tmp_path):
    import numpy as np
    x = np.linspace(-5, 5, 60)
    y = 2 * x ** 2 + 3 * x + 1
    p = tmp_path / "poly.csv"
    pd.DataFrame({"x": x, "y": y}).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\npredict y from x with polynomial 2')
    note = next(msg for msg in n if msg.startswith("predict"))
    assert "x^2" in note


def test_predict_polynomial_needs_a_number(run):
    with pytest.raises(AskError, match="needs a number"):
        run('load "sales.csv"\npredict revenue from cost with polynomial')


def test_predict_polynomial_needs_numeric_input(run):
    with pytest.raises(AskError, match="not just categories"):
        run('load "sales.csv"\npredict revenue from region with polynomial 2')


def test_predict_standardized_coefficients_match_formula(tmp_path):
    import numpy as np
    from asklang.interpreter import Env, Interpreter
    from asklang.parser import Parser
    from asklang.tokens import tokenize
    rng = np.random.default_rng(7)
    a = rng.normal(scale=1, size=200)
    b = rng.normal(scale=1000, size=200)   # very different scale from a
    y = 2 * a + 0.01 * b + rng.normal(scale=0.1, size=200)
    p = tmp_path / "std.csv"
    pd.DataFrame({"a": a, "b": b, "y": y}).to_csv(p, index=False)
    clauses = Parser(tokenize(f'load "{p}"\npredict y from a, b')).parse_program()
    interp = Interpreter(Env())
    interp.run(clauses)
    stat_df = interp.last_stat.df
    row_a = stat_df[stat_df["factor"] == "a"].iloc[0]
    row_b = stat_df[stat_df["factor"] == "b"].iloc[0]
    # the table's "effect" is itself rounded to 4dp, so compare against that
    # same rounded value (not the raw fit coefficient) with a looser tolerance
    expected_a = row_a["effect"] * a.std(ddof=1) / y.std(ddof=1)
    expected_b = row_b["effect"] * b.std(ddof=1) / y.std(ddof=1)
    assert row_a["standardized"] == pytest.approx(expected_a, abs=5e-3)
    assert row_b["standardized"] == pytest.approx(expected_b, abs=5e-3)
    # raw coefficients look ~200x apart (2 vs 0.01) purely from b's huge
    # scale; standardizing narrows that gap to reflect actual relative
    # contribution to y's variance, not just b's units
    raw_ratio = abs(row_a["effect"] / row_b["effect"])
    std_ratio = abs(row_a["standardized"] / row_b["standardized"])
    assert std_ratio < raw_ratio / 10


def test_relate_controlling_for_partial_correlation(run, tmp_path):
    import numpy as np
    rng = np.random.default_rng(1)
    z = rng.normal(size=100)
    a = z + rng.normal(scale=0.3, size=100)
    b = z + rng.normal(scale=0.3, size=100)
    p = tmp_path / "confound.csv"
    pd.DataFrame({"a": a, "b": b, "z": z}).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\nrelate a and b controlling for z')
    note = next(x for x in n if x.startswith("relate"))
    assert "partial r" in note and "controlling for z" in note


def test_relate_all_of_builds_correlation_matrix(run):
    t, ch, n, tr = run('load "sales.csv"\nrelate all of revenue, cost')
    note = next(x for x in n if x.startswith("relate"))
    assert "Correlation matrix" in note
    assert "revenue" in t.df.columns or "revenue" in note


def test_relate_all_of_needs_two_columns_teaches(run):
    with pytest.raises(AskError, match="two or more columns"):
        run('load "sales.csv"\nrelate all of revenue')


def test_compare_paired_matches_stats(run, tmp_path):
    rows = [{"before": 10, "after": 12}, {"before": 9, "after": 11},
           {"before": 14, "after": 13}, {"before": 11, "after": 13},
           {"before": 8, "after": 10}]
    p = tmp_path / "paired.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\ncompare before and after paired')
    note = next(x for x in n if x.startswith("compare"))
    assert "paired t-test" in note
    assert "Paired comparison of before and after" in note


def test_compare_paired_needs_two_shared_numbers(run, tmp_path):
    p = tmp_path / "paired2.csv"
    pd.DataFrame({"before": [1], "after": [2]}).to_csv(p, index=False)
    with pytest.raises(AskError, match="Not enough paired numbers"):
        run(f'load "{p}"\ncompare before and after paired')


def test_compare_two_group_yes_no_uses_proportion_test(run, tmp_path):
    rows = ([{"region": "East", "bought": "yes"}] * 30
           + [{"region": "East", "bought": "no"}] * 20
           + [{"region": "West", "bought": "yes"}] * 20
           + [{"region": "West", "bought": "no"}] * 30)
    p = tmp_path / "props.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\ncompare bought between region')
    note = next(x for x in n if x.startswith("compare"))
    assert "two-proportion z-test" in note
    assert "percentage points" in note


def test_compare_three_group_yes_no_still_uses_chi_square(run, tmp_path):
    rows = ([{"region": "East", "bought": "yes"}] * 10
           + [{"region": "West", "bought": "no"}] * 10
           + [{"region": "North", "bought": "yes"}] * 10)
    p = tmp_path / "props2.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\ncompare bought between region')
    note = next(x for x in n if x.startswith("compare"))
    assert "chi-square" in note


def test_understand_reports_numeric_summary_in_words(run):
    t, ch, n, tr = run('load "sales.csv"\nunderstand')
    note = next(x for x in n if x.startswith("understand"))
    assert "averages" in note and "median" in note and "spread" in note
    assert "middle half falls between" in note


def test_understand_flags_skew(run, tmp_path):
    import numpy as np
    vals = list(range(1, 20)) + [500]   # one big outlier -> right-skewed
    p = tmp_path / "skewed.csv"
    pd.DataFrame({"x": vals}).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\nunderstand')
    note = next(x for x in n if x.startswith("understand"))
    assert "right-skewed" in note


def test_understand_text_column_has_no_numeric_summary(run):
    t, ch, n, tr = run('load "sales.csv"\nunderstand')
    note = next(x for x in n if x.startswith("understand"))
    region_line = next(l for l in note.splitlines() if l.strip().startswith("region:"))
    assert "averages" not in region_line


def test_date_parts_year_month_quarter_day(run):
    t, ch, n, tr = run('load "signups.csv"\n'
                       'add y = year of signup_date\n'
                       'add m = month of signup_date\n'
                       'add q = quarter of signup_date\n'
                       'add d = day of signup_date')
    row = t.df.iloc[1]   # 2025-01-03
    assert row["y"] == 2025
    assert row["m"] == 1
    assert row["q"] == 1
    assert row["d"] == 3


def test_date_part_weekday_is_a_name(run):
    t, ch, n, tr = run('load "signups.csv"\nadd wd = weekday of signup_date')
    assert t.df["wd"].iloc[0] in (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def test_bare_column_named_year_still_works(run, tmp_path):
    """A column literally named 'year' (not followed by 'of') must still
    parse as a plain column reference, not misfire as the date-part form."""
    p = tmp_path / "years.csv"
    pd.DataFrame({"year": [2020, 2021]}).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\nadd next_year = year + 1')
    assert t.df["next_year"].tolist() == [2021, 2022]


def test_date_part_on_non_date_column_teaches(run):
    with pytest.raises(AskError, match="doesn't look like dates"):
        run('load "sales.csv"\nadd m = month of region')


def test_expand_splits_delimited_cell_into_rows(run, tmp_path):
    rows = [{"order": "O1", "tags": "red, blue, green"}, {"order": "O2", "tags": "yellow"}]
    p = tmp_path / "tags.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\nexpand tags by ","')
    assert len(t.df) == 4
    o1 = t.df[t.df["order"] == "O1"]["tags"].tolist()
    assert o1 == ["red", "blue", "green"]   # trimmed, one per row


def test_expand_missing_separator_teaches(run):
    with pytest.raises(AskError, match="expand looks like"):
        run('load "sales.csv"\nexpand region by')


def test_reshape_wide_default_aggregate_sums_many_to_one_cells(run, tmp_path):
    rows = [
        {"region": "East", "segment": "A", "revenue": 10},
        {"region": "East", "segment": "A", "revenue": 5},   # same cell as above
        {"region": "East", "segment": "B", "revenue": 7},
    ]
    p = tmp_path / "wide1.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\nreshape wide by segment using revenue')
    row = t.df[t.df["region"] == "East"].iloc[0]
    assert row["A"] == 15   # 10 + 5, the default aggregate is total/sum


def test_reshape_wide_aggregate_average(run, tmp_path):
    rows = [
        {"region": "East", "segment": "A", "revenue": 10},
        {"region": "East", "segment": "A", "revenue": 6},
        {"region": "East", "segment": "B", "revenue": 7},
    ]
    p = tmp_path / "wide2.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\n'
                       f'reshape wide by segment using revenue aggregate average')
    row = t.df[t.df["region"] == "East"].iloc[0]
    assert row["A"] == 8   # (10 + 6) / 2


def test_reshape_wide_unknown_aggregate_teaches(run):
    with pytest.raises(AskError, match="don't know the aggregate"):
        run('load "sales.csv"\n'
           'reshape wide by segment using revenue aggregate bogus')


def test_fill_down_ungrouped_still_mixes_with_other_transforms(run, tmp_path):
    """Without a group by, fill-down stays an ordinary transform - it must
    not block a normal transform from following it."""
    rows = [{"price": 10}, {"price": None}, {"price": 30}]
    p = tmp_path / "prices3.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\nfill missing price with previous\n'
                       f'where price > 5')
    assert len(t.df) == 3


def test_fill_up_carries_within_group(run, tmp_path):
    rows = [
        {"region": "East", "day": 1, "price": None}, {"region": "East", "day": 2, "price": 10},
        {"region": "West", "day": 1, "price": 20}, {"region": "West", "day": 2, "price": None},
    ]
    p = tmp_path / "prices2.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    t, ch, n, tr = run(f'load "{p}"\ngroup by region\n'
                       f'fill missing price with next')
    got = dict(zip(t.df["region"] + t.df["day"].astype(str), t.df["price"]))
    assert got["East1"] == 10
    assert pd.isna(got["West2"])       # nothing after it within West to pull from


def test_seeded_bootstrap_repeatable():
    src = 'set seed 7\nload "sales.csv"\nestimate average revenue with bootstrap'
    t1, *_ = run_program(src, Env())
    t2, *_ = run_program(src, Env())
    pd.testing.assert_frame_equal(t1.df, t2.df)
