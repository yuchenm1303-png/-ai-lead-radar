from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .policy import load_policy, query_ucb_score


@dataclass(frozen=True)
class QuerySpec:
    key: str
    keyword: str
    category: str
    intent_family: str
    topic_family: str
    prior: float = 1.0

    @property
    def weight(self) -> int:
        return max(1, round(self.prior * 3))


@dataclass(frozen=True)
class QueryPerformance:
    runs: int = 0
    fresh_count: int = 0
    qualified_count: int = 0


def _build_portfolio() -> tuple[QuerySpec, ...]:
    policy = load_policy()
    specs: list[QuerySpec] = []
    seen: set[tuple[str, str]] = set()
    for family in policy.get("intent_families", []):
        family_key = str(family.get("key") or "")
        family_prior = float(family.get("prior") or 1.0)
        templates = [str(item) for item in family.get("query_templates", []) if str(item).strip()]
        if not family_key or not templates:
            continue
        for topic in policy.get("topics", []):
            topic_key = str(topic.get("key") or "")
            category = str(topic.get("category") or "其他开发")
            topic_prior = float(topic.get("prior") or 1.0)
            query_terms = [str(item).strip() for item in topic.get("query_terms", []) if str(item).strip()]
            if not topic_key or not query_terms:
                continue
            # Use the canonical search term per topic; portfolio breadth comes from intent families,
            # not from repeatedly searching every alias.
            topic_term = query_terms[0]
            for index, template in enumerate(templates):
                keyword = template.replace("{topic}", topic_term).strip()
                signature = (family_key, keyword)
                if not keyword or signature in seen:
                    continue
                seen.add(signature)
                specs.append(
                    QuerySpec(
                        key=f"{family_key}:{topic_key}:{index}",
                        keyword=keyword,
                        category=category,
                        intent_family=family_key,
                        topic_family=topic_key,
                        prior=family_prior * topic_prior,
                    )
                )
    return tuple(specs)


QUERY_SPECS: tuple[QuerySpec, ...] = _build_portfolio()


def weighted_rotation() -> tuple[QuerySpec, ...]:
    slots: list[QuerySpec] = []
    for spec in QUERY_SPECS:
        slots.extend([spec] * spec.weight)
    return tuple(slots)


def _adaptive_rank(performance: dict[str, QueryPerformance]) -> list[QuerySpec]:
    policy = load_policy()
    exploration = float(policy.get("query_policy", {}).get("exploration", 0.35))
    total_runs = sum(max(0, metric.runs) for metric in performance.values())
    return sorted(
        QUERY_SPECS,
        key=lambda spec: query_ucb_score(
            prior=spec.prior,
            runs=performance.get(spec.key, QueryPerformance()).runs,
            fresh_count=performance.get(spec.key, QueryPerformance()).fresh_count,
            qualified_count=performance.get(spec.key, QueryPerformance()).qualified_count,
            total_runs=total_runs,
            exploration=exploration,
        ),
        reverse=True,
    )


def choose_queries(
    *,
    now: datetime | None = None,
    count: int = 1,
    interval_minutes: int | None = None,
    override: str | None = None,
    performance: dict[str, QueryPerformance] | None = None,
) -> list[QuerySpec]:
    if override:
        text = override.strip()
        if not text:
            return []
        return [QuerySpec("manual", text, "manual", "manual", "manual", 1.0)]

    if count <= 0 or not QUERY_SPECS:
        return []

    if performance:
        ranked = _adaptive_rank(performance)
        return ranked[: min(count, len(ranked))]

    policy_interval = int(load_policy().get("query_policy", {}).get("interval_minutes", 15))
    interval = interval_minutes if interval_minutes is not None else policy_interval
    if interval <= 0:
        raise ValueError("interval_minutes must be positive")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    slots = weighted_rotation()
    bucket = int(current.timestamp() // (interval * 60))
    start = bucket % len(slots)

    selected: list[QuerySpec] = []
    seen: set[str] = set()
    cursor = start
    max_steps = len(slots) * 2
    for _ in range(max_steps):
        spec = slots[cursor % len(slots)]
        cursor += 1
        if spec.key in seen:
            continue
        seen.add(spec.key)
        selected.append(spec)
        if len(selected) >= min(count, len(QUERY_SPECS)):
            break
    return selected
