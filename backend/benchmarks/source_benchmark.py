from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_KEYWORDS = [
    "找人做小程序",
    "有没有会做小程序的",
    "微信小程序 有偿",
    "小程序 外包",
    "网站开发 有偿",
    "找人做网站",
    "想做一个网站",
    "寻找开发团队",
    "AI智能体 开发团队",
    "AI智能体 外包",
    "网页开发 找人",
    "管理系统 开发",
    "独立站 开发",
    "Python 有偿",
    "Python 急",
    "数据处理 有偿",
    "自动化 开发",
    "软件开发 外包",
    "H5 开发",
    "预约系统 开发",
]

TITLE_KEYS = ("display_title", "title", "note_title", "name")
DESC_KEYS = ("desc", "description", "content", "note_desc", "text")
ID_KEYS = ("note_id", "noteId", "id", "contentId", "content_id")
TIME_KEYS = (
    "createTime",
    "create_time",
    "created_at",
    "publish_time",
    "published_at",
    "timestamp",
    "time",
    "post_time",
)
URL_KEYS = ("url", "share_url", "note_url", "web_url", "link", "originalUrl")


@dataclass(frozen=True)
class Candidate:
    external_id: str
    title: str
    excerpt: str
    published_at: datetime
    url: str | None


@dataclass
class ProbeResult:
    provider: str
    keyword: str
    ok: bool
    latency_ms: int
    raw_candidates: int
    normalized_candidates: int
    url_coverage: int
    newest_age_minutes: float | None
    within_30m: int
    within_2h: int
    within_24h: int
    error: str | None = None


class Provider:
    name: str
    env_key: str

    def enabled(self) -> bool:
        return bool(os.getenv(self.env_key, "").strip())

    def fetch(self, keyword: str, timeout: int) -> Any:
        raise NotImplementedError


def _json_get(url: str, headers: dict[str, str], timeout: int) -> Any:
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AI-Lead-Radar-Benchmark/1.0",
            **headers,
        },
    )
    with urlopen(req, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"non-json response: {text[:180]}") from exc


class TikHubProvider(Provider):
    name = "tikhub"
    env_key = "TIKHUB_API_KEY"

    def fetch(self, keyword: str, timeout: int) -> Any:
        params = urlencode(
            {
                "keyword": keyword,
                "page": 1,
                "sort_type": "time_descending",
                "note_type": "不限",
                "time_filter": "一天内",
            }
        )
        return _json_get(
            f"https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_notes?{params}",
            {"Authorization": f"Bearer {os.environ[self.env_key].strip()}"},
            timeout,
        )


class RnoteProvider(Provider):
    name = "rnote"
    env_key = "RNOTE_API_KEY"

    def fetch(self, keyword: str, timeout: int) -> Any:
        params = urlencode({"keyword": keyword, "page": 1, "sort": "time_descending", "note_type": 0})
        return _json_get(
            f"https://rnote.dev/api/v2/crawler/search/notes?{params}",
            {"X-API-Key": os.environ[self.env_key].strip()},
            timeout,
        )


class JustOneProvider(Provider):
    name = "justone"
    env_key = "JUSTONE_API_TOKEN"

    def fetch(self, keyword: str, timeout: int) -> Any:
        params = urlencode(
            {
                "token": os.environ[self.env_key].strip(),
                "keyword": keyword,
                "page": 1,
                "sortType": "time_descending",
                "noteType": "ALL",
                "timeFilter": "ONE_DAY",
            }
        )
        payload = _json_get(
            f"https://api.justoneapi.com/api/xiaohongshu/search-note/v4?{params}",
            {},
            timeout,
        )
        if isinstance(payload, dict) and payload.get("code") not in (None, 0):
            raise RuntimeError(f"Just One business error {payload.get('code')}: {payload.get('message') or payload.get('msg') or ''}")
        return payload


