from __future__ import annotations

from dataclasses import dataclass

SERVICE_KEYWORDS = (
    "小程序", "微信小程序", "网页", "网站", "独立站", "官网", "管理系统",
    "python", "爬虫", "数据处理", "程序开发", "前端", "后端", "ai", "自动化",
    "软件开发", "h5", "商城", "预约系统", "excel", "脚本", "智能体",
)

INTENT_KEYWORDS = (
    "求", "有偿", "找人", "有没有人会", "有没有会", "多少钱", "预算", "急", "外包",
    "帮忙做", "帮忙写", "需要开发", "需要做", "求开发", "找开发", "报价", "想做",
    "想搞", "找谁", "可以聊", "项目", "公司想", "公司准备",
)

NEGATIVE_KEYWORDS = (
    "学习路线", "怎么学", "课程推荐", "教程推荐", "零基础学习", "面试题", "找工作",
    "招聘", "源码分享", "学习小程序", "学习python", "前端学习",
)


@dataclass(frozen=True)
class PrefilterResult:
    accepted: bool
    service_hits: tuple[str, ...]
    intent_hits: tuple[str, ...]
    negative_hits: tuple[str, ...]
    reason: str


def prefilter_text(title: str, excerpt: str = "") -> PrefilterResult:
    text = f"{title} {excerpt}".lower()
    service = tuple(k for k in SERVICE_KEYWORDS if k.lower() in text)
    intent = tuple(k for k in INTENT_KEYWORDS if k.lower() in text)
    negative = tuple(k for k in NEGATIVE_KEYWORDS if k.lower() in text)

    has_strong_intent = any(k in intent for k in ("有偿", "找人", "求开发", "找开发", "外包", "帮忙做", "需要开发", "多少钱", "预算", "急"))
    accepted = bool(service and intent and (not negative or has_strong_intent))

    if not service:
        reason = "未命中接单能力范围"
    elif not intent:
        reason = "缺少明确需求/购买意向信号"
    elif negative and not has_strong_intent:
        reason = "明显偏学习、教程或求职内容"
    else:
        reason = "通过关键词低成本预筛"

    return PrefilterResult(accepted, service[:8], intent[:8], negative[:8], reason)
