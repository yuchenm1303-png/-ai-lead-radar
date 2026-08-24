from __future__ import annotations

from .base import ClassificationResult
from ..connectors.base import RawLead

SERVICE_KEYWORDS = {
    "微信小程序": ("微信小程序", "小程序", "预约小程序", "商城小程序"),
    "独立站": ("独立站", "shopify", "跨境网站"),
    "企业官网": ("企业官网", "英文官网", "公司官网", "官网"),
    "管理系统": ("管理系统", "后台系统", "erp", "crm"),
    "Python / 数据": ("python", "excel", "数据处理", "爬虫", "脚本"),
    "AI 工具": ("ai", "大模型", "智能体", "机器人", "ai应用", "ai 工具"),
    "网页开发": ("网页", "网站", "前端", "后端", "h5", "商城", "预约系统"),
    "自动化": ("自动化", "自动处理", "批处理"),
}

POSITIVE = {
    "有偿": 30, "预算": 18, "多少钱": 20, "报价": 18, "找人": 24, "找开发": 30,
    "求开发": 30, "帮忙做": 24, "帮忙写": 24, "需要开发": 28, "需要做": 18,
    "有没有会": 16, "有没有人会": 16, "急": 18, "外包": 30, "想做": 14,
    "想搞": 14, "找谁": 16, "公司": 8, "项目": 8, "可以聊": 8,
}

NEGATIVE = {
    "学习": 35, "教程": 35, "课程": 35, "怎么学": 45, "学习路线": 45,
    "招聘": 25, "找工作": 35, "面试": 35, "源码分享": 35, "课程推荐": 45,
}


class HeuristicClassifier:
    name = "heuristic"

    def classify(self, item: RawLead) -> ClassificationResult:
        text = f"{item.title} {item.excerpt}".lower()
        category = "其他开发"
        best_hits = 0
        for name, keywords in SERVICE_KEYWORDS.items():
            hits = sum(1 for keyword in keywords if keyword.lower() in text)
            if hits > best_hits:
                category, best_hits = name, hits

        positive_hits = [key for key in POSITIVE if key in text]
        negative_hits = [key for key in NEGATIVE if key in text]
        intent = 18 + sum(POSITIVE[key] for key in positive_hits) - sum(NEGATIVE[key] for key in negative_hits)
        intent = max(0, min(100, intent))

        fit = 92 if best_hits >= 2 else 82 if best_hits == 1 else 25
        if category in {"Python / 数据", "网页开发", "微信小程序", "企业官网", "独立站", "管理系统", "AI 工具", "自动化"}:
            fit = min(100, fit + 5)

        urgency = "normal"
        if any(word in text for word in ("马上", "今天必须", "非常急", "急急", "立刻")):
            urgency = "urgent"
        elif any(word in text for word in ("急", "尽快", "今天", "近期")):
            urgency = "high"
        elif any(word in text for word in ("不着急", "先了解", "以后")):
            urgency = "low"

        strong_negative = bool(negative_hits) and not any(word in text for word in ("有偿", "预算", "找开发", "求开发", "外包"))
        is_lead = best_hits > 0 and intent >= 45 and not strong_negative
        confidence = 0.86 if (positive_hits or negative_hits) else 0.58

        if is_lead:
            reason = f"检测到{category}需求，并出现“{' / '.join(positive_hits[:3]) or '明确项目语境'}”等行动意向信号。"
        elif strong_negative:
            reason = f"内容更像学习/教程讨论，命中“{' / '.join(negative_hits[:3])}”，不建议作为潜客。"
        else:
            reason = "开发能力词存在，但购买/委托意向不足，暂不进入高价值潜客。"

        signals = tuple(dict.fromkeys([category, *positive_hits[:4], *(f"排除:{x}" for x in negative_hits[:2])]))
        return ClassificationResult(
            is_lead=is_lead,
            need_type=category,
            intent_score=intent,
            fit_score=fit,
            urgency=urgency,
            budget_text=item.budget,
            reason=reason,
            confidence=confidence,
            signals=signals,
        )
