from __future__ import annotations

from .base import RawLead
from ..schemas import IngestItem


class ManualImportAdapter:
    name = "manual-import"

    def normalize(self, item: IngestItem) -> RawLead:
        return RawLead(
            source=item.source,
            external_id=item.source_id,
            title=item.title.strip(),
            excerpt=item.content.strip(),
            published_at=item.published_at,
            url=item.url,
            budget=item.budget_text,
        )


class BrowserHelperAdapter(ManualImportAdapter):
    name = "browser-helper"
