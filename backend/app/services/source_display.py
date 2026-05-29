"""Best-known publisher name for displaying article source.

Articles ingested via Google News (and other aggregator feeds) have
`source_name` set to the RSS feed label (e.g., "Google News — Cognetti
Congress Pennsylvania"), not the actual publisher. The real publisher
is stored on `SourceItem.publisher_domain` and, when matched against
the outlets table, `SourceItem.outlet_id`.

Use `display_source_name()` in every API route that returns an article's
source to the frontend — that way "Google News" appears only as the
absolute fallback, never as the primary display.

Resolution order:
  1. outlet.name  — if outlet_id is set and the row exists
  2. Pretty-printed publisher_domain — if domain known, outlet not yet linked
  3. source_name — the original RSS feed name (last resort)
  4. "(unknown source)" — only when nothing is set, which should be rare
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models import Outlet, SourceItem


# Curated overrides for domains where a naïve title-case would be ugly or wrong.
# Add to this as you notice. Keys are publisher_domain values (no www.).
_DOMAIN_NAME_OVERRIDES: dict[str, str] = {
    "riverreporter.com": "The River Reporter",
    "thetimes-tribune.com": "The Times-Tribune",
    "timesleader.com": "Times Leader",
    "citizensvoice.com": "Citizens' Voice",
    "standardspeaker.com": "Standard Speaker",
    "wnep.com": "WNEP",
    "wvia.org": "WVIA News",
    "via.org": "WVIA News",
    "fox56.com": "Fox 56 WOLF",
    "pahomepage.com": "PA Homepage",
    "wbre.com": "WBRE",
    "wnyt.com": "WNYT",
    "wytv.com": "WYTV",
    "wkbn.com": "WKBN",
    "spotlightpa.org": "Spotlight PA",
    "penncapital-star.com": "Pennsylvania Capital-Star",
    "inquirer.com": "The Philadelphia Inquirer",
    "pennlive.com": "PennLive",
    "post-gazette.com": "Pittsburgh Post-Gazette",
    "washingtonexaminer.com": "Washington Examiner",
    "washingtonpost.com": "The Washington Post",
    "nytimes.com": "The New York Times",
    "wsj.com": "The Wall Street Journal",
    "ap.org": "Associated Press",
    "apnews.com": "Associated Press",
    "reuters.com": "Reuters",
    "politico.com": "Politico",
    "thehill.com": "The Hill",
    "rollcall.com": "Roll Call",
    "axios.com": "Axios",
    "bloomberg.com": "Bloomberg",
    "cnn.com": "CNN",
    "foxnews.com": "Fox News",
    "msnbc.com": "MSNBC",
    "nbcnews.com": "NBC News",
    "abcnews.go.com": "ABC News",
    "cbsnews.com": "CBS News",
    "npr.org": "NPR",
    "pbs.org": "PBS",
    "huffpost.com": "HuffPost",
    "vox.com": "Vox",
    "theatlantic.com": "The Atlantic",
    "newyorker.com": "The New Yorker",
    "yahoo.com": "Yahoo News",
    "yahoo.news": "Yahoo News",
    "youtube.com": "YouTube",
    "facebook.com": "Facebook",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "reddit.com": "Reddit",
    "dccc.org": "DCCC",
    "nrcc.org": "NRCC",
    "afge.org": "AFGE",
    "bresnahan.house.gov": "Rep. Bresnahan (Official)",
    "politicspa.com": "PoliticsPA",
    "keystonenewsroom.com": "Keystone Newsroom",
    "whyy.org": "WHYY",
    "wesa.fm": "WESA",
    "wsls.com": "WSLS",
    "mcall.com": "The Morning Call",
    "lehighvalleylive.com": "Lehigh Valley Live",
    "6abc.com": "6abc Action News",
    "theguardian.com": "The Guardian",
    "pa.gov": "Commonwealth of Pennsylvania",
    "news.scranton.edu": "University of Scranton News",
    "cityandstatepa.com": "City & State PA",
    "quiverquant.com": "Quiver Quantitative",
    "270towin.com": "270toWin",
    "prnewswire.com": "PR Newswire",
    "ivn.us": "Independent Voter News",
    "msn.com": "MSN",
    "ripon-society.com": "The Ripon Society",
    "riponadvance.com": "Ripon Advance",
    "washingtonreporter.news": "Washington Reporter",
    "pahouse.com": "Pennsylvania House Democrats",
    "democraticmayors.org": "Democratic Mayors Association",
    "thehousemajoritypac.com": "House Majority PAC",
    "dioceseofscranton.org": "Diocese of Scranton",
    "armchairlehighvalley.substack.com": "Armchair Lehigh Valley",
    "legis1.com": "Legis1",
    "pewresearch.org": "Pew Research Center",
    "wnep.com": "WNEP",
    "poconorecord.com": "Pocono Record",
    "cnbc.com": "CNBC",
    "aol.com": "AOL News",
    "c-span.org": "C-SPAN",
    "northjersey.com": "NorthJersey.com",
    "centerforpolitics.org": "Sabato's Crystal Ball",
    "democracydocket.com": "Democracy Docket",
    "billypenn.com": "Billy Penn",
    "governing.com": "Governing",
    "newsnationnow.com": "NewsNation",
    "thephiladelphiacitizen.org": "The Philadelphia Citizen",
    "jewishinsider.com": "Jewish Insider",
    "lockhaven.com": "The Express",
    "goerie.com": "Erie Times-News",
}


def _prettify_domain(domain: str) -> str:
    """Fallback name for a publisher_domain not in the overrides map.

    "riverreporter.com" → "Riverreporter"
    "the-tribune.com"   → "The Tribune"
    "abc-news.go.com"   → "Abc News"  (best-effort; add overrides for these)
    """
    if not domain:
        return ""
    base = domain.lower()
    if base.startswith("www."):
        base = base[4:]
    # Strip TLD by taking everything before the last dot
    # (handles .com / .org / .news / etc. — multi-part TLDs like .co.uk
    # will be slightly wrong; add overrides for those if they appear.)
    parts = base.rsplit(".", 1)
    name = parts[0] if len(parts) > 1 else base
    # Replace - and _ with spaces, then title-case word by word
    name = name.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split() if w)


def display_source_name(
    item: "SourceItem",
    outlet: Optional["Outlet"] = None,
) -> str:
    """Best-known publisher name for `item`.

    `outlet` is an optional pre-loaded Outlet row (avoids re-querying
    when the caller already has it). If `outlet` is None and the item's
    `outlet_id` is set, callers should pass the eagerly-loaded outlet
    when possible — this function will NOT do a fresh DB query.
    """
    # 1. Pre-loaded outlet wins
    if outlet is not None and getattr(outlet, "name", None):
        return outlet.name

    # 2. publisher_domain via overrides or prettify
    pd = getattr(item, "publisher_domain", None)
    if pd:
        return _DOMAIN_NAME_OVERRIDES.get(pd.lower(), _prettify_domain(pd))

    # 3. Fall back to the RSS feed name
    if getattr(item, "source_name", None):
        return item.source_name

    return "(unknown source)"


def preload_outlets(db: "Session", items: Iterable["SourceItem"]) -> dict[int, "Outlet"]:
    """Batch-load outlets for a list of items. Returns {outlet_id: Outlet}.

    Use before calling display_source_name() over many items to avoid N+1.
    """
    from app.models import Outlet

    outlet_ids = {it.outlet_id for it in items if it.outlet_id}
    if not outlet_ids:
        return {}
    rows = db.query(Outlet).filter(Outlet.id.in_(outlet_ids)).all()
    return {o.id: o for o in rows}
