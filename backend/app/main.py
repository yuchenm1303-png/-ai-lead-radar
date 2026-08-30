from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .ai import get_ai_provider
from .connectors.manual import BrowserHelperAdapter, ManualImportAdapter
from .connectors.mock import MockConnector
from .notifications import notification_enabled, notify_high_score
from .pipeline import analyze_raw
from .schemas import (
    IngestRequest,
    Lead,
    LeadStatus,
    LeadStatusUpdate,
    ManualIngestRequest,
    MonitorStatus,
    ScanResponse,
)
from .storage import (
    database_health,
    get_lead,
    get_lead_by_dedupe,
    get_state,
    init_db,
    list_leads,
    make_dedupe_key,
    mark_notified,
    set_state,
    update_lead_status,
    upsert_lead,
    using_postgres,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


def _csv_env(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


app = FastAPI(title="AI Lead Radar API", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_csv_env(
        "CORS_ORIGINS",
        "https://smirel.com,https://www.smirel.com,http://localhost:3000,http://127.0.0.1:3000",
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Radar-Token"],
)


def _provider():
    return get_ai_provider()


def require_write_token(x_radar_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("RADAR_WRITE_TOKEN", "").strip()
    if expected and x_radar_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing write token")


def _ingest(raw_items) -> dict[str, object]:
    stored: list[Lead] = []
    filtered = 0
    created = 0
    notified = 0
    provider = _provider()

    for raw in raw_items:
        payload = analyze_raw(raw, provider)
        if payload is None:
            filtered += 1
            continue

        dedupe_key = make_dedupe_key(payload)
        existing = get_lead_by_dedupe(dedupe_key)
        lead = upsert_lead(payload)
        if existing is None:
            created += 1

        should_attempt_notification = existing is None or existing.notified_at is None
        if should_attempt_notification and notify_high_score(lead):
            notified += 1
            lead = mark_notified(lead.id) or lead
        stored.append(lead)

    return {
        "stored_leads": stored,
        "stored": len(stored),
        "filtered": filtered,
        "created": created,
        "notified": notified,
    }


def _ingest_request(payload: IngestRequest) -> dict[str, object]:
    adapter = ManualImportAdapter() if payload.adapter == "manual" else BrowserHelperAdapter()
    raw_items = [adapter.normalize(item) for item in payload.items]
    result = _ingest(raw_items)
    return {
        "ok": True,
        "adapter": adapter.name,
        "received": len(raw_items),
        "stored": result["stored"],
        "created": result["created"],
        "filtered": result["filtered"],
        "notified": result["notified"],
        "leads": result["stored_leads"],
    }


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": database_health(),
        "service": "ai-lead-radar-api",
        "version": "0.4.0",
        "database": "postgres" if using_postgres() else "sqlite",
        "ai_provider": _provider().name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/leads", response_model=list[Lead])
def get_leads(
    min_score: int = Query(default=0, ge=0, le=100),
    status: LeadStatus | None = None,
    include_non_leads: bool = False,
    source: str | None = Query(default=None, max_length=80),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Lead]:
    return list_leads(
        min_score=min_score,
        status=status,
        limit=limit,
        include_non_leads=include_non_leads,
        source=source,
        query=q,
    )


@app.get("/api/v1/leads/{lead_id}", response_model=Lead)
def get_lead_detail(lead_id: int) -> Lead:
    lead = get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.patch(
    "/api/v1/leads/{lead_id}/status",
    response_model=Lead,
    dependencies=[Depends(require_write_token)],
)
def patch_status(lead_id: int, payload: LeadStatusUpdate) -> Lead:
    lead = update_lead_status(lead_id, payload.status)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.post("/api/v1/ingest", dependencies=[Depends(require_write_token)])
def ingest(payload: IngestRequest) -> dict[str, object]:
    return _ingest_request(payload)


@app.post("/api/v1/ingest/manual", dependencies=[Depends(require_write_token)])
def manual_ingest(payload: ManualIngestRequest) -> dict[str, object]:
    return _ingest_request(IngestRequest(adapter="manual", items=payload.items))


@app.get("/api/v1/monitor/status", response_model=MonitorStatus)
def monitor_status() -> MonitorStatus:
    state = get_state("last_scan", {}) or {}
    last_scan_at = state.get("last_scan_at")
    return MonitorStatus(
        running=bool(state.get("running", False)),
        mode="safe-public-monitoring",
        platforms=["xiaohongshu"],
        connectors=["manual", "browser-helper", *_csv_env("SCAN_CONNECTORS", "mock")],
        last_scan_at=datetime.fromisoformat(last_scan_at) if last_scan_at else None,
        last_scan_counts=state.get("counts", {}),
        ai_provider=_provider().name,
        notification_enabled=notification_enabled(),
        note="Manual/Browser Helper ingest is enabled. Automatic connectors run only when explicitly configured.",
    )


@app.post(
    "/api/v1/monitor/scan",
    response_model=ScanResponse,
    dependencies=[Depends(require_write_token)],
)
def scan() -> ScanResponse:
    enabled = _csv_env("SCAN_CONNECTORS", "mock")
    raw_items = []
    used: list[str] = []

    if "mock" in enabled:
        connector = MockConnector()
        raw_items.extend(connector.fetch_new_items())
        used.append(connector.name)

    if not raw_items:
        raise HTTPException(
            status_code=409,
            detail=(
                "No approved automatic connector is enabled. Use /api/v1/ingest with manual/browser-helper data "
                "or configure an approved SourceAdapter."
            ),
        )

    set_state("last_scan", {"running": True, "last_scan_at": None, "counts": {}})
    try:
        result = _ingest(raw_items)
        scan_time = datetime.now(timezone.utc)
        counts = {
            "scanned": len(raw_items),
            "stored": int(result["stored"]),
            "created": int(result["created"]),
            "filtered": int(result["filtered"]),
            "high_intent": sum(1 for lead in result["stored_leads"] if lead.score >= 80),
            "notified": int(result["notified"]),
        }
        set_state(
            "last_scan",
            {"running": False, "last_scan_at": scan_time.isoformat(), "counts": counts},
        )
    except Exception:
        set_state(
            "last_scan",
            {
                "running": False,
                "last_scan_at": datetime.now(timezone.utc).isoformat(),
                "counts": {},
            },
        )
        raise

    return ScanResponse(
        ok=True,
        connectors=used,
        scanned=counts["scanned"],
        stored=counts["stored"],
        created=counts["created"],
        filtered=counts["filtered"],
        high_intent=counts["high_intent"],
        notified=counts["notified"],
        last_scan_at=scan_time,
    )
