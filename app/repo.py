"""All database access. Every query about proposals and profiles takes the user id and filters on
it, so one customer can never reach another customer's data through any route."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (email TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_failures_email ON login_failures (email, at);
CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    job_text TEXT NOT NULL,
    body TEXT NOT NULL,
    engine TEXT NOT NULL,
    warnings TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_user ON proposals (user_id, created_at);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, month)
);
"""

STATUSES = ("draft", "sent", "won", "lost")
EDITABLE = {"title", "body", "status"}
DEFAULT_PROFILE = {"name": "", "headline": "", "skills": [], "proof_points": [], "tone": "friendly"}


def month_key(now: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(now))


class Repo:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self.path = str(path)
        self.clock = clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    # users and sessions
    def create_user(self, email: str, password_hash: str) -> int | None:
        try:
            with closing(self._connect()) as db, db:
                return db.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)", (email, password_hash, self.clock())).lastrowid
        except sqlite3.IntegrityError:
            return None

    def user_by_email(self, email: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None

    def create_session(self, user_id: int, token_hash: str, ttl_seconds: float) -> None:
        now = self.clock()
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (token_hash, user_id, now + ttl_seconds))

    def user_for_token(self, token_hash: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=? AND s.expires_at > ?",
                             (token_hash, self.clock())).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    # login throttling
    def record_failure(self, email: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO login_failures (email, at) VALUES (?, ?)", (email, self.clock()))

    def failures(self, email: str, window: float) -> int:
        with closing(self._connect()) as db:
            return db.execute("SELECT COUNT(*) FROM login_failures WHERE email=? AND at > ?", (email, self.clock() - window)).fetchone()[0]

    def clear_failures(self, email: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM login_failures WHERE email=?", (email,))

    # profile
    def get_profile(self, user_id: int) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        return {**DEFAULT_PROFILE, **(json.loads(row["data"]) if row else {})}

    def set_profile(self, user_id: int, data: dict) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO profiles (user_id, data) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data", (user_id, json.dumps(data)))

    # usage, with the check and the count in one statement so two requests cannot both take the last slot
    def usage(self, user_id: int) -> int:
        with closing(self._connect()) as db:
            row = db.execute("SELECT count FROM usage WHERE user_id=? AND month=?", (user_id, month_key(self.clock()))).fetchone()
        return row["count"] if row else 0

    def try_consume(self, user_id: int, limit: int) -> bool:
        month = month_key(self.clock())
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO usage (user_id, month, count) VALUES (?, ?, 0)", (user_id, month))
            return db.execute("UPDATE usage SET count = count + 1 WHERE user_id=? AND month=? AND count < ?", (user_id, month, limit)).rowcount == 1

    def refund(self, user_id: int) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE usage SET count = count - 1 WHERE user_id=? AND month=? AND count > 0", (user_id, month_key(self.clock())))

    # proposals
    @staticmethod
    def _proposal(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["warnings"] = json.loads(d["warnings"])
        return d

    def add_proposal(self, user_id: int, title: str, job_text: str, body: str, engine: str, warnings: list[str]) -> int:
        now = self.clock()
        with closing(self._connect()) as db, db:
            return db.execute("INSERT INTO proposals (user_id, title, job_text, body, engine, warnings, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                              (user_id, title, job_text, body, engine, json.dumps(warnings), now, now)).lastrowid

    def proposals(self, user_id: int) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM proposals WHERE user_id=? ORDER BY created_at DESC, id DESC", (user_id,)).fetchall()
        return [self._proposal(r) for r in rows]

    def proposal(self, user_id: int, proposal_id: int) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM proposals WHERE id=? AND user_id=?", (proposal_id, user_id)).fetchone()
        return self._proposal(row) if row else None

    def update_proposal(self, user_id: int, proposal_id: int, **fields) -> bool:
        fields = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
        if not fields:
            return self.proposal(user_id, proposal_id) is not None
        sets = ", ".join(f"{k}=?" for k in fields)
        with closing(self._connect()) as db, db:
            return db.execute(f"UPDATE proposals SET {sets}, updated_at=? WHERE id=? AND user_id=?", (*fields.values(), self.clock(), proposal_id, user_id)).rowcount == 1

    def delete_proposal(self, user_id: int, proposal_id: int) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM proposals WHERE id=? AND user_id=?", (proposal_id, user_id)).rowcount == 1

    def stats(self, user_id: int) -> dict:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT status, COUNT(*) AS n FROM proposals WHERE user_id=? GROUP BY status", (user_id,)).fetchall()
        counts = {s: 0 for s in STATUSES} | {r["status"]: r["n"] for r in rows}
        decided = counts["won"] + counts["lost"]
        return {**counts, "total": sum(counts.values()), "win_rate": round(counts["won"] / decided, 2) if decided else None}
