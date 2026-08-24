from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_path: str
    cors_origins: tuple[str, ...]
    ai_provider: str
    openai_api_key: str | None
    openai_model: str | None
    scan_connectors: tuple[str, ...]
    notify_min_score: int
    feishu_webhook_url: str | None
    write_api_token: str | None

    @property
    def notification_enabled(self) -> bool:
        return bool(self.feishu_webhook_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_path=os.getenv("DATABASE_PATH", "./lead_radar.db"),
        cors_origins=_csv(
            "CORS_ORIGINS",
            "https://smirel.com,https://www.smirel.com,http://localhost:3000,http://127.0.0.1:3000",
        ),
        ai_provider=(os.getenv("AI_PROVIDER", "heuristic").strip().lower() or "heuristic"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or None,
        scan_connectors=_csv("SCAN_CONNECTORS", "mock"),
        notify_min_score=max(0, min(100, _int("NOTIFY_MIN_SCORE", 85))),
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL") or None,
        write_api_token=os.getenv("RADAR_WRITE_TOKEN") or None,
    )
