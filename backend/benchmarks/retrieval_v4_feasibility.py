from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.query_engine import choose_queries, retrieval_version
from benchmarks.source_benchmark import Candidate, extract_candidates


JUSTONE_BASE_URL = "https://api.justoneapi.com"
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_INTENT_DISCOVERY = '"有报酬" || "项目急需" || "有能力者联系我" || "求专业人士" || "有没有人会" || "找个人做"'


@dataclass(frozen=True)
class Probe:
    key: str
    generator: str
    endpoint: str
    keyword: str | None
    window_hours: int | None = None
    source: str = "XIAOHONGSHU"


@dataclass
class ProbeResult:
    key: str
    generator: str
    endpoint: str
    keyword: str | None
    ok: bool
    latency_ms: int
    raw_candidates: int
    normalized_candidates: int
    within_30m: int
    within_2h: int
    within_24h: int
    newest_age_minutes: float | None
    url_coverage: int
    unique_contribution: int = 0
    overlap_with_known: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ProbeExecution:
    probe: Probe
    result: ProbeResult
    candidates: tuple[Candidate, ...]


def build_default_plan(
    *,
    hours: int = 24,
    known_keyword: str | None = None,
    intent_keyword: str = DEFAULT_INTENT_DISCOVERY,
) -> list[Probe]:
    """Build the V4 feasibility plan without touching any provider.

    The plan deliberately compares three different acquisition mechanisms under
    the same three-call ceiling:
      1. the current V3 known-intent lexical search;
      2. topic-free buyer-intent discovery;
      3. fully open recent discovery with the keyword parameter omitted.
    """

    if known_keyword is None:
        selected = choose_queries(count=1)
        known_keyword = selected[0].keyword if selected else "找人做小程序"

    return [
        Probe(
            key="known_intent",
            generator="known_intent_search",
            endpoint="/api/xiaohongshu/search-note/v4",
            keyword=known_keyword.strip(),
        ),
        Probe(
            key="intent_discovery",
            generator="topic_free_intent_discovery",
            endpoint="/api/search/v1",
            keyword=intent_keyword.strip() or None,
            window_hours=max(1, hours),
        ),
        Probe(
            key="open_recent",
            generator="open_recent_discovery",
            endpoint="/api/search/v1",
            keyword=None,
            window_hours=max(1, hours),
        ),
    ]


