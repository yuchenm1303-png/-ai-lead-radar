from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from source_benchmark import JustOneProvider, extract_candidates


def _business_message(payload: dict[str, Any]) -> str:
    for key in ("message", "msg", "message_zh", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="One-call Just One Xiaohongshu search smoke test.")
    parser.add_argument("--keyword", default="寻找开发团队")
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
    safe = {
        "ok": True,
        "business_code": 0,
        "keyword": args.keyword,
        "raw_candidates": raw_count,
        "normalized_candidates": len(candidates),
        "newest_age_minutes": round(min(ages), 1) if ages else None,
        "within_30m": sum(1 for age in ages if age <= 30),
        "within_2h": sum(1 for age in ages if age <= 120),
        "within_24h": sum(1 for age in ages if age <= 1440),
        "url_coverage": sum(1 for item in candidates if item.url),
    }
    (output_dir / "justone-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Just One Xiaohongshu smoke result:")
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
