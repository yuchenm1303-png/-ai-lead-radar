from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

JUSTONE_BASE_URL = "https://api.justoneapi.com"

# Real anchors observed by Lead Radar. They are deliberately different controls:
# - buyer note: clear paid website demand with an active comment thread;
# - provider note: software-development marketing where commenters may be buyers;
# - provider author: recent provider account with a stable user id for history checks.
DEFAULT_BUYER_NOTE_ID = "6a8fb04b000000002501b7ab"
DEFAULT_PROVIDER_NOTE_ID = "6a8ff544000000000302bf1e"
DEFAULT_PROVIDER_AUTHOR_ID = "6a76cbe80000000013003800"
DEFAULT_SUGGESTION_SEED = "网页自动化"


@dataclass(frozen=True)
class SignalProbe:
    key: str
    generator: str
    endpoint: str
    note_id: str | None = None
    user_id: str | None = None
    keyword: str | None = None
    sort: str | None = None


@dataclass
class SignalResult:
    key: str
    generator: str
    endpoint: str
    ok: bool
    latency_ms: int
    raw_objects: int
    normalized_items: int
    unique_authors: int
    error: str | None = None
    schema_probe: dict[str, Any] | None = None


def build_default_plan(
    *,
    buyer_note_id: str = DEFAULT_BUYER_NOTE_ID,
    provider_note_id: str = DEFAULT_PROVIDER_NOTE_ID,
    provider_author_id: str = DEFAULT_PROVIDER_AUTHOR_ID,
) -> list[SignalProbe]:
    return [
        SignalProbe(
            key="buyer_note_comments",
            generator="conversation_negative_control",
            endpoint="/api/xiaohongshu/get-note-comment/v2",
            note_id=buyer_note_id.strip(),
            sort="latest",
        ),
        SignalProbe(
            key="provider_note_comments",
            generator="conversation_buyer_discovery",
            endpoint="/api/xiaohongshu/get-note-comment/v2",
            note_id=provider_note_id.strip(),
            sort="latest",
        ),
        SignalProbe(
            key="provider_actor_history",
            generator="actor_expansion",
            endpoint="/api/xiaohongshu/get-user-note-list/v4",
            user_id=provider_author_id.strip(),
        ),
    ]


def keyword_suggestion_probe(seed: str = DEFAULT_SUGGESTION_SEED) -> SignalProbe:
    return SignalProbe(
        key="platform_language_suggestions",
        generator="platform_language_expansion",
        endpoint="/api/xiaohongshu/search-recommend/v1",
        keyword=seed.strip(),
    )


def validate_plan_budget(plan: list[SignalProbe], max_provider_calls: int) -> None:
    if len(plan) > max(0, int(max_provider_calls)):
        raise ValueError(f"plan needs {len(plan)} calls but max_provider_calls={max_provider_calls}")


def build_request_url(probe: SignalProbe, token: str) -> str:
    params: dict[str, str] = {"token": token}
    if probe.endpoint == "/api/xiaohongshu/get-note-comment/v2":
        if not probe.note_id:
            raise ValueError("comment probe requires note_id")
        params["noteId"] = probe.note_id
        params["sort"] = probe.sort or "latest"
    elif probe.endpoint == "/api/xiaohongshu/get-user-note-list/v4":
        if not probe.user_id:
            raise ValueError("actor-history probe requires user_id")
        params["userId"] = probe.user_id
    elif probe.endpoint == "/api/xiaohongshu/search-recommend/v1":
        if not probe.keyword:
            raise ValueError("keyword-suggestion probe requires keyword")
        params["keyword"] = probe.keyword
    else:
        raise ValueError(f"unsupported endpoint: {probe.endpoint}")
    return f"{JUSTONE_BASE_URL}{probe.endpoint}?{urlencode(params)}"


def _fetch_json(url: str, timeout: int) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "AI-Lead-Radar-V4-Signal-Benchmark/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _business_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "unexpected non-object response"
    if payload.get("code") in (None, 0):
        return None
    message = payload.get("message") or payload.get("msg") or payload.get("message_zh") or ""
    return f"business code {payload.get('code')}: {str(message)[:220]}"


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


def _user_from_node(node: dict[str, Any]) -> tuple[str, str]:
    for key in ("user", "author", "user_info", "userInfo", "note_user"):
        user = node.get(key)
        if isinstance(user, dict):
            user_id = _first_text(user, ("id", "user_id", "userId", "userid", "red_id"))
            name = _first_text(user, ("nickname", "nick_name", "name", "user_name", "userName"))
            if user_id or name:
                return user_id[:160], name[:120]
    return "", ""


def extract_comments(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = 0
    for node in _walk_dicts(payload.get("data") if isinstance(payload, dict) else payload):
        text = _first_text(node, ("content", "text", "comment", "comment_content", "commentContent"))
        if not text:
            continue
        user_id, user_name = _user_from_node(node)
        comment_id = _first_text(node, ("id", "comment_id", "commentId", "comment_id_str"))
        # Avoid treating a note object as a comment: require either a comment id/user
        # or a reply/comment-specific key.
        commentish = bool(
            comment_id
            or user_id
            or user_name
            or any(key in node for key in ("sub_comments", "subComments", "reply_count", "replyCount", "comment_id"))
        )
        if not commentish:
            continue
        raw += 1
        key = comment_id or f"{user_id}|{user_name}|{text[:120]}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "comment_id": comment_id or None,
                "text": text[:1600],
                "author_id": user_id or None,
                "author_name": user_name or None,
                "like_count": node.get("like_count") or node.get("liked_count") or node.get("likes") or None,
            }
        )
    return raw, candidates


