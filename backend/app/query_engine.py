from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class QuerySpec:
    key: str
    keyword: str
    category: str
    weight: int = 1


# High-recall service anchors, not intent-sentence permutations.
# Intent/lead qualification happens after retrieval in the classifier.
QUERY_SPECS: tuple[QuerySpec, ...] = (
    QuerySpec("mini-program", "小程序", "微信小程序", 3),
    QuerySpec("website", "网站", "网页开发", 2),
    QuerySpec("management-system", "管理系统", "网页开发", 1),
    QuerySpec("ai-agent", "AI智能体", "AI / 自动化", 1),
    QuerySpec("software-dev", "软件开发", "AI / 自动化", 1),
    QuerySpec("automation", "自动化", "AI / 自动化", 1),
)


def weighted_rotation() -> tuple[QuerySpec, ...]:
    slots: list[QuerySpec] = []
    for spec in QUERY_SPECS:
        slots.extend([spec] * max(1, spec.weight))
    return tuple(slots)


def choose_queries(
    *,
    now: datetime | None = None,
    count: int = 1,
    interval_minutes: int = 15,
    override: str | None = None,
) -> list[QuerySpec]:
    if override:
        text = override.strip()
        if not text:
            return []
        return [QuerySpec("manual", text, "manual", 1)]

    if count <= 0:
        return []
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    slots = weighted_rotation()
    bucket = int(current.timestamp() // (interval_minutes * 60))
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
