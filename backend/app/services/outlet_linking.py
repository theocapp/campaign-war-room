"""
Match SourceItems to Outlet records by domain so authority_score weighting works.

Three matching strategies (applied in order):
  1. URL domain → Outlet.domain (exact, via outlet_index)
  2. Publisher domain extracted from entry.source.href during RSS ingestion
     (stored in source_name as "Publisher Name — Google News Feed")
  3. source_name pattern matching for already-ingested Google News articles

Domain aliases: some outlets publish under multiple domains; the alias table
maps secondary domains to the canonical Outlet domain.
"""
import logging
import re
from urllib.parse import urlparse

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Secondary domains that should map to a canonical outlet domain.
# Key = alias domain (no www.), value = canonical domain in outlets table.
DOMAIN_ALIASES: dict[str, str] = {
    "2822news.com":     "pahomepage.com",   # WBRE/WYOU secondary site
    "wnep16.com":       "wnep.com",
    "wbreitv.com":      "pahomepage.com",
    "wyoutv.com":       "pahomepage.com",
    "poconorecord.com": "poconorecord.com", # already in catalog; explicit for clarity
}


def extract_domain(url: str) -> str | None:
    """Return the bare domain (no www prefix) from a URL, or None if unparseable."""
    if not url:
        return None
    try:
        netloc = urlparse(url).netloc or ""
        domain = re.sub(r"^www\.", "", netloc).lower()
        return domain or None
    except Exception:
        return None


def build_outlet_index(db: Session) -> dict[str, int]:
    """Return {domain: outlet_id} for all outlets in the DB, including aliases."""
    from app.models import Outlet
    outlets = db.query(Outlet.id, Outlet.domain).all()
    index: dict[str, int] = {o.domain.lower(): o.id for o in outlets if o.domain}
    # Resolve aliases: map alias domain → canonical outlet_id
    for alias, canonical in DOMAIN_ALIASES.items():
        if canonical in index and alias not in index:
            index[alias] = index[canonical]
    return index


def _extract_gnews_publisher_name(source_name: str) -> str | None:
    """Extract publisher name from a Google News feed source_name.

    Patterns:
      "Times-Tribune — Google News Feed"  → "Times-Tribune"
      "WNEP 16 — Google News Feed"        → "WNEP 16"
    Returns None for generic Google News labels like "Google News: Rob Bresnahan".
    """
    if not source_name:
        return None
    m = re.match(r"^(.+?)\s+[—–-]+\s+Google News Feed$", source_name.strip())
    if m:
        return m.group(1).strip()
    return None


def build_outlet_name_index(db: Session) -> dict[str, int]:
    """Return {lowercased_outlet_name: outlet_id} for fuzzy source_name matching."""
    from app.models import Outlet
    outlets = db.query(Outlet.id, Outlet.name).all()
    return {o.name.lower(): o.id for o in outlets if o.name}


def link_outlet_to_item(item, outlet_index: dict[str, int],
                        name_index: dict[str, int] | None = None) -> bool:
    """Set item.outlet_id if the item's URL domain or source_name matches an outlet.

    Matching order:
      1. URL domain match (exact, including alias expansion)
      2. source_name publisher pattern match (for Google News redirect URLs)

    Returns True if a match was made.
    """
    if item.outlet_id is not None:
        return False  # already linked

    # Strategy 1: URL domain
    domain = extract_domain(item.source_url or "")
    if domain:
        outlet_id = outlet_index.get(domain)
        if outlet_id:
            item.outlet_id = outlet_id
            return True

    # Strategy 2: source_name publisher extraction (Google News articles)
    if name_index:
        publisher = _extract_gnews_publisher_name(item.source_name or "")
        if publisher:
            pub_lower = publisher.lower()
            # Exact match first
            if pub_lower in name_index:
                item.outlet_id = name_index[pub_lower]
                return True
            # Word-level match — any significant word (>3 chars, not a number) from
            # the publisher name must appear in the outlet name.
            pub_words = {w for w in re.split(r"[\s\-–]+", pub_lower) if len(w) > 3 and not w.isdigit()}
            if pub_words:
                for oname, oid in name_index.items():
                    if any(w in oname for w in pub_words):
                        item.outlet_id = oid
                        return True

    return False


def backfill_outlet_links(db: Session) -> int:
    """Link all un-linked SourceItems to outlets by domain or source_name. Idempotent.

    Returns the number of items linked.
    """
    from app.models import SourceItem
    outlet_index = build_outlet_index(db)
    if not outlet_index:
        return 0
    name_index = build_outlet_name_index(db)

    items = (
        db.query(SourceItem)
        .filter(SourceItem.outlet_id.is_(None))
        .all()
    )

    linked = 0
    for item in items:
        if link_outlet_to_item(item, outlet_index, name_index=name_index):
            linked += 1

    if linked:
        db.commit()
        logger.info("outlet_linking: linked %d source items to outlets", linked)

    return linked
