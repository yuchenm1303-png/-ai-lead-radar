from datetime import datetime, timezone
from .ai import AIProvider
from .connectors.base import RawLead
from .schemas import LeadCreate
from .scoring import prefilter_text, score_text

def analyze_raw(raw: RawLead, provider: AIProvider) -> LeadCreate | None:
    pf = prefilter_text(raw.title, raw.excerpt)
    if not pf.passed: return None
    rule = score_text(raw.title, raw.excerpt, raw.published_at, raw.budget)
    ai = provider.classify(raw.title, raw.excerpt)
    intent, fit, urgency, reason, confidence, category, budget, signals, is_lead = rule.intent_score, rule.fit_score, rule.urgency, rule.reason, rule.confidence, rule.category, raw.budget, rule.signals, rule.is_lead
    if ai:
        intent, fit, urgency, reason, confidence, category, budget, signals, is_lead = ai.intent_score, ai.fit_score, ai.urgency, ai.reason, ai.confidence, ai.need_type or category, ai.budget_text or budget, list(dict.fromkeys(rule.signals + ai.signals))[:8], ai.is_lead
    freshness = rule.freshness_score
    budget_signal = 100 if budget else 35
    urgency_bonus = 10 if urgency == 'high' else (5 if urgency == 'medium' else 0)
    score = max(0,min(100,round(intent*.40 + freshness*.30 + fit*.20 + budget_signal*.10 + urgency_bonus)))
    if not is_lead: return None
    priority = 'high' if score >= 85 else ('medium' if score >= 65 else 'low')
    return LeadCreate(source=raw.source, external_id=raw.external_id, title=raw.title, excerpt=raw.excerpt, category=category, score=score,
        is_lead=True,intent_score=intent,fit_score=fit,freshness_score=freshness,urgency=urgency,confidence=confidence,priority=priority,
        published_at=raw.published_at,discovered_at=datetime.now(timezone.utc),budget=budget,reason=reason,url=raw.url,signals=signals)
