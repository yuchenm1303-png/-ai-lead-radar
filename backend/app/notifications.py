from __future__ import annotations

import logging

import httpx

from .schemas import Lead
from .settings import get_settings

logger = logging.getLogger(__name__)


def notify_high_value(lead: Lead) -> bool:
    settings = get_settings()
    if not settings.feishu_webhook_url:
        return False

    lines = [
        "【AI Lead Radar】发现高分开发需求",
        f"来源：{lead.source}",
        f"类型：{lead.category}",
        f"AI Score：{lead.score}",
        f"需求：{lead.title}",
        f"发布时间：{lead.published_at.isoformat()}",
    ]
    if lead.url:
        lines.append(f"链接：{lead.url}")
    try:
        response = httpx.post(
            settings.feishu_webhook_url,
            json={"msg_type": "text", "content": {"text": "\n".join(lines)}},
            timeout=8.0,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Notification failed: %s", exc)
        return False
