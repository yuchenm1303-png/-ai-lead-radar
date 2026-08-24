from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RawLead:
    source: str
    external_id: str
    title: str
    excerpt: str
    published_at: datetime
    url: str | None = None
    budget: str | None = None


class LeadConnector(Protocol):
    name: str

    def fetch_latest(self) -> list[RawLead]:
        ...