PROVIDERS = [TikHubProvider(), RnoteProvider(), JustOneProvider()]


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first_text(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_scalar(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = node.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return value
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_datetime(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _external_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)", url)
    if match:
        return match.group(1)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"url-{digest}"


def _looks_like_note(node: dict[str, Any]) -> bool:
    if any(key in node for key in ("note_id", "noteId", "contentId", "content_id")):
        return True
    title = _first_text(node, TITLE_KEYS)
    desc = _first_text(node, DESC_KEYS)
    url = _first_text(node, URL_KEYS).lower()
    source = str(node.get("sourceName") or node.get("source") or "").lower()
    return bool(
        (title or desc)
        and (
            "xiaohongshu.com" in url
            or "xhslink.com" in url
            or source in {"小红书", "xiaohongshu", "rednote"}
        )
    )


def extract_candidates(payload: Any) -> tuple[int, list[Candidate]]:
    raw_nodes = [node for node in _walk_dicts(payload) if _looks_like_note(node)]
    candidates: list[Candidate] = []
    seen: set[str] = set()

    for node in raw_nodes:
        title = _first_text(node, TITLE_KEYS)
        excerpt = _first_text(node, DESC_KEYS)
        published_at = _parse_datetime(_first_scalar(node, TIME_KEYS))
        url = _first_text(node, URL_KEYS) or None
        external_id = str(_first_scalar(node, ID_KEYS) or "").strip()
        if not external_id and url:
            external_id = _external_id_from_url(url)

        if not external_id or not published_at or not (title or excerpt):
            continue
        if external_id in seen:
            continue
        seen.add(external_id)

        if not title:
            title = excerpt[:100].strip()
        if not excerpt:
            excerpt = title
        if not url and len(external_id) >= 16 and not external_id.startswith("url-"):
            url = f"https://www.xiaohongshu.com/explore/{external_id}"

        candidates.append(
            Candidate(
                external_id=external_id,
                title=title[:240],
                excerpt=excerpt[:1600],
                published_at=published_at,
                url=url,
            )
        )

    return len(raw_nodes), candidates


def probe(provider: Provider, keyword: str, timeout: int = 90) -> ProbeResult:
    started = time.perf_counter()
    try:
        payload = provider.fetch(keyword, timeout)
        raw_count, candidates = extract_candidates(payload)
        now = datetime.now(timezone.utc)
        ages = [max(0.0, (now - item.published_at).total_seconds() / 60.0) for item in candidates]
        return ProbeResult(
            provider=provider.name,
            keyword=keyword,
            ok=True,
            latency_ms=round((time.perf_counter() - started) * 1000),
            raw_candidates=raw_count,
            normalized_candidates=len(candidates),
            url_coverage=sum(1 for item in candidates if item.url),
            newest_age_minutes=min(ages) if ages else None,
            within_30m=sum(1 for age in ages if age <= 30),
            within_2h=sum(1 for age in ages if age <= 120),
            within_24h=sum(1 for age in ages if age <= 1440),
        )
    except (HTTPError, URLError, TimeoutError, RuntimeError, KeyError, OSError) as exc:
        return ProbeResult(
            provider=provider.name,
            keyword=keyword,
            ok=False,
            latency_ms=round((time.perf_counter() - started) * 1000),
            raw_candidates=0,
            normalized_candidates=0,
            url_coverage=0,
            newest_age_minutes=None,
            within_30m=0,
            within_2h=0,
            within_24h=0,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


def summarize(results: list[ProbeResult]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for provider_name in sorted({row.provider for row in results}):
        rows = [row for row in results if row.provider == provider_name]
        ok_rows = [row for row in rows if row.ok]
        latencies = [row.latency_ms for row in ok_rows]
        ages = [row.newest_age_minutes for row in ok_rows if row.newest_age_minutes is not None]
        normalized = sum(row.normalized_candidates for row in ok_rows)
        urls = sum(row.url_coverage for row in ok_rows)
        summary.append(
            {
                "provider": provider_name,
                "queries": len(rows),
                "success_rate": round(len(ok_rows) / len(rows), 3) if rows else 0,
                "median_latency_ms": round(statistics.median(latencies)) if latencies else None,
                "median_newest_age_minutes": round(statistics.median(ages), 1) if ages else None,
                "normalized_candidates": normalized,
                "within_30m": sum(row.within_30m for row in ok_rows),
                "within_2h": sum(row.within_2h for row in ok_rows),
                "within_24h": sum(row.within_24h for row in ok_rows),
                "url_coverage_rate": round(urls / normalized, 3) if normalized else 0,
            }
        )
    return summary


def write_reports(results: list[ProbeResult], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = output_dir / f"source-benchmark-{stamp}.csv"
    json_path = output_dir / f"source-benchmark-{stamp}.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for row in results:
            writer.writerow(asdict(row))
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "results": [asdict(row) for row in results],
                "summary": summarize(results),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return csv_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Xiaohongshu search data providers for AI Lead Radar.")
    parser.add_argument("--provider", action="append", choices=[p.name for p in PROVIDERS])
    parser.add_argument("--keyword", action="append")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    selected_names = set(args.provider or [p.name for p in PROVIDERS])
    selected = [p for p in PROVIDERS if p.name in selected_names]
    missing = [f"{p.name}:{p.env_key}" for p in selected if not p.enabled()]
    if missing:
        print("Missing API credentials:", ", ".join(missing))
        return 2

    keywords = args.keyword or DEFAULT_KEYWORDS
    results: list[ProbeResult] = []
    for provider in selected:
        for keyword in keywords:
            row = probe(provider, keyword, timeout=args.timeout)
            results.append(row)
            newest = f"{row.newest_age_minutes:.1f}m" if row.newest_age_minutes is not None else "-"
            print(
                f"[{'ok' if row.ok else 'fail'}] {provider.name:<8} {keyword:<18} "
                f"{row.latency_ms:>5}ms normalized={row.normalized_candidates:<3} newest={newest}"
            )

    if not results:
        print("No benchmark rows produced.")
        return 1
    csv_path, json_path = write_reports(results, Path(args.output_dir))
    print(json.dumps(summarize(results), ensure_ascii=False, indent=2))
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
