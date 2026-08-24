from dataclasses import dataclass
from datetime import datetime, timezone
import re

SERVICE_KEYWORDS = {
    'AI / 自动化': ['ai','智能体','大模型','自动化','软件开发'],
    '微信小程序': ['小程序','微信小程序','预约小程序','商城小程序'],
    '独立站': ['独立站','shopify','跨境网站','英文官网'],
    '网页开发': ['网页','网站','官网','前端','后端','管理系统','h5'],
    'Python / 数据': ['python','excel','数据处理','爬虫','脚本'],
}
INTENT_WORDS = {
    '有偿':24,'预算':18,'多少钱':18,'报价':18,'找人':22,'寻找':26,'寻求':24,
    '求开发':26,'找开发':26,'开发团队':26,'开发个人':22,'程序员':18,'开发者':16,
    '帮忙做':22,'需要开发':24,'需要做':16,'急':16,'外包':24,'公司':8,'项目':8,
    '可以聊':8,'有没有人会':14,'求助':12,'找谁':15,'想做':16,'想搞':16,'帮忙写':20,'有没有会':14,
}
NEGATIVE_WORDS = ['教程','怎么学','学习路线','推荐课程','课程推荐','想学','零基础','入门','找工作','面试','源码分享','难吗']
PAYMENT_WORDS = ['有偿','预算','多少钱','报价']
DIRECT_BUYER_PATTERNS = [
    re.compile(r'(?:寻找|寻求|找|求).{0,12}(?:开发|程序员|开发者|技术团队|开发团队)'),
    re.compile(r'(?:有没有|有没).{0,10}(?:会|能).{0,8}(?:做|开发|写|搭建)'),
    re.compile(r'(?:需要|想要|准备|打算|想).{0,10}(?:做|开发|搭建|制作|写).{0,16}(?:小程序|网站|网页|系统|ai|智能体|脚本|自动化|独立站|h5)'),
]

@dataclass(frozen=True)
class PrefilterResult:
    passed: bool
    service_hits: list[str]
    intent_hits: list[str]
    negative_hits: list[str]
    direct_buyer: bool = False

@dataclass(frozen=True)
class ScoreResult:
    score: int
    category: str
    is_lead: bool
    intent_score: int
    fit_score: int
    freshness_score: int
    urgency: str
    confidence: int
    priority: str
    budget: str | None
    reason: str
    signals: list[str]

def _direct_buyer(text: str) -> bool:
    return any(pattern.search(text) for pattern in DIRECT_BUYER_PATTERNS)

def prefilter_text(title: str, excerpt: str='') -> PrefilterResult:
    text = f'{title} {excerpt}'.lower()
    service_hits = [name for name, words in SERVICE_KEYWORDS.items() if any(w.lower() in text for w in words)]
    intent_hits = [w for w in INTENT_WORDS if w in text]
    negative_hits = [w for w in NEGATIVE_WORDS if w in text]
    if re.search(r'学习.{0,12}(?:课程|教程|路线|开发|编程|python|前端|小程序|ai)', text) and '学习咨询' not in negative_hits:
        negative_hits.append('学习咨询')
    direct_buyer = _direct_buyer(text)
    paid = any(w in text for w in PAYMENT_WORDS)
    has_intent = bool(intent_hits) or direct_buyer
    blocked_by_negative = bool(negative_hits and not paid)
    passed = bool(service_hits and has_intent) and not blocked_by_negative
    return PrefilterResult(passed, service_hits, intent_hits, negative_hits, direct_buyer)

def _freshness(published_at: datetime | None) -> int:
    if not published_at: return 50
    dt = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    hours = max(0.0, (datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/3600)
    if hours <= .5: return 100
    if hours <= 2: return 94
    if hours <= 6: return 86
    if hours <= 24: return 72
    if hours <= 72: return 52
    if hours <= 168: return 35
    return 15

def score_text(title: str, excerpt: str='', published_at: datetime | None=None, budget: str | None=None) -> ScoreResult:
    text = f'{title} {excerpt}'.lower()
    pf = prefilter_text(title, excerpt)
    category = pf.service_hits[0] if pf.service_hits else '其他开发'
    intent = min(100, sum(INTENT_WORDS[w] for w in pf.intent_hits))
    if pf.direct_buyer:
        intent = max(intent, 82)
    if any(w in text for w in PAYMENT_WORDS): intent = min(100, intent+12)
    if pf.negative_hits and not any(w in text for w in PAYMENT_WORDS): intent = max(0, intent-55)
    fit = 92 if category != '其他开发' else 35
    freshness = _freshness(published_at)
    urgency = 'high' if any(w in text for w in ['急','今天','尽快','马上']) else ('medium' if any(w in text for w in ['近期','最近']) else 'low')
    if any(w in text for w in PAYMENT_WORDS) or budget:
        budget_signal = 100
    elif pf.direct_buyer:
        budget_signal = 75
    else:
        budget_signal = 35
    urgency_bonus = 10 if urgency == 'high' else (5 if urgency == 'medium' else 0)
    score = round(intent*.40 + freshness*.30 + fit*.20 + budget_signal*.10 + urgency_bonus)
    is_lead = pf.passed and score >= 45
    if not is_lead: score = min(score, 39)
    score = max(0, min(100, score))
    confidence = min(99, 58 + len(pf.service_hits)*9 + len(pf.intent_hits)*6 + (12 if pf.direct_buyer else 0) - len(pf.negative_hits)*12)
    priority = 'high' if is_lead and score >= 85 else ('medium' if is_lead and score >= 65 else 'low')
    signals = (pf.service_hits + (['明确找开发方'] if pf.direct_buyer else []) + pf.intent_hits + [f'排除:{x}' for x in pf.negative_hits])[:8]
    reason = '；'.join(filter(None,[
        f'识别为{category}' if pf.service_hits else '',
        '存在直接寻找开发方的表达' if pf.direct_buyer else '',
        f"需求意向信号：{'、'.join(pf.intent_hits[:4])}" if pf.intent_hits else '',
        f"负向信号：{'、'.join(pf.negative_hits[:3])}" if pf.negative_hits else ''
    ])) or '信号不足'
    return ScoreResult(score, category, is_lead, intent, fit, freshness, urgency, confidence, priority, budget, reason, signals)
