import matplotlib
import pytest

matplotlib.use("Agg")

from asklang import Env, run_program  # noqa: E402


@pytest.fixture
def run():
    """Run an Ask program in a fresh Env; returns (table, chart, notices, trace)."""
    def _run(src):
        return run_program(src, Env())
    return _run
