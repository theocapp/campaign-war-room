from fastapi import APIRouter
from app.schemas import SourceTemplate

router = APIRouter()

_TEMPLATES: list[SourceTemplate] = [
    # Local news
    SourceTemplate(
        id="local-news-generic",
        name="Local Newspaper RSS",
        category="Local News",
        description="RSS feed from your local newspaper or TV station. Best for tracking coverage of your race, opponent activity, and community issues.",
        example_url=None,
        url_pattern="https://<yournewspaper>.com/rss",
        source_type="news",
        setup_note="Check the newspaper's website footer for an RSS link, or try appending /rss or /feed to the homepage URL.",
    ),
    SourceTemplate(
        id="google-news-candidate",
        name="Google News — Candidate Name",
        category="Local News",
        description="Google News RSS for searches mentioning your name or opponent. Aggregates coverage across outlets.",
        example_url=None,
        url_pattern="https://news.google.com/rss/search?q={candidate+name}&hl=en-US&gl=US&ceid=US:en",
        source_type="news",
        setup_note="Replace {candidate+name} with your name (use + for spaces). Create a separate feed for your opponent.",
    ),
    SourceTemplate(
        id="google-news-race",
        name="Google News — Race / District",
        category="Local News",
        description="Google News RSS for your race name, district, or key local issues.",
        example_url=None,
        url_pattern="https://news.google.com/rss/search?q={city+council+district+7}&hl=en-US&gl=US&ceid=US:en",
        source_type="news",
        setup_note="Customize the search term with your city, district, or issue keywords.",
    ),
    # Government records
    SourceTemplate(
        id="city-council-agenda",
        name="City Council Agenda Feed",
        category="Government Records",
        description="Official meeting agendas and minutes. Essential for tracking votes, budget decisions, and policy changes relevant to your platform.",
        example_url=None,
        url_pattern="https://<yourcity>.gov/council/agendas/rss",
        source_type="public_record",
        setup_note="Search your city government website for 'RSS' or 'agenda feed'. Many use Legistar or Granicus — check their RSS export options.",
    ),
    SourceTemplate(
        id="county-records",
        name="County Public Records",
        category="Government Records",
        description="County commission, planning board, or zoning board agendas. Relevant if your race involves county-level decisions.",
        example_url=None,
        url_pattern=None,
        source_type="public_record",
        setup_note="Paste agenda PDFs or minutes as text sources using the manual text paste tool.",
    ),
    # Opponent monitoring
    SourceTemplate(
        id="opponent-campaign-site",
        name="Opponent Campaign Website",
        category="Opponent Monitoring",
        description="Monitor your opponent's press releases and news section for new claims, promises, and attacks.",
        example_url=None,
        url_pattern="https://<opponent-site>.com/news/rss",
        source_type="opponent_statement",
        setup_note="Check the opponent's site for a news or press section with RSS. If none, use URL ingestion to manually check key pages.",
    ),
    SourceTemplate(
        id="opponent-endorsements",
        name="Endorsement Tracking",
        category="Opponent Monitoring",
        description="Track endorsement announcements from both campaigns. Signals organizational support and credibility.",
        example_url=None,
        url_pattern=None,
        source_type="opponent_statement",
        setup_note="Paste endorsement announcements as text sources. Tag with 'opponent_statement' to link them to opponent activity.",
    ),
    # Community & civic
    SourceTemplate(
        id="nextdoor-neighborhood",
        name="Neighborhood Forum / Nextdoor",
        category="Community",
        description="Neighborhood online discussions surface hyper-local concerns that may not appear in mainstream media.",
        example_url=None,
        url_pattern=None,
        source_type="canvassing",
        setup_note="Nextdoor does not offer RSS. Copy relevant posts or threads and paste as text sources to surface community concerns.",
    ),
    SourceTemplate(
        id="community-org-newsletter",
        name="Community Organization Newsletter",
        category="Community",
        description="Newsletters from civic groups, neighborhood associations, PTAs, or advocacy orgs. These represent organized voter blocs.",
        example_url=None,
        url_pattern=None,
        source_type="news",
        setup_note="Many orgs publish newsletters as web pages with RSS feeds. Check their sites. Otherwise paste newsletter text as a source.",
    ),
    # Social media
    SourceTemplate(
        id="local-twitter-search",
        name="Twitter / X — Local Hashtag",
        category="Social Media",
        description="Track local hashtags or candidate name mentions on X/Twitter. Surfaces emerging narratives early.",
        example_url=None,
        url_pattern=None,
        source_type="social",
        setup_note="Use third-party RSS bridges (e.g., nitter instances) to convert Twitter searches to RSS. Paste notable threads as text sources.",
    ),
]


@router.get("/source-templates", response_model=list[SourceTemplate])
def get_source_templates():
    return _TEMPLATES
