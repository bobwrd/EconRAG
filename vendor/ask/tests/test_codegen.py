"""Code export: faithful for core verbs, honest about gaps (F6)."""

from asklang import SAMPLES, generate_code


def test_pandas_core_pipeline():
    code = generate_code(SAMPLES["2. Group and chart"], "pandas")
    assert 'pd.read_csv("sales.csv")' in code
    assert "groupby" in code and "agg(" in code


def test_r_core_pipeline():
    code = generate_code(SAMPLES["2. Group and chart"], "r")
    assert "read_csv" in code and "summarise" in code


def test_incomplete_translation_is_flagged():
    """The ILOStat sample uses extract; the emitted code must warn it's partial."""
    code = generate_code(SAMPLES["5. Real research data (ILOStat)"], "pandas")
    assert "TODO" in code
    assert "NOTE" in code.splitlines()[0]


def test_complete_translation_not_flagged():
    code = generate_code('load "sales.csv"\nwhere region is "West"\n'
                         'group by segment\nshow total revenue as s', "pandas")
    assert "NOTE" not in code.splitlines()[0]


def test_combine_defines_other_frame():
    code = generate_code('load "orders_messy.csv"\n'
                         'combine with customers on name', "pandas")
    assert 'customers = pd.read_csv("customers.csv")' in code


def test_parse_error_reported_gently():
    code = generate_code('wheer x is 1', "pandas")
    assert code.startswith("# Could not translate")


def test_distribution_codegen_pandas_and_r():
    pandas_code = generate_code(SAMPLES["19. Distribution of a number"], "pandas")
    assert 'kind="density"' in pandas_code
    r_code = generate_code(SAMPLES["19. Distribution of a number"], "r")
    assert "geom_density" in r_code


def test_change_codegen_pandas_and_r():
    pandas_code = generate_code(SAMPLES["20. Change across categories"], "pandas")
    assert "marker=" in pandas_code
    r_code = generate_code(SAMPLES["20. Change across categories"], "r")
    assert "geom_line" in r_code


def test_flow_and_network_codegen_marked_incomplete():
    for name in ("21. Flow between two categories",
                 "23. Network of connected categories"):
        code = generate_code(SAMPLES[name], "pandas")
        assert "NOTE" in code.splitlines()[0]
        r_code = generate_code(SAMPLES[name], "r")
        assert "install.packages" in r_code


def test_predict_r_bridge_codegen_shows_real_r_call():
    src = 'load "sales.csv"\npredict revenue from cost using r package "stats"'
    for dialect in ("pandas", "r"):
        code = generate_code(src, dialect)
        assert "library(stats)" in code
        assert "lm(" in code
        assert "NOTE" in code.splitlines()[0]


def test_date_part_codegen_pandas_and_r():
    src = 'load "signups.csv"\nadd m = month of signup_date'
    pandas_code = generate_code(src, "pandas")
    assert "dt.month" in pandas_code
    r_code = generate_code(src, "r")
    assert "lubridate::month" in r_code


def test_weekday_codegen_pandas_and_r():
    src = 'load "signups.csv"\nadd wd = weekday of signup_date'
    pandas_code = generate_code(src, "pandas")
    assert "day_name" in pandas_code
    r_code = generate_code(src, "r")
    assert "wday" in r_code


def test_expand_codegen_pandas_and_r():
    src = 'load "sales.csv"\nexpand region by ","'
    pandas_code = generate_code(src, "pandas")
    assert ".explode(" in pandas_code
    r_code = generate_code(src, "r")
    assert "separate_rows" in r_code


def test_tree_codegen_pandas_and_r():
    code = generate_code(SAMPLES["22. Tree of nested categories"], "pandas")
    assert "groupby" in code
    r_code = generate_code(SAMPLES["22. Tree of nested categories"], "r")
    assert "treemapify" in r_code