def extract_actor_notes(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = 0
    for node in _walk_dicts(payload.get("data") if isinstance(payload, dict) else payload):
        note_id = _first_text(node, ("note_id", "noteId", "id", "content_id", "contentId"))
        title = _first_text(node, ("display_title", "title", "note_title", "name"))
        excerpt = _first_text(node, ("desc", "description", "content", "note_desc", "text"))
        if not note_id or not (title or excerpt):
            continue
        # User-history responses can contain nested users/cover objects. Keep only
        # objects that look note-like rather than any arbitrary id-bearing node.
        if not any(key in node for key in ("title", "display_title", "desc", "note_title", "note_type", "noteType", "timestamp", "time")):
            continue
        raw += 1
        if note_id in seen:
            continue
        seen.add(note_id)
        result.append(
            {
                "note_id": note_id[:160],
                "title": title[:240],
                "excerpt": excerpt[:1600],
                "url": f"https://www.xiaohongshu.com/explore/{note_id}",
            }
        )
    return raw, result


def extract_suggestions(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = 0
    for node in _walk_dicts(payload.get("data") if isinstance(payload, dict) else payload):
        text = _first_text(node, ("keyword", "query", "text", "name", "suggestion", "word"))
        if not text:
            continue
        raw += 1
        normalized = " ".join(text.split())
        if normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        suggestions.append({"keyword": normalized[:240]})
    return raw, suggestions


def _schema_probe(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    samples: list[list[str]] = []
    for node in _walk_dicts(data):
        keys = sorted(str(key) for key in node.keys())
        if keys and keys not in samples:
            samples.append(keys[:50])
        if len(samples) >= 5:
            break
    return {"data_type": type(data).__name__, "sample_keys": samples}


def run_probe(probe: SignalProbe, token: str, *, timeout: int = 120) -> tuple[SignalResult, list[dict[str, Any]]]:
    started = time.monotonic()
    try:
        payload = _fetch_json(build_request_url(probe, token), timeout)
        error = _business_error(payload)
        if error:
            raise RuntimeError(error)
        if probe.generator.startswith("conversation_"):
            raw, items = extract_comments(payload)
        elif probe.generator == "actor_expansion":
            raw, items = extract_actor_notes(payload)
        else:
            raw, items = extract_suggestions(payload)
        authors = {
            str(item.get("author_id") or item.get("author_name") or "").strip()
            for item in items
            if str(item.get("author_id") or item.get("author_name") or "").strip()
        }
        return (
            SignalResult(
                key=probe.key,
                generator=probe.generator,
                endpoint=probe.endpoint,
                ok=True,
                latency_ms=round((time.monotonic() - started) * 1000),
                raw_objects=raw,
                normalized_items=len(items),
                unique_authors=len(authors),
                schema_probe=_schema_probe(payload) if raw == 0 or not items else None,
            ),
            items,
        )
    except Exception as exc:
        return (
            SignalResult(
                key=probe.key,
                generator=probe.generator,
                endpoint=probe.endpoint,
                ok=False,
                latency_ms=round((time.monotonic() - started) * 1000),
                raw_objects=0,
                normalized_items=0,
                unique_authors=0,
                error=f"{type(exc).__name__}: {exc}"[:320],
            ),
            [],
        )


def build_report(plan: list[SignalProbe], executions: list[tuple[SignalResult, list[dict[str, Any]]]], *, executed: bool) -> dict[str, Any]:
    manifests = []
    for probe, (result, items) in zip(plan, executions):
        manifests.append({"key": probe.key, "generator": probe.generator, "items": items})
    return {
        "benchmark": "retrieval-v4-signal-benchmark",
        "executed": executed,
        "provider_call_ceiling": len(plan),
        "plan": [asdict(probe) for probe in plan],
        "results": [asdict(result) for result, _ in executions],
        "manifests": manifests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Quota-safe V4 Conversation/Actor signal benchmark. Dry-run by default.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-provider-calls", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--buyer-note-id", default=DEFAULT_BUYER_NOTE_ID)
    parser.add_argument("--provider-note-id", default=DEFAULT_PROVIDER_NOTE_ID)
    parser.add_argument("--provider-author-id", default=DEFAULT_PROVIDER_AUTHOR_ID)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    plan = build_default_plan(
        buyer_note_id=args.buyer_note_id,
        provider_note_id=args.provider_note_id,
        provider_author_id=args.provider_author_id,
    )
    try:
        validate_plan_budget(plan, args.max_provider_calls)
    except ValueError as exc:
        print(f"Refusing benchmark: {exc}")
        return 2

    if not args.execute:
        print(json.dumps(build_report(plan, [], executed=False), ensure_ascii=False, indent=2))
        print("Dry run only. Add --execute to consume exactly the planned provider calls.")
        return 0

    token = os.getenv("JUSTONE_API_TOKEN", "").strip()
    if not token:
        print("Missing JUSTONE_API_TOKEN")
        return 2

    executions = [run_probe(probe, token, timeout=max(60, args.timeout)) for probe in plan]
    payload = build_report(plan, executions, executed=True)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "retrieval-v4-signal-benchmark.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Saved: {path}")
    return 0 if all(result.ok for result, _ in executions) else 1


if __name__ == "__main__":
    raise SystemExit(main())
