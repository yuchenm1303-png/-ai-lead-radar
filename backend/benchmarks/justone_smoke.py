from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .source_benchmark import extract_candidates
except ImportError:  # direct script execution from backend/benchmarks
    from source_benchmark import extract_candidates


_SENSITIVE_HINTS = ("token", "authorization", "cookie", "secret", "password", "credential")
_ID_KEYS = ("noteid", "note_id", "id", "noteidstr", "note_id_str")
_TITLE_KEYS = ("title", "displaytitle", "display_title", "notetitle", "note_title", "name")
_TEXT_KEYS = ("desc", "description", "content", "text")
_TIME_KEYS = (
    "createtime",
    "create_time",
    "publishtime",
    "publish_time",
    "publishedat",
    "published_at",
    "timestamp",
    "time",
)
_URL_KEYS = ("url", "link", "noteurl", "note_url", "shareurl", "share_url")
_XHS_ID_RE = re.compile(r"/explore/([0-9a-zA-Z]+)")


def _business_message(payload: dict[str, Any]) -> str:
    for key in ("message", "msg", "message_zh", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return ""


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "<max-depth>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(hint in lowered for hint in _SENSITIVE_HINTS):
                out[key_text] = "<redacted>"
            else:
                out[key_text] = _redact(child, depth + 1)
        return out
    if isinstance(value, list):
        return [_redact(child, depth + 1) for child in value[:100]]
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _lower_map(node: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    return {str(key).lower(): (str(key), value) for key, value in node.items()}


def _first_value(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = _lower_map(node)
    for key in keys:
        if key in lowered:
            return lowered[key][1]
    return None


def _first_text(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = _first_value(node, keys)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:
            number /= 1000.0
        if 1_000_000_000 <= number <= 4_000_000_000:
            try:
                return datetime.fromtimestamp(number, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            number = None
        if number is not None:
            return _parse_time(number)
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _url_from_node(node: dict[str, Any], note_id: str) -> str:
    for key in _URL_KEYS:
        value = _first_value(node, (key,))
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for value in node.values():
        if isinstance(value, str) and "xiaohongshu.com/explore/" in value:
            return value
    return f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""


def _generic_records(payload: dict[str, Any], limit: int = 80) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in _walk_dicts(payload.get("data")):
        note_id_value = _first_value(node, _ID_KEYS)
        note_id = str(note_id_value).strip() if isinstance(note_id_value, (str, int)) else ""
        title = _first_text(node, _TITLE_KEYS)
        if not title:
            text = _first_text(node, _TEXT_KEYS)
            title = text[:120] if text else ""
        published = None
        for key in _TIME_KEYS:
            published = _parse_time(_first_value(node, (key,)))
            if published:
                break
        url = _url_from_node(node, note_id)
        if not note_id and url:
            match = _XHS_ID_RE.search(url)
            if match:
                note_id = match.group(1)

        evidence = sum(bool(value) for value in (note_id, title, published, url))
        if evidence < 2:
            continue
        dedupe = note_id or url or f"{title}|{published.isoformat() if published else ''}"
        if not dedupe or dedupe in seen:
            continue
        seen.add(dedupe)
        records.append(
            {
                "note_id": note_id,
                "title": title[:200],
                "published_at": published.isoformat() if published else None,
                "url": url,
                "keys": sorted(str(key) for key in node.keys())[:80],
            }
        )
        if len(records) >= limit:
            break
    return records


def _data_shape(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return {
        "payload_top_keys": sorted(str(key) for key in payload.keys())[:80],
        "data_type": type(data).__name__,
        "data_top_keys": sorted(str(key) for key in data.keys())[:80] if isinstance(data, dict) else [],
        "data_list_length": len(data) if isinstance(data, list) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One-call final verdict for Just One Xiaohongshu V4 freshness.")
    parser.add_argument("--keyword", default="小程序")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    token = os.getenv("JUSTONE_API_TOKEN", "").strip()
    if not token:
        print("Missing JUSTONE_API_TOKEN")
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    params = urlencode(
        {
            "token": token,
            "keyword": args.keyword,
            "page": 1,
            "sortType": "time_descending",
            "noteType": "ALL",
            "timeFilter": "ALL",
        }
    )
    url = f"https://api.justoneapi.com/api/xiaohongshu/search-note/v4?{params}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "AI-Lead-Radar-Benchmark/1.0"})

    try:
        with urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        safe = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        (output_dir / "justone-v4-final-verdict.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    if not isinstance(payload, dict):
        safe = {"ok": False, "error": "Unexpected non-object response"}
        (output_dir / "justone-v4-final-verdict.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    redacted = _redact(payload)
    (output_dir / "justone-v4-redacted-response.json").write_text(
        json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    code = payload.get("code")
    if code != 0:
        safe = {
            "ok": False,
            "business_code": code,
            "message": _business_message(payload),
            "request": {"keyword": args.keyword, "sortType": "time_descending", "noteType": "ALL", "timeFilter": "ALL"},
            "data_shape": _data_shape(payload),
        }
        (output_dir / "justone-v4-final-verdict.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Just One V4 final verdict response:")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 1

    parser_raw_count, parser_candidates = extract_candidates(payload)
    generic = _generic_records(payload)
    now = datetime.now(timezone.utc)

    timestamps: list[datetime] = []
    for item in parser_candidates:
        timestamps.append(item.published_at)
    for item in generic:
        if item["published_at"]:
            parsed = _parse_time(item["published_at"])
            if parsed:
                timestamps.append(parsed)

    ages = [max(0.0, (now - dt).total_seconds() / 60.0) for dt in timestamps]
    urls = {item.url for item in parser_candidates if item.url}
    urls.update(item["url"] for item in generic if item["url"])
    titles = [item.title for item in parser_candidates[:5] if item.title]
    if len(titles) < 5:
        titles.extend(item["title"] for item in generic if item["title"] and item["title"] not in titles)

    record_count = max(len(parser_candidates), len(generic))
    newest = min(ages) if ages else None
    if record_count == 0:
        verdict = "FAIL_EMPTY_OR_UNUSABLE_RESPONSE"
    elif newest is None:
        verdict = "INCONCLUSIVE_NO_PARSEABLE_TIMESTAMPS"
    elif newest <= 120:
        verdict = "PASS_FRESH_ENOUGH_FOR_RADAR"
    elif newest <= 1440:
        verdict = "CONDITIONAL_SAME_DAY_BUT_NOT_FAST"
    else:
        verdict = "FAIL_STALE_FOR_LEAD_RADAR"

    safe = {
        "ok": True,
        "business_code": 0,
        "verdict": verdict,
        "request": {
            "keyword": args.keyword,
            "page": 1,
            "sortType": "time_descending",
            "noteType": "ALL",
            "timeFilter": "ALL",
            "local_freshness_filter": True,
        },
        "data_shape": _data_shape(payload),
        "parser_raw_candidates": parser_raw_count,
        "parser_normalized_candidates": len(parser_candidates),
        "generic_record_candidates": len(generic),
        "effective_record_count": record_count,
        "parseable_timestamps": len(timestamps),
        "newest_age_minutes": round(newest, 1) if newest is not None else None,
        "within_30m": sum(1 for age in ages if age <= 30),
        "within_2h": sum(1 for age in ages if age <= 120),
        "within_24h": sum(1 for age in ages if age <= 1440),
        "url_coverage": len(urls),
        "sample_titles": titles[:5],
        "generic_samples": generic[:5],
        "artifact_note": "Full API response is saved separately after recursive secret redaction; no second API call is needed for parser diagnosis.",
    }
    (output_dir / "justone-v4-final-verdict.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Just One Xiaohongshu V4 FINAL VERDICT (one API request):")
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