def _cross_search_params(probe: Probe, token: str, now: datetime) -> dict[str, str]:
    if probe.window_hours is None:
        raise ValueError("cross-platform probes require window_hours")
    local_now = now.astimezone(_SHANGHAI_TZ)
    start = local_now - timedelta(hours=max(1, probe.window_hours))
    params = {
        "token": token,
        "source": probe.source,
        "start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": local_now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if probe.keyword:
        params["keyword"] = probe.keyword
    return params


def build_request_url(probe: Probe, token: str, *, now: datetime | None = None) -> str:
    """Create a provider URL while intentionally omitting empty keyword params."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if probe.endpoint == "/api/search/v1":
        params = _cross_search_params(probe, token, current)
    elif probe.endpoint == "/api/xiaohongshu/search-note/v4":
        if not probe.keyword:
            raise ValueError("known-intent note search requires keyword")
        params = {
            "token": token,
            "keyword": probe.keyword,
            "page": "1",
            "sortType": "time_descending",
            "noteType": "ALL",
            "timeFilter": "ONE_DAY",
        }
    else:
        raise ValueError(f"unsupported benchmark endpoint: {probe.endpoint}")

    return f"{JUSTONE_BASE_URL}{probe.endpoint}?{urlencode(params)}"


def _business_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "unexpected non-object response"
    code = payload.get("code")
    if code in (None, 0):
        return None
    message = payload.get("message") or payload.get("msg") or payload.get("message_zh") or ""
    return f"business code {code}: {str(message)[:220]}"


def _fetch_json(url: str, timeout: int) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AI-Lead-Radar-V4-Benchmark/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def run_probe(probe: Probe, token: str, *, timeout: int = 120, now: datetime | None = None) -> ProbeExecution:
    started = time.monotonic()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    try:
        payload = _fetch_json(build_request_url(probe, token, now=current), timeout)
        error = _business_error(payload)
        if error:
            raise RuntimeError(error)
        raw_count, candidates = extract_candidates(payload)
        ages = [max(0.0, (current - item.published_at).total_seconds() / 60.0) for item in candidates]
        result = ProbeResult(
            key=probe.key,
            generator=probe.generator,
            endpoint=probe.endpoint,
            keyword=probe.keyword,
            ok=True,
            latency_ms=round((time.monotonic() - started) * 1000),
            raw_candidates=raw_count,
            normalized_candidates=len(candidates),
            within_30m=sum(1 for age in ages if age <= 30),
            within_2h=sum(1 for age in ages if age <= 120),
            within_24h=sum(1 for age in ages if age <= 1440),
            newest_age_minutes=round(min(ages), 1) if ages else None,
            url_coverage=sum(1 for item in candidates if item.url),
        )
        return ProbeExecution(probe, result, tuple(candidates))
    except Exception as exc:
        result = ProbeResult(
            key=probe.key,
            generator=probe.generator,
            endpoint=probe.endpoint,
            keyword=probe.keyword,
            ok=False,
            latency_ms=round((time.monotonic() - started) * 1000),
            raw_candidates=0,
            normalized_candidates=0,
            within_30m=0,
            within_2h=0,
            within_24h=0,
            newest_age_minutes=None,
            url_coverage=0,
            error=f"{type(exc).__name__}: {exc}"[:320],
        )
        return ProbeExecution(probe, result, tuple())


def _candidate_key(candidate: Candidate) -> str:
    return candidate.external_id or candidate.url or f"{candidate.title}|{candidate.published_at.isoformat()}"


def apply_overlap_metrics(executions: list[ProbeExecution]) -> None:
    key_sets = {execution.probe.key: {_candidate_key(item) for item in execution.candidates} for execution in executions}
    known = key_sets.get("known_intent", set())
    all_sets = list(key_sets.values())

    for execution in executions:
        own = key_sets.get(execution.probe.key, set())
        others: set[str] = set()
        for candidate_set in all_sets:
            if candidate_set is own:
                continue
            others.update(candidate_set)
        execution.result.unique_contribution = len(own - others)
        execution.result.overlap_with_known = len(own & known) if execution.probe.key != "known_intent" else len(known)


def report(executions: list[ProbeExecution], *, plan: list[Probe], executed: bool) -> dict[str, Any]:
    if executed:
        apply_overlap_metrics(executions)
    candidate_union = {_candidate_key(item) for execution in executions for item in execution.candidates}
    known = next((execution for execution in executions if execution.probe.key == "known_intent"), None)
    discovery = [execution for execution in executions if execution.probe.key != "known_intent"]
    discovery_union = {_candidate_key(item) for execution in discovery for item in execution.candidates}
    known_keys = {_candidate_key(item) for item in known.candidates} if known else set()

    return {
        "benchmark": "retrieval-v4-feasibility",
        "retrieval_version": retrieval_version(),
        "executed": executed,
        "provider_call_ceiling": len(plan),
        "plan": [asdict(probe) for probe in plan],
        "results": [asdict(execution.result) for execution in executions],
        "summary": {
            "total_unique_candidates": len(candidate_union),
            "discovery_unique_candidates": len(discovery_union),
            "discovery_novel_vs_known": len(discovery_union - known_keys),
            "discovery_overlap_with_known": len(discovery_union & known_keys),
        },
    }


def _write_report(payload: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "retrieval-v4-feasibility.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retrieval V4 multi-signal feasibility benchmark. Dry-run by default; --execute is required for provider calls."
    )
    parser.add_argument("--execute", action="store_true", help="Actually call Just One. Without this flag the script is quota-free.")
    parser.add_argument("--hours", type=int, default=24, help="Cross-platform discovery window in hours.")
    parser.add_argument("--known-keyword", default=None, help="Override the V3 known-intent reference query.")
    parser.add_argument("--intent-keyword", default=DEFAULT_INTENT_DISCOVERY, help="Topic-free buyer-intent Boolean expression.")
    parser.add_argument("--max-provider-calls", type=int, default=3, help="Hard safety ceiling for a benchmark run.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    plan = build_default_plan(hours=max(1, args.hours), known_keyword=args.known_keyword, intent_keyword=args.intent_keyword)
    max_calls = max(0, args.max_provider_calls)
    if len(plan) > max_calls:
        print(f"Refusing benchmark: plan needs {len(plan)} calls but --max-provider-calls={max_calls}.")
        return 2

    if not args.execute:
        payload = report([], plan=plan, executed=False)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("Dry run only. Add --execute to consume up to 3 provider calls.")
        return 0

    token = os.getenv("JUSTONE_API_TOKEN", "").strip()
    if not token:
        print("Missing JUSTONE_API_TOKEN")
        return 2

    executions = [run_probe(probe, token, timeout=max(30, args.timeout)) for probe in plan]
    payload = report(executions, plan=plan, executed=True)
    path = _write_report(payload, Path(args.output_dir))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Saved: {path}")
    return 0 if all(execution.result.ok for execution in executions) else 1


if __name__ == "__main__":
    raise SystemExit(main())
