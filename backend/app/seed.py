"""
Seed data for Campaign War Room AI.

Demo scenario: Maria Chen (D) vs. Roy Harmon (R, incumbent)
Race: Lakeview City Council, District 7, 2026 General Election

Usage:
  python -m app.seed                  # seed demo data (no-op if already seeded)
  python -m app.seed --reset          # drop all data and re-seed from scratch
"""
import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import (
    CampaignConfig, SourceItem, Issue, IssueMention,
    Opponent, OpponentActivity, CanvassingNote,
    SourcePack, SourcePackItem,
)


def _d(days_ago: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days_ago)


def seed(db: Session) -> None:
    # Always ensure source packs are seeded (idempotent)
    _seed_source_packs(db)
    db.commit()

    if db.query(CampaignConfig).first():
        return  # demo data already seeded — do not overwrite user edits

    # ── Campaign configuration ────────────────────────────────────────────────
    db.add(CampaignConfig(
        candidate_name="Maria Chen",
        party="Democrat",
        race="Lakeview City Council, District 7",
        district="District 7",
        office="City Council Member",
        location="Lakeview, CA",
        election_date=datetime(2026, 11, 3),
        campaign_message=(
            "District 7 deserves a council member who shows up. "
            "I will fight for housing families can afford, schools our kids deserve, "
            "and a community where everyone has a seat at the table."
        ),
        key_priorities=json.dumps([
            "Housing & Affordability",
            "Education & Schools",
            "Public Safety",
            "Infrastructure",
        ]),
    ))

    # ── Issues ────────────────────────────────────────────────────────────────
    housing = Issue(name="Housing & Affordability", urgency="high", mention_count=28, trend="rising",
        last_seen_at=_d(0),
        summary="Rents in District 7 are up 34% since 2021, pushing longtime residents out of the neighborhood. "
                "Harmon has blocked two affordable housing bills this term.")
    safety = Issue(name="Public Safety", urgency="medium", mention_count=19, trend="stable",
        last_seen_at=_d(2),
        summary="Crime data is mixed: downtown incidents fell 8% in 2025, but south District 7 has seen a 12% increase in "
                "vehicle break-ins. Harmon is selectively citing only the positive numbers.")
    education = Issue(name="Education & Schools", urgency="medium", mention_count=16, trend="rising",
        last_seen_at=_d(1),
        summary="East Lakeview Elementary is at 130% capacity with 38-student classrooms and no art or music programs. "
                "The school board has been requesting City Council support for two years. Harmon skipped two parent forums.")
    infrastructure = Issue(name="Infrastructure", urgency="low", mention_count=12, trend="stable",
        last_seen_at=_d(5),
        summary="Pothole complaints are up 42% from last year. The city deferred $2.1M in road repairs from the FY2025 budget. "
                "Seniors and families in Precincts 7A and 7D are most affected.")
    development = Issue(name="Downtown Development", urgency="low", mention_count=9, trend="falling",
        last_seen_at=_d(10),
        summary="A proposed mixed-use development at Elm & 3rd has divided the district. "
                "Harmon's PAC received $15,000 from the developer three months before his approving vote.")

    db.add_all([housing, safety, education, infrastructure, development])
    db.flush()

    # ── Source items ──────────────────────────────────────────────────────────
    s1 = SourceItem(
        title="Rents in District 7 up 34% since 2021, new data shows",
        source_name="Lakeview Tribune",
        source_url="https://lakeviewtribune.example.com/rent-data-2026",
        source_type="news", urgency="high",
        published_at=_d(7),
        raw_text="New analysis from the Lakeview Housing Authority shows median rent in City Council District 7 has increased 34% since 2021, "
                 "from $1,240 to $1,662 per month. The spike is driven by conversion of older rental stock to luxury units. "
                 "Councilman Roy Harmon voted against the Affordable Housing Protection Act in 2023 and 2024.",
        summary="Median rent in District 7 has jumped 34% since 2021. Harmon voted against two affordable housing protection bills during his current term.",
    )
    s2 = SourceItem(
        title="City announces downtown crime fell 8% in 2025 annual report",
        source_name="City of Lakeview — Press Office",
        source_url="https://lakeview.gov/press/crime-stats-2025",
        source_type="public_record", urgency="medium",
        published_at=_d(9),
        raw_text="The Lakeview Police Department released annual crime statistics showing a net 8% decline in downtown incidents in 2025. "
                 "However, the report notes a 12% increase in vehicle break-ins in the southern portion of District 7. Total property crime remained flat.",
        summary="Downtown crime down 8% overall, but south District 7 saw a 12% rise in vehicle break-ins. Harmon's 'District is safer than ever' claim is only partially supported.",
        credibility_note="Official statistics are accurate but Harmon is selectively citing city-wide improvement while south district crime increased.",
    )
    s3 = SourceItem(
        title="Harmon campaign mailer claims Chen 'wants to defund the police'",
        source_name="Harmon for Council Campaign",
        source_url=None,
        source_type="opponent_statement", urgency="high",
        published_at=_d(5),
        raw_text="A campaign mailer distributed this week by the Harmon for Council campaign states: "
                 "'Maria Chen wants to defund the police and make our streets unsafe.' "
                 "Chen's published platform calls for a mental health co-responder program alongside sworn officers — not police defunding.",
        summary="Harmon campaign mailer misrepresents Chen's public safety platform as 'defund the police.' Chen's actual proposal is a mental health co-responder program.",
        credibility_note="RISK: Harmon's claim directly misrepresents Chen's published platform. Respond within 48 hours. Preserve the mailer — potential false advertising issue.",
    )
    s4 = SourceItem(
        title="East Lakeview Elementary at 130% capacity, parents demand action",
        source_name="Lakeview Gazette",
        source_url="https://lakeviewgazette.example.com/school-overcrowding-2026",
        source_type="news", urgency="medium",
        published_at=_d(10),
        raw_text="Overcrowding at East Lakeview Elementary has reached a crisis point, with 38 students per classroom and the elimination of art and music programs. "
                 "Parents packed the April school board meeting to demand City Council support for a new facility bond. Councilman Harmon did not attend.",
        summary="East Lakeview Elementary is 30% over capacity with 38-student classrooms. Parents are demanding City Council action. Harmon skipped the school board meeting on the issue.",
    )
    s5 = SourceItem(
        title="Pothole complaints in District 7 surge 42% as repairs are deferred",
        source_name="Lakeview Tribune",
        source_url="https://lakeviewtribune.example.com/potholes-2026",
        source_type="news", urgency="low",
        published_at=_d(12),
        raw_text="Residents in District 7, especially Precincts 7A and 7D, have filed 42% more pothole complaints than the same period last year. "
                 "City records show $2.1 million in road repairs were deferred from the FY2025 budget. Seniors and disabled residents report sidewalk hazards.",
        summary="Pothole complaints up 42%. $2.1M in road repairs deferred from 2025 budget. Precincts 7A and 7D most affected.",
    )
    s6 = SourceItem(
        title="Harmon on housing: 'The market will fix it — government shouldn't pick winners'",
        source_name="WLKV Radio — Morning Drive Interview",
        source_url="https://wlkv.example.com/harmon-interview-april",
        source_type="opponent_statement", urgency="medium",
        published_at=_d(6),
        raw_text="In a live radio interview Councilman Harmon said: 'The market will fix the housing problem. "
                 "Government shouldn't be picking winners and losers. If we build more units, prices come down naturally.' "
                 "When asked about the 34% rent increase under his tenure, Harmon said 'those numbers need context.'",
        summary="Harmon rejects affordable housing policy intervention, citing market solutions. Refuses to directly address the 34% rent increase data.",
        credibility_note="Harmon's market-based housing stance contradicts his 2019 campaign promise to 'protect renters.' Track as a documented contradiction.",
    )
    s7 = SourceItem(
        title="Developer behind Elm & 3rd gave $15,000 to Harmon PAC before council vote",
        source_name="Lakeview Business Journal",
        source_url="https://lbj.example.com/harmon-developer-donation",
        source_type="public_record", urgency="medium",
        published_at=_d(13),
        raw_text="Campaign finance records show Meridian Development Group contributed $15,000 to the Harmon for District 7 PAC in October 2025, "
                 "three months before Harmon voted to advance the controversial Elm & 3rd mixed-use project over community objections.",
        summary="Campaign finance records show developer behind Elm & 3rd gave $15K to Harmon PAC, three months before his vote to advance the project.",
    )
    s8 = SourceItem(
        title="Harmon op-ed: 'Chen's affordable housing plan would cost taxpayers $40 million'",
        source_name="Lakeview Tribune (Op-Ed)",
        source_url="https://lakeviewtribune.example.com/harmon-oped-housing",
        source_type="opponent_statement", urgency="high",
        published_at=_d(4),
        raw_text="In an op-ed, Harmon claims Chen's housing affordability proposal would cost taxpayers $40 million. "
                 "Chen's campaign says the actual cost estimate from an independent analyst is $8.2 million over 5 years, "
                 "partially offset by federal HOME Investment Partnerships Program grants.",
        summary="Harmon claims Chen's housing plan costs $40M. Independent analysis puts it at $8.2M over 5 years with federal offsets. Harmon's figure is inflated ~5x.",
        credibility_note="RISK: Harmon's $40M figure appears inflated by approximately 5x. Release the independent cost analysis proactively and cite federal grant eligibility.",
    )
    s9 = SourceItem(
        title="April canvassing report: Housing and schools dominate voter concerns",
        source_name="Chen Campaign — April Canvass",
        source_url=None,
        source_type="canvassing", urgency="medium",
        published_at=_d(2),
        raw_text="April canvassing across all four District 7 precincts reached 164 voters. "
                 "Housing and rent were the top concerns in Precincts 7A and 7B. School overcrowding was second-most cited overall. "
                 "68% of canvassed voters expressed negative sentiment about the direction of the district under current leadership.",
        summary="164 voters canvassed. Housing is the top concern in 7A and 7B. Schools are the top concern in 7B. 68% expressed negative sentiment about district direction.",
    )
    s10 = SourceItem(
        title="Harmon skips second consecutive school overcrowding forum",
        source_name="District 7 Parent Coalition Newsletter",
        source_url=None,
        source_type="public_record", urgency="low",
        published_at=_d(8),
        raw_text="For the second consecutive month, Councilman Harmon did not attend the District 7 Parent Coalition's community forum on school overcrowding. "
                 "The coalition sent two formal invitations. Harmon's office cited scheduling conflicts.",
        summary="Harmon has missed two consecutive parent coalition forums on school overcrowding despite formal invitations.",
    )

    db.add_all([s1, s2, s3, s4, s5, s6, s7, s8, s9, s10])
    db.flush()

    # ── Issue ↔ Source links ───────────────────────────────────────────────────
    links = [
        (housing.id, s1.id), (housing.id, s6.id), (housing.id, s8.id), (housing.id, s9.id),
        (safety.id, s2.id), (safety.id, s3.id),
        (education.id, s4.id), (education.id, s9.id), (education.id, s10.id),
        (infrastructure.id, s5.id),
        (development.id, s7.id),
    ]
    for issue_id, source_id in links:
        db.add(IssueMention(issue_id=issue_id, source_item_id=source_id))

    # ── Opponent ──────────────────────────────────────────────────────────────
    harmon = Opponent(
        name="Roy Harmon",
        office="City Council, District 7 (incumbent)",
        party="Republican",
        notes="Two-term incumbent. Market-first on housing. Selective use of crime data. Has taken PAC money from Elm & 3rd developer.",
    )
    db.add(harmon)
    db.flush()

    # ── Opponent activities ───────────────────────────────────────────────────
    db.add_all([
        OpponentActivity(
            opponent_id=harmon.id, source_item_id=s3.id,
            claim=None,
            attack="Maria Chen wants to defund the police and make our streets unsafe",
            promise=None,
            contradiction_note="Chen's published platform explicitly does NOT call for defunding police. This is a documented misrepresentation of her platform.",
            repeated_theme="public safety / law and order",
            created_at=_d(5),
        ),
        OpponentActivity(
            opponent_id=harmon.id, source_item_id=s6.id,
            claim="The market will naturally solve the housing crisis without government intervention",
            attack=None,
            promise=None,
            contradiction_note="Harmon's 2019 campaign platform included a renter protection pledge. He has since shifted to a pure market position — a direct contradiction.",
            repeated_theme="market-first / anti-government",
            created_at=_d(6),
        ),
        OpponentActivity(
            opponent_id=harmon.id, source_item_id=s8.id,
            claim="Chen's affordable housing plan would cost taxpayers $40 million",
            attack="Chen's housing plan is fiscally irresponsible and would raise taxes",
            promise=None,
            contradiction_note="Independent analysis puts the cost at $8.2M over 5 years with federal grant offsets. Harmon's figure appears inflated by approximately 5x.",
            repeated_theme="fiscal responsibility / tax attacks",
            created_at=_d(4),
        ),
        OpponentActivity(
            opponent_id=harmon.id, source_item_id=s2.id,
            claim="District 7 is safer than ever under my leadership",
            attack=None,
            promise=None,
            contradiction_note="City's own data shows south District 7 saw 12% increase in vehicle break-ins. Harmon is citing only downtown improvement while south district crime increased.",
            repeated_theme="cherry-picked public safety data",
            created_at=_d(9),
        ),
    ])

    # ── Canvassing notes ──────────────────────────────────────────────────────
    notes = [
        ("7A", "housing", "negative", "Rent went up $300 this year, looking to move out of district", _d(14)),
        ("7A", "housing", "negative", "Landlord converting building to condos, worried about displacement", _d(14)),
        ("7A", "housing", "negative", "Three neighbors moved out this year because of rent increases", _d(13)),
        ("7A", "housing", "negative", "Can't find an affordable 2-bedroom in the district anymore", _d(13)),
        ("7A", "housing", "negative", "Senior on fixed income, rent increase is 12% — can't absorb it", _d(12)),
        ("7A", "infrastructure", "negative", "Pothole on Oak St damaged my car last month", _d(12)),
        ("7A", "infrastructure", "negative", "Sidewalk near the park is cracked and dangerous for my elderly mother", _d(11)),
        ("7A", "housing", "negative", "Been here 20 years. Never seen housing this unaffordable.", _d(11)),
        ("7A", "housing", "mixed", "Want development but not at the expense of current residents", _d(10)),
        ("7A", "infrastructure", "negative", "Same pothole on Maple Ave for two years, nobody fixes it", _d(10)),
        ("7B", "education", "negative", "Class sizes at East Lakeview are out of control — 38 kids per room", _d(14)),
        ("7B", "education", "negative", "Art program was cut last year. Kids have nothing after school now.", _d(13)),
        ("7B", "education", "negative", "Principal told me they can't take new students next year", _d(13)),
        ("7B", "education", "negative", "My kid shares a textbook with two other students — unacceptable", _d(12)),
        ("7B", "housing", "negative", "Rent is eating 50% of my income. Something has to change.", _d(12)),
        ("7B", "education", "positive", "The new after-school STEM program is great — want more of that", _d(11)),
        ("7B", "education", "negative", "Teachers are burning out. Third teacher leaving our class this year.", _d(11)),
        ("7B", "housing", "mixed", "Would support development if it includes affordable units", _d(10)),
        ("7B", "education", "negative", "School needs a new building — this one is falling apart", _d(10)),
        ("7C", "crime", "negative", "Car broken into twice in the last month, south side of district is not safe", _d(14)),
        ("7C", "crime", "negative", "Feel unsafe walking home after dark. Need more street lighting.", _d(13)),
        ("7C", "crime", "mixed", "Police response time improved but crime itself is up in this area", _d(12)),
        ("7C", "housing", "negative", "Section 8 voucher doesn't cover market rents anymore", _d(12)),
        ("7C", "crime", "negative", "Need more foot patrols, not just cars driving by", _d(11)),
        ("7C", "crime", "negative", "Neighbor's car stolen twice this year. Police report filed, nothing happened.", _d(11)),
        ("7C", "housing", "negative", "Afraid rising rents will force me out of the neighborhood I grew up in", _d(10)),
        ("7D", "infrastructure", "negative", "Roads in 7D are the worst they have ever been in 15 years", _d(14)),
        ("7D", "infrastructure", "negative", "Fell on broken sidewalk near the park, still healing", _d(14)),
        ("7D", "infrastructure", "negative", "Bus stop at Cedar and 7th has been broken for 6 months. Reported 3 times.", _d(13)),
        ("7D", "infrastructure", "negative", "Street floods every time it rains because drain is clogged", _d(13)),
        ("7D", "infrastructure", "negative", "Potholes destroyed two tires this winter alone", _d(12)),
        ("7D", "housing", "negative", "Senior living on fixed income, rent going up 15% next month", _d(12)),
        ("7D", "infrastructure", "negative", "Street lights on our block have been out for three weeks", _d(11)),
        ("7D", "infrastructure", "negative", "Can't bike to work safely — roads too dangerous", _d(10)),
    ]

    for precinct, issue, sentiment, note_text, date in notes:
        db.add(CanvassingNote(
            precinct=precinct,
            issue=issue,
            sentiment=sentiment,
            notes=note_text,
            date=date,
        ))

    db.commit()
    print("[seed] Database seeded with Lakeview City District 7 demo scenario.")


