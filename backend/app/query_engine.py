from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

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
    lane: str = "explore"
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


def _unique_strings(value: Any) -> list[str]:
    result: list[str] = []
    for raw in value if isinstance(value, list) else []:
        text = str(raw or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _build_portfolio() -> tuple[QuerySpec, ...]:
    config = load_retrieval_policy()
    alias_decay = max(0.1, min(1.0, _number(config.get("alias_prior_decay"), 0.92)))
    specs: list[QuerySpec] = []
    seen: set[str] = set()

    for archetype_key, archetype in config.get("archetypes", {}).items():
        templates = _unique_strings(archetype.get("templates"))
        archetype_prior = max(0.05, _number(archetype.get("prior"), 1.0))
        if not templates:
            continue
        for topic_key, topic in config.get("topics", {}).items():
            terms = _unique_strings(topic.get("terms"))
            if not terms:
                continue
            category = str(topic.get("category") or "其他开发")
            topic_prior = max(0.05, _number(topic.get("prior"), 1.0))
            for term_index, term in enumerate(terms):
                for template_index, template in enumerate(templates):
                    keyword = " ".join(template.replace("{topic}", term).split())
                    signature = keyword.lower()
                    if not keyword or signature in seen:
                        continue
                    seen.add(signature)
                    specs.append(
                        QuerySpec(
                            key=f"v3:{archetype_key}:{topic_key}:{template_index}:{term_index}",
                            keyword=keyword,
                            category=category,
                            intent_family=str(archetype_key),
                            topic_family=str(topic_key),
                            lane="explore",
                            prior=archetype_prior * topic_prior * (alias_decay**term_index),
                        )
                    )
    return tuple(specs)


QUERY_SPECS: tuple[QuerySpec, ...] = _build_portfolio()


def _pair_from_key(key: str) -> tuple[str, str] | None:
    config = load_retrieval_policy()
    parts = str(key or "").split(":")
    if len(parts) >= 3 and parts[0] == "v3" and parts[1] and parts[2]:
        return parts[1], parts[2]
    mapped = str(config.get("legacy_archetype_map", {}).get(parts[0] if parts else "") or "")
    if mapped and len(parts) >= 2 and parts[1]:
        return mapped, parts[1]
    return None


def _metric_values(metric: QueryPerformance | None) -> dict[str, float]:
    value = metric or QueryPerformance()
    return {
        "runs": max(0.0, _number(value.runs)),
        "api_calls": max(0.0, _number(value.api_calls or value.runs)),
        "returned_count": max(0.0, _number(value.returned_count)),
        "fresh_count": max(0.0, _number(value.fresh_count)),
        "qualified_count": max(0.0, _number(value.qualified_count)),
        "filtered_count": max(0.0, _number(value.filtered_count)),
        "duplicate_count": max(0.0, _number(value.duplicate_count)),
        "human_positive_count": max(0.0, _number(value.human_positive_count)),
        "human_negative_count": max(0.0, _number(value.human_negative_count)),
    }


def _add_values(target: dict[str, float], source: dict[str, float], weight: float = 1.0) -> dict[str, float]:
    for key in target:
        target[key] += source.get(key, 0.0) * weight
    return target


def _effective_values(spec: QuerySpec, performance: dict[str, QueryPerformance]) -> dict[str, float]:
    config = load_retrieval_policy()
    result = _metric_values(performance.get(spec.key))
    group_weight = max(0.0, min(1.0, _number(config.get("scheduler", {}).get("group_history_weight"), 0.35)))
    for key, metric in performance.items():
        if key == spec.key:
            continue
        pair = _pair_from_key(key)
        if pair == (spec.intent_family, spec.topic_family):
            _add_values(result, _metric_values(metric), group_weight)
    return result


def _signals(values: dict[str, float]) -> dict[str, float]:
    config = load_retrieval_policy()
    scheduler = config.get("scheduler", {})
    api_calls = max(0.0, values["api_calls"])
    fresh = max(0.0, values["fresh_count"])
    duplicates = max(0.0, values["duplicate_count"])
    new_unique = max(0.0, fresh - duplicates)
    qualified = max(0.0, values["qualified_count"])
    filtered = max(0.0, values["filtered_count"])
    human_positive = max(0.0, values["human_positive_count"])
    human_negative = max(0.0, values["human_negative_count"])
    buyer_evidence = qualified + human_positive * 1.75
    precision = (buyer_evidence + 0.75) / (new_unique + human_positive + human_negative + 3.0)
    buyer_yield = (buyer_evidence + 0.5) / (api_calls + 1.5)
    human_precision = (human_positive + 1.0) / (human_positive + human_negative + 2.0)
    freshness_signal = math.tanh((fresh / max(1.0, api_calls)) / 10.0)
    duplicate_rate = (duplicates + 0.25) / (fresh + 1.0)
    noise_rate = (filtered + human_negative) / (fresh + human_positive + human_negative + 2.0)
    quality = (
        0.36 * precision
        + 0.24 * math.tanh(buyer_yield / 1.5)
        + 0.18 * human_precision
        + 0.22 * freshness_signal
        - max(0.0, _number(scheduler.get("provider_noise_penalty"), 0.22)) * min(1.0, noise_rate)
        - max(0.0, _number(scheduler.get("duplicate_penalty"), 0.28)) * min(1.0, duplicate_rate)
    )
    return {
        "qualified": qualified,
        "human_positive": human_positive,
        "buyer_evidence": buyer_evidence,
        "quality": quality,
        "duplicate_rate": duplicate_rate,
    }


def _minutes_since(value: datetime | None, now: datetime) -> float:
    if value is None:
        return math.inf
    last = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - last.astimezone(timezone.utc)).total_seconds() / 60.0)


