from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..connectors.base import RawLead
from ..schemas import UrgencyLevel


@dataclass(frozen=True)
class ClassificationResult:
    is_lead: bool
    need_type: str
    intent_score: int
    fit_score: int
    urgency: UrgencyLevel
    budget_text: str | None
    reason: str
    confidence: float
    signals: tuple[str, ...]


class LeadClassifier(Protocol):
    name: str

    def classify(self, item: RawLead) -> ClassificationResult:
        ...
