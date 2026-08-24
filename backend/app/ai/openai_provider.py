from __future__ import annotations

import json

import httpx

from .base import ClassificationResult
from ..connectors.base import RawLead

ALLOWED_TYPES = [
    "微信小程序", "网页开发", "企业官网", "独立站", "管理系统", "H5", "商城",
    "预约系统", "前端", "后端", "Python / 数据", "AI 工具", "自动化", "软件开发", "其他开发",
]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_lead": {"type": "boolean"},
        "need_type": {"type": "string", "enum": ALLOWED_TYPES},
        "intent_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "urgency": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "budget_text": {"type": ["string", "null"]},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "signals": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["is_lead", "need_type", "intent_score", "fit_score", "urgency", "budget_text", "reason", "confidence", "signals"],
}


class OpenAIClassifier:
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 20.0):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def classify(self, item: RawLead) -> ClassificationResult:
        prompt = (
            "判断下面公开内容是否是一个值得软件开发者人工联系的真实外包/开发需求。"
            "重点区分购买/委托意图与学习、教程、讨论、招聘求职。"
            "不要推断或输出个人敏感信息；只根据给定文本判断。\n\n"
            f"来源：{item.source}\n标题：{item.title}\n内容：{item.excerpt}\n"
            f"显式预算：{item.budget or '未提供'}\n发布时间：{item.published_at.isoformat()}"
        )
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "store": False,
                "instructions": "你是 AI Lead Radar 的潜客分类器。宁可少报，也不要把学习讨论误判成付费开发需求。",
                "input": prompt,
                "text": {"format": {"type": "json_schema", "name": "lead_classification", "strict": True, "schema": SCHEMA}},
                "max_output_tokens": 500,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        output_text = ""
        for output in data.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    output_text = content.get("text", "")
                    break
            if output_text:
                break
        if not output_text:
            raise RuntimeError("OpenAI response did not contain output_text")
        parsed = json.loads(output_text)
        return ClassificationResult(
            is_lead=bool(parsed["is_lead"]),
            need_type=str(parsed["need_type"]),
            intent_score=int(parsed["intent_score"]),
            fit_score=int(parsed["fit_score"]),
            urgency=parsed["urgency"],
            budget_text=parsed.get("budget_text") or item.budget,
            reason=str(parsed["reason"])[:1200],
            confidence=float(parsed["confidence"]),
            signals=tuple(str(x)[:80] for x in parsed.get("signals", [])[:8]),
        )
