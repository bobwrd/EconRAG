"""Backtick column references, for names that aren't valid bare words
(dots, spaces - common in raw ILOStat/Eurostat-style exports).

Root-caused from a real user report: quoting a dotted column name with
double quotes silently parsed it as a STRING LITERAL rather than a column
reference (`where "sex.label" is "Total"` compares two constants), which
then crashed `mask.fillna()` with a raw AttributeError instead of filtering
anything. Backticks disambiguate; the old double-quote form now fails with
a teaching error instead of a crash.
"""

import pytest

from asklang import AskError


def _ilo_csv(tmp_path):
    p = tmp_path / "ilo.csv"
    p.write_text(
        "ref_area.label,sex.label,obs_value\n"
        "Chile,Total,100\n"
        "Chile,Male,60\n"
        "Peru,Total,80\n"
    )
    return p


def test_backtick_column_in_where(tmp_path, run):
    p = _ilo_csv(tmp_path)
    t, *_ = run(f'load "{p}"\nwhere `sex.label` is "Total"\nshow count')
    assert int(t.df.iloc[0, 0]) == 2


def test_backtick_column_in_add_and_rename(tmp_path, run):
    p = _ilo_csv(tmp_path)
    t, *_ = run(f'load "{p}"\nrename `ref_area.label` to country\n'
                'where country is "Chile"\nshow count')
    assert int(t.df.iloc[0, 0]) == 2


def test_backtick_missing_check(tmp_path, run):
    p = _ilo_csv(tmp_path)
    t, *_ = run(f'load "{p}"\nwhere `obs_value` is not missing\nshow count')
    assert int(t.df.iloc[0, 0]) == 3


def test_quoted_dotted_column_now_teaches_instead_of_crashing(tmp_path, run):
    """The exact bug as originally reported: double-quoting a dotted column
    name in `where` used to raise a raw AttributeError."""
    p = _ilo_csv(tmp_path)
    with pytest.raises(AskError, match="doesn't refer to any column"):
        run(f'load "{p}"\nwhere "sex.label" is "Total"\nshow count')


def test_double_quoted_column_name_in_group_by(tmp_path, run):
    """Column-list positions (group by/keep/drop) have no value/column
    ambiguity, so double quotes work there too, not just backticks."""
    p = _ilo_csv(tmp_path)
    t, *_ = run(f'load "{p}"\ngroup by "ref_area.label"\nshow count')
    assert set(t.df["ref_area.label"]) == {"Chile", "Peru"}


def test_backtick_column_in_group_by(tmp_path, run):
    p = _ilo_csv(tmp_path)
    t, *_ = run(f'load "{p}"\ngroup by `ref_area.label`\nshow count')
    assert set(t.df["ref_area.label"]) == {"Chile", "Peru"}


def test_unclosed_backtick_teaches(tmp_path, run):
    p = _ilo_csv(tmp_path)
    with pytest.raises(AskError, match="closing"):
        run(f'load "{p}"\nwhere `sex.label is "Total"\nshow count')


def test_in_list_syntax_still_works_after_backtick_change(run):
    """Guard against the backtick change breaking the pre-existing
    `in [...]` list membership syntax, which also uses square brackets."""
    t, *_ = run('load "sales.csv"\nwhere region in ["West", "East"]\n'
                'show count')
    assert int(t.df.iloc[0, 0]) > 0
