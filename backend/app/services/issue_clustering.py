"""
Issue clustering service.

Maps source text to normalized issue categories, auto-creates issues that
don't exist yet, updates mention counts correctly, and computes trend.
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import Issue, IssueMention, SourceItem

# ── Canonical issue taxonomy ──────────────────────────────────────────────────
# Each entry: issue_name → {keywords, urgency_bump_keywords}
# Keywords are sub-strings matched against lowercased source text.
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
            "city council", "council member", "mayor", "election", "ballot",
            "city hall", "ordinance", "resolution", "public hearing",
            "public comment", "term limit", "constituent", "district",
            "ward", "precinct", "campaign", "candidate", "incumbent",
        ],
        "urgency_bump": ["scandal", "resign", "recall", "impeach"],
    },
}


def _match_taxonomy(text: str) -> list[tuple[str, bool]]:
    """Return (issue_name, has_urgency_bump) pairs for all matching taxonomy entries."""
    text_lower = text.lower()
    matched = []
    for name, cfg in ISSUE_TAXONOMY.items():
        if any(kw in text_lower for kw in cfg["keywords"]):
            bump = any(kw in text_lower for kw in cfg.get("urgency_bump", []))
            matched.append((name, bump))
    return matched


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


def _update_trend(db: Session, issue: Issue) -> None:
    """Compute mention velocity over last 7 days vs. prior 7 days to set trend."""
    now = datetime.utcnow()
    seven_ago = now - timedelta(days=7)
    fourteen_ago = now - timedelta(days=14)

    recent = (
        db.query(IssueMention)
        .join(SourceItem, IssueMention.source_item_id == SourceItem.id)
        .filter(IssueMention.issue_id == issue.id)
        .filter(SourceItem.published_at >= seven_ago)
        .count()
    )
    prior = (
        db.query(IssueMention)
        .join(SourceItem, IssueMention.source_item_id == SourceItem.id)
        .filter(IssueMention.issue_id == issue.id)
        .filter(SourceItem.published_at >= fourteen_ago)
        .filter(SourceItem.published_at < seven_ago)
        .count()
    )

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
    text = " ".join(filter(None, [source_item.title, source_item.raw_text, source_item.summary]))
    matches = _match_taxonomy(text)
    matched_issues = []

    for issue_name, _has_bump in matches:
        issue = _get_or_create_issue(db, issue_name)

        # Only create a link and bump count if this source isn't already linked
        existing = (
            db.query(IssueMention)
            .filter_by(issue_id=issue.id, source_item_id=source_item.id)
            .first()
        )
        if not existing:
            db.add(IssueMention(issue_id=issue.id, source_item_id=source_item.id))
            issue.mention_count = (issue.mention_count or 0) + 1

        issue.last_seen_at = datetime.utcnow()
        matched_issues.append(issue)

    db.flush()

    # Update derived fields for each matched issue
    for issue in matched_issues:
        _update_trend(db, issue)
        _update_urgency(db, issue)

    db.commit()
    return matched_issues