def _cooldown_factor(metric: QueryPerformance | None, now: datetime) -> float:
    config = load_retrieval_policy()
    cooldown = max(1.0, _number(config.get("scheduler", {}).get("query_cooldown_minutes"), 120.0))
    elapsed = _minutes_since(metric.last_run_at if metric else None, now)
    if not math.isfinite(elapsed):
        return 1.0
    if elapsed < cooldown:
        return 0.05 + 0.25 * (elapsed / cooldown)
    return 1.0 + min(0.18, (elapsed - cooldown) / max(cooldown * 10.0, 1.0))


def _saturation_factor(metric: QueryPerformance | None, now: datetime) -> float:
    if metric is None:
        return 1.0
    config = load_retrieval_policy()
    scheduler = config.get("scheduler", {})
    values = _metric_values(metric)
    signals = _signals(values)
    threshold = max(0.0, min(1.0, _number(scheduler.get("duplicate_saturation_threshold"), 0.65)))
    saturation_cooldown = max(1.0, _number(scheduler.get("saturation_cooldown_minutes"), 360.0))
    if signals["duplicate_rate"] >= threshold and _minutes_since(metric.last_run_at, now) < saturation_cooldown:
        return 0.14
    return max(0.34, 1.0 - min(0.66, signals["duplicate_rate"] * 0.72))


def query_score(spec: QuerySpec, metric: QueryPerformance, *, total_runs: int, now: datetime) -> float:
    config = load_retrieval_policy()
    signals = _signals(_metric_values(metric))
    exploration = max(0.0, _number(config.get("scheduler", {}).get("exploration"), 0.55)) * math.sqrt(
        math.log(max(0, total_runs) + 2.0) / (max(0, metric.runs) + 1.0)
    )
    return spec.prior * (0.72 + signals["quality"] + 0.12 * exploration) * _cooldown_factor(metric, now) * _saturation_factor(metric, now)


def _exploit_score(spec: QuerySpec, performance: dict[str, QueryPerformance], total_runs: int, now: datetime) -> float:
    config = load_retrieval_policy()
    exact = performance.get(spec.key, QueryPerformance())
    signals = _signals(_effective_values(spec, performance))
    exploration = max(0.0, _number(config.get("scheduler", {}).get("exploration"), 0.55)) * math.sqrt(
        math.log(max(0, total_runs) + 2.0) / (max(0, exact.runs) + 1.0)
    )
    return spec.prior * (0.82 + signals["quality"] + 0.08 * exploration) * _cooldown_factor(exact, now) * _saturation_factor(exact, now)


def _explore_score(spec: QuerySpec, performance: dict[str, QueryPerformance], total_runs: int, now: datetime) -> float:
    config = load_retrieval_policy()
    exact = performance.get(spec.key, QueryPerformance())
    quality = max(-0.4, _signals(_effective_values(spec, performance))["quality"])
    uncertainty = math.sqrt(math.log(max(0, total_runs) + 3.0) / (max(0, exact.runs) + 1.0))
    exploration = max(0.05, _number(config.get("scheduler", {}).get("exploration"), 0.55))
    return spec.prior * (0.68 + exploration * uncertainty + 0.18 * max(0.0, quality)) * _cooldown_factor(exact, now)


