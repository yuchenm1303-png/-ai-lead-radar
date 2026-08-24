from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LeadStatus = Literal["new", "saved", "contacted", "ignored"]


class LeadBase(BaseModel):
    source: str
    title: str
    excerpt: str
    category: str
    score: int = Field(default=0, ge=0, le=100)
    published_at: datetime
    budget: str | None = None
    status: LeadStatus = "new"
    url: str | None = None
    signals: list[str] = Field(default_factory=list)
    external_id: str | None = None


class LeadCreate(LeadBase):
    pass


class Lead(LeadBase):
    id: int
    created_at: datetime
    updated_at: datetime


class LeadStatusUpdate(BaseModel):
    status: LeadStatus


class MonitorStatus(BaseModel):
    running: bool
    mode: str
    platforms: list[str]
    last_scan_at: datetime | None = None
    note: str | None = None
