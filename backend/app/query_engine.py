from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .policy import load_policy

RETRIEVAL_POLICY_PATH = Path(__file__).resolve().parents[2] / "supabase" / "functions" / "_shared" / "retrieval_policy.json"


@lru_cache(maxsize=1)
def load_retrieval_policy() -> dict[str, Any]:
    return json.loads(RETRIEVAL_POLICY_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class QuerySpec:
    key: str
    keyword: str
    category: str
    intent_family: str
    topic_family: str
    lane: str = "precision"
    prior: float = 1.0


@dataclass(frozen=True)
class QueryPerformance:
    runs: int = 0
    api_calls: int = 0
    returned_count: int = 0
    fresh_count: int = 0
    qualified_count: int = 0
    filtered_count: int = 0
    duplicate_count: int = 0
    human_positive_count: int = 0
    human_negative_count: int = 0
    last_run_at: datetime | None = None


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if math.isfinite(parsed) else fallback


def _query_terms(topic: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for raw in topic.get("query_terms", []):
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _lane_prior(config: dict[str, Any], lane: str) -> float:
    return _number(config.get("lanes", {}).get(lane, {}).get("prior"), 1.0)


def _lane_weight(config: dict[str, Any], lane: str) -> float:
    defaults = {"precision": 0.62, "discovery": 0.30, "broad": 0.08, "manual": 1.0}
    if lane == "manual":
        return 1.0
    return _number(config.get("scheduler", {}).get("lane_mix", {}).get(lane), defaults.get(lane, 0.1))


def _build_portfolio() -> tuple[QuerySpec, ...]:
    policy = load_policy()
    config = load_retrieval_policy()
    alias_decay = max(0.1, min(1.0, _number(config.get("alias_prior_decay"), 0.9)))
    specs: list[QuerySpec] = []
    seen: set[tuple[str, str]] = set()

    for topic in policy.get("topics", []):
        topic_key = str(topic.get("key") or "").strip()
        category = str(topic.get("category") or "其他开发")
        topic_prior = _number(topic.get("prior"), 1.0)
        terms = _query_terms(topic)
        if not topic_key or not terms:
            continue

        for family in policy.get("intent_families", []):
            family_key = str(family.get("key") or "").strip()
            family_prior = _number(family.get("prior"), 1.0)
            templates = [str(item).strip() for item in family.get("query_templates", []) if str(item).strip()]
            if not family_key or not templates:
                continue
            for term_index, term in enumerate(terms):
                for template_index, template in enumerate(templates):
                    keyword = template.replace("{topic}", term).strip()
                    signature = ("precision", keyword.lower())
                    if not keyword or signature in seen:
                        continue
                    seen.add(signature)
                    key = (
                        f"{family_key}:{topic_key}:{template_index}"
                        if term_index == 0
                        else f"{family_key}:{topic_key}:{template_index}:alias{term_index}"
                    )
                    specs.append(
                        QuerySpec(
                            key=key,
                            keyword=keyword,
                            category=category,
                            intent_family=family_key,
                            topic_family=topic_key,
                            lane="precision",
                            prior=family_prior * topic_prior * _lane_prior(config, "precision") * (alias_decay**term_index),
                        )
                    )

        discovery_templates = [str(item) for item in config.get("lanes", {}).get("discovery", {}).get("templates", []) if str(item).strip()]
        for term_index, term in enumerate(terms):
            for template_index, template in enumerate(discovery_templates):
                keyword = template.replace("{topic}", term).strip()
                signature = ("discovery", keyword.lower())
                if not keyword or signature in seen:
                    continue
                seen.add(signature)
                specs.append(
                    QuerySpec(
                        key=f"discovery:{topic_key}:{template_index}:{term_index}",
                        keyword=keyword,
                        category=category,
                        intent_family="discovery",
                        topic_family=topic_key,
                        lane="discovery",
                        prior=topic_prior * _lane_prior(config, "discovery") * (alias_decay**term_index),
                    )
                )

        broad_templates = [str(item) for item in config.get("lanes", {}).get("broad", {}).get("templates", ["{topic}"]) if str(item).strip()]
        for term_index, term in enumerate(terms):
            for template_index, template in enumerate(broad_templates):
                keyword = template.replace("{topic}", term).strip()
                signature = ("broad", keyword.lower())
                if not keyword or signature in seen:
                    continue
                seen.add(signature)
                specs.append(
                    QuerySpec(
                        key=f"broad:{topic_key}:{template_index}:{term_index}",
                        keyword=keyword,
                        category=category,
                        intent_family="discovery",
                        topic_family=topic_key,
                        lane="broad",
                        prior=topic_prior * _lane_prior(config, "broad") * (alias_decay**term_index),
                    )
                )
    return tuple(specs)


QUERY_SPECS: tuple[QuerySpec, ...] = _build_portfolio()


def query_score(spec: QuerySpec, metric: QueryPerformance, *, total_runs: int, now: datetime) -> float:
    config = load_retrieval_policy()
    scheduler = config.get("scheduler", {})
    runs = max(0, int(metric.runs))
    api_calls = max(runs, int(metric.api_calls or runs))
    fresh = max(0, int(metric.fresh_count))
    duplicates = max(0, int(metric.duplicate_count))
    new_unique = max(0, fresh - duplicates)
    qualified = max(0, int(metric.qualified_count))
    human_positive = max(0, int(metric.human_positive_count))
    human_negative = max(0, int(metric.human_negative_count))

    precision = (qualified + 0.5) / (new_unique + 2.0)
    unique_rate = (new_unique + 1.0) / (fresh + 2.0)
    duplicate_rate = (duplicates + 0.25) / (fresh + 1.0)
    human_precision = (human_positive + 1.0) / (human_positive + human_negative + 2.0)
    yield_per_call = (qualified + 0.5) / (api_calls + 1.5)
    yield_signal = math.tanh(yield_per_call / 2.5)
    exploration = _number(scheduler.get("exploration"), 0.42) * math.sqrt(math.log(max(0, total_runs) + 2.0) / (runs + 1.0))

    cooldown_minutes = max(1.0, _number(scheduler.get("query_cooldown_minutes"), 120.0))
    saturation_cooldown = max(cooldown_minutes, _number(scheduler.get("saturation_cooldown_minutes"), 360.0))
    saturation_threshold = max(0.0, min(1.0, _number(scheduler.get("duplicate_saturation_threshold"), 0.65)))
    minutes_since_last = math.inf
    if metric.last_run_at is not None:
        last = metric.last_run_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        minutes_since_last = max(0.0, (now - last.astimezone(timezone.utc)).total_seconds() / 60.0)

    if minutes_since_last < cooldown_minutes:
        cooldown_factor = 0.06 + 0.24 * (minutes_since_last / cooldown_minutes)
    elif math.isfinite(minutes_since_last):
        cooldown_factor = 1.0 + min(0.22, (minutes_since_last - cooldown_minutes) / max(cooldown_minutes * 8.0, 1.0))
    else:
        cooldown_factor = 1.0

    if duplicate_rate >= saturation_threshold and minutes_since_last < saturation_cooldown:
        saturation_factor = 0.16
    else:
        saturation_factor = max(0.35, 1.0 - min(0.65, duplicate_rate * 0.72))

    quality = 0.30 * precision + 0.23 * unique_rate + 0.17 * human_precision + 0.15 * yield_signal + 0.15 * exploration
    return spec.prior * (0.38 + quality) * (0.65 + _lane_weight(config, spec.lane)) * cooldown_factor * saturation_factor


def _preferred_lane(now: datetime) -> str:
    config = load_retrieval_policy()
    scheduler = config.get("scheduler", {})
    interval = max(1, int(_number(scheduler.get("interval_minutes"), 15)))
    mix = scheduler.get("lane_mix", {})
    cycle: list[str] = []
    for lane, default in (("precision", 0.62), ("discovery", 0.30), ("broad", 0.08)):
        count = max(1, round(_number(mix.get(lane), default) * 10))
        cycle.extend([lane] * count)
    bucket = int(now.timestamp() // (interval * 60))
    return cycle[bucket % len(cycle)] if cycle else "precision"


def choose_queries(
    *,
    now: datetime | None = None,
    count: int = 1,
    override: str | None = None,
    performance: dict[str, QueryPerformance] | None = None,
) -> list[QuerySpec]:
    if override:
        text = override.strip()
        if not text:
            return []
        return [QuerySpec("manual", text, "manual", "manual", "manual", "manual", 1.0)]
    if count <= 0 or not QUERY_SPECS:
        return []

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    performance = performance or {}
    total_runs = sum(max(0, metric.runs) for metric in performance.values())
    ranked = sorted(
        QUERY_SPECS,
        key=lambda spec: query_score(spec, performance.get(spec.key, QueryPerformance()), total_runs=total_runs, now=current),
        reverse=True,
    )

    selected: list[QuerySpec] = []
    used_keys: set[str] = set()
    used_topics: set[str] = set()

    def pick(lane: str | None, prefer_new_topic: bool = True) -> bool:
        candidates = [spec for spec in ranked if spec.key not in used_keys and (lane is None or spec.lane == lane)]
        chosen = next((spec for spec in candidates if spec.topic_family not in used_topics), None) if prefer_new_topic else None
        chosen = chosen or (candidates[0] if candidates else None)
        if chosen is None:
            return False
        selected.append(chosen)
        used_keys.add(chosen.key)
        used_topics.add(chosen.topic_family)
        return True

    target = min(count, len(ranked))
    if target == 1:
        if not pick(_preferred_lane(current), prefer_new_topic=False):
            pick(None, prefer_new_topic=False)
        return selected

    for lane in ("precision", "discovery", "broad"):
        if len(selected) >= target:
            break
        pick(lane, prefer_new_topic=True)
    while len(selected) < target:
        if not pick(None, prefer_new_topic=True) and not pick(None, prefer_new_topic=False):
            break
    return selected


def retrieval_version() -> str:
    return str(load_retrieval_policy().get("version") or "unknown")
