"""
Issue clustering service.

Maps source text to normalized issue categories, auto-creates issues that
don't exist yet, updates mention counts correctly, and computes trend.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import Issue, IssueMention, SourceItem

# ── Canonical issue taxonomy ──────────────────────────────────────────────────
# Each entry: issue_name → {keywords, urgency_bump_keywords}
# Keywords are matched with word boundaries and scored for confidence.
ISSUE_TAXONOMY: dict[str, dict] = {
    "Housing & Affordability": {
        "keywords": [
            "rent", "housing", "afford", "tenant", "landlord", "evict", "homebuyer",
            "mortgage", "rental", "apartment", "homelessness", "homeless", "shelter",
            "displacement", "gentrification", "rent control", "inclusionary zoning",
            "section 8", "subsidized housing", "affordable unit",
        ],
        "urgency_bump": ["evict", "homeless", "displacement", "crisis"],
    },
    "Public Safety": {
        "keywords": [
            "crime", "police", "safety", "break-in", "theft", "patrol", "enforcement",
            "defund", "officer", "robbery", "assault", "shooting", "violence",
            "burglary", "vandalism", "911", "emergency response", "sheriff",
            "safe streets", "neighborhood watch", "gun", "weapon",
        ],
        "urgency_bump": ["shooting", "murder", "assault", "robbery", "defund"],
    },
    "Education & Schools": {
        "keywords": [
            "school", "education", "classroom", "student", "teacher", "overcrowd",
            "art program", "music program", "principal", "curriculum", "reading",
            "test scores", "school board", "elementary", "middle school",
            "high school", "kindergarten", "tutoring", "after-school",
            "literacy", "graduation", "superintendent",
        ],
        "urgency_bump": ["overcrowd", "closing", "budget cut", "strike"],
    },
    "Infrastructure": {
        "keywords": [
            "pothole", "road", "sidewalk", "infrastructure", "repair", "bus",
            "street", "flood", "bridge", "sewer", "water main", "traffic",
            "intersection", "crosswalk", "bike lane", "construction", "deferred",
            "crumbling", "transit", "stop light", "drainage",
        ],
        "urgency_bump": ["flood", "collapse", "broken water", "no water"],
    },
    "Taxes & Budget": {
        "keywords": [
            "tax", "budget", "fiscal", "spending", "revenue", "deficit", "surplus",
            "levy", "property tax", "sales tax", "income tax", "tax rate", "tax hike",
            "allocation", "funding", "appropriation", "taxpayer", "bond",
            "millage", "assessment",
        ],
        "urgency_bump": ["tax hike", "tax increase", "deficit", "shortfall"],
    },
    "Healthcare": {
        "keywords": [
            "health", "hospital", "clinic", "doctor", "insurance", "medicaid",
            "medicare", "mental health", "opioid", "addiction", "drug",
            "prescription", "nurse", "emergency room", "treatment", "coverage",
            "uninsured", "healthcare access", "behavioral health", "co-responder",
        ],
        "urgency_bump": ["hospital closing", "overdose", "uninsured", "emergency"],
    },
    "Environment": {
        "keywords": [
            "environment", "climate", "pollution", "clean air", "clean water",
            "emissions", "green", "sustainability", "recycling", "park", "tree",
            "nature", "fossil fuel", "solar", "renewable", "carbon", "plastic",
            "waste", "contamination", "lead", "toxic",
        ],
        "urgency_bump": ["contamination", "toxic", "pollution spill", "lead"],
    },
    "Economy & Jobs": {
        "keywords": [
            "job", "employment", "unemploy", "business", "economy", "economic",
            "wage", "minimum wage", "worker", "union", "layoff", "manufacturing",
            "retail", "small business", "startup", "investment", "poverty",
            "income", "inequality", "labor",
        ],
        "urgency_bump": ["layoff", "closure", "plant closing", "mass unemployment"],
    },
    "Downtown Development": {
        "keywords": [
            "development", "developer", "downtown", "mixed-use", "commercial",
            "rezoning", "permit", "variance", "demolition", "renovation",
            "new construction", "historic preservation", "community benefit",
            "gentrification", "zoning change", "real estate project",
        ],
        "urgency_bump": ["demolition", "historic", "forced relocation"],
    },
    "Transportation": {
        "keywords": [
            "transit", "train", "subway", "highway", "commute", "parking",
            "bike", "pedestrian", "traffic light", "congestion", "rideshare",
            "carpool", "mobility", "bus route", "rapid transit", "ferry",
            "light rail", "bus stop",
        ],
        "urgency_bump": ["accident", "fatality", "service cut"],
    },
    "Immigration": {
        "keywords": [
            "immigration", "immigrant", "undocumented", "asylum", "border", "visa",
            "citizenship", "deportation", "sanctuary", "ICE", "refugee",
            "foreign-born", "naturalization", "daca", "dreamer",
        ],
        "urgency_bump": ["deportation", "raid", "detention"],
    },
    "Corruption & Ethics": {
        "keywords": [
            "corruption", "bribe", "kickback", "conflict of interest", "ethics",
            "pac money", "donation", "campaign finance", "transparency",
            "accountability", "investigation", "misconduct", "nepotism",
            "fraud", "scandal", "self-dealing",
        ],
        "urgency_bump": ["bribe", "fraud", "criminal investigation", "indictment"],
    },
    "Local Government": {
        "keywords": [
            "city council", "council member", "mayor", "ballot",
            "city hall", "ordinance", "resolution", "public hearing",
            "public comment", "term limit", "ward", "precinct", "incumbent",
            "board of elections", "election board", "community board",
            "county commission", "county board", "filing deadline", "ballot access",
        ],
        "urgency_bump": ["scandal", "resign", "recall", "impeach"],
    },
}

WEAK_KEYWORDS: dict[str, set[str]] = {
    "Taxes & Budget": {"funding", "spending", "allocation", "budget"},
    "Environment": {"green", "park", "tree", "waste"},
    "Economy & Jobs": {"business", "economic", "income", "labor", "worker"},
    "Infrastructure": {"street", "road", "bus", "construction"},
    "Public Safety": {"safety", "police"},
    "Healthcare": {"health", "coverage"},
    "Local Government": {"ballot", "ward", "precinct", "incumbent"},
}


@dataclass
class IssueMatch:
    issue_name: str
    has_urgency_bump: bool
    strength: int
    reasons: list[str]


def _keyword_pattern(keyword: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])")


def _keyword_hits(text: str, keyword: str) -> int:
    return len(_keyword_pattern(keyword).findall(text))


def _score_issue_match(text: str, issue_name: str, cfg: dict) -> IssueMatch | None:
    matched_keywords: list[str] = []
    strength = 0
    weak_hits = 0
    weak_terms = WEAK_KEYWORDS.get(issue_name, set())
    title_text, _, body_text = text.partition("\n")

    for keyword in cfg["keywords"]:
        hits = _keyword_hits(text, keyword)
        if hits == 0:
            continue
        matched_keywords.append(keyword)
        keyword_strength = 35 if " " in keyword else 30
        if keyword in weak_terms:
            weak_hits += hits
            keyword_strength = 12
        if _keyword_hits(title_text, keyword):
            keyword_strength += 10
        if hits > 1 and keyword not in weak_terms:
            keyword_strength += 5
        strength += keyword_strength

    if not matched_keywords:
        return None

    # Weak civic/economic words alone should not link a source to an issue.
    strong_keywords = [k for k in matched_keywords if k not in weak_terms]
    if not strong_keywords and weak_hits < 2:
        return None

    # Local Government is especially prone to pollution; require a concrete civic term.
    if issue_name == "Local Government" and not any(k in matched_keywords for k in {
        "city council", "council member", "city hall", "ordinance", "resolution",
        "public hearing", "public comment", "term limit", "board of elections",
        "election board", "community board", "county commission", "county board",
        "filing deadline", "ballot access",
    }):
        return None

    bump = any(_keyword_hits(text, kw) for kw in cfg.get("urgency_bump", []))
    if bump:
        strength += 10

    threshold = 30
    if strength < threshold:
        return None

    return IssueMatch(
        issue_name=issue_name,
        has_urgency_bump=bump,
        strength=min(100, strength),
        reasons=[f"Matched issue terms: {', '.join(matched_keywords[:4])}"],
    )


def _match_taxonomy_detailed(text: str) -> list[IssueMatch]:
    text_lower = text.lower()
    matches: list[IssueMatch] = []
    for name, cfg in ISSUE_TAXONOMY.items():
        match = _score_issue_match(text_lower, name, cfg)
        if match:
            matches.append(match)
    matches.sort(key=lambda m: m.strength, reverse=True)
    return matches


def _match_taxonomy(text: str) -> list[tuple[str, bool]]:
    """Return (issue_name, has_urgency_bump) pairs for all matching taxonomy entries."""
    return [(m.issue_name, m.has_urgency_bump) for m in _match_taxonomy_detailed(text)]


def _get_or_create_issue(db: Session, name: str) -> Issue:
    """Return existing issue by canonical name (case-insensitive) or create it."""
    existing = db.query(Issue).filter(Issue.name.ilike(name)).first()
    if existing:
        return existing
    issue = Issue(
        name=name,
        urgency="low",
        mention_count=0,
        trend="rising",  # new issue = rising by definition
        last_seen_at=datetime.utcnow(),
        summary=None,
    )
    db.add(issue)
    db.flush()
    return issue


def _cluster_key(source: SourceItem) -> str:
    return source.story_cluster_id or f"source-{source.id}"


def _count_issue_clusters(db: Session, issue_id: int, start: datetime | None = None, end: datetime | None = None) -> int:
    q = (
        db.query(SourceItem)
        .join(IssueMention, SourceItem.id == IssueMention.source_item_id)
        .filter(IssueMention.issue_id == issue_id)
    )
    if start:
        q = q.filter(SourceItem.published_at >= start)
    if end:
        q = q.filter(SourceItem.published_at < end)
    return len({_cluster_key(source) for source in q.all()})


def _update_trend(db: Session, issue: Issue) -> None:
    """Compute mention velocity over last 7 days vs. prior 7 days to set trend."""
    now = datetime.utcnow()
    seven_ago = now - timedelta(days=7)
    fourteen_ago = now - timedelta(days=14)

    recent = _count_issue_clusters(db, issue.id, seven_ago)
    prior = _count_issue_clusters(db, issue.id, fourteen_ago, seven_ago)

    if prior == 0:
        issue.trend = "rising" if recent > 0 else "stable"
    elif recent > prior * 1.3:
        issue.trend = "rising"
    elif recent < prior * 0.7:
        issue.trend = "falling"
    else:
        issue.trend = "stable"


def _update_urgency(db: Session, issue: Issue) -> None:
    """Set issue urgency based on max urgency of linked source items."""
    rows = (
        db.query(SourceItem.urgency)
        .join(IssueMention, SourceItem.id == IssueMention.source_item_id)
        .filter(IssueMention.issue_id == issue.id)
        .all()
    )
    urgencies = {r[0] for r in rows}
    if "high" in urgencies:
        issue.urgency = "high"
    elif "medium" in urgencies:
        issue.urgency = "medium"
    else:
        issue.urgency = "low"


def assign_issues_to_source(db: Session, source_item: SourceItem) -> list[Issue]:
    """
    Match a source item to taxonomy issues, auto-create missing ones,
    link them, and update mention counts + trend + urgency.
    Returns the list of matched Issue objects.
    """
    if source_item.extraction_quality_label == "poor" and source_item.source_type == "news":
        matching_text = f"{source_item.title or ''}\n{source_item.summary or ''}"
        max_links = 2
    else:
        matching_text = f"{source_item.title or ''}\n{source_item.raw_text or ''}\n{source_item.summary or ''}"
        max_links = 4
    matches = _match_taxonomy_detailed(matching_text)
    matched_issues = []

    for match in matches[:max_links]:
        if source_item.extraction_quality_label == "poor" and match.strength < 70:
            continue
        issue = _get_or_create_issue(db, match.issue_name)

        # Only create a link and bump count if this source isn't already linked
        existing = (
            db.query(IssueMention)
            .filter_by(issue_id=issue.id, source_item_id=source_item.id)
            .first()
        )
        if existing:
            existing.link_strength = match.strength
            existing.link_reasons = json.dumps(match.reasons)
        else:
            db.add(IssueMention(
                issue_id=issue.id,
                source_item_id=source_item.id,
                link_strength=match.strength,
                link_reasons=json.dumps(match.reasons),
            ))
            duplicate_cluster_exists = False
            if source_item.story_cluster_id:
                linked_sources = (
                    db.query(SourceItem)
                    .join(IssueMention, SourceItem.id == IssueMention.source_item_id)
                    .filter(IssueMention.issue_id == issue.id)
                    .filter(SourceItem.id != source_item.id)
                    .all()
                )
                duplicate_cluster_exists = any(_cluster_key(s) == _cluster_key(source_item) for s in linked_sources)
            if not duplicate_cluster_exists:
                issue.mention_count = (issue.mention_count or 0) + 1

        issue.last_seen_at = datetime.utcnow()
        matched_issues.append(issue)

    db.flush()

    # Update derived fields for each matched issue
    for issue in matched_issues:
        issue.mention_count = _count_issue_clusters(db, issue.id)
        _update_trend(db, issue)
        _update_urgency(db, issue)

    db.commit()
    return matched_issues
