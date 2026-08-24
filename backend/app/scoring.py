from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .ai.base import ClassificationResult
from .ai.heuristic import HeuristicClassifier
from .connectors.base import RawLead


@dataclass(frozen=True)
class ScoreResult:
    score: int
    category: str
    signals: list[str]


def freshness_score(published_at: datetime, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (now - published_at.astimezone(timezone.utc)).total_seconds())
    minutes = age_seconds / 60
    if minutes <= 15:
        return 100
    if minutes <= 60:
        return 95
    if minutes <= 360:
        return 85
    if minutes <= 1440:
        return 70
    if minutes <= 4320:
        return 50
    if minutes <= 10080:
        return 30
    return 15


def budget_urgency_score(classification: ClassificationResult, explicit_budget: str | None) -> int:
    urgency = {"low": 25, "normal": 50, "high": 80, "urgent": 100}[classification.urgency]
    budget_text = classification.budget_text or explicit_budget or ""
    budget = 85 if budget_text and budget_text not in {"—", "未公开", "未提供"} else 40
    return round(0.6 * urgency + 0.4 * budget)


def final_score(classification: ClassificationResult, published_at: datetime, explicit_budget: str | None = None) -> tuple[int, int]:
    fresh = freshness_score(published_at)
    tail = budget_urgency_score(classification, explicit_budget)
    score = round(
        0.40 * classification.intent_score
        + 0.30 * fresh
        + 0.20 * classification.fit_score
        + 0.10 * tail
    )
    if not classification.is_lead:
        score = min(score, 49)
    if classification.confidence < 0.5:
        score = min(score, 69)
    return max(0, min(100, score)), fresh


def score_text(title: str, excerpt: str = "") -> ScoreResult:
    now = datetime.now(timezone.utc)
    raw = RawLead("manual", None, title, excerpt, now)
    classified = HeuristicClassifier().classify(raw)
    score, _ = final_score(classified, now)
    return ScoreResult(score=score, category=classified.need_type, signals=list(classified.signals))
