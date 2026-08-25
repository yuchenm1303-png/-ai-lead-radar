from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import RawLead


DEFAULT_ENDPOINT = "https://api.justoneapi.com/api/xiaohongshu/search-note/v4"


class JustOneError(RuntimeError):
    pass


@dataclass(frozen=True)
class JustOneFetchResult:
    keyword: str
    raw_count: int
    leads: list[RawLead]
    has_more: bool | None = None
    request_id: str | None = None


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    else:
        return None

    if number > 1e12:
        number /= 1000.0
    if not (1_000_000_000 <= number <= 4_000_000_000):
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _note_to_raw(note: Any) -> RawLead | None:
    if not isinstance(note, dict):
        return None
    note_id = str(note.get("id") or "").strip()
    title = str(note.get("title") or "").strip()
    excerpt = str(note.get("desc") or "").strip()
    published_at = _parse_timestamp(note.get("timestamp"))
    if not note_id or not published_at or not (title or excerpt):
        return None
    if not title:
        title = excerpt[:120]
    return RawLead(
        source="小红书",
        external_id=note_id,
        title=title[:240],
        excerpt=excerpt[:1600],
        published_at=published_at,
        url=f"https://www.xiaohongshu.com/explore/{note_id}",
        budget=None,
    )


class JustOneConnector:
    name = "justone-xiaohongshu-v4"

    def __init__(self, token: str | None = None, endpoint: str | None = None) -> None:
        self.token = (token or os.getenv("JUSTONE_API_TOKEN", "")).strip()
        self.endpoint = (endpoint or os.getenv("JUSTONE_API_ENDPOINT", DEFAULT_ENDPOINT)).strip()
        if not self.token:
            raise JustOneError("JUSTONE_API_TOKEN is required")

    def fetch_query(self, keyword: str, *, timeout: int = 60, page: int = 1) -> JustOneFetchResult:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("keyword is required")
        params = urlencode(
            {
                "token": self.token,
                "keyword": keyword,
                "page": max(1, int(page)),
                "sortType": "time_descending",
                "noteType": "ALL",
                # Fetch newest results without trusting server-side freshness filters.
                # Freshness is evaluated locally after retrieval.
                "timeFilter": "ALL",
            }
        )
        request = Request(
            f"{self.endpoint}?{params}",
            headers={"Accept": "application/json", "User-Agent": "AI-Lead-Radar/1.0"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise JustOneError(f"Just One HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise JustOneError(f"Just One network error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise JustOneError("Just One returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise JustOneError("Just One returned a non-object response")
        code = payload.get("code")
        if code != 0:
            message = str(payload.get("message") or payload.get("msg") or "business error")[:240]
            raise JustOneError(f"Just One business code {code}: {message}")

        data = payload.get("data")
        notes = data.get("notes") if isinstance(data, dict) else None
        if not isinstance(notes, list):
            notes = []

        leads: list[RawLead] = []
        seen: set[str] = set()
        for note in notes:
            raw = _note_to_raw(note)
            if raw is None or raw.external_id in seen:
                continue
            seen.add(raw.external_id)
            leads.append(raw)

        return JustOneFetchResult(
            keyword=keyword,
            raw_count=len(notes),
            leads=leads,
            has_more=data.get("has_more") if isinstance(data, dict) and isinstance(data.get("has_more"), bool) else None,
            request_id=str(payload.get("requestId")) if payload.get("requestId") else None,
        )
