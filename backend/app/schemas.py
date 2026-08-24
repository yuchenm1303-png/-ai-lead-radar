from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, computed_field

LeadStatus = Literal["new", "saved", "contacted", "ignored"]
Urgency = Literal["low", "medium", "high"]
IngestAdapter = Literal["manual", "browser-helper"]


class LeadBase(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    external_id: str | None = Field(default=None, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(default="", max_length=4000)
    category: str = Field(default="其他开发", max_length=80)
    score: int = Field(default=0, ge=0, le=100)
    is_lead: bool = True
    intent_score: int = Field(default=0, ge=0, le=100)
    fit_score: int = Field(default=0, ge=0, le=100)
    freshness_score: int = Field(default=0, ge=0, le=100)
    urgency: Urgency = "low"
    confidence: int = Field(default=0, ge=0, le=100)
    priority: str = Field(default="low", max_length=40)
    published_at: datetime
    discovered_at: datetime | None = None
    budget: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="", max_length=1500)
    status: LeadStatus = "new"
    url: str | None = Field(default=None, max_length=2000)
    signals: list[str] = Field(default_factory=list, max_length=20)
    dedupe_key: str | None = Field(default=None, max_length=128)


class LeadCreate(LeadBase):
    pass


class Lead(LeadBase):
    id: int
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


class ManualLeadItem(BaseModel):
    source: str = Field(default="manual", min_length=1, max_length=80)
    external_id: str | None = Field(default=None, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(default="", max_length=4000)
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    budget: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)


class ManualIngestRequest(BaseModel):
    items: list[ManualLeadItem] = Field(min_length=1, max_length=200)


class IngestRequest(BaseModel):
    adapter: IngestAdapter = "manual"
    items: list[ManualLeadItem] = Field(min_length=1, max_length=200)


class MonitorStatus(BaseModel):
    running: bool
    mode: str
    platforms: list[str]
    connectors: list[str] = Field(default_factory=list)
    last_scan_at: datetime | None = None
    last_scan_counts: dict[str, int] = Field(default_factory=dict)
    ai_provider: str
    notification_enabled: bool
    note: str | None = None


class ScanResponse(BaseModel):
    ok: bool
    connectors: list[str]
    scanned: int
    stored: int
    created: int
    filtered: int
    high_intent: int
    notified: int
    last_scan_at: datetime
