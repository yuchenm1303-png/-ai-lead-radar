from __future__ import annotations

import json
import os
import urllib.request

from .schemas import Lead


def notification_enabled() -> bool:
    return bool(os.getenv("FEISHU_WEBHOOK_URL"))


def notify_high_score(lead: Lead) -> bool:
    url = os.getenv("FEISHU_WEBHOOK_URL")
    try:
        threshold = max(0, min(100, int(os.getenv("NOTIFY_MIN_SCORE", "85"))))
    except ValueError:
        threshold = 85
    if not url or lead.score < threshold or lead.status != "new" or lead.notified_at is not None:
        return False

    text = (
        "【AI Lead Radar】发现高分开发需求\n"
        f"来源：{lead.source}\n"
        f"类型：{lead.category}\n"
        f"AI Score：{lead.score}\n"
        f"发布时间：{lead.published_at.isoformat()}\n"
        f"需求：{lead.title}\n"
        f"链接：{lead.url or '未提供'}"
    )
    data = json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode()
    try:
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:
        return False
