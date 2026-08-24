import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .schemas import Lead, LeadCreate, LeadStatus

DB_PATH = Path(os.getenv("DATABASE_PATH", "./lead_radar.db"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT,
                title TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                category TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                published_at TEXT NOT NULL,
                budget TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                url TEXT,
                signals_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source, external_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_published ON leads(published_at DESC)")


def _row_to_lead(row: sqlite3.Row) -> Lead:
    return Lead(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        title=row["title"],
        excerpt=row["excerpt"],
        category=row["category"],
        score=row["score"],
        published_at=datetime.fromisoformat(row["published_at"]),
        budget=row["budget"],
        status=row["status"],
        url=row["url"],
        signals=json.loads(row["signals_json"] or "[]"),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def list_leads(min_score: int = 0, status: LeadStatus | None = None, limit: int = 100) -> list[Lead]:
    sql = "SELECT * FROM leads WHERE score >= ?"
    params: list[object] = [min_score]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY published_at DESC, score DESC LIMIT ?"
    params.append(limit)

    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_lead(row) for row in rows]


def upsert_lead(payload: LeadCreate) -> Lead:
    now = utc_now()
    external_id = payload.external_id or f"manual:{payload.source}:{payload.title}:{payload.published_at.isoformat()}"
    values = (
        payload.source,
        external_id,
        payload.title,
        payload.excerpt,
        payload.category,
        payload.score,
        payload.published_at.isoformat(),
        payload.budget,
        payload.status,
        payload.url,
        json.dumps(payload.signals, ensure_ascii=False),
        now,
        now,
    )

    with connection() as conn:
        conn.execute(
            """
            INSERT INTO leads (
                source, external_id, title, excerpt, category, score,
                published_at, budget, status, url, signals_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
                title = excluded.title,
                excerpt = excluded.excerpt,
                category = excluded.category,
                score = excluded.score,
                published_at = excluded.published_at,
                budget = excluded.budget,
                url = excluded.url,
                signals_json = excluded.signals_json,
                updated_at = excluded.updated_at
            """,
            values,
        )
        row = conn.execute(
            "SELECT * FROM leads WHERE source = ? AND external_id = ?",
            (payload.source, external_id),
        ).fetchone()

    if row is None:
        raise RuntimeError("Lead insert failed")
    return _row_to_lead(row)


def update_lead_status(lead_id: int, status: LeadStatus) -> Lead | None:
    with connection() as conn:
        conn.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), lead_id),
        )
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row is not None else None
