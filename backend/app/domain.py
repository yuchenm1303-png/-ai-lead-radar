from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ActorRole(StrEnum):
    BUYER = "buyer"
    PROVIDER = "provider"
    RECRUITER = "recruiter"
    LEARNER = "learner"
    CONTENT = "content"
    UNKNOWN = "unknown"


class BuyingStage(StrEnum):
    EXPLICIT = "explicit"
    PAID = "paid"
    CONSIDERING = "considering"
    PROBLEM = "problem"
    NONE = "none"


@dataclass(frozen=True)
class PolicyAssessment:
    policy_version: str
    actor_role: ActorRole
    buying_stage: BuyingStage
    is_lead: bool
    category: str
    topic_hits: list[str]
    intent_hits: list[str]
    negative_hits: list[str]
    intent_score: int
    fit_score: int
    actionability_score: int
    confidence: int
    reason_codes: list[str]
    evidence: list[str]
