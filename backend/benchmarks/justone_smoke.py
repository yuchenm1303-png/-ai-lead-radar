from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from source_benchmark import JustOneProvider, extract_candidates


_SCHEMA_HINTS = (
    "id",
    "note",
    "card",
    "title",
    "display",
    "desc",
    "content",
    "time",
    "publish",
    "url",
    "link",
    "type",
    "user",
)
_SENSITIVE_HINTS = ("token", "authorization", "cookie", "secret", "password")


def _business_message(payload: dict[str, Any]) -> str:
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
        return value[:160]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _schema_probe(payload: dict[str, Any], limit: int = 3) -> dict[str, Any]:
    data = payload.get("data")
    probe: dict[str, Any] = {
        "payload_top_keys": sorted(str(key) for key in payload.keys())[:80],
        "data_type": type(data).__name__,
        "data_top_keys": sorted(str(key) for key in data.keys())[:80] if isinstance(data, dict) else [],
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
                "keys": sorted(str(key) for key in node.keys())[:100],
                "selected": selected,
            }
        )
    return probe


def main() -> int:
    parser = argparse.ArgumentParser(description="One-call Just One Xiaohongshu search smoke test.")
    parser.add_argument("--keyword", default="小程序")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    token = os.getenv("JUSTONE_API_TOKEN", "").strip()
    if not token:
        print("Missing JUSTONE_API_TOKEN")
        return 2

    provider = JustOneProvider()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        payload = provider.fetch(args.keyword, timeout=args.timeout)
    except Exception as exc:  # network/HTTP failure; secret is never printed
        safe = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        (output_dir / "justone-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    if not isinstance(payload, dict):
        safe = {"ok": False, "error": "Unexpected non-object response"}
        (output_dir / "justone-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    code = payload.get("code")
    if code != 0:
        safe = {
            "ok": False,
            "business_code": code,
            "message": _business_message(payload),
        }
        (output_dir / "justone-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Just One API business response:")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    raw_count, candidates = extract_candidates(payload)
    now = datetime.now(timezone.utc)
    ages = [max(0.0, (now - item.published_at).total_seconds() / 60.0) for item in candidates]
    safe: dict[str, Any] = {
        "ok": True,
        "business_code": 0,
        "keyword": args.keyword,
        "raw_candidates": raw_count,
        "normalized_candidates": len(candidates),
        "newest_age_minutes": round(min(ages), 1) if ages else None,
        "within_30m": sum(1 for age in ages if age <= 30),
        "within_2h": sum(1 for age in ages if age <= 120),
        "within_24h": sum(1 for age in ages if age <= 1440),
        "sample_titles": [item.title for item in candidates[:3]],
        "url_coverage": sum(1 for item in candidates if item.url),
    }
    if not candidates:
        safe["schema_probe"] = _schema_probe(payload)

    (output_dir / "justone-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Just One Xiaohongshu smoke result:")
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
