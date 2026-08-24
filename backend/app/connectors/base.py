from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RawLead:
    source: str
    external_id: str | None
    title: str
    excerpt: str
    published_at: datetime
    url: str | None = None
    budget: str | None = None


class SourceAdapter(Protocol):
    name: str

    def fetch_new_items(self) -> list[RawLead]:
        ...


LeadConnector = SourceAdapter
