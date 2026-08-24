from __future__ import annotations

import logging

from .base import ClassificationResult, LeadClassifier
from .heuristic import HeuristicClassifier
from .openai_provider import OpenAIClassifier
from ..connectors.base import RawLead
from ..settings import get_settings

logger = logging.getLogger(__name__)


class ResilientClassifier:
    def __init__(self, primary: LeadClassifier, fallback: LeadClassifier):
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+fallback"

    def classify(self, item: RawLead) -> ClassificationResult:
        try:
            return self.primary.classify(item)
        except Exception as exc:
            logger.warning("AI provider failed; using heuristic fallback: %s", exc)
            fallback = self.fallback.classify(item)
            return ClassificationResult(
                is_lead=fallback.is_lead,
                need_type=fallback.need_type,
                intent_score=fallback.intent_score,
                fit_score=fallback.fit_score,
                urgency=fallback.urgency,
                budget_text=fallback.budget_text,
                reason=f"AI 服务暂不可用，已回退规则判断。{fallback.reason}",
                confidence=min(fallback.confidence, 0.72),
                signals=tuple(dict.fromkeys((*fallback.signals, "AI fallback"))),
            )


def get_classifier() -> LeadClassifier:
    settings = get_settings()
    heuristic = HeuristicClassifier()
    if settings.ai_provider == "openai" and settings.openai_api_key and settings.openai_model:
        return ResilientClassifier(
            OpenAIClassifier(settings.openai_api_key, settings.openai_model),
            heuristic,
        )
    return heuristic
