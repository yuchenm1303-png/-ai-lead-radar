from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_INFO_URL = "https://api.tikhub.io/api/v1/tikhub/user/get_user_info"
REQUIRED_SCOPES = {
    "/api/v1/tikhub/user/",
    "/api/v1/xiaohongshu/app_v2/",
}


def _safe_error_payload(raw: bytes, status: int) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "http_status": status, "message": text[:200]}

    if not isinstance(payload, dict):
        return {"ok": False, "http_status": status, "message": str(payload)[:200]}

    safe: dict[str, Any] = {"ok": False, "http_status": status}
    for key in ("code", "message", "message_zh", "detail", "router"):
        if key in payload and not isinstance(payload[key], (dict, list)):
            safe[key] = payload[key]
    return safe


def fetch_status(api_key: str, timeout: int = 30) -> tuple[int, dict[str, Any]]:
    request = Request(
        USER_INFO_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "AI-Lead-Radar-Benchmark/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        return 1, _safe_error_payload(exc.read(), exc.code)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return 1, {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:240]}

    if not isinstance(payload, dict):
        return 1, {"ok": False, "error": "Unexpected non-object account response"}

    api_key_data = payload.get("api_key_data") if isinstance(payload.get("api_key_data"), dict) else {}
    user_data = payload.get("user_data") if isinstance(payload.get("user_data"), dict) else {}
    scopes = [str(item) for item in api_key_data.get("api_key_scopes", []) if item]

    safe = {
        "ok": True,
        "code": payload.get("code"),
        "api_key_name": api_key_data.get("api_key_name"),
        "api_key_status": api_key_data.get("api_key_status"),
        "api_key_scopes": scopes,
        "expires_at": api_key_data.get("expires_at"),
        "balance": user_data.get("balance"),
        "free_credit": user_data.get("free_credit"),
        "email_verified": user_data.get("email_verified"),
        "account_disabled": user_data.get("account_disabled"),
        "is_active": user_data.get("is_active"),
    }

    blockers: list[str] = []
    if safe["email_verified"] is False:
        blockers.append("email_not_verified")
    if safe["account_disabled"] is True:
        blockers.append("account_disabled")
    if safe["is_active"] is False:
        blockers.append("account_not_active")
    missing_scopes = sorted(REQUIRED_SCOPES.difference(scopes))
    if missing_scopes:
        blockers.append("missing_required_scope")
        safe["missing_scopes"] = missing_scopes
    safe["blockers"] = blockers
    return (3 if blockers else 0), safe


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely inspect TikHub account/API-key status without printing secrets.")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    api_key = os.getenv("TIKHUB_API_KEY", "").strip()
    if not api_key:
        print("TikHub account diagnostic: missing TIKHUB_API_KEY")
        return 2

    code, safe = fetch_status(api_key, timeout=args.timeout)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "tikhub-account-status.json"
    output_path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")

    print("TikHub account diagnostic (secret and email intentionally omitted):")
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    print(f"Report: {output_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