def _seed_source_packs(db: Session) -> None:
    """Seed default source packs (idempotent — skip if name already exists)."""
    if db.query(SourcePack).filter_by(name="US House Race Starter Pack").first():
        return

    pack = SourcePack(
        name="US House Race Starter Pack",
        description=(
            "Generic starting point for a U.S. House of Representatives race. "
            "Replace placeholder URLs with real ones for your district. "
            "Items marked [PLACEHOLDER] need your input before they are useful."
        ),
        race_level="federal",
        geography="us_house",
        created_at=datetime.utcnow(),
    )
    db.add(pack)
    db.flush()

    items = [
        # ── Candidate sources ───────────────────────────────────────────────
        dict(
            name="Your Campaign Website — News/Press",
            category="Your Campaign",
            source_type="campaign_note",
            url=None,
            setup_note="[PLACEHOLDER] Add your campaign website's press or news page URL. Paste new press releases as text sources to keep them in the intelligence database.",
        ),
        dict(
            name="Your Campaign — FEC Committee Page",
            category="Your Campaign",
            source_type="public_record",
            url="https://www.fec.gov/data/committees/",
            setup_note="Search for your committee on FEC.gov. Bookmark the filing page and check quarterly for new disclosures.",
        ),
        # ── Opponent sources ────────────────────────────────────────────────
        dict(
            name="Opponent Campaign Website — News/Press",
            category="Opponent Monitoring",
            source_type="opponent_statement",
            url=None,
            setup_note="[PLACEHOLDER] Add your opponent's press or news page URL. Paste new releases as text sources.",
        ),
        dict(
            name="Opponent — FEC Committee Page",
            category="Opponent Monitoring",
            source_type="public_record",
            url="https://www.fec.gov/data/committees/",
            setup_note="Search for your opponent's committee on FEC.gov. Track large donor changes and late contributions.",
        ),
        dict(
            name="Opponent Facebook / Social Media",
            category="Opponent Monitoring",
            source_type="social",
            url=None,
            setup_note="[PLACEHOLDER] Monitor opponent's public Facebook, X/Twitter, and Instagram for new attacks and announcements. Paste notable posts as text sources.",
        ),
        # ── Election administration ─────────────────────────────────────────
        dict(
            name="State Secretary of State — Election Results",
            category="Election Administration",
            source_type="public_record",
            url=None,
            setup_note="[PLACEHOLDER] Find your state's SOS election results page. Bookmark for primary results, filing deadlines, and district maps.",
        ),
        dict(
            name="County Election Board — Voter Registration",
            category="Election Administration",
            source_type="public_record",
            url=None,
            setup_note="[PLACEHOLDER] Add your county election board URL. Track voter registration trends and absentee ballot returns.",
        ),
        dict(
            name="Ballotpedia — District Page",
            category="Election Administration",
            source_type="public_record",
            url="https://ballotpedia.org/",
            setup_note="Search Ballotpedia for your congressional district. Provides candidate history, election results, and district demographics.",
        ),
        # ── Local news ──────────────────────────────────────────────────────
        dict(
            name="Primary Local Newspaper — RSS",
            category="Local News",
            source_type="news",
            url=None,
            setup_note="[PLACEHOLDER] Add the RSS feed URL for your district's main newspaper. Try appending /rss or /feed to the homepage.",
        ),
        dict(
            name="Local TV Station — Politics RSS",
            category="Local News",
            source_type="news",
            url=None,
            setup_note="[PLACEHOLDER] Add your local TV station's politics/news RSS feed URL.",
        ),
        dict(
            name="Google News — Your Name",
            category="Local News",
            source_type="news",
            url="https://news.google.com/rss/search?q={your+name}&hl=en-US&gl=US&ceid=US:en",
            setup_note="[PLACEHOLDER] Replace {your+name} with your name (use + for spaces). Creates an aggregated news feed across outlets.",
        ),
        dict(
            name="Google News — Opponent Name",
            category="Local News",
            source_type="news",
            url="https://news.google.com/rss/search?q={opponent+name}&hl=en-US&gl=US&ceid=US:en",
            setup_note="[PLACEHOLDER] Replace {opponent+name} with your opponent's name.",
        ),
        dict(
            name="Google News — District/Race",
            category="Local News",
            source_type="news",
            url="https://news.google.com/rss/search?q={congressional+district+race}&hl=en-US&gl=US&ceid=US:en",
            setup_note="[PLACEHOLDER] Replace the search term with your district name, e.g. 'PA-08 congressional race'.",
        ),
        # ── Events & endorsements ───────────────────────────────────────────
        dict(
            name="Debate / Forum Announcements",
            category="Events",
            source_type="campaign_note",
            url=None,
            setup_note="Manually track debate and candidate forum announcements. Paste debate schedules and transcripts as text sources after events.",
        ),
        dict(
            name="Endorsement Tracking",
            category="Events",
            source_type="campaign_note",
            url=None,
            setup_note="Paste endorsement press releases (your own and opponent's) as text sources to feed into talking point generation.",
        ),
        # ── Civic organizations ─────────────────────────────────────────────
        dict(
            name="District Labor Council / Union",
            category="Civic Organizations",
            source_type="news",
            url=None,
            setup_note="[PLACEHOLDER] Add the local labor council's news page or RSS feed if available. Endorsements and voter guides matter.",
        ),
        dict(
            name="Local Chamber of Commerce",
            category="Civic Organizations",
            source_type="news",
            url=None,
            setup_note="[PLACEHOLDER] Track the chamber's endorsements and candidate questionnaire responses.",
        ),
    ]

    for item_data in items:
        db.add(SourcePackItem(
            source_pack_id=pack.id,
            name=item_data["name"],
            category=item_data.get("category"),
            source_type=item_data.get("source_type", "news"),
            url=item_data.get("url"),
            setup_note=item_data.get("setup_note"),
            active=True,
        ))


if __name__ == "__main__":
    import argparse
    from app.db import init_db, engine, Base, SessionLocal

    parser = argparse.ArgumentParser(description="Seed Campaign War Room demo data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables and re-seed from scratch (destructive).",
    )
    args = parser.parse_args()

    init_db()

    if args.reset:
        print("[seed] --reset: dropping all tables…")
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        print("[seed] Tables recreated.")

    with SessionLocal() as db:
        seed(db)
    print("[seed] Done.")
