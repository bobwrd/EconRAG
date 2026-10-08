"""
Tests for sessions.py (saved conversations): answers are stored and read back
with their evidence intact, listed most recent first, and deleted with their
conversation. Temporary database, no network, no models, under a second:

    .venv/bin/python tests/test_sessions.py
"""

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sessions  # noqa: E402


def _db():
    return sessions.Sessions(Path(tempfile.mkdtemp()) / "sessions.db")


def _record(question, answer):
    return {"question": question, "answer": answer, "asked": "2026-10-09T10:00:00",
            "evidence": {"structured": ['{"x": 38.5}'], "passages": [], "sources": {"Chetty et al. (2014)"},
                         "results": [("county_profile", {"county": "Cook"}, {"upward_mobility": 38.5})]}}


def test_answers_are_saved_and_read_back():
    db = _db()
    sid = db.new("How   does Cook County\ncompare?")
    first = db.add(sid, _record("How does Cook County compare?", "Cook is at 38.5."),
                   {"answer": "Cook is at 38.5.", "events": [{"kind": "tool", "name": "county_profile"}]})
    db.add(sid, _record("And Wayne?", "Wayne is at 37.1."), {"answer": "Wayne is at 37.1."})
    s = db.get(sid)
    assert s["title"] == "How does Cook County compare?"
    assert [a["question"] for a in s["answers"]] == ["How does Cook County compare?", "And Wayne?"]
    assert s["answers"][0]["id"] == str(first) and s["answers"][0]["events"][0]["name"] == "county_profile"
    assert s["pairs"][-1] == ("And Wayne?", "Wayne is at 37.1.")
    record = db.record(first)  # what downloads and the technical rewrite need
    assert record["evidence"]["sources"] == {"Chetty et al. (2014)"}
    name, args, result = record["evidence"]["results"][0]
    assert (name, args, result) == ("county_profile", {"county": "Cook"}, {"upward_mobility": 38.5})
    record["technical"] = {"text": "Upward mobility is 38.5.", "unverified": []}
    db.update_record(first, record)
    assert db.record(first)["technical"]["text"] == "Upward mobility is 38.5."
    assert db.record(999) is None and db.get(999) is None


def test_list_is_most_recent_first_and_delete_removes_answers():
    db = _db()
    old = db.new("x" * 200)
    db.add(old, _record("x", "y"), {})
    new = db.new("Second chat")
    assert len(db.list()[1]["title"]) == sessions.TITLE_CHARS and db.list()[1]["title"].endswith("…")
    db.db.execute("UPDATE sessions SET updated = '2026-01-01T00:00:00' WHERE id = ?", (old,))
    assert [(s["id"], s["answers"]) for s in db.list()] == [(new, 0), (old, 1)]
    answer = db.get(old)["answers"][0]["id"]
    assert db.delete(old) and not db.delete(old)
    assert db.record(int(answer)) is None and [s["id"] for s in db.list()] == [new]


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
