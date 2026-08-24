from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

LeadStatus = Literal['new','saved','contacted','ignored']
Urgency = Literal['low','medium','high']

class LeadBase(BaseModel):
    source: str
    external_id: str | None = None
    title: str
    excerpt: str = ''
    category: str = '其他开发'
    score: int = Field(default=0, ge=0, le=100)
    is_lead: bool = True
    intent_score: int = Field(default=0, ge=0, le=100)
    fit_score: int = Field(default=0, ge=0, le=100)
    freshness_score: int = Field(default=0, ge=0, le=100)
    urgency: Urgency = 'low'
    confidence: int = Field(default=0, ge=0, le=100)
    priority: str = 'low'
    published_at: datetime
    discovered_at: datetime | None = None
    budget: str | None = None
    reason: str = ''
    status: LeadStatus = 'new'
    url: str | None = None
    signals: list[str] = Field(default_factory=list)
    dedupe_key: str | None = None

class LeadCreate(LeadBase):
    pass

class Lead(LeadBase):
    id: int
    created_at: datetime
    updated_at: datetime

class LeadStatusUpdate(BaseModel):
    status: LeadStatus

class ManualLeadItem(BaseModel):
    source: str = 'manual'
    external_id: str | None = None
    title: str
    excerpt: str = ''
    published_at: datetime
    budget: str | None = None
    url: str | None = None

class ManualIngestRequest(BaseModel):
    items: list[ManualLeadItem] = Field(min_length=1, max_length=200)

class MonitorStatus(BaseModel):
    running: bool
    mode: str
    platforms: list[str]
    last_scan_at: datetime | None = None
    ai_provider: str
    notification_enabled: bool
    note: str | None = None
