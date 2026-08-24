from __future__ import annotations

from datetime import datetime, timezone

from .ai.service import get_classifier
from .connectors.base import RawLead
from .notifications import notify_high_value
from .prefilter import prefilter_text
from .schemas import IngestSummary, Lead, LeadCreate
from .scoring import final_score
from .settings import get_settings
from .storage import make_dedupe_key, mark_notified, upsert_lead


def process_items(items: list[RawLead]) -> IngestSummary:
    settings = get_settings()
    classifier = get_classifier()
    stored: list[Lead] = []
    prefiltered = created = updated = filtered_out = notified = 0

    for item in items:
        pre = prefilter_text(item.title, item.excerpt)
        if not pre.accepted:
            filtered_out += 1
            continue
        prefiltered += 1

        classification = classifier.classify(item)
        score, fresh = final_score(classification, item.published_at, item.budget)
        signals = list(dict.fromkeys([*pre.service_hits[:2], *pre.intent_hits[:3], *classification.signals]))[:12]
        payload = LeadCreate(
            source=item.source,
            external_id=item.external_id,
            title=item.title,
            excerpt=item.excerpt,
            category=classification.need_type,
            score=score,
            published_at=item.published_at,
            discovered_at=datetime.now(timezone.utc),
            budget=classification.budget_text or item.budget,
            url=item.url,
            signals=signals,
            is_lead=classification.is_lead,
            intent_score=classification.intent_score,
            fit_score=classification.fit_score,
            freshness_score=fresh,
            urgency=classification.urgency,
            reason=classification.reason,
            confidence=classification.confidence,
        )
        dedupe = make_dedupe_key(item.source, item.external_id, item.url, item.title, item.published_at)
        result = upsert_lead(payload, dedupe)
        lead = result.lead
        if result.created:
            created += 1
        else:
            updated += 1

        if result.created and lead.is_lead and lead.score >= settings.notify_min_score and notify_high_value(lead):
            notified += 1
            lead = mark_notified(lead.id) or lead
        stored.append(lead)

    return IngestSummary(
        received=len(items),
        prefiltered=prefiltered,
        stored=len(stored),
        created=created,
        updated=updated,
        filtered_out=filtered_out,
        notified=notified,
        ai_provider=classifier.name,
        leads=stored,
    )
