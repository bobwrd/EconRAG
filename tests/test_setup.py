"""
Setup tests (setup_assistant.py) and the "missing data switches off, doesn't
crash" behavior a fresh copy relies on. No network: downloads use file:// URLs
in a temporary folder. Run after changing setup_assistant.py:

    .venv/bin/python tests/test_setup.py
"""

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import setup_assistant as sa  # noqa: E402  (chdirs to the project root)


def _in_empty_copy(fn):
    """Runs fn with the setup script pointed at an empty folder."""
    saved = {k: getattr(sa, k) for k in ("DATA", "DOCS", "ENV", "VENV_PY", "ASK_PY")}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sa.DATA, sa.DOCS, sa.ENV = tmp / "data", tmp / "docs", tmp / ".env"
        sa.VENV_PY, sa.ASK_PY = tmp / ".venv/bin/python", tmp / "ask/.venv/bin/python"
        try:
            return fn(tmp)
        finally:
            for k, v in saved.items():
                setattr(sa, k, v)


def test_fresh_copy_lists_what_to_do():
    todo = _in_empty_copy(lambda tmp: sa.report())
    assert {"packages", "keys", "atlas", "papers", "index", "longrun", "jpal", "charts"} <= set(todo)
    assert "python" not in todo and "sandbox" not in todo  # can't be installed by the script


def test_download_keeps_only_complete_pdfs():
    def check(tmp):
        pdf, html = tmp / "a.pdf", tmp / "b.html"
        pdf.write_bytes(b"%PDF-1.7 test")
        html.write_text("<html>challenge</html>")
        assert sa.download(pdf.as_uri(), tmp / "out" / "a.pdf", expect_pdf=True)
        assert not sa.download(html.as_uri(), tmp / "out" / "b.pdf", expect_pdf=True)
        assert not sa.download((tmp / "missing.pdf").as_uri(), tmp / "out" / "c.pdf")
        assert sorted(p.name for p in (tmp / "out").iterdir()) == ["a.pdf"]  # no .part files left
    _in_empty_copy(check)


def test_keys_are_saved_privately_and_read_back():
    def check(tmp):
        sa.ENV.write_text("FRED_API_KEY=abc  # comment\nOPENROUTER_API_KEY=\n")
        answers = iter(["gsk_test", ""])
        getpass, works = sa.getpass.getpass, sa._key_works
        sa.getpass.getpass, sa._key_works = (lambda prompt: next(answers)), (lambda n, v: None)
        try:
            sa.install_keys()
        finally:
            sa.getpass.getpass, sa._key_works = getpass, works
        assert sa.env_keys() == {"FRED_API_KEY": "abc", "GROQ_API_KEY": "gsk_test"}
        assert oct(sa.ENV.stat().st_mode)[-3:] == "600"
        assert sa.check_keys()[0]
    _in_empty_copy(check)


def test_every_paper_has_a_download_link():
    papers = json.loads((ROOT / "papers.json").read_text())
    assert all(m.get("url", "").startswith("https://") for m in papers.values())
    assert [f for f, m in papers.items() if m.get("browser_only")] == ["Haushofer_Shapiro_2016_cash_transfers_Kenya.pdf"]


def test_missing_paper_index_and_atlas_switch_off():
    import analyst
    import ask
    saved_dir = ask.DATA_DIR
    with tempfile.TemporaryDirectory() as tmp:
        ask.DATA_DIR = Path(tmp)
        try:
            assert ask.load_index() == ([], None, None)
        finally:
            ask.DATA_DIR = saved_dir

    def no_files():
        raise FileNotFoundError("data/atlas/national_county.txt")
    saved_atlas = analyst.Atlas
    analyst.Atlas = no_files
    try:
        bot = analyst.Analyst(None, None, wb=object())
        assert "isn't set up" in bot._call("search_papers", {"query": "x"})["error"]
        assert "FileNotFoundError" in bot._call("county_profile", {"county": "Cook", "state": "IL"})["error"]
    finally:
        analyst.Atlas = saved_atlas


if __name__ == "__main__":
    failed = 0
    for name, fn in [(n, f) for n, f in list(globals().items()) if n.startswith("test_")]:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
