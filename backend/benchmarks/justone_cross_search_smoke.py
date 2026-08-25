from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from source_benchmark import extract_candidates


_SCHEMA_HINTS = (
    "id",
    "title",
    "content",
    "desc",
    "text",
    "time",
    "date",
    "url",
    "link",
    "source",
    "platform",
)
_SENSITIVE_HINTS = ("token", "authorization", "cookie", "secret", "password")
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _message(payload: dict[str, Any]) -> str:
    for key in ("message", "msg", "message_zh", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return ""


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value[:120]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _schema_probe(payload: dict[str, Any], limit: int = 2) -> dict[str, Any]:
    data = payload.get("data")
    probe: dict[str, Any] = {
        "data_type": type(data).__name__,
        "data_top_keys": sorted(data.keys())[:80] if isinstance(data, dict) else [],
        "samples": [],
    }
    scored: list[tuple[int, dict[str, Any]]] = []
    for node in _walk_dicts(data):
        keys = [str(key) for key in node.keys()]
        relevant = [
            key
            for key in keys
            if any(hint in key.lower() for hint in _SCHEMA_HINTS)
            and not any(secret in key.lower() for secret in _SENSITIVE_HINTS)
        ]
        if relevant:
            scored.append((len(relevant), node))
    scored.sort(key=lambda item: item[0], reverse=True)
    for _, node in scored[:limit]:
        selected: dict[str, Any] = {}
        for key, value in node.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(secret in lowered for secret in _SENSITIVE_HINTS):
                continue
            if any(hint in lowered for hint in _SCHEMA_HINTS):
                selected[key_text] = _safe_scalar(value)
        probe["samples"].append(
            {
                "keys": sorted(str(key) for key in node.keys())[:80],
                "selected": selected,
            }
        )
    return probe


def main() -> int:
    parser = argparse.ArgumentParser(description="One-call free Just One cross-platform Xiaohongshu smoke test.")
    parser.add_argument("--keyword", default="找人做小程序")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    token = os.getenv("JUSTONE_API_TOKEN", "").strip()
    if not token:
        print("Missing JUSTONE_API_TOKEN")
        return 2

    # Just One's unified-search API accepts naive yyyy-MM-dd HH:mm:ss values.
    # Generate that window explicitly in Asia/Shanghai so GitHub's UTC runner
    # cannot silently shift a Xiaohongshu freshness query by eight hours.
    end = datetime.now(_SHANGHAI_TZ)
    start = end - timedelta(hours=max(1, args.hours))
    params = urlencode(
        {
            "token": token,
            "keyword": args.keyword,
            "source": "XIAOHONGSHU",
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    url = f"https://api.justoneapi.com/api/search/v1?{params}"
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "AI-Lead-Radar-Benchmark/1.0"})
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with urlopen(req, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        safe = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        (output_dir / "justone-cross-search-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    if not isinstance(payload, dict):
        safe = {"ok": False, "error": "Unexpected non-object response"}
        (output_dir / "justone-cross-search-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    code = payload.get("code")
    if code != 0:
        safe = {"ok": False, "business_code": code, "message": _message(payload)}
        (output_dir / "justone-cross-search-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Just One cross-platform business response:")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    raw_count, candidates = extract_candidates(payload)
    now = datetime.now(timezone.utc)
    ages = [max(0.0, (now - item.published_at).total_seconds() / 60.0) for item in candidates]
    safe = {
        "ok": True,
        "business_code": 0,
        "endpoint": "/api/search/v1",
        "source": "XIAOHONGSHU",
        "keyword": args.keyword,
        "window_hours": args.hours,
        "window_timezone": "Asia/Shanghai",
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "raw_candidates": raw_count,
        "normalized_candidates": len(candidates),
        "newest_age_minutes": round(min(ages), 1) if ages else None,
        "within_30m": sum(1 for age in ages if age <= 30),
        "within_2h": sum(1 for age in ages if age <= 120),
        "within_24h": sum(1 for age in ages if age <= 1440),
        "sample_titles": [item.title for item in candidates[:3]],
        "sample_urls": [item.url for item in candidates[:3] if item.url],
        "url_coverage": sum(1 for item in candidates if item.url),
    }
    if raw_count and not candidates:
        safe["schema_probe"] = _schema_probe(payload)

    (output_dir / "justone-cross-search-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Just One free cross-platform Xiaohongshu smoke result:")
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
