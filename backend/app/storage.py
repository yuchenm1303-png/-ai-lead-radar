from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .schemas import Lead, LeadCreate, LeadStatus

NEW_COLUMNS = {
    "discovered_at": "TEXT",
    "is_lead": "INTEGER NOT NULL DEFAULT 1",
    "intent_score": "INTEGER NOT NULL DEFAULT 0",
    "fit_score": "INTEGER NOT NULL DEFAULT 0",
    "freshness_score": "INTEGER NOT NULL DEFAULT 0",
    "urgency": "TEXT NOT NULL DEFAULT 'low'",
    "confidence": "INTEGER NOT NULL DEFAULT 0",
    "priority": "TEXT NOT NULL DEFAULT 'low'",
    "reason": "TEXT NOT NULL DEFAULT ''",
    "dedupe_key": "TEXT",
    "notified_at": "TEXT",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def using_postgres() -> bool:
    return _database_url().startswith(("postgres://", "postgresql://"))


def _sqlite_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", "./lead_radar.db"))


@contextmanager
def connection() -> Iterator[Any]:
    if using_postgres():
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(_database_url(), row_factory=dict_row)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    path = _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sql(statement: str) -> str:
    return statement.replace("?", "%s") if using_postgres() else statement


def _execute(conn: Any, statement: str, params: tuple[Any, ...] | list[Any] = ()):
    return conn.execute(_sql(statement), params)


def _table_columns(conn: Any) -> set[str]:
    if using_postgres():
        rows = _execute(
            conn,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'leads'",
        ).fetchall()
        return {row["column_name"] for row in rows}
    return {row["name"] for row in _execute(conn, "PRAGMA table_info(leads)").fetchall()}


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    raw = url.strip()
    try:
        parts = urlsplit(raw)
        clean_query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not (key.lower().startswith("utm_") or key.lower() in {"spm", "source", "from"})
        ]
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(clean_query), "")
        )
    except ValueError:
        return raw


def make_dedupe_key(payload: LeadCreate) -> str:
    if payload.dedupe_key:
        return payload.dedupe_key
    source = re.sub(r"\s+", " ", payload.source.strip().lower())
    if payload.external_id:
        identity = f"id|{source}|{payload.external_id.strip()}"
    elif normalize_url(payload.url):
        identity = f"url|{source}|{normalize_url(payload.url)}"
    else:
        title = re.sub(r"\s+", " ", payload.title.strip().lower())
        identity = f"fallback|{source}|{title}|{payload.published_at.date().isoformat()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _legacy_dedupe_key(row: Any) -> str:
    published_at = datetime.fromisoformat(row["published_at"])
    payload = LeadCreate(
        source=row["source"],
        external_id=row["external_id"],
        title=row["title"],
        excerpt=row["excerpt"],
        category=row["category"],
        score=row["score"],
        published_at=published_at,
        budget=row["budget"],
        status=row["status"],
        url=row["url"],
        signals=json.loads(row["signals_json"] or "[]"),
    )
    return make_dedupe_key(payload)


