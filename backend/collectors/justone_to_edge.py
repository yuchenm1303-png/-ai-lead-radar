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
from app.query_engine import choose_queries  # noqa: E402

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
            "User-Agent": "AI-Lead-Radar-Collector/2.0",
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
    dry_run: bool = False,
    timeout: int = 60,
    now: datetime | None = None,
    oidc_token: str | None = None,
    scan_request_id: int | None = None,
) -> dict[str, Any]:
    started = now or utc_now()
    specs = choose_queries(now=started, count=max_queries, override=query_override)
    if not specs:
        raise RuntimeError("No queries selected")

    connector = JustOneConnector()
    unique: dict[str, Any] = {}
    query_stats: list[dict[str, Any]] = []
    for spec in specs:
        result = connector.fetch_query(spec.keyword, timeout=timeout)
        fresh = [raw for raw in result.leads if _fresh(raw, now=started, max_age_minutes=max_age_minutes)]
        for raw in fresh:
            unique.setdefault(raw.external_id, raw)
        query_stats.append(
            {
                "key": spec.key,
                "keyword": spec.keyword,
                "category": spec.category,
                "intent_family": spec.intent_family,
                "topic_family": spec.topic_family,
                "raw_count": result.raw_count,
                "normalized_count": len(result.leads),
                "fresh_count": len(fresh),
                "request_id": result.request_id,
            }
        )

    items = [_item(raw) for raw in unique.values()]
    body: dict[str, Any] = {
        "connector": connector.name,
        "started_at": started.isoformat(),
        "scanned": sum(int(stat["raw_count"]) for stat in query_stats),
        "queries": query_stats,
        "items": items,
    }
    if scan_request_id:
        body["scan_request_id"] = int(scan_request_id)

    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "connector": connector.name,
        "query_count": len(specs),
        "queries": query_stats,
        "candidate_count": len(items),
        "max_age_minutes": max_age_minutes,
    }
    if scan_request_id:
        summary["scan_request_id"] = int(scan_request_id)
    if dry_run:
        summary["sample_titles"] = [item["title"] for item in items[:5]]
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
        {"run_id": os.getenv("GITHUB_RUN_ID", "")},
        timeout=timeout,
    )
    if not claim.get("claimed"):
        return {
            "ok": True,
            "queue_empty": True,
            "provider_called": False,
            "message": "No queued web scan request; Just One was not called.",
        }

    request_info = claim.get("request") if isinstance(claim.get("request"), dict) else {}
    request_id = int(request_info.get("id") or 0)
    if request_id <= 0:
        raise RuntimeError("Collector queue returned an invalid request id")
    query_override = str(request_info.get("query_override") or "").strip() or None
    max_queries = max(1, min(3, int(request_info.get("max_queries") or 1)))

    try:
        result = run(
            query_override=query_override,
            max_queries=max_queries,
            max_age_minutes=max_age_minutes,
            dry_run=False,
            timeout=timeout,
            oidc_token=oidc_token,
            scan_request_id=request_id,
        )
        result["queue_empty"] = False
        result["provider_called"] = True
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
    parser = argparse.ArgumentParser(description="Collect fresh Xiaohongshu posts from Just One V4 and ingest them into Lead Radar.")
    parser.add_argument("--query", default=None, help="Optional one-off query override")
    parser.add_argument("--max-queries", type=int, default=int(os.getenv("JUSTONE_MAX_QUERIES_PER_RUN", "1")))
    parser.add_argument("--max-age-minutes", type=int, default=int(os.getenv("JUSTONE_MAX_AGE_MINUTES", "1440")))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-queue", action="store_true", help="Claim at most one queued web scan request; no provider call when queue is empty")
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
