from app.models import SourceItem
from app.services import intelligence


def check_source_risk(source_item: SourceItem) -> str | None:
    text = f"{source_item.title} {source_item.raw_text or ''}"
    return intelligence.generate_risk_warning(text, source_item.credibility_note or "")