def init_db() -> None:
    with connection() as conn:
        if using_postgres():
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id BIGSERIAL PRIMARY KEY,
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
                """,
            )
        else:
            _execute(
                conn,
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
                """,
            )

        existing = _table_columns(conn)
        for name, ddl in NEW_COLUMNS.items():
            if name not in existing:
                _execute(conn, f"ALTER TABLE leads ADD COLUMN {name} {ddl}")

        _execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        _execute(conn, "CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC)")
        _execute(conn, "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
        _execute(conn, "CREATE INDEX IF NOT EXISTS idx_leads_published ON leads(published_at DESC)")

        rows = _execute(
            conn,
            "SELECT id, source, external_id, title, excerpt, category, score, published_at, budget, status, url, signals_json, created_at, discovered_at, dedupe_key FROM leads ORDER BY id",
        ).fetchall()
        seen: set[str] = set()
        for row in rows:
            dedupe = row["dedupe_key"] or _legacy_dedupe_key(row)
            if dedupe in seen:
                dedupe = hashlib.sha256(f"{dedupe}|legacy:{row['id']}".encode("utf-8")).hexdigest()
            seen.add(dedupe)
            discovered = row["discovered_at"] or row["created_at"] or row["published_at"]
            _execute(
                conn,
                "UPDATE leads SET dedupe_key = ?, discovered_at = ? WHERE id = ?",
                (dedupe, discovered, row["id"]),
            )

        _execute(conn, "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_dedupe ON leads(dedupe_key)")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_lead(row: Any) -> Lead:
    return Lead(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        title=row["title"],
        excerpt=row["excerpt"],
        category=row["category"],
        score=row["score"],
        is_lead=bool(row["is_lead"]),
        intent_score=row["intent_score"],
        fit_score=row["fit_score"],
        freshness_score=row["freshness_score"],
        urgency=row["urgency"],
        confidence=row["confidence"],
        priority=row["priority"],
        published_at=datetime.fromisoformat(row["published_at"]),
        discovered_at=_dt(row["discovered_at"]),
        budget=row["budget"],
        reason=row["reason"],
        status=row["status"],
        url=row["url"],
        signals=json.loads(row["signals_json"] or "[]"),
        dedupe_key=row["dedupe_key"],
        notified_at=_dt(row["notified_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def list_leads(
    min_score: int = 0,
    status: LeadStatus | None = None,
    limit: int = 100,
    include_non_leads: bool = False,
    source: str | None = None,
    query: str | None = None,
) -> list[Lead]:
    sql = "SELECT * FROM leads WHERE score >= ?"
    params: list[Any] = [min_score]
    if not include_non_leads:
        sql += " AND is_lead = 1"
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if query:
        sql += " AND (LOWER(title) LIKE LOWER(?) OR LOWER(excerpt) LIKE LOWER(?) OR LOWER(category) LIKE LOWER(?) OR LOWER(reason) LIKE LOWER(?))"
        needle = f"%{query}%"
        params.extend([needle, needle, needle, needle])
    sql += " ORDER BY score DESC, published_at DESC, discovered_at DESC LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = _execute(conn, sql, params).fetchall()
    return [_row_to_lead(row) for row in rows]


def get_lead(lead_id: int) -> Lead | None:
    with connection() as conn:
        row = _execute(conn, "SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def get_lead_by_dedupe(dedupe_key: str) -> Lead | None:
    with connection() as conn:
        row = _execute(conn, "SELECT * FROM leads WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
    return _row_to_lead(row) if row else None


def upsert_lead(payload: LeadCreate) -> Lead:
    now = utc_now()
    dedupe = make_dedupe_key(payload)
    external = payload.external_id or f"manual:{dedupe[:20]}"
    discovered = payload.discovered_at.isoformat() if payload.discovered_at else now
    values = (
        payload.source,
        external,
        payload.title,
        payload.excerpt,
        payload.category,
        payload.score,
        payload.published_at.isoformat(),
        discovered,
        payload.budget,
        payload.status,
        payload.url,
        json.dumps(payload.signals, ensure_ascii=False),
        int(payload.is_lead),
        payload.intent_score,
        payload.fit_score,
        payload.freshness_score,
        payload.urgency,
        payload.confidence,
        payload.priority,
        payload.reason,
        dedupe,
        now,
        now,
    )
    with connection() as conn:
        _execute(
            conn,
            """
            INSERT INTO leads(
                source, external_id, title, excerpt, category, score, published_at, discovered_at,
                budget, status, url, signals_json, is_lead, intent_score, fit_score, freshness_score,
                urgency, confidence, priority, reason, dedupe_key, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                title=excluded.title,
                excerpt=excluded.excerpt,
                category=excluded.category,
                score=excluded.score,
                published_at=excluded.published_at,
                budget=excluded.budget,
                url=excluded.url,
                signals_json=excluded.signals_json,
                is_lead=excluded.is_lead,
                intent_score=excluded.intent_score,
                fit_score=excluded.fit_score,
                freshness_score=excluded.freshness_score,
                urgency=excluded.urgency,
                confidence=excluded.confidence,
                priority=excluded.priority,
                reason=excluded.reason,
                updated_at=excluded.updated_at
            """,
            values,
        )
        row = _execute(conn, "SELECT * FROM leads WHERE dedupe_key = ?", (dedupe,)).fetchone()
    if row is None:
        raise RuntimeError("Lead upsert failed")
    return _row_to_lead(row)


def update_lead_status(lead_id: int, status: LeadStatus) -> Lead | None:
    with connection() as conn:
        _execute(conn, "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), lead_id))
        row = _execute(conn, "SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def mark_notified(lead_id: int) -> Lead | None:
    now = utc_now()
    with connection() as conn:
        _execute(conn, "UPDATE leads SET notified_at = ?, updated_at = ? WHERE id = ?", (now, now, lead_id))
        row = _execute(conn, "SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def set_state(key: str, value: object) -> None:
    now = utc_now()
    payload = json.dumps(value, ensure_ascii=False)
    with connection() as conn:
        _execute(
            conn,
            """
            INSERT INTO app_state(key, value_json, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (key, payload, now),
        )


def get_state(key: str, default: object = None) -> object:
    with connection() as conn:
        row = _execute(conn, "SELECT value_json FROM app_state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value_json"]) if row else default


def database_health() -> bool:
    try:
        with connection() as conn:
            _execute(conn, "SELECT 1").fetchone()
        return True
    except Exception:
        return False
