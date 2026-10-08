"""
Saved conversations and reports for the web UI (web.py), in data/sessions.db
(SQLite, built into Python), so they survive a restart: chats can be reopened,
continued, renamed, pinned, searched or deleted; reports reopened and downloaded
again. Nothing here calls a model.

Each answer row keeps two JSON blobs:
  view   — what the page shows (answer, fact-check, charts, tool trail, technical details)
  record — what downloads and the technical rewrite need (question, tool results, evidence)
"""

from __future__ import annotations  # the method named "list" would hide list[...] in hints

import json
import re
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
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    created TEXT NOT NULL,
    report TEXT NOT NULL
);
"""
SNIPPET_CHARS = 90  # text shown around a search match


def _plain(markdown: str) -> str:
    """Answer text without Markdown marks, for search snippets."""
    text = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)]|#{1,6}|>)\s+", "", markdown)  # list markers, headings, quotes
    return re.sub(r"\*\*|__|`|\|", "", text)


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
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(sessions)")}
        if "pinned" not in columns:  # databases made before pinning existed
            self.db.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        self.lock = threading.Lock()

    @staticmethod
    def _title(text: str) -> str:
        title = " ".join(str(text).split())
        return title if len(title) <= TITLE_CHARS else title[:TITLE_CHARS - 1].rstrip() + "…"

    def new(self, first_question: str) -> int:
        title = self._title(first_question)
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

    def list(self, limit: int = 200, query: str = "") -> list[dict]:
        """Pinned first, then most recently used. With a query: only chats whose title, questions or
        answers contain it (any case), each with a snippet of where it matched."""
        words = query.strip()
        where, args = "", []
        if words:
            like = "%" + words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where = ("WHERE s.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM answers m WHERE m.session_id = s.id "
                     "AND (m.question LIKE ? ESCAPE '\\' OR m.answer LIKE ? ESCAPE '\\'))")
            args = [like, like, like]
        with self.lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT s.id, s.title, s.created, s.updated, s.pinned, COUNT(a.id) AS answers FROM sessions s "
                f"LEFT JOIN answers a ON a.session_id = s.id {where} GROUP BY s.id "
                "ORDER BY s.pinned DESC, s.updated DESC, s.id DESC LIMIT ?", (*args, limit)).fetchall()]
            if words:
                for row in rows:
                    row["snippet"] = self._snippet(row, words)
        for row in rows:
            row["pinned"] = bool(row["pinned"])
        return rows

    def _snippet(self, row: dict, words: str) -> str:
        """The first place a chat mentions the search words (lock held by the caller)."""
        if words.lower() in row["title"].lower():
            return ""
        for q, a in self.db.execute("SELECT question, answer FROM answers WHERE session_id = ? ORDER BY id",
                                    (row["id"],)):
            for text in (q, _plain(a)):
                at = text.lower().find(words.lower())
                if at >= 0:
                    begin = max(0, at - SNIPPET_CHARS // 2)
                    piece = " ".join(text[begin:begin + SNIPPET_CHARS].split())
                    return ("…" if begin else "") + piece + ("…" if begin + SNIPPET_CHARS < len(text) else "")
        return ""

    def rename(self, session_id: int, title: str) -> bool:
        title = self._title(title)
        if not title:
            return False
        with self.lock, self.db:
            return self.db.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)).rowcount > 0

    def pin(self, session_id: int, pinned: bool) -> bool:
        with self.lock, self.db:
            return self.db.execute("UPDATE sessions SET pinned = ? WHERE id = ?",
                                   (int(bool(pinned)), session_id)).rowcount > 0

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

    # ---- reports (workflows.py / report_recipes.py dicts, kept whole so every download works later)
    def add_report(self, report: dict) -> int:
        with self.lock, self.db:
            return self.db.execute(
                "INSERT INTO reports (kind, title, created, report) VALUES (?, ?, ?, ?)",
                (report.get("kind", ""), report["title"], report.get("created") or _now(),
                 json.dumps(report, ensure_ascii=False, default=str))).lastrowid

    def report(self, report_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT report FROM reports WHERE id = ?", (report_id,)).fetchone()
        return json.loads(row["report"]) if row else None

    def latest_report(self, kind: str, title: str) -> dict | None:
        """The most recent saved report of this kind and title (the same country or countries), if any."""
        with self.lock:
            row = self.db.execute("SELECT report FROM reports WHERE kind = ? AND title = ? ORDER BY id DESC LIMIT 1",
                                  (kind, title)).fetchone()
        return json.loads(row["report"]) if row else None

    def reports(self, limit: int = 100) -> list[dict]:
        """Newest first: id, kind, title, created."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id, kind, title, created FROM reports ORDER BY id DESC LIMIT ?", (limit,))]

    def delete_report(self, report_id: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM reports WHERE id = ?", (report_id,)).rowcount > 0
