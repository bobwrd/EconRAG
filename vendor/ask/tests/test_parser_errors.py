"""Every half-typed line must raise a teaching AskError, never IndexError (A1-A8)."""

import pytest

from asklang import AskError

DANGLING = [
    'load "sales.csv"\nbin',                       # A1
    'load "sales.csv"\nbin margin',
    'load "sales.csv"\ncompare revenue between',   # A2
    'load "sales.csv"\ncompare between region',
    'load "sales.csv"\nrelate cost and',           # A3
    'load "sales.csv"\nrelate and cost',
    'load "sales.csv"\ncombine with',              # A4
    'load "sales.csv"\ncombine on region',
    'load "sales.csv"\nreshape wide by',           # A5
    'load "sales.csv"\nrunning total of',          # A6
    'load "sales.csv"\nmoving average of',
    'load "sales.csv"\nrank',
    'load "sales.csv"\nlag',
    'load "sales.csv"\nsplit',                     # A7
    'load "sales.csv"\nsplit region',
    'load "sales.csv"\nadd x = extract after',     # A8
    'load "sales.csv"\nadd x = extract',
    'load "sales.csv"\nadd x =',
    'load "sales.csv"\nadd x as',
    'load "sales.csv"\nwhere',
    'load "sales.csv"\nreplace "a" with "b"',      # F2: no `in <col>`
    'load "sales.csv"\nflag outliers',             # F2
    'load "sales.csv"\nchart revenue by as bar',
    'load "sales.csv"\nchart by region as bar',
    'load "sales.csv"\nfor each region do recipe',
    'define function f(x):',
]


@pytest.mark.parametrize("src", DANGLING)
def test_dangling_lines_teach(run, src):
    with pytest.raises(AskError):
        run(src)


def test_recursive_recipe_teaches(run):
    with pytest.raises(AskError, match="calling itself"):
        run('define recipe loop from data:\n  loop\nload "sales.csv"\n  loop')


def test_recursive_function_teaches(run):
    with pytest.raises(AskError, match="calling itself"):
        run('define function f(x): f(x)\nload "sales.csv"\nadd y = f(revenue)')


def test_mutually_recursive_recipes_teach(run):
    src = ('define recipe a from data:\n  b\n'
           'define recipe b from data:\n  a\n'
           'load "sales.csv"\n  a')
    with pytest.raises(AskError):
        run(src)


def test_apply_fix_word_anchored():
    """F3: apply-fix must replace whole words, not substrings."""
    from asklang.errors import replace_word_in_line
    assert replace_word_in_line("sort by sort_key", "sort", "show") \
        == "show by sort_key"
