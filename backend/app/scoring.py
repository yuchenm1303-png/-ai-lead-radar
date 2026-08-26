from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .domain import ActorRole, BuyingStage
from .policy import assess_text, load_policy


@dataclass(frozen=True)
class PrefilterResult:
    passed: bool
    service_hits: list[str]
    intent_hits: list[str]
    negative_hits: list[str]
    direct_buyer: bool = False


@dataclass(frozen=True)
class ScoreResult:
    score: int
    category: str
    is_lead: bool
    intent_score: int
    fit_score: int
    freshness_score: int
    urgency: str
    confidence: int
    priority: str
    budget: str | None
    reason: str
    signals: list[str]


INTENT_LABELS = {
    "explicit_outsource": "明确找开发方",
    "paid_request": "付费/预算",
    "build_intent": "建设意图",
    "problem_help": "问题求助",
}


def _freshness(published_at: datetime | None) -> int:
    if not published_at:
        return 50
    dt = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    hours = max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600)
    if hours <= 0.5:
        return 100
    if hours <= 2:
        return 94
    if hours <= 6:
        return 86
    if hours <= 24:
        return 72
    if hours <= 72:
        return 52
    if hours <= 168:
        return 35
    return 15


def _urgency(text: str) -> str:
    policy = load_policy()
    lowered = text.lower()
    if any(str(word).lower() in lowered for word in policy.get("urgency", {}).get("high", [])):
        return "high"
    if any(str(word).lower() in lowered for word in policy.get("urgency", {}).get("medium", [])):
        return "medium"
    return "low"


def prefilter_text(title: str, excerpt: str = "") -> PrefilterResult:
    assessment = assess_text(title, excerpt)
    service_hits = [assessment.category] if assessment.topic_hits else []
    intent_hits = [INTENT_LABELS.get(key, key) for key in assessment.intent_hits]
    return PrefilterResult(
        passed=assessment.is_lead,
        service_hits=service_hits,
        intent_hits=intent_hits,
        negative_hits=assessment.negative_hits,
        direct_buyer=assessment.actor_role == ActorRole.BUYER,
    )


def score_text(
    title: str,
    excerpt: str = "",
    published_at: datetime | None = None,
    budget: str | None = None,
) -> ScoreResult:
    assessment = assess_text(title, excerpt)
    freshness = _freshness(published_at)
    intent = assessment.intent_score
    actionability = assessment.actionability_score
    if budget:
        intent = max(intent, 82)
        if assessment.actor_role == ActorRole.BUYER:
            actionability = max(actionability, 88)

    weights = load_policy().get("scoring", {}).get("weights", {})
    score = round(
        intent * float(weights.get("intent", 0.4))
        + assessment.fit_score * float(weights.get("fit", 0.2))
        + freshness * float(weights.get("freshness", 0.2))
        + actionability * float(weights.get("actionability", 0.2))
    )
    threshold = int(load_policy().get("scoring", {}).get("lead_threshold", 65))
    is_lead = assessment.is_lead and score >= threshold
    if not is_lead:
        score = min(score, threshold - 1)
    score = max(0, min(100, score))

    high_threshold = int(load_policy().get("scoring", {}).get("high_threshold", 85))
    priority = "high" if is_lead and score >= high_threshold else ("medium" if is_lead and score >= 70 else "low")
    urgency = _urgency(f"{title} {excerpt}")

    signals: list[str] = []
    if assessment.topic_hits:
        signals.append(assessment.category)
    if assessment.actor_role == ActorRole.BUYER:
        signals.append("角色:需求方")
    if "intent:direct_buyer" in assessment.reason_codes or assessment.buying_stage == BuyingStage.EXPLICIT:
        signals.append("明确找开发方")
    signals.extend(INTENT_LABELS.get(key, key) for key in assessment.intent_hits)
    signals.extend(f"排除:{label}" for label in assessment.negative_hits)
    signals = list(dict.fromkeys(signals))[:8]

    reason_parts = [
        f"角色={assessment.actor_role.value}",
        f"阶段={assessment.buying_stage.value}",
        f"类型={assessment.category}" if assessment.topic_hits else "",
        f"排除={'、'.join(assessment.negative_hits)}" if assessment.negative_hits else "",
        f"policy={assessment.policy_version}",
    ]

    return ScoreResult(
        score=score,
        category=assessment.category,
        is_lead=is_lead,
        intent_score=intent,
        fit_score=assessment.fit_score,
        freshness_score=freshness,
        urgency=urgency,
        confidence=assessment.confidence,
        priority=priority,
        budget=budget,
        reason="；".join(part for part in reason_parts if part),
        signals=signals,
    )
