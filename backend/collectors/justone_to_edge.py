from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.connectors.justone import JustOneConnector, JustOneError  # noqa: E402
from app.query_engine import QuerySpec, choose_queries, load_retrieval_policy, retrieval_version  # noqa: E402

DEFAULT_API_BASE = "https://nfzkphjbelyltrzgkdwt.supabase.co/functions/v1/lead-radar-collector"
OIDC_AUDIENCE = "lead-radar-collector"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_github_oidc_token(audience: str = OIDC_AUDIENCE, timeout: int = 20) -> str:
    request_url = os.getenv("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    request_token = os.getenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not request_url or not request_token:
        raise RuntimeError("GitHub Actions OIDC environment is unavailable")
    separator = "&" if "?" in request_url else "?"
    request = Request(
        f"{request_url}{separator}audience={quote(audience)}",
        headers={"Authorization": f"Bearer {request_token}", "Accept": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    token = str(payload.get("value") or "").strip() if isinstance(payload, dict) else ""
    if not token:
        raise RuntimeError("GitHub Actions did not return an OIDC token")
    return token


def _fresh(raw, *, now: datetime, max_age_minutes: int) -> bool:
    published = raw.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = (now - published.astimezone(timezone.utc)).total_seconds() / 60.0
    return -5 <= age <= max_age_minutes


def _item(raw) -> dict[str, Any]:
    return {
        "source": raw.source,
        "external_id": raw.external_id,
        "title": raw.title,
        "excerpt": raw.excerpt,
        "published_at": raw.published_at.astimezone(timezone.utc).isoformat(),
        "url": raw.url,
        "budget": raw.budget,
    }


def _spec_from_payload(value: dict[str, Any]) -> QuerySpec:
    return QuerySpec(
        key=str(value.get("key") or "").strip(),
        keyword=str(value.get("keyword") or "").strip(),
        category=str(value.get("category") or "其他开发").strip(),
        intent_family=str(value.get("intent_family") or "discovery").strip(),
        topic_family=str(value.get("topic_family") or "unknown").strip(),
        lane=str(value.get("lane") or "precision").strip(),
        prior=1.0,
    )


def _page_bounds(leads: list[Any]) -> tuple[str | None, str | None]:
    values: list[datetime] = []
    for raw in leads:
        published = raw.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        values.append(published.astimezone(timezone.utc))
    if not values:
        return None, None
    return max(values).isoformat(), min(values).isoformat()


def _should_fetch_next_page(state: dict[str, Any], *, now: datetime, max_age_minutes: int, provider_calls_used: int, provider_call_budget: int) -> bool:
    config = load_retrieval_policy()
    scheduler = config.get("scheduler", {})
    max_pages = max(1, int(scheduler.get("max_pages_per_query", 2)))
    if int(state["pages"]) >= max_pages or provider_calls_used >= provider_call_budget:
        return False
    if state.get("has_more") is False:
        return False
    if int(state.get("last_page_raw_count") or 0) < max(1, int(scheduler.get("min_page_fill_for_pagination", 16))):
        return False
    oldest_text = state.get("last_page_oldest_published_at")
    if not oldest_text:
        return False
    try:
        oldest = datetime.fromisoformat(str(oldest_text).replace("Z", "+00:00"))
    except ValueError:
        return False
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    margin = max(0, int(scheduler.get("page_frontier_margin_minutes", 60)))
    age = (now - oldest.astimezone(timezone.utc)).total_seconds() / 60.0
    return age <= max_age_minutes + margin


def _merge_page(state: dict[str, Any], result: Any, *, now: datetime, max_age_minutes: int) -> None:
    newest, oldest = _page_bounds(result.leads)
    state["raw_count"] += int(result.raw_count)
    state["normalized_count"] += len(result.leads)
    state["pages"] += 1
    state["api_calls"] += 1
    state["has_more"] = result.has_more
    state["last_page_raw_count"] = int(result.raw_count)
    state["last_page_oldest_published_at"] = oldest
    if result.request_id:
        state["request_ids"].append(str(result.request_id))
    if newest and (not state["newest_published_at"] or newest > state["newest_published_at"]):
        state["newest_published_at"] = newest
    if oldest and (not state["oldest_published_at"] or oldest < state["oldest_published_at"]):
        state["oldest_published_at"] = oldest
    for raw in result.leads:
        if _fresh(raw, now=now, max_age_minutes=max_age_minutes):
            state["fresh"].setdefault(raw.external_id, raw)


def _execute_plan(
    connector: JustOneConnector,
    specs: list[QuerySpec],
    *,
    now: datetime,
    max_age_minutes: int,
    provider_call_budget: int,
    timeout: int,
) -> tuple[list[dict[str, Any]], int]:
    states: list[dict[str, Any]] = [
        {
            "spec": spec,
            "started_at": utc_now().isoformat(),
            "raw_count": 0,
            "normalized_count": 0,
            "pages": 0,
            "api_calls": 0,
            "request_ids": [],
            "has_more": None,
            "newest_published_at": None,
            "oldest_published_at": None,
            "last_page_raw_count": 0,
            "last_page_oldest_published_at": None,
            "fresh": {},
        }
        for spec in specs
    ]
    provider_calls_used = 0

    # Breadth first: every selected query gets page 1 before any query gets page 2.
    for state in states:
        if provider_calls_used >= provider_call_budget:
            break
        result = connector.fetch_query(state["spec"].keyword, timeout=timeout, page=1)
        provider_calls_used += 1
        _merge_page(state, result, now=now, max_age_minutes=max_age_minutes)

    while provider_calls_used < provider_call_budget:
        candidates = [
            state
            for state in states
            if state["pages"] > 0
            and _should_fetch_next_page(
                state,
                now=now,
                max_age_minutes=max_age_minutes,
                provider_calls_used=provider_calls_used,
                provider_call_budget=provider_call_budget,
            )
        ]
        candidates.sort(key=lambda state: (len(state["fresh"]), state["raw_count"]), reverse=True)
        if not candidates:
            break
        state = candidates[0]
        result = connector.fetch_query(state["spec"].keyword, timeout=timeout, page=int(state["pages"]) + 1)
        provider_calls_used += 1
        _merge_page(state, result, now=now, max_age_minutes=max_age_minutes)

    return [state for state in states if state["pages"] > 0], provider_calls_used


def post_oidc_json(
    api_base: str,
    oidc_token: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    request = Request(
        f"{api_base.rstrip('/')}{path}",
        method="POST",
        headers={
            "Authorization": f"Bearer {oidc_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AI-Lead-Radar-Collector/3.0",
        },
        data=json.dumps(body or {}, ensure_ascii=False).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Lead Radar Edge HTTP {exc.code}: {text}") from exc
    except URLError as exc:
        raise RuntimeError(f"Lead Radar Edge network error: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Lead Radar Edge returned a non-object response")
    return payload


def run(
    *,
    query_override: str | None = None,
    max_queries: int = 1,
    max_age_minutes: int = 1440,
    max_provider_calls: int | None = None,
    planned_queries: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    timeout: int = 60,
    now: datetime | None = None,
    oidc_token: str | None = None,
    scan_request_id: int | None = None,
) -> dict[str, Any]:
    started = now or utc_now()
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    started = started.astimezone(timezone.utc)

    if planned_queries:
        specs = [_spec_from_payload(item) for item in planned_queries if isinstance(item, dict)]
        specs = [spec for spec in specs if spec.key and spec.keyword]
    else:
        specs = choose_queries(now=started, count=max_queries, override=query_override)
    if not specs:
        raise RuntimeError("No queries selected")

    config = load_retrieval_policy()
    default_budget = int(config.get("scheduler", {}).get("max_provider_calls_web", max(1, len(specs))))
    provider_call_budget = max(1, min(12, int(max_provider_calls or default_budget)))
    specs = specs[: min(len(specs), provider_call_budget)]

    connector = JustOneConnector()
    states, provider_calls_used = _execute_plan(
        connector,
        specs,
        now=started,
        max_age_minutes=max_age_minutes,
        provider_call_budget=provider_call_budget,
        timeout=timeout,
    )

    batches: list[dict[str, Any]] = []
    union: dict[str, Any] = {}
    query_stats: list[dict[str, Any]] = []
    for state in states:
        spec: QuerySpec = state["spec"]
        items = [_item(raw) for raw in state["fresh"].values()]
        for raw in state["fresh"].values():
            union.setdefault(raw.external_id, raw)
        query = {
            "key": spec.key,
            "keyword": spec.keyword,
            "category": spec.category,
            "lane": spec.lane,
            "intent_family": spec.intent_family,
            "topic_family": spec.topic_family,
            "started_at": state["started_at"],
            "raw_count": state["raw_count"],
            "normalized_count": state["normalized_count"],
            "fresh_count": len(items),
            "pages": state["pages"],
            "api_calls": state["api_calls"],
            "request_ids": state["request_ids"],
            "newest_published_at": state["newest_published_at"],
            "oldest_published_at": state["oldest_published_at"],
        }
        query_stats.append(query)
        batches.append({"query": query, "items": items})

    body: dict[str, Any] = {
        "connector": connector.name,
        "retrieval_version": retrieval_version(),
        "started_at": started.isoformat(),
        "scanned": sum(int(stat["raw_count"]) for stat in query_stats),
        "queries": query_stats,
        "batches": batches,
    }
    if scan_request_id:
        body["scan_request_id"] = int(scan_request_id)

    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "connector": connector.name,
        "retrieval_version": retrieval_version(),
        "query_count": len(states),
        "provider_calls": provider_calls_used,
        "provider_call_budget": provider_call_budget,
        "queries": query_stats,
        "candidate_count": len(union),
        "max_age_minutes": max_age_minutes,
    }
    if scan_request_id:
        summary["scan_request_id"] = int(scan_request_id)
    if dry_run:
        summary["sample_titles"] = [raw.title for raw in list(union.values())[:8]]
        return summary

    token = oidc_token or get_github_oidc_token()
    api_base = os.getenv("LEAD_RADAR_API_BASE", DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
    summary["ingest"] = post_oidc_json(api_base, token, "/api/v1/ingest/source", body, timeout=timeout)
    return summary


def run_from_queue(*, max_age_minutes: int = 1440, timeout: int = 60) -> dict[str, Any]:
    oidc_token = get_github_oidc_token()
    api_base = os.getenv("LEAD_RADAR_API_BASE", DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
    claim = post_oidc_json(
        api_base,
        oidc_token,
        "/api/v1/scan/claim",
        {"run_id": os.getenv("GITHUB_RUN_ID", ""), "allow_auto": True},
        timeout=timeout,
    )
    if not claim.get("claimed"):
        return {
            "ok": True,
            "queue_empty": True,
            "provider_called": False,
            "message": "No queued or budget-approved automatic scan; Just One was not called.",
        }

    request_info = claim.get("request") if isinstance(claim.get("request"), dict) else {}
    request_id = int(request_info.get("id") or 0)
    if request_id <= 0:
        raise RuntimeError("Collector queue returned an invalid request id")
    query_override = str(request_info.get("query_override") or "").strip() or None
    max_queries = max(1, min(3, int(request_info.get("max_queries") or 1)))
    provider_call_budget = max(1, min(6, int(request_info.get("provider_call_budget") or max_queries)))
    planned_queries = request_info.get("queries") if isinstance(request_info.get("queries"), list) else None

    try:
        result = run(
            query_override=query_override,
            max_queries=max_queries,
            max_provider_calls=provider_call_budget,
            planned_queries=planned_queries,
            max_age_minutes=max_age_minutes,
            dry_run=False,
            timeout=timeout,
            oidc_token=oidc_token,
            scan_request_id=request_id,
        )
        result["queue_empty"] = False
        result["provider_called"] = True
        result["requested_from"] = str(request_info.get("requested_from") or "web")
        return result
    except Exception as exc:
        try:
            post_oidc_json(
                api_base,
                oidc_token,
                "/api/v1/scan/fail",
                {"scan_request_id": request_id, "error": f"{type(exc).__name__}: {exc}"[:700]},
                timeout=min(timeout, 30),
            )
        except Exception as report_exc:
            print(f"warning: failed to report queue error: {report_exc}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect fresh Xiaohongshu posts from Just One V4 and ingest them into Lead Radar Retrieval V2.")
    parser.add_argument("--query", default=None, help="Optional one-off query override")
    parser.add_argument("--max-queries", type=int, default=int(os.getenv("JUSTONE_MAX_QUERIES_PER_RUN", "1")))
    parser.add_argument("--max-provider-calls", type=int, default=int(os.getenv("JUSTONE_MAX_PROVIDER_CALLS_PER_RUN", "0")))
    parser.add_argument("--max-age-minutes", type=int, default=int(os.getenv("JUSTONE_MAX_AGE_MINUTES", "1440")))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-queue", action="store_true", help="Claim queued work or a budget-approved automatic scan; no provider call when neither exists")
    parser.add_argument("--output", default="backend/collectors/output/last-run.json")
    args = parser.parse_args()

    try:
        timeout = max(10, min(180, args.timeout))
        max_age_minutes = max(30, min(10080, args.max_age_minutes))
        if args.from_queue:
            result = run_from_queue(max_age_minutes=max_age_minutes, timeout=timeout)
        else:
            result = run(
                query_override=args.query,
                max_queries=max(1, min(6, args.max_queries)),
                max_provider_calls=max(1, min(12, args.max_provider_calls)) if args.max_provider_calls > 0 else None,
                max_age_minutes=max_age_minutes,
                dry_run=args.dry_run,
                timeout=timeout,
            )
        exit_code = 0
    except (JustOneError, RuntimeError, ValueError, HTTPError, URLError) as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:700]}
        exit_code = 1
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:700]}
        exit_code = 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
