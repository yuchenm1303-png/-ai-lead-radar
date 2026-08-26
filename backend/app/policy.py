from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .domain import ActorRole, BuyingStage, PolicyAssessment

POLICY_PATH = Path(__file__).resolve().parents[2] / "supabase" / "functions" / "_shared" / "lead_policy.json"


@lru_cache(maxsize=1)
def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def policy_version() -> str:
    return str(load_policy().get("version") or "unknown")


def _text(title: str, excerpt: str = "") -> str:
    return f"{title or ''} {excerpt or ''}".strip().lower()


def _topic_terms(policy: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for topic in policy.get("topics", []):
        for term in topic.get("terms", []):
            value = str(term).strip().lower()
            if value and value not in terms:
                terms.append(value)
    return sorted(terms, key=len, reverse=True)


def _topic_pattern(policy: dict[str, Any]) -> str:
    return "(?:" + "|".join(re.escape(term) for term in _topic_terms(policy)) + ")"


def _matched_topics(text: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for topic in policy.get("topics", []):
        if any(str(term).lower() in text for term in topic.get("terms", [])):
            matched.append(topic)
    return matched


def _matched_intents(text: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for family in policy.get("intent_families", []):
        if any(str(term).lower() in text for term in family.get("terms", [])):
            matched.append(family)
    return matched


def _actor_match(text: str, policy: dict[str, Any]) -> tuple[ActorRole, list[str], list[str]]:
    labels: list[str] = []
    evidence: list[str] = []
    for rule in policy.get("actor_rules", []):
        for raw_pattern in rule.get("patterns", []):
            if re.search(str(raw_pattern), text, flags=re.IGNORECASE):
                role = ActorRole(str(rule.get("role") or "unknown"))
                label = str(rule.get("label") or role.value)
                labels.append(label)
                evidence.append(f"actor:{label}")
                return role, labels, evidence
    return ActorRole.UNKNOWN, labels, evidence


def _buyer_match(text: str, policy: dict[str, Any]) -> bool:
    if not _matched_topics(text, policy):
        return False
    topic = _topic_pattern(policy)
    for raw_pattern in policy.get("buyer_patterns", []):
        pattern = str(raw_pattern).replace("{topic}", topic)
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def _buying_stage(intent_keys: set[str], direct_buyer: bool) -> BuyingStage:
    if "explicit_outsource" in intent_keys:
        return BuyingStage.EXPLICIT
    if "paid_request" in intent_keys and direct_buyer:
        return BuyingStage.PAID
    if "build_intent" in intent_keys or direct_buyer:
        return BuyingStage.CONSIDERING
    if "problem_help" in intent_keys:
        return BuyingStage.PROBLEM
    return BuyingStage.NONE


def assess_text(title: str, excerpt: str = "") -> PolicyAssessment:
    policy = load_policy()
    text = _text(title, excerpt)
    topics = _matched_topics(text, policy)
    intents = _matched_intents(text, policy)
    intent_keys = {str(item.get("key")) for item in intents}
    actor_role, negative_hits, actor_evidence = _actor_match(text, policy)
    direct_buyer = _buyer_match(text, policy)

    if actor_role == ActorRole.UNKNOWN and direct_buyer:
        actor_role = ActorRole.BUYER

    intent_score = min(100, sum(int(item.get("weight") or 0) for item in intents))
    if direct_buyer:
        if "explicit_outsource" in intent_keys:
            intent_score = max(intent_score, 88)
        elif "paid_request" in intent_keys:
            intent_score = max(intent_score, 82)
        else:
            intent_score = max(intent_score, 72)

    fit_score = 92 if topics else 0
    stage = _buying_stage(intent_keys, direct_buyer)
    scoring = policy.get("scoring", {})
    if actor_role == ActorRole.BUYER:
        if stage == BuyingStage.EXPLICIT:
            actionability = int(scoring.get("explicit_actionability", 95))
        elif stage in {BuyingStage.PAID, BuyingStage.CONSIDERING, BuyingStage.PROBLEM}:
            actionability = int(scoring.get("buyer_base_actionability", 78))
        else:
            actionability = 65
    elif actor_role == ActorRole.UNKNOWN:
        actionability = int(scoring.get("unknown_actionability", 25))
    else:
        actionability = 5

    is_lead = bool(
        actor_role == ActorRole.BUYER
        and topics
        and intent_score >= 55
        and actionability >= 70
    )

    topic_hits = [str(item.get("key")) for item in topics]
    intent_hits = [str(item.get("key")) for item in intents]
    category = str(topics[0].get("category")) if topics else "其他开发"
    reason_codes: list[str] = []
    evidence: list[str] = []

    if actor_role == ActorRole.BUYER:
        reason_codes.append("actor:buyer")
        evidence.append("明确需求方表达")
    elif actor_role != ActorRole.UNKNOWN:
        reason_codes.append(f"actor:{actor_role.value}")
    else:
        reason_codes.append("actor:unknown")

    if direct_buyer:
        reason_codes.append("intent:direct_buyer")
    reason_codes.extend(f"intent:{key}" for key in intent_hits)
    reason_codes.extend(f"topic:{key}" for key in topic_hits)
    reason_codes.extend(f"exclude:{label}" for label in negative_hits)
    evidence.extend(actor_evidence)
    evidence.extend(intent_hits[:4])
    evidence.extend(topic_hits[:3])

    confidence = 54
    confidence += 18 if actor_role == ActorRole.BUYER else 0
    confidence += 12 if direct_buyer else 0
    confidence += min(12, len(intent_hits) * 4)
    confidence += min(9, len(topic_hits) * 3)
    if actor_role not in {ActorRole.BUYER, ActorRole.UNKNOWN}:
        confidence = max(confidence, 90)
    confidence = max(0, min(99, confidence))

    return PolicyAssessment(
        policy_version=str(policy.get("version") or "unknown"),
        actor_role=actor_role,
        buying_stage=stage,
        is_lead=is_lead,
        category=category,
        topic_hits=topic_hits,
        intent_hits=intent_hits,
        negative_hits=negative_hits,
        intent_score=intent_score,
        fit_score=fit_score,
        actionability_score=max(0, min(100, actionability)),
        confidence=confidence,
        reason_codes=reason_codes,
        evidence=list(dict.fromkeys(evidence))[:8],
    )


def evaluate_gold_set(samples: list[dict[str, Any]]) -> dict[str, float | int]:
    tp = fp = tn = fn = 0
    actor_correct = 0
    for sample in samples:
        assessment = assess_text(str(sample.get("title") or ""), str(sample.get("excerpt") or ""))
        expected = sample.get("label") == "lead"
        if assessment.is_lead and expected:
            tp += 1
        elif assessment.is_lead and not expected:
            fp += 1
        elif not assessment.is_lead and expected:
            fn += 1
        else:
            tn += 1
        if str(sample.get("actor_role") or "") == assessment.actor_role.value:
            actor_correct += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    actor_accuracy = actor_correct / len(samples) if samples else 1.0
    return {
        "samples": len(samples),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "actor_accuracy": actor_accuracy,
    }


def query_ucb_score(*, prior: float, runs: int, fresh_count: int, qualified_count: int, total_runs: int, exploration: float) -> float:
    precision = (qualified_count + 0.5) / (fresh_count + 2.0)
    explore = math.sqrt(math.log(total_runs + 2.0) / (runs + 1.0))
    return float(prior) * (precision + exploration * explore)
