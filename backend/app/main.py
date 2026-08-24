from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="AI Lead Radar API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LeadStatus = Literal["new", "saved", "contacted", "ignored"]


class Lead(BaseModel):
    id: int
    source: str
    title: str
    excerpt: str
    category: str
    score: int = Field(ge=0, le=100)
    published_at: datetime
    budget: str | None = None
    status: LeadStatus = "new"
    url: str | None = None
    signals: list[str] = []


SAMPLE_LEADS = [
    Lead(
        id=1,
        source="小红书",
        title="想找人做一个预约类微信小程序，有偿",
        excerpt="工作室需要预约、时间段选择和后台查看订单的小程序，预算可以沟通。",
        category="微信小程序",
        score=96,
        published_at=datetime.now(timezone.utc),
        budget="预算待聊",
        signals=["有偿", "明确需求", "近期项目"],
    ),
    Lead(
        id=2,
        source="小红书",
        title="公司准备做一个英文官网，求靠谱开发",
        excerpt="主要用于海外客户展示产品，希望手机端适配，后续可能还要接询盘表单。",
        category="企业官网",
        score=93,
        published_at=datetime.now(timezone.utc),
        budget="未公开",
        signals=["公司项目", "找开发", "官网"],
    ),
]


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "ai-lead-radar-api",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/leads", response_model=list[Lead])
def list_leads(
    min_score: int = Query(default=0, ge=0, le=100),
    status: LeadStatus | None = None,
) -> list[Lead]:
    leads = [lead for lead in SAMPLE_LEADS if lead.score >= min_score]
    if status is not None:
        leads = [lead for lead in leads if lead.status == status]
    return leads


@app.get("/api/v1/monitor/status")
def monitor_status() -> dict[str, object]:
    return {
        "running": False,
        "mode": "mock",
        "platforms": ["xiaohongshu"],
        "note": "MVP currently uses mock data; real data connectors are not enabled yet.",
    }
