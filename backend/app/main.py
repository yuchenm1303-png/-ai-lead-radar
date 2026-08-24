from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .connectors.mock import MockConnector
from .schemas import Lead, LeadCreate, LeadStatus, LeadStatusUpdate, MonitorStatus
from .scoring import score_text
from .storage import init_db, list_leads, update_lead_status, upsert_lead

monitor_state: dict[str, object] = {
    "running": False,
    "mode": "mock",
    "platforms": ["xiaohongshu"],
    "last_scan_at": None,
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Lead Radar API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "ai-lead-radar-api",
        "version": "0.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/leads", response_model=list[Lead])
def get_leads(
    min_score: int = Query(default=0, ge=0, le=100),
    status: LeadStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Lead]:
    return list_leads(min_score=min_score, status=status, limit=limit)


@app.post("/api/v1/leads", response_model=Lead)
def create_lead(payload: LeadCreate) -> Lead:
    if payload.score == 0:
        scored = score_text(payload.title, payload.excerpt)
        payload = payload.model_copy(
            update={
                "score": scored.score,
                "category": scored.category,
                "signals": scored.signals,
            }
        )
    return upsert_lead(payload)


@app.patch("/api/v1/leads/{lead_id}/status", response_model=Lead)
def patch_lead_status(lead_id: int, payload: LeadStatusUpdate) -> Lead:
    lead = update_lead_status(lead_id, payload.status)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.get("/api/v1/monitor/status", response_model=MonitorStatus)
def get_monitor_status() -> MonitorStatus:
    return MonitorStatus(
        running=bool(monitor_state["running"]),
        mode=str(monitor_state["mode"]),
        platforms=list(monitor_state["platforms"]),
        last_scan_at=monitor_state["last_scan_at"],
        note="Mock connector only. Real platform connectors are not enabled yet.",
    )


@app.post("/api/v1/monitor/scan")
def run_mock_scan() -> dict[str, object]:
    connector = MockConnector()
    monitor_state["running"] = True

    stored: list[Lead] = []
    for raw in connector.fetch_latest():
        scored = score_text(raw.title, raw.excerpt)
        stored.append(
            upsert_lead(
                LeadCreate(
                    source=raw.source,
                    external_id=raw.external_id,
                    title=raw.title,
                    excerpt=raw.excerpt,
                    category=scored.category,
                    score=scored.score,
                    published_at=raw.published_at,
                    budget=raw.budget,
                    url=raw.url,
                    signals=scored.signals,
                )
            )
        )

    scan_time = datetime.now(timezone.utc)
    monitor_state["last_scan_at"] = scan_time
    monitor_state["running"] = False

    return {
        "ok": True,
        "connector": connector.name,
        "scanned": len(stored),
        "high_intent": sum(1 for lead in stored if lead.score >= 80),
        "last_scan_at": scan_time.isoformat(),
    }
