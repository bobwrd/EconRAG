"""
Saved conversations for the web UI (web.py): every answer is kept in
data/sessions.db (SQLite, built into Python), so past chats survive a restart
and can be reopened, continued, or deleted. Nothing here calls a model.

Each answer row keeps two JSON blobs:
  view   — what the page shows (answer, fact-check, charts, tool trail, technical details)
  record — what downloads and the technical rewrite need (question, tool results, evidence)
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "sessions.db"
TITLE_CHARS = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    asked TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    view TEXT NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS answers_by_session ON answers(session_id, id);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dump_record(record: dict) -> str:
    """The evidence's "sources" is a set; JSON has no sets."""
    ev = record.get("evidence") or {}
    return json.dumps({**record, "evidence": {**ev, "sources": sorted(ev.get("sources") or [])}},
                      ensure_ascii=False, default=str)


def _load_record(text: str) -> dict:
    record = json.loads(text)
    ev = record.setdefault("evidence", {})
    ev["sources"] = set(ev.get("sources") or [])
    for key in ("structured", "passages", "results"):
        ev.setdefault(key, [])
    return record


class Sessions:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)  # web.py's threads share it, under the lock
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def new(self, first_question: str) -> int:
        title = " ".join(first_question.split())
        title = title if len(title) <= TITLE_CHARS else title[:TITLE_CHARS - 1].rstrip() + "…"
        with self.lock, self.db:
            now = _now()
            return self.db.execute("INSERT INTO sessions (title, created, updated) VALUES (?, ?, ?)",
                                   (title, now, now)).lastrowid

    def add(self, session_id: int, record: dict, view: dict) -> int:
        """Saves one answer; returns its id (the page's answer id, for downloads)."""
        with self.lock, self.db:
            answer_id = self.db.execute(
                "INSERT INTO answers (session_id, asked, question, answer, view, record) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, record.get("asked") or _now(), record["question"], record.get("answer") or "",
                 json.dumps(view, ensure_ascii=False, default=str), _dump_record(record))).lastrowid
            self.db.execute("UPDATE sessions SET updated = ? WHERE id = ?", (_now(), session_id))
            return answer_id

    def update_record(self, answer_id: int, record: dict):
        """After a technical rewrite, so it isn't paid for twice."""
        with self.lock, self.db:
            self.db.execute("UPDATE answers SET record = ? WHERE id = ?", (_dump_record(record), answer_id))

    def record(self, answer_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT record FROM answers WHERE id = ?", (answer_id,)).fetchone()
        return _load_record(row["record"]) if row else None

    def list(self, limit: int = 200) -> list[dict]:
        """Most recently used first."""
        with self.lock:
            rows = self.db.execute(
                "SELECT s.id, s.title, s.created, s.updated, COUNT(a.id) AS answers FROM sessions s "
                "LEFT JOIN answers a ON a.session_id = s.id GROUP BY s.id ORDER BY s.updated DESC, s.id DESC "
                "LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, session_id: int) -> dict | None:
        """The session with its answers' page views (each with its id), oldest first."""
        with self.lock:
            s = self.db.execute("SELECT id, title, created, updated FROM sessions WHERE id = ?",
                                (session_id,)).fetchone()
            if not s:
                return None
            rows = self.db.execute("SELECT id, question, answer, view FROM answers WHERE session_id = ? "
                                   "ORDER BY id", (session_id,)).fetchall()
        answers = [{**json.loads(r["view"]), "id": str(r["id"]), "question": r["question"]} for r in rows]
        return {**dict(s), "answers": answers, "pairs": [(r["question"], r["answer"]) for r in rows]}

    def delete(self, session_id: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0
