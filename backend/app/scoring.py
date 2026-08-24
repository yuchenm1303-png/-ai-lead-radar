from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreResult:
    score: int
    category: str
    signals: list[str]


SERVICE_KEYWORDS = {
    "微信小程序": ["小程序", "微信小程序", "预约小程序", "商城小程序"],
    "网页开发": ["网页", "网站", "官网", "前端", "后端", "管理系统", "h5"],
    "独立站": ["独立站", "shopify", "跨境网站", "英文官网"],
    "Python / 数据": ["python", "excel", "数据处理", "爬虫", "自动化", "脚本"],
    "AI 工具": ["ai", "智能体", "大模型", "机器人", "自动回复"],
}

INTENT_SIGNALS = {
    "有偿": 18,
    "预算": 12,
    "多少钱": 12,
    "报价": 12,
    "找人": 16,
    "求开发": 18,
    "求靠谱": 12,
    "帮忙做": 14,
    "需要做": 12,
    "急": 10,
    "公司": 7,
    "项目": 7,
    "可以聊": 5,
}

NEGATIVE_SIGNALS = {
    "学习": -26,
    "教程": -24,
    "课程": -24,
    "怎么学": -24,
    "招聘": -12,
    "找工作": -18,
    "面试": -18,
    "源码分享": -20,
}


def score_text(title: str, excerpt: str = "") -> ScoreResult:
    text = f"{title} {excerpt}".lower()
    score = 22
    signals: list[str] = []
    category = "其他开发"

    category_hits: list[tuple[str, int]] = []
    for name, keywords in SERVICE_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword.lower() in text)
        if hits:
            category_hits.append((name, hits))

    if category_hits:
        category_hits.sort(key=lambda item: item[1], reverse=True)
        category = category_hits[0][0]
        score += min(24, 10 + category_hits[0][1] * 5)
        signals.append(category)

    for signal, weight in INTENT_SIGNALS.items():
        if signal in text:
            score += weight
            signals.append(signal)

    for signal, weight in NEGATIVE_SIGNALS.items():
        if signal in text:
            score += weight
            signals.append(f"排除:{signal}")

    if "有偿" in text and ("找人" in text or "帮忙" in text or "求" in text):
        score += 10
    if any(word in text for word in ["今天", "尽快", "马上", "急"]):
        score += 5

    score = max(0, min(100, score))
    return ScoreResult(score=score, category=category, signals=signals[:6])
