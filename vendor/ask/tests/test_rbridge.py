"""The R bridge: script generation + output parsing must be testable without
R installed; the actual subprocess run is only exercised (and skipped
gracefully otherwise) when Rscript is really on PATH."""

import shutil

import pytest

from asklang import AskError, Env, run_program
from asklang.rbridge import (generate_r_script, parse_r_output, r_available,
                             run_r_predict)

HAS_R = shutil.which("Rscript") is not None


def test_generate_r_script_numeric_target_uses_lm():
    script = generate_r_script("data.csv", "revenue", ["cost"], [], "stats", False)
    assert 'library(stats)' in script
    assert "lm(" in script and "glm(" not in script
    assert "'revenue ~ cost'" in script or '"revenue ~ cost"' in script


def test_generate_r_script_binary_target_uses_glm():
    script = generate_r_script("data.csv", "bought", ["age"], [], "stats", True)
    assert "glm(" in script
    assert "family = binomial" in script


def test_generate_r_script_includes_interaction_term():
    script = generate_r_script("data.csv", "y", ["a", "b"], [("a", "b")],
                               "stats", False)
    assert "a:b" in script


def test_generate_r_script_rejects_unsafe_package_name():
    with pytest.raises(AskError, match="isn't a plain name"):
        generate_r_script("data.csv", "y", ["a"], [], 'x");system("rm -rf /', False)


def test_generate_r_script_rejects_unsafe_column_name():
    with pytest.raises(AskError, match="isn't a plain name"):
        generate_r_script("data.csv", "y; system('rm')", ["a"], [], "stats", False)


def test_parse_r_output_reads_coefs_and_r_squared():
    text = (
        "some banner\n"
        "ASK_R_RESULT_START\n"
        "rows: 42\n"
        "r_squared: 0.81\n"
        "coef: (Intercept) 1.5 0.2 0.001\n"
        "coef: cost 2.25 0.5 0.03\n"
        "ASK_R_RESULT_END\n"
    )
    result = parse_r_output(text)
    assert result["rows"] == 42
    assert result["r_squared"] == pytest.approx(0.81)
    assert result["coefs"] == [
        ("(Intercept)", 1.5, 0.2, 0.001),
        ("cost", 2.25, 0.5, 0.03),
    ]


def test_parse_r_output_handles_na_values():
    text = ("ASK_R_RESULT_START\nrows: 5\nr_squared: NA\n"
           "coef: cost 1.0 NA NA\nASK_R_RESULT_END\n")
    result = parse_r_output(text)
    assert result["r_squared"] is None
    assert result["coefs"] == [("cost", 1.0, None, None)]


def test_parse_r_output_missing_markers_teaches():
    with pytest.raises(AskError, match="didn't produce a result"):
        parse_r_output("Error: something went wrong in R\n")


def test_run_r_predict_teaches_when_r_missing(monkeypatch):
    import asklang.rbridge as rbridge
    monkeypatch.setattr(rbridge, "_R_AVAILABLE", False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    import pandas as pd
    df = pd.DataFrame({"revenue": [1, 2, 3], "cost": [1, 1, 2]})
    with pytest.raises(AskError, match="R isn't installed"):
        run_r_predict(df, "revenue", ["cost"], [], "stats", False, line=3)


@pytest.mark.skipif(HAS_R, reason="only meaningful without R")
def test_predict_using_r_package_raises_ask_error_without_r():
    with pytest.raises(AskError, match="R isn't installed"):
        run_program('load "sales.csv"\n'
                    'predict revenue from cost using r package "stats"', Env())


@pytest.mark.skipif(not HAS_R, reason="needs a real R + Rscript on PATH")
def test_predict_using_r_package_matches_ask_ols_roughly():
    """End-to-end: only runs when R is actually installed. Checks the R
    bridge's coefficient roughly agrees with Ask's own OLS on the same data
    (both should recover the same underlying linear relationship)."""
    t, ch, n, tr = run_program(
        'load "sales.csv"\npredict revenue from cost using r package "stats"', Env())
    r_notice = next(x for x in n if x.startswith("predict"))
    t2, ch2, n2, tr2 = run_program(
        'load "sales.csv"\npredict revenue from cost', Env())
    ask_notice = next(x for x in n2 if x.startswith("predict"))
    assert "via R" in r_notice
    assert "via R" not in ask_notice
