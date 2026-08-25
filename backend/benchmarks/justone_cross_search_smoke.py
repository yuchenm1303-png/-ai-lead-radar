from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from source_benchmark import extract_candidates


def _message(payload: dict[str, Any]) -> str:
    for key in ("message", "msg", "message_zh", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="One-call free Just One cross-platform Xiaohongshu smoke test.")
    parser.add_argument("--keyword", default="寻找开发团队")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    token = os.getenv("JUSTONE_API_TOKEN", "").strip()
    if not token:
        print("Missing JUSTONE_API_TOKEN")
        return 2

    end = datetime.now().astimezone()
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
    safe = {
        "ok": True,
        "business_code": 0,
        "endpoint": "/api/search/v1",
        "source": "XIAOHONGSHU",
        "keyword": args.keyword,
        "window_hours": args.hours,
        "raw_candidates": raw_count,
        "normalized_candidates": len(candidates),
        "sample_titles": [item.title for item in candidates[:3]],
        "url_coverage": sum(1 for item in candidates if item.url),
    }
    (output_dir / "justone-cross-search-smoke.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Just One free cross-platform Xiaohongshu smoke result:")
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
