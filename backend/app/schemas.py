from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, computed_field

LeadStatus = Literal["new", "saved", "contacted", "ignored"]
UrgencyLevel = Literal["low", "normal", "high", "urgent"]


class LeadBase(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(default="", max_length=4000)
    category: str = Field(default="其他开发", max_length=80)
    score: int = Field(default=0, ge=0, le=100)
    published_at: datetime
    discovered_at: datetime | None = None
    budget: str | None = Field(default=None, max_length=200)
    status: LeadStatus = "new"
    url: str | None = Field(default=None, max_length=2000)
    signals: list[str] = Field(default_factory=list, max_length=20)
    external_id: str | None = Field(default=None, max_length=300)
    is_lead: bool = True
    intent_score: int = Field(default=0, ge=0, le=100)
    fit_score: int = Field(default=0, ge=0, le=100)
    freshness_score: int = Field(default=0, ge=0, le=100)
    urgency: UrgencyLevel = "normal"
    reason: str = Field(default="", max_length=1200)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LeadCreate(LeadBase):
    pass


class Lead(LeadBase):
    id: int
    dedupe_key: str
    notified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def source_id(self) -> str | None:
        return self.external_id

    @computed_field
    @property
    def content(self) -> str:
        return self.excerpt

    @computed_field
    @property
    def need_type(self) -> str:
        return self.category

    @computed_field
    @property
    def ai_score(self) -> int:
        return self.score

    @computed_field
    @property
    def budget_text(self) -> str | None:
        return self.budget


class LeadStatusUpdate(BaseModel):
    status: LeadStatus


class IngestItem(BaseModel):
    source: str = Field(default="manual", min_length=1, max_length=80)
    source_id: str | None = Field(default=None, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=4000)
    url: str | None = Field(default=None, max_length=2000)
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    budget_text: str | None = Field(default=None, max_length=200)


class IngestRequest(BaseModel):
    adapter: Literal["manual", "browser-helper"] = "manual"
    items: list[IngestItem] = Field(min_length=1, max_length=100)


class IngestSummary(BaseModel):
    received: int
    prefiltered: int
    stored: int
    created: int
    updated: int
    filtered_out: int
    notified: int
    ai_provider: str
    leads: list[Lead]


class MonitorStatus(BaseModel):
    running: bool
    mode: str
    platforms: list[str]
    connectors: list[str]
    ai_provider: str
    notification_enabled: bool
    last_scan_at: datetime | None = None
    last_scan_counts: dict[str, int] = Field(default_factory=dict)
    note: str | None = None


class ScanResponse(BaseModel):
    ok: bool
    connectors: list[str]
    scanned: int
    stored: int
    created: int
    high_intent: int
    notified: int
    last_scan_at: datetime
