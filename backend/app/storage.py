from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .schemas import Lead, LeadCreate, LeadStatus
from .settings import get_settings


@dataclass(frozen=True)
class UpsertResult:
    lead: Lead
    created: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", get_settings().database_path))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not (k.lower().startswith("utm_") or k.lower() in {"spm", "source", "from"})]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))
    except ValueError:
        return url.strip()


def make_dedupe_key(source: str, external_id: str | None, url: str | None, title: str, published_at: datetime) -> str:
    source_norm = re.sub(r"\s+", " ", source.strip().lower())
    if external_id:
        identity = f"id|{source_norm}|{external_id.strip()}"
    elif normalize_url(url):
        identity = f"url|{source_norm}|{normalize_url(url)}"
    else:
        title_norm = re.sub(r"\s+", " ", title.strip().lower())
        identity = f"fallback|{source_norm}|{title_norm}|{published_at.date().isoformat()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    definitions = {
        "discovered_at": "TEXT",
        "is_lead": "INTEGER NOT NULL DEFAULT 1",
        "intent_score": "INTEGER NOT NULL DEFAULT 0",
        "fit_score": "INTEGER NOT NULL DEFAULT 0",
        "freshness_score": "INTEGER NOT NULL DEFAULT 0",
        "urgency": "TEXT NOT NULL DEFAULT 'normal'",
        "reason": "TEXT NOT NULL DEFAULT ''",
        "confidence": "REAL NOT NULL DEFAULT 0",
        "dedupe_key": "TEXT",
        "notified_at": "TEXT",
    }
    existing = _columns(conn)
    for name, definition in definitions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {name} {definition}")


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
                discovered_at TEXT,
                budget TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                url TEXT,
                signals_json TEXT NOT NULL DEFAULT '[]',
                is_lead INTEGER NOT NULL DEFAULT 1,
                intent_score INTEGER NOT NULL DEFAULT 0,
                fit_score INTEGER NOT NULL DEFAULT 0,
                freshness_score INTEGER NOT NULL DEFAULT 0,
                urgency TEXT NOT NULL DEFAULT 'normal',
                reason TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                dedupe_key TEXT,
                notified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source, external_id)
            )
            """
        )
        _ensure_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_published ON leads(published_at DESC)")

        rows = conn.execute("SELECT id, source, external_id, url, title, published_at, created_at, discovered_at, dedupe_key FROM leads").fetchall()
        for row in rows:
            published = datetime.fromisoformat(row["published_at"])
            dedupe = row["dedupe_key"] or make_dedupe_key(row["source"], row["external_id"], row["url"], row["title"], published)
            discovered = row["discovered_at"] or row["created_at"] or row["published_at"]
            conn.execute("UPDATE leads SET dedupe_key = ?, discovered_at = ? WHERE id = ?", (dedupe, discovered, row["id"]))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_dedupe ON leads(dedupe_key)")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


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
        discovered_at=_dt(row["discovered_at"]),
        budget=row["budget"],
        status=row["status"],
        url=row["url"],
        signals=json.loads(row["signals_json"] or "[]"),
        is_lead=bool(row["is_lead"]),
        intent_score=row["intent_score"],
        fit_score=row["fit_score"],
        freshness_score=row["freshness_score"],
        urgency=row["urgency"],
        reason=row["reason"],
        confidence=float(row["confidence"]),
        dedupe_key=row["dedupe_key"],
        notified_at=_dt(row["notified_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def list_leads(
    min_score: int = 0,
    status: LeadStatus | None = None,
    is_lead: bool | None = True,
    source: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[Lead]:
    sql = "SELECT * FROM leads WHERE score >= ?"
    params: list[object] = [min_score]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if is_lead is not None:
        sql += " AND is_lead = ?"
        params.append(1 if is_lead else 0)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if query:
        sql += " AND (title LIKE ? OR excerpt LIKE ? OR category LIKE ? OR reason LIKE ?)"
        needle = f"%{query}%"
        params.extend([needle, needle, needle, needle])
    sql += " ORDER BY score DESC, published_at DESC, discovered_at DESC LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_lead(row) for row in rows]


def get_lead(lead_id: int) -> Lead | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def upsert_lead(payload: LeadCreate, dedupe_key: str | None = None) -> UpsertResult:
    discovered_at = payload.discovered_at or datetime.now(timezone.utc)
    dedupe_key = dedupe_key or make_dedupe_key(payload.source, payload.external_id, payload.url, payload.title, payload.published_at)
    now = utc_now()
    with connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        if row is None and payload.external_id:
            row = conn.execute("SELECT * FROM leads WHERE source = ? AND external_id = ?", (payload.source, payload.external_id)).fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO leads (
                    source, external_id, title, excerpt, category, score, published_at, discovered_at,
                    budget, status, url, signals_json, is_lead, intent_score, fit_score,
                    freshness_score, urgency, reason, confidence, dedupe_key, notified_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    payload.source, payload.external_id, payload.title, payload.excerpt, payload.category,
                    payload.score, payload.published_at.isoformat(), discovered_at.isoformat(), payload.budget,
                    payload.status, payload.url, json.dumps(payload.signals, ensure_ascii=False), int(payload.is_lead),
                    payload.intent_score, payload.fit_score, payload.freshness_score, payload.urgency, payload.reason,
                    payload.confidence, dedupe_key, now, now,
                ),
            )
            row = conn.execute("SELECT * FROM leads WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
            created = True
        else:
            conn.execute(
                """
                UPDATE leads SET
                    source = ?, external_id = ?, title = ?, excerpt = ?, category = ?, score = ?,
                    published_at = ?, discovered_at = ?, budget = ?, url = ?, signals_json = ?, is_lead = ?,
                    intent_score = ?, fit_score = ?, freshness_score = ?, urgency = ?, reason = ?, confidence = ?,
                    dedupe_key = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.source, payload.external_id, payload.title, payload.excerpt, payload.category,
                    payload.score, payload.published_at.isoformat(), discovered_at.isoformat(), payload.budget,
                    payload.url, json.dumps(payload.signals, ensure_ascii=False), int(payload.is_lead),
                    payload.intent_score, payload.fit_score, payload.freshness_score, payload.urgency, payload.reason,
                    payload.confidence, dedupe_key, now, row["id"],
                ),
            )
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (row["id"],)).fetchone()
            created = False

    if row is None:
        raise RuntimeError("Lead upsert failed")
    return UpsertResult(_row_to_lead(row), created)


def update_lead_status(lead_id: int, status: LeadStatus) -> Lead | None:
    with connection() as conn:
        conn.execute("UPDATE leads SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), lead_id))
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def mark_notified(lead_id: int) -> Lead | None:
    with connection() as conn:
        now = utc_now()
        conn.execute("UPDATE leads SET notified_at = ?, updated_at = ? WHERE id = ?", (now, now, lead_id))
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def set_state(key: str, value: object) -> None:
    with connection() as conn:
        conn.execute(
            """INSERT INTO app_state(key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
            (key, json.dumps(value, ensure_ascii=False), utc_now()),
        )


def get_state(key: str, default: object = None) -> object:
    with connection() as conn:
        row = conn.execute("SELECT value_json FROM app_state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value_json"]) if row else default


def database_health() -> bool:
    try:
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
