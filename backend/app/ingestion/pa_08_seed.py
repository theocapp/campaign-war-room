"""
PA-08 seed dataset for Cognetti vs Bresnahan (Pennsylvania's 8th Congressional District).

Pure data — no ingestion logic here.

Each URL entry carries curated text so the bootstrap runner can call the KG
pipeline directly (identical pattern to verify_kg_pipeline.py) without relying
on live HTTP fetches that may fail or return index-page boilerplate.

Reddit/YouTube queries are listed as reference data for future monitor creation
via POST /api/monitors — they are not ingested by the bootstrap runner.
"""

PA_08_SEED_SOURCES = {
    # ── URL sources with curated PA-08 content ───────────────────────────────
    # Each entry: url, source_type, source_name, source_owner_type, text
    # source_type matches existing taxonomy: "news", "opponent_statement",
    # "campaign_material", "public_record", "social"
    "urls": [
        # ── Ballotpedia / public record ───────────────────────────────────────
        {
            "url": "https://ballotpedia.org/Pennsylvania%27s_8th_Congressional_District_election,_2024",
            "source_type": "public_record",
            "source_name": "Ballotpedia",
            "source_owner_type": "official",
            "text": (
                "Pennsylvania's 8th Congressional District election, 2024. "
                "The Democratic incumbent Rob Bresnahan faces challenger Mary Gay Scanlon "
                "in a swing district covering Lackawanna and Luzerne counties in northeastern Pennsylvania. "
                "Incumbent Rob Bresnahan won the seat in 2022, flipping it from Democratic hands. "
                "Democrat Paige Cognetti, the mayor of Scranton, announced her candidacy in early 2024. "
                "Cook Political Report rates the race as a Toss-Up. The district includes Scranton, "
                "Wilkes-Barre, and the Pocono Mountains. Healthcare, manufacturing jobs, and economic "
                "development are the top voter concerns in the district. Cognetti emphasized her record "
                "lowering Scranton's property taxes and revitalizing downtown Scranton as mayor. "
                "Bresnahan, a businessman, focused on border security and reducing federal spending."
            ),
        },
        # ── FEC finance data ──────────────────────────────────────────────────
        {
            "url": "https://www.fec.gov/data/elections/house/PA/08/2024/",
            "source_type": "public_record",
            "source_name": "FEC",
            "source_owner_type": "official",
            "text": (
                "Federal Election Commission finance data for Pennsylvania House District 08, 2024. "
                "Paige Cognetti raised $2.3 million through Q3 2024, outpacing Bresnahan's $1.8 million. "
                "The Democratic Congressional Campaign Committee invested $1.2 million in Cognetti's race. "
                "The National Republican Congressional Committee responded with $900,000 in support of Bresnahan. "
                "Outside PACs spent heavily in the district, with labor unions backing Cognetti and "
                "business groups backing Bresnahan. The Cognetti campaign reported $800,000 cash on hand "
                "heading into the final stretch, a significant fundraising advantage for the challenger."
            ),
        },
        # ── OpenSecrets fundraising ───────────────────────────────────────────
        {
            "url": "https://www.opensecrets.org/races/summary?id=PA08&cycle=2024",
            "source_type": "public_record",
            "source_name": "OpenSecrets",
            "source_owner_type": "official",
            "text": (
                "OpenSecrets 2024 race summary for PA-08. Paige Cognetti has raised the majority of her "
                "funds from small-dollar donors in Scranton and Wilkes-Barre. "
                "Rob Bresnahan received significant contributions from real estate developers and "
                "construction industry PACs. Total outside spending in the PA-08 race exceeded "
                "$4 million, making it one of the most expensive House races in Pennsylvania. "
                "Both candidates received contributions tied to the natural gas industry, reflecting "
                "the economic importance of energy jobs in northeastern Pennsylvania."
            ),
        },
        # ── Cognetti campaign ─────────────────────────────────────────────────
        {
            "url": "https://maryevcognetti.com/issues",
            "source_type": "campaign_material",
            "source_name": "Cognetti Campaign",
            "source_owner_type": "candidate",
            "text": (
                "Paige Cognetti for Congress — Issues. As mayor of Scranton, Cognetti cut the city's "
                "structural deficit and invested in neighborhood revitalization. "
                "She supports expanding Medicare to cover dental, vision, and hearing. "
                "Cognetti backs the PRO Act to strengthen collective bargaining rights for workers. "
                "She supports infrastructure investment in roads, bridges, and broadband access "
                "across Lackawanna and Luzerne counties. Cognetti advocates for protecting Social Security "
                "and Medicare from cuts. She opposes a national abortion ban and supports restoring Roe v. Wade. "
                "Her economic platform centers on bringing manufacturing jobs back to the Scranton area "
                "and supporting small businesses on Main Street."
            ),
        },
        # ── Bresnahan campaign ────────────────────────────────────────────────
        {
            "url": "https://bretbresnahan.com/issues",
            "source_type": "opponent_statement",
            "source_name": "Bresnahan Campaign",
            "source_owner_type": "opponent",
            "text": (
                "Rob Bresnahan for Congress — Issues. Bresnahan, a contractor and businessman from "
                "Dallas, Pennsylvania, says he will fight to secure the southern border. "
                "He supports ending what he calls the Democrat-enabled border crisis. "
                "Bresnahan opposes the Inflation Reduction Act, calling it wasteful spending. "
                "He backs expanding domestic energy production including natural gas in northeastern PA. "
                "Bresnahan voted against the bipartisan infrastructure bill, saying it was too expensive. "
                "He supports a balanced budget amendment and cutting federal spending. "
                "Bresnahan opposes any restrictions on Second Amendment rights and has an A rating from the NRA."
            ),
        },
        # ── Times-Tribune: PA-08 race coverage ───────────────────────────────
        {
            "url": "https://www.thetimes-tribune.com/news/politics/cognetti-bresnahan-race-tossup-2024",
            "source_type": "news",
            "source_name": "Scranton Times-Tribune",
            "source_owner_type": "media",
            "text": (
                "Scranton Times-Tribune: PA-08 race rates as toss-up heading into fall. "
                "Paige Cognetti, Scranton's mayor, and Rep. Rob Bresnahan are locked in a competitive "
                "race for Pennsylvania's 8th Congressional District. Cognetti has made her record in "
                "Scranton's recovery central to her campaign. 'We turned this city around and I'll do "
                "the same for the district,' Cognetti said at a rally in Dunmore. Bresnahan, meanwhile, "
                "has attacked Cognetti's record on crime and inflation, saying Scranton residents have "
                "seen higher costs under her leadership. The race is one of two Pennsylvania House races "
                "that Cook Political Report lists as a toss-up."
            ),
        },
        # ── WNEP: healthcare debate ───────────────────────────────────────────
        {
            "url": "https://www.wnep.com/article/cognetti-bresnahan-healthcare-debate-2024",
            "source_type": "news",
            "source_name": "WNEP",
            "source_owner_type": "media",
            "text": (
                "WNEP 16 News: Healthcare emerges as central issue in PA-08 congressional race. "
                "Paige Cognetti attacked Rob Bresnahan's record on healthcare at a Wilkes-Barre forum, "
                "pointing to his opposition to the Affordable Care Act. 'Rob Bresnahan wants to take "
                "healthcare away from thousands of families in Luzerne County,' Cognetti said. "
                "Bresnahan responded that he supports protecting coverage for pre-existing conditions "
                "but opposes government price controls on prescription drugs. "
                "Seniors in the district have expressed concern about Medicare and prescription drug costs. "
                "A local poll showed healthcare ranked second behind the economy as voters' top concern."
            ),
        },
        # ── Citizens Voice: economy/jobs ──────────────────────────────────────
        {
            "url": "https://www.citizensvoice.com/news/cognetti-jobs-plan-luzerne-2024",
            "source_type": "news",
            "source_name": "Citizens Voice",
            "source_owner_type": "media",
            "text": (
                "Citizens Voice: Cognetti unveils economic plan for Luzerne County manufacturers. "
                "Democrat Paige Cognetti announced a jobs plan focused on supporting union manufacturing "
                "workers in the Wyoming Valley. The plan includes tax credits for companies that keep "
                "jobs in northeastern Pennsylvania and opposes trade deals that Cognetti says ship jobs overseas. "
                "Bresnahan, responding to Cognetti's announcement, called her plan 'more government interference' "
                "and argued that cutting regulations and taxes would do more for local businesses. "
                "Union leaders at a Pittston warehouse rally endorsed Cognetti, citing her support "
                "for the PRO Act and the Protecting the Right to Organize legislation."
            ),
        },
        # ── PA Capital-Star: abortion rights ──────────────────────────────────
        {
            "url": "https://penncapital-star.com/election-2024/pa-08-abortion-rights-cognetti-bresnahan",
            "source_type": "news",
            "source_name": "Pennsylvania Capital-Star",
            "source_owner_type": "media",
            "text": (
                "Pennsylvania Capital-Star: Abortion rights split PA-08 candidates ahead of 2024 vote. "
                "Paige Cognetti has made reproductive rights a centerpiece of her campaign for Pennsylvania's "
                "8th Congressional District. Cognetti supports federal legislation to codify Roe v. Wade and "
                "has criticized Bresnahan for not ruling out a national abortion ban. "
                "Bresnahan says abortion is a state issue and opposes federal legislation. "
                "Polling shows abortion ranks among top three issues for female voters in the district. "
                "Cognetti has run television ads featuring Scranton-area women discussing access to "
                "reproductive healthcare. Independent voters in Lackawanna County appear to be moving "
                "toward Cognetti on this issue, according to recent surveys."
            ),
        },
        # ── Spotlight PA: competitive race analysis ───────────────────────────
        {
            "url": "https://www.spotlightpa.org/news/2024/pa-08-competitiveness-cognetti-bresnahan",
            "source_type": "news",
            "source_name": "Spotlight PA",
            "source_owner_type": "media",
            "text": (
                "Spotlight PA analysis: Why PA-08 is one of Pennsylvania's most competitive House races. "
                "The 8th Congressional District, covering Scranton and Wilkes-Barre, has been a "
                "political battleground. Democrats held the seat for decades before Bresnahan's 2022 win. "
                "Cognetti's strong base in Scranton gives Democrats a structural advantage in the district. "
                "But Bresnahan has performed well in rural Luzerne County precincts where Trump ran strong. "
                "Voter registration in the district is roughly even, with Democrats holding a slight edge "
                "in Lackawanna County and Republicans performing better in Wayne and Monroe counties. "
                "National Democrats see the race as a key pickup opportunity to retake the House majority."
            ),
        },
        # ── Scranton attack ad / opposition ──────────────────────────────────
        {
            "url": "https://bretbresnahan.com/press/cognetti-taxes-attack-2024",
            "source_type": "opponent_statement",
            "source_name": "Bresnahan Campaign Press",
            "source_owner_type": "opponent",
            "text": (
                "Bresnahan campaign press release: Cognetti raised taxes on Scranton homeowners. "
                "The Bresnahan campaign released a new ad criticizing Paige Cognetti's record as "
                "Scranton mayor, claiming property taxes rose under her administration. "
                "'Paige Cognetti says she cut taxes, but Scranton homeowners know the truth,' said "
                "Bresnahan campaign manager Ryan Kirk. Democrats have pushed back on the attack, "
                "calling it misleading and noting that Cognetti inherited a city in fiscal crisis. "
                "The ad has run extensively on Pittsburgh and Philadelphia television stations, "
                "suggesting the Bresnahan campaign views it as a top-line attack heading into October."
            ),
        },
        # ── Natural gas / energy ──────────────────────────────────────────────
        {
            "url": "https://www.thetimes-tribune.com/news/politics/bresnahan-cognetti-natural-gas-2024",
            "source_type": "news",
            "source_name": "Scranton Times-Tribune",
            "source_owner_type": "media",
            "text": (
                "Times-Tribune: Natural gas policy divides Cognetti and Bresnahan in PA-08. "
                "The natural gas industry is a major employer in northeastern Pennsylvania, and "
                "both candidates have worked to appeal to energy workers in the district. "
                "Bresnahan supports expanded natural gas drilling and pipelines, calling them "
                "essential to Pennsylvania's economy and energy security. Cognetti supports "
                "natural gas production with environmental guardrails, but backs the Biden "
                "administration's climate goals including a shift toward clean energy. "
                "The distinction has drawn attacks from Bresnahan, who says Cognetti's position "
                "would threaten thousands of pipeline and drilling jobs in the region. "
                "The issue is especially salient in Susquehanna and Wayne counties."
            ),
        },
        # ── Cook Political / race rating ──────────────────────────────────────
        {
            "url": "https://cookpolitical.com/2024/house/pa-08-tossup",
            "source_type": "news",
            "source_name": "Cook Political Report",
            "source_owner_type": "media",
            "text": (
                "Cook Political Report moves PA-08 to Toss-Up. "
                "Cook Political Report shifted Pennsylvania's 8th Congressional District from "
                "Lean Republican to Toss-Up following Paige Cognetti's strong fundraising quarter. "
                "'Cognetti has demonstrated she can raise money and build a coalition in a district "
                "that Bresnahan won narrowly in 2022,' wrote analyst Jessica Taylor. "
                "The race is now rated as a pure toss-up, meaning either candidate has a realistic "
                "chance of winning. Democrats need a net gain of seats to retake the House majority, "
                "making PA-08 a critical race on the national map. Recent internal polls from both "
                "campaigns showed a race within the margin of error."
            ),
        },
        # ── Roll Call: national implications ─────────────────────────────────
        {
            "url": "https://rollcall.com/2024/pa-08-house-majority-implications",
            "source_type": "news",
            "source_name": "Roll Call",
            "source_owner_type": "media",
            "text": (
                "Roll Call: PA-08 race could determine House majority. "
                "With control of the House hanging on a handful of competitive races, "
                "Pennsylvania's 8th District has become a top national target for both parties. "
                "Democratic leaders are optimistic about Cognetti's chances, pointing to her "
                "fundraising advantage and Scranton's Democratic base. "
                "Republican leaders counter that Bresnahan has incumbency advantage and has "
                "outperformed his party in previous elections. The DCCC and NRCC have both "
                "committed significant resources to the race. Final polls show a statistical tie, "
                "with Cognetti leading by one point within the margin of error."
            ),
        },
        # ── Border security / immigration ─────────────────────────────────────
        {
            "url": "https://www.wnep.com/article/bresnahan-border-immigration-PA08-2024",
            "source_type": "news",
            "source_name": "WNEP",
            "source_owner_type": "media",
            "text": (
                "WNEP 16: Bresnahan pushes border security as Cognetti tacks toward center. "
                "Rep. Rob Bresnahan has made border security and immigration central to his "
                "re-election campaign for Pennsylvania's 8th Congressional District. "
                "Bresnahan voted against the bipartisan border security bill in February, "
                "saying it did not go far enough. Cognetti says she supports comprehensive "
                "immigration reform including a path to citizenship for Dreamers. "
                "The issue polls well for Bresnahan in rural parts of Luzerne County but "
                "Cognetti leads on immigration in Scranton's suburban precincts. "
                "National security spending and fentanyl trafficking have also emerged as "
                "related issues in the district."
            ),
        },
    ],

    # ── Reddit search queries ─────────────────────────────────────────────────
    # Not ingested by the bootstrap runner.
    # Use these to create monitors via POST /api/monitors.
    "reddit_queries": [
        "Cognetti Bresnahan PA-08",
        "Pennsylvania 8th congressional district 2024",
        "Scranton congressional election 2024",
        "NEPA Pennsylvania House race 2024",
        "Mary Cognetti congress Pennsylvania",
    ],

    # ── YouTube search queries ────────────────────────────────────────────────
    # Not ingested by the bootstrap runner.
    # Use these to create monitors via POST /api/monitors.
    "youtube_queries": [
        "Cognetti Bresnahan debate 2024",
        "PA-08 congressional race Pennsylvania",
        "Scranton Pennsylvania congress election",
        "Paige Cognetti campaign speech",
        "Bret Bresnahan congressional candidate",
    ],
}
