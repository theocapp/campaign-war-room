"""Classify a SourceItem into a coarse category for the Articles-page filter.

The per-outlet Source dropdown gets unwieldy fast (~40 entries even within
a 7-day window). For a campaign user, what matters is the *kind* of source:
hometown paper, national pickup, social chatter, or a campaign-side press
release. Four buckets cover that intent.

Classification order matters — the first matching rule wins:

  1. campaign_source  — DCCC/NRCC, candidate/opponent official accounts,
                        or explicit opponent_statement source_type.
  2. social_media     — source_type='social', or a source_name that names
                        a social platform (X/Twitter/Reddit/YouTube/etc.)
  3. local_news       — outlet has outlet_type in {local_news,
                        regional_news, broadcast} AND state='PA', or
                        source_name matches well-known NEPA outlets.
  4. national_news    — everything else with a recognized outlet/source.

`other` is the fallback for rows we can't confidently bucket — typically
Google News aggregator hits with no outlet metadata. Keeping these in a
visible category (rather than dropping them) is intentional: the user
should see ungrouped rows so it's obvious when classification needs work.
"""
from __future__ import annotations

import re

from app.models import Outlet, SourceItem

SourceCategory = str  # local_news | national_news | social_media | campaign_source | other

CATEGORY_LABELS: dict[str, str] = {
    "local_news": "Local news",
    "national_news": "National news",
    "social_media": "Social media",
    "campaign_source": "Campaign source",
    "other": "Other",
}

_SOCIAL_NAME = re.compile(
    r"\b(x/twitter|twitter|reddit|youtube|bluesky|mastodon|truth social|facebook|instagram|tiktok)\b",
    re.IGNORECASE,
)

# Campaign-side press release / official account markers. Limited to
# unambiguous signals: party committee acronyms (DCCC, NRCC, DSCC, NRSC),
# official .gov surfaces, and explicit "X for Congress/Senate" phrasing.
# Critically NOT matched on bare candidate names — those appear in Google
# News *search feed* names (e.g. "Google News: Rob Bresnahan") which are
# third-party news pickups, not campaign output. Bare candidate names get
# bucketed by their underlying outlet metadata instead.
_CAMPAIGN_NAME = re.compile(
    r"\b(dccc|nrcc|dscc|nrsc|"
    r"house gov|senate gov|bresnahan house gov|"
    r"cognetti for (congress|senate)|bresnahan for (congress|senate))\b",
    re.IGNORECASE,
)

# NEPA / PA-08 hometown outlets — used as a fallback when the source row
# isn't joined to an outlet_id but the name still tells us it's local.
_LOCAL_NEPA_NAME = re.compile(
    r"\b(times[- ]?tribune|times leader|standard[- ]?speaker|citizens'? voice|"
    r"river reporter|wnep|wbre|wyou|pahomepage|fox56|abc27|pennlive|"
    r"penn live|spotlight pa|stateimpact pennsylvania|psdispatch|"
    r"wilkes[- ]?barre|scranton)\b",
    re.IGNORECASE,
)


def categorize(item: SourceItem, outlet: Outlet | None = None) -> SourceCategory:
    name = (item.source_name or "").strip()
    stype = (item.source_type or "").strip().lower()

    # 1. Campaign / press-release surfaces win first — DCCC press feeds are
    # tagged outlet_type='blog' but conceptually they're campaign output, not
    # journalism, so the campaign rule has to fire before the news rules.
    if stype == "opponent_statement":
        return "campaign_source"
    if _CAMPAIGN_NAME.search(name):
        return "campaign_source"

    # 2. Social platforms
    if stype == "social":
        return "social_media"
    if _SOCIAL_NAME.search(name):
        return "social_media"

    # 3. Local news — prefer outlet metadata when we have it
    if outlet is not None:
        ot = (outlet.outlet_type or "").strip().lower()
        st = (outlet.state or "").strip().upper()
        if ot in ("local_news", "regional_news", "broadcast") and st == "PA":
            return "local_news"
        if ot == "local_news":  # local_news without state is still local
            return "local_news"
        if ot == "national":
            return "national_news"
        if ot in ("regional_news", "broadcast"):
            # Out-of-state regional / broadcast — treat as national pickup
            return "national_news"
        if ot == "blog":
            return "national_news"
    # Fallback: name-based local detection for orphan rows
    if _LOCAL_NEPA_NAME.search(name):
        return "local_news"

    # 4. Default — we can't confidently bucket; mark as "other" so it shows
    # up in the dropdown and the user notices ungrouped rows.
    if name:
        return "national_news"
    return "other"
