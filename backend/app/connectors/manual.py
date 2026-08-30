from __future__ import annotations

from .base import RawLead
from ..schemas import ManualLeadItem


class ManualImportAdapter:
    name = "manual"

    def normalize(self, item: ManualLeadItem) -> RawLead:
        return RawLead(
            source=item.source,
            external_id=item.external_id,
            title=item.title.strip(),
            excerpt=item.excerpt.strip(),
            published_at=item.published_at,
            url=item.url,
            budget=item.budget,
        )


class BrowserHelperAdapter(ManualImportAdapter):
    name = "browser-helper"