def _aggregate_pair(pair: tuple[str, str], performance: dict[str, QueryPerformance]) -> dict[str, float]:
    total = _metric_values(None)
    for key, metric in performance.items():
        if _pair_from_key(key) == pair:
            _add_values(total, _metric_values(metric))
    return total


def _winning_pair(performance: dict[str, QueryPerformance]) -> tuple[str, str] | None:
    pairs = {(spec.intent_family, spec.topic_family) for spec in QUERY_SPECS}
    best: tuple[tuple[str, str], float] | None = None
    for pair in pairs:
        signals = _signals(_aggregate_pair(pair, performance))
        if signals["qualified"] + signals["human_positive"] <= 0:
            continue
        score = signals["quality"] + 0.08 * math.log1p(signals["qualified"] + 2 * signals["human_positive"])
        if best is None or score > best[1]:
            best = (pair, score)
    return best[0] if best else None


def _expand_score(spec: QuerySpec, winner: tuple[str, str] | None, performance: dict[str, QueryPerformance], total_runs: int, now: datetime) -> float:
    if winner is None:
        return _explore_score(spec, performance, total_runs, now)
    exact = performance.get(spec.key, QueryPerformance())
    same_archetype = spec.intent_family == winner[0]
    same_topic = spec.topic_family == winner[1]
    if same_archetype and same_topic:
        adjacency = 0.62
    elif same_archetype:
        adjacency = 1.0
    elif same_topic:
        adjacency = 0.88
    else:
        adjacency = 0.20
    novelty = 1.0 / math.sqrt(max(0, exact.runs) + 1.0)
    quality = max(0.0, _signals(_effective_values(spec, performance))["quality"])
    return spec.prior * (0.55 + adjacency + 0.48 * novelty + 0.12 * quality) * _cooldown_factor(exact, now)


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
    target = min(count, len(QUERY_SPECS))
    selected: list[QuerySpec] = []
    used: set[str] = set()

    exploit_ranked = sorted(QUERY_SPECS, key=lambda spec: _exploit_score(spec, performance, total_runs, current), reverse=True)
    first = exploit_ranked[0]
    selected.append(QuerySpec(**{**first.__dict__, "lane": "exploit"}))
    used.add(first.key)
    if len(selected) >= target:
        return selected

    explore_ranked = sorted(QUERY_SPECS, key=lambda spec: _explore_score(spec, performance, total_runs, current), reverse=True)
    explore = next(
        (spec for spec in explore_ranked if spec.key not in used and spec.topic_family != first.topic_family and spec.intent_family != first.intent_family),
        None,
    )
    explore = explore or next((spec for spec in explore_ranked if spec.key not in used and spec.topic_family != first.topic_family), None)
    explore = explore or next((spec for spec in explore_ranked if spec.key not in used), None)
    if explore:
        selected.append(QuerySpec(**{**explore.__dict__, "lane": "explore"}))
        used.add(explore.key)
    if len(selected) >= target:
        return selected

    winner = _winning_pair(performance)
    expand_ranked = sorted(QUERY_SPECS, key=lambda spec: _expand_score(spec, winner, performance, total_runs, current), reverse=True)
    expand = None
    if winner:
        expand = next(
            (
                spec
                for spec in expand_ranked
                if spec.key not in used
                and (spec.intent_family == winner[0] or spec.topic_family == winner[1])
                and not (spec.intent_family == winner[0] and spec.topic_family == winner[1])
            ),
            None,
        )
    if expand is None:
        existing_topics = {spec.topic_family for spec in selected}
        expand = next((spec for spec in expand_ranked if spec.key not in used and spec.topic_family not in existing_topics), None)
    expand = expand or next((spec for spec in expand_ranked if spec.key not in used), None)
    if expand:
        selected.append(QuerySpec(**{**expand.__dict__, "lane": "expand"}))
        used.add(expand.key)

    while len(selected) < target:
        extra = next((spec for spec in explore_ranked if spec.key not in used), None)
        if extra is None:
            break
        selected.append(QuerySpec(**{**extra.__dict__, "lane": "explore"}))
        used.add(extra.key)
    return selected


def retrieval_version() -> str:
    return str(load_retrieval_policy().get("version") or "unknown")
