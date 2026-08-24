from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .connectors.manual import BrowserHelperAdapter, ManualImportAdapter
from .connectors.mock import MockConnector
from .pipeline import process_items
from .schemas import (
    IngestRequest,
    IngestSummary,
    Lead,
    LeadCreate,
    LeadStatus,
    LeadStatusUpdate,
    MonitorStatus,
    ScanResponse,
)
from .scoring import score_text
from .settings import get_settings
from .storage import (
    database_health,
    get_lead,
    get_state,
    init_db,
    list_leads,
    set_state,
    update_lead_status,
    upsert_lead,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(title="AI Lead Radar API", version="0.5.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Radar-Token"],
)


def require_write_token(x_radar_token: str | None = Header(default=None)) -> None:
    expected = get_settings().write_api_token
    if expected and x_radar_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing write token")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": database_health(),
        "service": "ai-lead-radar-api",
        "version": "0.5.0",
        "database": "sqlite",
        "ai_provider": get_settings().ai_provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/leads", response_model=list[Lead])
def get_leads(
    min_score: int = Query(default=0, ge=0, le=100),
    status: LeadStatus | None = None,
    is_lead: bool | None = True,
    source: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Lead]:
    return list_leads(min_score=min_score, status=status, is_lead=is_lead, source=source, query=q, limit=limit)


@app.get("/api/v1/leads/{lead_id}", response_model=Lead)
def get_lead_by_id(lead_id: int) -> Lead:
    lead = get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.post("/api/v1/leads", response_model=Lead, dependencies=[Depends(require_write_token)])
def create_lead(payload: LeadCreate) -> Lead:
    if payload.score == 0:
        scored = score_text(payload.title, payload.excerpt)
        payload = payload.model_copy(update={"score": scored.score, "category": scored.category, "signals": scored.signals})
    return upsert_lead(payload).lead


@app.patch("/api/v1/leads/{lead_id}/status", response_model=Lead, dependencies=[Depends(require_write_token)])
def patch_lead_status(lead_id: int, payload: LeadStatusUpdate) -> Lead:
    lead = update_lead_status(lead_id, payload.status)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.post("/api/v1/ingest", response_model=IngestSummary, dependencies=[Depends(require_write_token)])
def ingest(payload: IngestRequest) -> IngestSummary:
    adapter = ManualImportAdapter() if payload.adapter == "manual" else BrowserHelperAdapter()
    raw_items = [adapter.normalize(item) for item in payload.items]
    return process_items(raw_items)


@app.get("/api/v1/monitor/status", response_model=MonitorStatus)
def get_monitor_status() -> MonitorStatus:
    state = get_state("last_scan", {}) or {}
    last_scan_at = state.get("last_scan_at")
    return MonitorStatus(
        running=False,
        mode="safe-public-monitoring",
        platforms=["xiaohongshu"],
        connectors=["manual", "browser-helper", *get_settings().scan_connectors],
        ai_provider=get_settings().ai_provider,
        notification_enabled=get_settings().notification_enabled,
        last_scan_at=datetime.fromisoformat(last_scan_at) if last_scan_at else None,
        last_scan_counts=state.get("counts", {}),
        note="Manual/Browser Helper ingestion is enabled. Automatic connectors must be explicitly configured and must follow platform rules.",
    )


@app.post("/api/v1/monitor/scan", response_model=ScanResponse, dependencies=[Depends(require_write_token)])
def run_scan() -> ScanResponse:
    enabled = get_settings().scan_connectors
    raw_items = []
    used: list[str] = []
    if "mock" in enabled:
        connector = MockConnector()
        raw_items.extend(connector.fetch_new_items())
        used.append(connector.name)

    if not raw_items:
        raise HTTPException(
            status_code=409,
            detail="No automatic source connector is enabled. Use /api/v1/ingest with manual or browser-helper data, or configure an approved connector.",
        )

    result = process_items(raw_items)
    scan_time = datetime.now(timezone.utc)
    counts = {
        "scanned": len(raw_items),
        "stored": result.stored,
        "created": result.created,
        "high_intent": sum(1 for lead in result.leads if lead.is_lead and lead.score >= 80),
        "notified": result.notified,
    }
    set_state("last_scan", {"last_scan_at": scan_time.isoformat(), "counts": counts})
    return ScanResponse(
        ok=True,
        connectors=used,
        scanned=counts["scanned"],
        stored=counts["stored"],
        created=counts["created"],
        high_intent=counts["high_intent"],
        notified=counts["notified"],
        last_scan_at=scan_time,
    )
