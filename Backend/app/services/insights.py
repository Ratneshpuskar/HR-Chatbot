"""HR insights: questions the policies don't answer, and employee feedback on answers.

Stored in a local SQLite file. Only text that has passed the sensitive-data guard is
stored, no employee identity is recorded, and rows older than the retention
period are purged on startup.
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.answer_cache import normalise

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    question TEXT NOT NULL,
    question_key TEXT NOT NULL,
    top_score REAL
);
CREATE INDEX IF NOT EXISTS idx_gaps_ts ON knowledge_gaps (ts);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    rating INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT NOT NULL,
    comment TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback (ts);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


class InsightsStore:
    def __init__(self, db_path: Path, retention_days: int) -> None:
        self._db_path = db_path
        self._retention_days = retention_days

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            cutoff = _since(self._retention_days)
            conn.execute("DELETE FROM knowledge_gaps WHERE ts < ?", (cutoff,))
            conn.execute("DELETE FROM feedback WHERE ts < ?", (cutoff,))

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _execute(self, sql: str, params: tuple) -> None:
        with self._connect() as conn:
            conn.execute(sql, params)

    async def record_gap(self, question: str, top_score: float | None) -> None:
        await asyncio.to_thread(
            self._execute,
            "INSERT INTO knowledge_gaps (ts, question, question_key, top_score) VALUES (?, ?, ?, ?)",
            (_now(), question, normalise(question), top_score),
        )

    async def record_feedback(
        self, rating: int, question: str, answer: str, sources: list[str], comment: str | None
    ) -> None:
        await asyncio.to_thread(
            self._execute,
            "INSERT INTO feedback (ts, rating, question, answer, sources, comment) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), rating, question, answer, json.dumps(sources), comment),
        )

    def _summary_sync(self, days: int, limit: int) -> dict:
        since = _since(days)
        with self._connect() as conn:
            gaps = conn.execute(
                """SELECT MIN(question) AS question, COUNT(*) AS times_asked, MAX(ts) AS last_asked
                   FROM knowledge_gaps WHERE ts >= ?
                   GROUP BY question_key ORDER BY times_asked DESC, last_asked DESC LIMIT ?""",
                (since, limit),
            ).fetchall()
            counts = conn.execute(
                """SELECT SUM(rating > 0) AS helpful, SUM(rating < 0) AS not_helpful
                   FROM feedback WHERE ts >= ?""",
                (since,),
            ).fetchone()
            negative = conn.execute(
                """SELECT ts, question, answer, sources, comment FROM feedback
                   WHERE ts >= ? AND rating < 0 ORDER BY ts DESC LIMIT ?""",
                (since, limit),
            ).fetchall()
        return {
            "period_days": days,
            "unanswered_questions": [dict(r) for r in gaps],
            "feedback": {"helpful": counts["helpful"] or 0, "not_helpful": counts["not_helpful"] or 0},
            "negative_feedback": [{**dict(r), "sources": json.loads(r["sources"])} for r in negative],
        }

    async def summary(self, days: int = 30, limit: int = 50) -> dict:
        return await asyncio.to_thread(self._summary_sync, days, limit)
