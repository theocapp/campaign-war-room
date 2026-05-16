"""
LLM provider abstraction.

Set LLM_PROVIDER=mock|openai|anthropic in your .env (or environment).
Mock is the default when no provider is configured or the required package
is missing.
"""
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger(__name__)

# Try to load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Exceptions ────────────────────────────────────────────────────────────────

class ProviderRateLimitError(Exception):
    """Raised when an LLM provider hits its rate or daily token limit (HTTP 429)."""
    pass


# ── Abstract interface ────────────────────────────────────────────────────────

class BaseLLMProvider(ABC):
    @abstractmethod
    def summarize(self, text: str, max_words: int = 80) -> str: ...

    @abstractmethod
    def classify_urgency(self, text: str) -> str: ...

    @abstractmethod
    def extract_issues(self, text: str) -> list[str]: ...

    @abstractmethod
    def detect_opponent_activity(self, text: str, opponent_name: str) -> dict: ...

    @abstractmethod
    def generate_talking_points(
        self,
        issue: str,
        tone: str,
        context: str = "",
        campaign_profile: Optional[dict] = None,
        sources: Optional[list[dict]] = None,
        opponent_activities: Optional[list[dict]] = None,
    ) -> dict: ...

    @abstractmethod
    def generate_risk_warning(self, text: str, credibility_note: str) -> Optional[str]: ...

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Send a single freeform prompt and return the raw text response."""
        ...

    @abstractmethod
    def verify_opponent_subject(self, sentence: str, opponent_name: str, candidate_name: str) -> str:
        """
        Determine who is the grammatical agent (actor) in this sentence.
        Returns one of: "opponent", "candidate", "both", "unclear".
        Used to catch passive-voice misclassifications the heuristic misses.
        """
        ...


# ── Shared prompt helpers ─────────────────────────────────────────────────────

_ETHICS_BLOCK = """
ETHICS CONSTRAINTS (non-negotiable):
- Ground all claims in the provided sources only. Do not invent statistics or facts not in those sources.
- Do not defame individuals beyond what the sources directly support.
- Do not generate content that could constitute voter suppression, harassment, intimidation, impersonation, or psychological manipulation.
- If evidence for a claim is weak or disputed, say so explicitly in risk_warning.
- Always cite the specific source titles you used in evidence_notes.
""".strip()

_TONE_GUIDE = {
    "calm": "measured, factual, forward-looking — avoid partisan attacks",
    "aggressive": "direct contrast with opponent's record, call out failures by name, cite evidence",
    "policy-focused": "detailed, substantive, solution-oriented — lead with the plan",
    "debate": "crisp contrast, anticipate counterarguments, close with a clear choice",
    "social": "under 240 characters for social_post, punchy, no jargon",
}

_TP_JSON_SCHEMA = """{
  "short_answer": "2-3 sentences, usable at a door",
  "long_answer": "3-4 paragraphs, usable in an interview or town hall",
  "debate_answer": "2-3 sentences of sharp contrast without defamation",
  "social_post": "under 240 characters, platform-neutral",
  "risk_warning": "blunt warnings about weak evidence or legally risky claims, or null",
  "evidence_notes": "cite specific source titles and any figures used",
  "source_titles_used": ["array", "of", "source", "titles"],
  "source_urls_used": ["array", "of", "source", "urls", "or", "empty"]
}"""


def _build_tp_prompt(
    issue: str,
    tone: str,
    context: str,
    campaign_profile: Optional[dict],
    sources: Optional[list[dict]],
    opponent_activities: Optional[list[dict]],
) -> str:
    lines = []

    if campaign_profile:
        name = campaign_profile.get("candidate_name", "the candidate")
        office = campaign_profile.get("office") or campaign_profile.get("race") or "local office"
        district = campaign_profile.get("district") or campaign_profile.get("location") or ""
        party = campaign_profile.get("party") or ""
        msg = campaign_profile.get("campaign_message") or ""
        prios = campaign_profile.get("key_priorities") or []
        if isinstance(prios, str):
            try:
                prios = json.loads(prios)
            except Exception:
                prios = []
        lines.append(f"CANDIDATE: {name}{', ' + party if party else ''}, running for {office}{' in ' + district if district else ''}")
        if msg:
            lines.append(f"CORE MESSAGE: {msg}")
        if prios:
            lines.append(f"KEY PRIORITIES: {', '.join(prios)}")
        lines.append("")

    lines.append(f"ISSUE: {issue}")
    if context:
        lines.append(f"Issue context: {context}")
    lines.append("")

    if sources:
        lines.append("RELEVANT SOURCES:")
        for i, s in enumerate(sources, 1):
            title = s.get("title", "Untitled")
            summary = s.get("summary") or s.get("raw_text", "")[:200]
            urgency = s.get("urgency", "")
            url = s.get("source_url") or ""
            note = s.get("credibility_note") or ""
            line = f"{i}. \"{title}\""
            if summary:
                line += f" — {summary}"
            if urgency:
                line += f" [urgency: {urgency}]"
            if note:
                line += f" ⚠ {note}"
            if url:
                line += f" ({url})"
            lines.append(line)
        lines.append("")

    if opponent_activities:
        lines.append("OPPONENT ACTIVITY:")
        for act in opponent_activities:
            if act.get("attack"):
                lines.append(f"  ATTACK: \"{act['attack']}\"")
                if act.get("contradiction_note"):
                    lines.append(f"  Fact-check: {act['contradiction_note']}")
            if act.get("claim"):
                lines.append(f"  CLAIM: \"{act['claim']}\"")
                if act.get("contradiction_note"):
                    lines.append(f"  Context: {act['contradiction_note']}")
        lines.append("")

    tone_desc = _TONE_GUIDE.get(tone, "measured and factual")
    lines.append(f"REQUESTED TONE: {tone} — {tone_desc}")
    lines.append("")
    lines.append(_ETHICS_BLOCK)
    lines.append("")
    lines.append(f"Respond ONLY with a valid JSON object matching this schema:\n{_TP_JSON_SCHEMA}")

    return "\n".join(lines)


def _parse_json_response(raw: str) -> dict:
    """Extract and parse JSON from an LLM response that may have markdown fences."""
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()
    # Find the first { ... } block
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # Last resort: try parsing the whole thing
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _default_tp_result(issue: str) -> dict:
    return {
        "short_answer": f"We need evidence-based, community-centered solutions on {issue}.",
        "long_answer": f"This is an important issue for our community. My approach is to gather facts, consult residents, and pursue solutions grounded in evidence.",
        "debate_answer": f"On {issue}, I focus on facts and solutions. I'd encourage voters to compare each candidate's specific platform.",
        "social_post": f"Every issue in our district deserves a real answer. I'm committed to evidence-based leadership.",
        "risk_warning": None,
        "evidence_notes": "Gather local data sources and resident testimony to strengthen messaging on this topic.",
        "source_titles_used": [],
        "source_urls_used": [],
    }


# ── Mock provider ─────────────────────────────────────────────────────────────

_STATIC_TALKING_POINTS: dict[str, dict] = {
    "Housing & Affordability": {
        "short_answer": (
            "Rents in our district are up 34% since 2021. Families and seniors are being forced out. "
            "Roy Harmon has blocked every affordable housing bill that came before the council. "
            "I will fight for rental stabilization, first-time homebuyer grants, and inclusionary zoning."
        ),
        "long_answer": (
            "The housing crisis in District 7 is not an accident — it's the result of deliberate inaction. "
            "Median rent has risen 34% since 2021, from $1,240 to $1,662 per month, while wages have grown only 9%. "
            "Councilman Harmon voted against the Affordable Housing Protection Act in both 2023 and 2024.\n\n"
            "My plan: (1) Rental stabilization — cap annual rent increases at CPI plus 3% for buildings over 10 units. "
            "(2) Inclusionary zoning — require 15% affordable units in any new development over 20 units receiving city permits. "
            "(3) First-time homebuyer fund — $2M annual allocation for down-payment assistance for District 7 residents earning under 120% AMI. "
            "An independent analysis puts the cost at $8.2M over five years, partially offset by federal housing grants."
        ),
        "debate_answer": (
            "My opponent says the market will solve our housing crisis. It has had four years. Rents are up 34%. "
            "He voted against housing protection bills — twice. I have a fully-costed, independently verified plan. Roy Harmon does not."
        ),
        "social_post": (
            "Rents in District 7 are up 34% since 2021. Harmon blocked 2 affordable housing bills and calls it 'the market working.' "
            "I call it a failure of leadership. I have a plan. #LakeviewDistrict7"
        ),
        "risk_warning": (
            "Always cite the Lakeview Housing Authority report (2026) when using the 34% figure. "
            "Do not promise specific rent reduction percentages. The $8.2M cost estimate should always be cited as 'independent analysis.'"
        ),
        "evidence_notes": (
            "Sources: Lakeview Housing Authority Rent Report 2026; City Council voting records 2023-2024; "
            "Independent cost analysis by Midwest Policy Group (April 2026)."
        ),
        "source_titles_used": [
            "Rents in District 7 up 34% since 2021, new data shows",
            "Harmon on housing: 'The market will fix it'",
            "Harmon op-ed: 'Chen's affordable housing plan would cost taxpayers $40 million'",
        ],
        "source_urls_used": [
            "https://lakeviewtribune.example.com/rent-data-2026",
            "https://wlkv.example.com/harmon-interview-april",
            "https://lakeviewtribune.example.com/harmon-oped-housing",
        ],
    },
    "Public Safety": {
        "short_answer": (
            "Public safety means the whole district — not just the numbers that look good in a press release. "
            "Downtown crime is down, but vehicle break-ins in south District 7 are up 12%. "
            "I support our police and will add a mental health co-responder program."
        ),
        "long_answer": (
            "Roy Harmon is claiming District 7 is 'safer than ever.' The city's own data tells a more complicated story. "
            "Downtown incidents fell 8% — that's real. But in south District 7, vehicle break-ins rose 12% in the same period.\n\n"
            "Harmon's campaign mailer says I want to 'defund the police.' That is false. "
            "My platform calls for a mental health co-responder program — trained crisis counselors who ride alongside officers "
            "for non-violent calls. This is already working in Denver and Austin. "
            "It reduces officer burnout and costs less than a full police response."
        ),
        "debate_answer": (
            "My opponent sent a mailer saying I want to defund the police. That is a lie, and he knows it. "
            "My platform proposes a mental health co-responder program that police unions in other cities have praised. "
            "South District 7 is not safer than ever. The city's own data says so."
        ),
        "social_post": (
            "Harmon says I want to 'defund the police.' That's false — my platform is public. "
            "I support officers AND a mental health co-responder program. Read the plan. #District7"
        ),
        "risk_warning": (
            "Harmon's 'defund the police' attack is the highest-urgency narrative to counter. "
            "Respond quickly. Do not say 'I never said that' without immediately stating what you DID say."
        ),
        "evidence_notes": (
            "Sources: Lakeview PD Annual Crime Report 2025; Harmon campaign mailer (April 2026); "
            "Chen campaign platform (published); Denver STAR program results 2023."
        ),
        "source_titles_used": [
            "City announces downtown crime fell 8% in 2025 annual report",
            "Harmon campaign mailer claims Chen 'wants to defund the police'",
        ],
        "source_urls_used": [
            "https://lakeview.gov/press/crime-stats-2025",
        ],
    },
    "Education & Schools": {
        "short_answer": (
            "East Lakeview Elementary is at 130% capacity with 38 kids per classroom and no art or music programs. "
            "Roy Harmon was invited to two parent coalition forums and skipped both. "
            "I will show up, and I will fight for a new school facility bond."
        ),
        "long_answer": (
            "Our kids are sitting in 38-student classrooms at a school built for 800 that now holds over 1,000. "
            "Art, music, and enrichment programs have been cut. Teachers are burning out.\n\n"
            "The Lakeview School Board has been asking City Council for support for two years. "
            "The District 7 representative — Roy Harmon — has not attended either of the parent coalition meetings. "
            "His office cited 'scheduling conflicts.' Both times.\n\n"
            "I will: (1) Appear at every school board meeting involving District 7. (2) Champion the new facility bond. "
            "(3) Pursue state and federal grants to restore enrichment programs in the interim."
        ),
        "debate_answer": (
            "Roy Harmon was invited twice to meet with the parents of East Lakeview Elementary. He didn't go — either time. "
            "Meanwhile, 1,000 kids are crammed into a school built for 800. "
            "Our kids need a council member who actually shows up."
        ),
        "social_post": (
            "East Lakeview Elementary: 130% capacity. 38 kids per classroom. No art. No music. "
            "Harmon was invited to TWO parent forums. Didn't show. #District7"
        ),
        "risk_warning": (
            "Do not imply Harmon is legally responsible for the school's capacity — school funding is complex. "
            "Frame the attendance failure as an accountability issue, not a legal one."
        ),
        "evidence_notes": (
            "Sources: Lakeview Gazette school overcrowding report (April 2026); "
            "School board meeting minutes (March & April 2026); District 7 Parent Coalition newsletter."
        ),
        "source_titles_used": [
            "East Lakeview Elementary at 130% capacity, parents demand action",
            "Harmon skips second consecutive school overcrowding forum",
        ],
        "source_urls_used": [
            "https://lakeviewgazette.example.com/school-overcrowding-2026",
        ],
    },
    "Infrastructure": {
        "short_answer": (
            "Pothole complaints are up 42% and $2.1M in road repairs were deferred from last year's budget. "
            "Seniors and families in Precincts 7A and 7D are most affected. "
            "I'll fight to restore deferred maintenance funding and establish a 90-day repair guarantee."
        ),
        "long_answer": (
            "Infrastructure is not glamorous, but it matters every single day. "
            "In District 7, the city deferred $2.1 million in road repairs from the FY2025 budget — and it shows. "
            "Pothole complaints are up 42%. A resident in 7D reported her bus stop has been broken for six months.\n\n"
            "My platform: restore the $2.1M in deferred maintenance, establish a 90-day maximum repair response time, "
            "and audit the city's infrastructure prioritization formula to ensure lower-income precincts are not deprioritized."
        ),
        "debate_answer": (
            "Two terms. Eight years. And we have 42% more pothole complaints, $2.1 million in deferred repairs, "
            "and a bus stop in Precinct 7D broken for six months. "
            "I will restore the deferred maintenance funding and establish a 90-day repair guarantee."
        ),
        "social_post": (
            "42% more pothole complaints. $2.1M in deferred repairs. A bus stop broken for 6 months. "
            "Two terms and this is what we have to show. I'll fix this. #District7"
        ),
        "risk_warning": (
            "Avoid implying Harmon personally approved the deferral. Budget decisions involve the full council. "
            "The 90-day repair guarantee should be framed as a goal, not a legal commitment."
        ),
        "evidence_notes": (
            "Sources: Lakeview Tribune infrastructure report (April 2026); "
            "City FY2025 Budget Deferral List (public record); 311 complaint data."
        ),
        "source_titles_used": [
            "Pothole complaints in District 7 surge 42% as repairs are deferred",
        ],
        "source_urls_used": [
            "https://lakeviewtribune.example.com/potholes-2026",
        ],
    },
    "Downtown Development": {
        "short_answer": (
            "District 7 needs development that benefits current residents — not just developers who write checks to campaigns. "
            "Harmon's PAC received $15,000 from the Elm & 3rd developer, then he voted to advance the project over community objections."
        ),
        "long_answer": (
            "Economic development is important — but it has to work for the people who already live here. "
            "The Elm & 3rd mixed-use development has divided the district. "
            "Campaign finance records show Meridian Development Group gave $15,000 to the Harmon for District 7 PAC "
            "in October 2025, just three months before Harmon voted to advance the project over community objections.\n\n"
            "I support development — with community benefit agreements, affordable unit requirements, and transparent process."
        ),
        "debate_answer": (
            "Roy Harmon took $15,000 from the developer behind Elm & 3rd. Three months later, he voted for the project over community objections. "
            "I support development. I do not support pay-to-play development."
        ),
        "social_post": (
            "Campaign finance records: Developer behind Elm & 3rd gave Harmon's PAC $15,000. "
            "Three months later: Harmon voted for the project over community objections. #District7"
        ),
        "risk_warning": (
            "Always cite the public campaign finance record when mentioning the $15,000 donation. "
            "Do not call it a 'bribe' — call it a 'conflict of interest.' Avoid implying illegal conduct without legal evidence."
        ),
        "evidence_notes": (
            "Sources: Lakeview Business Journal (April 2026); "
            "City campaign finance disclosures (public record); City Council vote log (January 2026)."
        ),
        "source_titles_used": [
            "Developer behind Elm & 3rd gave $15,000 to Harmon PAC before council vote",
        ],
        "source_urls_used": [
            "https://lbj.example.com/harmon-developer-donation",
        ],
    },
}

_URGENCY_KEYWORDS = {
    "high": ["attack", "defund", "fabricat", "false", "misinform", "cost", "crisis", "urgent", "danger", "misrepresent", "inflat"],
    "medium": ["concern", "increas", "problem", "controversy", "compet", "oppos", "challeng", "contrad"],
}

_ISSUE_KEYWORDS = {
    "Housing & Affordability": ["rent", "housing", "afford", "tenant", "landlord", "evict", "homebuyer", "mortgage", "zoning"],
    "Public Safety": ["crime", "police", "safety", "break-in", "theft", "patrol", "enforcement", "defund", "officer"],
    "Education & Schools": ["school", "education", "classroom", "student", "teacher", "overcrowd", "art", "music", "parent"],
    "Infrastructure": ["pothole", "road", "sidewalk", "infrastructure", "repair", "transit", "bus", "street", "flood"],
    "Downtown Development": ["development", "developer", "downtown", "project", "zoning", "construction", "gentrification"],
}


class MockLLMProvider(BaseLLMProvider):
    def summarize(self, text: str, max_words: int = 80) -> str:
        words = text.split()
        if len(words) <= max_words:
            return text.strip()
        return " ".join(words[:max_words]) + "..."

    def extract_issues(self, text: str) -> list[str]:
        text_lower = text.lower()
        matched = []
        for issue, keywords in _ISSUE_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                matched.append(issue)
        return matched or ["General Campaign"]

    def classify_urgency(self, text: str) -> str:
        text_lower = text.lower()
        for kw in _URGENCY_KEYWORDS["high"]:
            if kw in text_lower:
                return "high"
        for kw in _URGENCY_KEYWORDS["medium"]:
            if kw in text_lower:
                return "medium"
        return "low"

    def detect_opponent_activity(self, text: str, opponent_name: str) -> dict:
        result: dict = {"claim": None, "attack": None, "promise": None, "contradiction_note": None, "repeated_theme": None}
        text_lower = text.lower()
        if opponent_name.lower() not in text_lower:
            return result
        if any(w in text_lower for w in ["claims", "says", "argues", "stated", "announced"]):
            result["claim"] = self.summarize(text, max_words=30)
        if any(w in text_lower for w in ["attack", "false", "lie", "defund", "accused"]):
            result["attack"] = self.summarize(text, max_words=30)
        if any(w in text_lower for w in ["promises", "pledged", "vowed", "will ensure"]):
            result["promise"] = self.summarize(text, max_words=20)
        return result

    def generate_talking_points(
        self,
        issue: str,
        tone: str,
        context: str = "",
        campaign_profile: Optional[dict] = None,
        sources: Optional[list[dict]] = None,
        opponent_activities: Optional[list[dict]] = None,
    ) -> dict:
        pts = _STATIC_TALKING_POINTS.get(issue)
        if pts:
            result = dict(pts)
        else:
            result = _default_tp_result(issue)

        # Personalize with campaign name if available
        candidate = (campaign_profile or {}).get("candidate_name", "")
        if candidate and candidate != "Maria Chen":
            for key in ("short_answer", "long_answer", "debate_answer", "social_post"):
                result[key] = result[key].replace("Maria Chen", candidate).replace("I will", f"{candidate} will")

        # Apply source context: add real source titles/urls if passed
        if sources:
            result["source_titles_used"] = [s["title"] for s in sources if s.get("title")]
            result["source_urls_used"] = [s["source_url"] for s in sources if s.get("source_url")]

        # Tone adjustments
        if tone == "aggressive":
            result["short_answer"] = "My opponent has failed this district. " + result["short_answer"]
        elif tone == "social":
            result["short_answer"] = result["social_post"]
        elif tone == "debate":
            result["short_answer"] = result["debate_answer"]

        return result

    def generate_risk_warning(self, text: str, credibility_note: str) -> Optional[str]:
        if credibility_note and credibility_note.strip().upper().startswith("RISK"):
            return credibility_note
        text_lower = text.lower()
        if any(w in text_lower for w in ["defund", "million", "40 million", "fabricat", "false"]):
            return "This source contains claims that may be disputed or misrepresented. Verify before responding publicly."
        return None

    def complete(self, prompt: str) -> str:
        return "[]"

    def verify_opponent_subject(self, sentence: str, opponent_name: str, candidate_name: str) -> str:
        # Mock can't reason about grammar — pass through so heuristic result stands.
        return "opponent"


# ── OpenAI provider ───────────────────────────────────────────────────────────

class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)
            self._model = model
        except ImportError as e:
            raise RuntimeError("openai package not installed. Run: pip install openai") from e

    def _chat(self, user_prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        kwargs: dict = {"model": self._model, "messages": messages}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self._client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit_exceeded" in err or "Rate limit" in err or "tokens per day" in err:
                raise ProviderRateLimitError(err) from e
            raise

    def summarize(self, text: str, max_words: int = 80) -> str:
        prompt = f"Summarize the following in at most {max_words} words. Return only the summary, no preamble.\n\n{text[:3000]}"
        try:
            return self._chat(prompt).strip()
        except Exception as e:
            log.warning("OpenAI summarize failed: %s", e)
            return MockLLMProvider().summarize(text, max_words)

    def classify_urgency(self, text: str) -> str:
        prompt = (
            "Classify the urgency of the following political campaign intelligence as 'high', 'medium', or 'low'. "
            "High: active attacks, fabricated claims, immediate threats to the campaign. "
            "Medium: ongoing concerns, competitor activity, rising issues. "
            "Low: background context, routine news.\n"
            "Return only the single word: high, medium, or low.\n\n" + text[:2000]
        )
        try:
            result = self._chat(prompt).strip().lower()
            if result in ("high", "medium", "low"):
                return result
        except Exception as e:
            log.warning("OpenAI classify_urgency failed: %s", e)
        return MockLLMProvider().classify_urgency(text)

    def extract_issues(self, text: str) -> list[str]:
        prompt = (
            "You are analyzing political campaign intelligence. "
            "Identify which of these issue categories are mentioned in the text: "
            "Healthcare, Economy & Jobs, Education, Housing, Infrastructure, "
            "Taxes & Budget, Immigration, Environment, Public Safety, "
            "Corruption & Ethics, Veterans, Social Issues.\n"
            "Return a JSON array of matching category names only. "
            "Return an empty array if none match clearly.\n\n"
            f"Text: {text[:2000]}"
        )
        try:
            raw = self._chat(prompt, json_mode=True)
            parsed = _parse_json_response(raw)
            if isinstance(parsed, list):
                return [s for s in parsed if isinstance(s, str)] or MockLLMProvider().extract_issues(text)
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        return [s for s in v if isinstance(s, str)] or MockLLMProvider().extract_issues(text)
        except Exception as e:
            log.warning("LLM extract_issues failed: %s", e)
        return MockLLMProvider().extract_issues(text)

    def detect_opponent_activity(self, text: str, opponent_name: str) -> dict:
        result: dict = {"claim": None, "attack": None, "promise": None, "contradiction_note": None, "repeated_theme": None}
        if not opponent_name or opponent_name.lower() not in text.lower():
            return result
        prompt = (
            f"Analyze this political text for activity by {opponent_name}. "
            "Return a JSON object with these keys (use null if not present):\n"
            '- "claim": a factual claim they made (1 sentence)\n'
            '- "attack": an attack or criticism they made (1 sentence)\n'
            '- "promise": a promise or pledge they made (1 sentence)\n'
            '- "contradiction_note": any contradiction with their past positions (1 sentence)\n'
            '- "repeated_theme": a recurring theme or talking point (3-5 words)\n\n'
            f"Text: {text[:2000]}"
        )
        try:
            raw = self._chat(prompt, json_mode=True)
            parsed = _parse_json_response(raw)
            if isinstance(parsed, dict):
                for key in result:
                    if parsed.get(key):
                        result[key] = str(parsed[key])
        except Exception as e:
            log.warning("LLM detect_opponent_activity failed: %s", e)
        return result

    def generate_talking_points(
        self,
        issue: str,
        tone: str,
        context: str = "",
        campaign_profile: Optional[dict] = None,
        sources: Optional[list[dict]] = None,
        opponent_activities: Optional[list[dict]] = None,
    ) -> dict:
        system = (
            "You are an expert political communication strategist. "
            "Generate campaign talking points that are evidence-grounded and ethically sound. "
            "Respond only with valid JSON."
        )
        user = _build_tp_prompt(issue, tone, context, campaign_profile, sources, opponent_activities)
        try:
            raw = self._chat(user, system_prompt=system, json_mode=True)
            result = _parse_json_response(raw)
            if result.get("short_answer"):
                return result
        except ProviderRateLimitError:
            raise
        except Exception as e:
            log.warning("OpenAI generate_talking_points failed: %s", e)
        return MockLLMProvider().generate_talking_points(issue, tone, context, campaign_profile, sources, opponent_activities)

    def generate_risk_warning(self, text: str, credibility_note: str) -> Optional[str]:
        prompt = (
            "You are a political campaign risk analyst. "
            "Review this content and identify any risks: unverified claims, "
            "misleading framing, legal exposure, or backfire potential. "
            "If there is a meaningful risk, return one concise sentence describing it. "
            "If the content is low-risk, return null.\n\n"
            f"Credibility note: {credibility_note or 'none'}\n"
            f"Content: {text[:1500]}"
        )
        try:
            raw = self._chat(prompt).strip()
            if raw and raw.lower() not in ("null", "none", "no risk", "low risk", ""):
                return raw
        except Exception as e:
            log.warning("LLM generate_risk_warning failed: %s", e)
        return None

    def complete(self, prompt: str) -> str:
        try:
            return self._chat(prompt)
        except ProviderRateLimitError:
            raise
        except Exception as e:
            log.warning("OpenAI complete failed: %s", e)
            return "[]"

    def verify_opponent_subject(self, sentence: str, opponent_name: str, candidate_name: str) -> str:
        prompt = (
            f"In the following political news sentence, who is the ACTOR performing the action?\n\n"
            f"Sentence: \"{sentence}\"\n\n"
            f'Candidate: "{candidate_name}"\n'
            f'Opponent: "{opponent_name}"\n\n'
            "Reply with exactly one word: opponent, candidate, both, or unclear."
        )
        try:
            raw = self._chat(prompt).strip().lower()
            if raw in ("opponent", "candidate", "both", "unclear"):
                return raw
        except Exception as e:
            log.warning("OpenAI verify_opponent_subject failed: %s", e)
        return "unclear"


# ── Anthropic provider ────────────────────────────────────────────────────────

class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        try:
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic(api_key=api_key)
            self._model = model
        except ImportError as e:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic") from e

    def _message(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
        kwargs: dict = {"model": self._model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        try:
            response = self._client.messages.create(**kwargs)
            return response.content[0].text if response.content else ""
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit_exceeded" in err or "Rate limit" in err or "overloaded" in err:
                raise ProviderRateLimitError(err) from e
            raise

    def summarize(self, text: str, max_words: int = 80) -> str:
        prompt = f"Summarize the following in at most {max_words} words. Return only the summary.\n\n{text[:3000]}"
        try:
            return self._message(prompt, max_tokens=300).strip()
        except Exception as e:
            log.warning("Anthropic summarize failed: %s", e)
            return MockLLMProvider().summarize(text, max_words)

    def classify_urgency(self, text: str) -> str:
        prompt = (
            "Classify the urgency of the following political campaign intelligence. "
            "Respond with exactly one word: high, medium, or low.\n\n" + text[:2000]
        )
        try:
            result = self._message(prompt, max_tokens=10).strip().lower()
            if result in ("high", "medium", "low"):
                return result
        except Exception as e:
            log.warning("Anthropic classify_urgency failed: %s", e)
        return MockLLMProvider().classify_urgency(text)

    def extract_issues(self, text: str) -> list[str]:
        prompt = (
            "Identify which of these issue categories are mentioned in the political text: "
            "Healthcare, Economy & Jobs, Education, Housing, Infrastructure, "
            "Taxes & Budget, Immigration, Environment, Public Safety, "
            "Corruption & Ethics, Veterans, Social Issues.\n"
            "Return only a JSON array of matching category names.\n\n"
            f"Text: {text[:2000]}"
        )
        try:
            raw = self._message(prompt, max_tokens=200).strip()
            parsed = _parse_json_response(raw)
            if isinstance(parsed, list):
                return [s for s in parsed if isinstance(s, str)] or MockLLMProvider().extract_issues(text)
        except Exception as e:
            log.warning("Anthropic extract_issues failed: %s", e)
        return MockLLMProvider().extract_issues(text)

    def detect_opponent_activity(self, text: str, opponent_name: str) -> dict:
        result: dict = {"claim": None, "attack": None, "promise": None, "contradiction_note": None, "repeated_theme": None}
        if not opponent_name or opponent_name.lower() not in text.lower():
            return result
        prompt = (
            f"Analyze this text for activity by {opponent_name}. "
            'Return JSON with keys: "claim", "attack", "promise", "contradiction_note", "repeated_theme" (null if absent).\n\n'
            f"Text: {text[:2000]}"
        )
        try:
            raw = self._message(prompt, max_tokens=400)
            parsed = _parse_json_response(raw)
            if isinstance(parsed, dict):
                for key in result:
                    if parsed.get(key):
                        result[key] = str(parsed[key])
        except Exception as e:
            log.warning("Anthropic detect_opponent_activity failed: %s", e)
        return result

    def generate_talking_points(
        self,
        issue: str,
        tone: str,
        context: str = "",
        campaign_profile: Optional[dict] = None,
        sources: Optional[list[dict]] = None,
        opponent_activities: Optional[list[dict]] = None,
    ) -> dict:
        system = (
            "You are an expert political communication strategist. "
            "Generate campaign talking points that are evidence-grounded and ethically sound. "
            "Respond only with valid JSON — no explanation, no markdown fences."
        )
        user = _build_tp_prompt(issue, tone, context, campaign_profile, sources, opponent_activities)
        try:
            raw = self._message(user, system=system, max_tokens=2000)
            result = _parse_json_response(raw)
            if result.get("short_answer"):
                return result
        except Exception as e:
            log.warning("Anthropic generate_talking_points failed: %s", e)
        return MockLLMProvider().generate_talking_points(issue, tone, context, campaign_profile, sources, opponent_activities)

    def generate_risk_warning(self, text: str, credibility_note: str) -> Optional[str]:
        prompt = (
            "Review this political content for campaign risks: unverified claims, "
            "misleading framing, legal exposure, or backfire potential. "
            "If there is a meaningful risk, return one sentence describing it. "
            "If low-risk, return only the word null.\n\n"
            f"Credibility note: {credibility_note or 'none'}\nContent: {text[:1500]}"
        )
        try:
            raw = self._message(prompt, max_tokens=150).strip()
            if raw and raw.lower() not in ("null", "none", "no risk", "low risk", ""):
                return raw
        except Exception as e:
            log.warning("Anthropic generate_risk_warning failed: %s", e)
        return None

    def complete(self, prompt: str) -> str:
        try:
            return self._message(prompt, max_tokens=1000)
        except ProviderRateLimitError:
            raise
        except Exception as e:
            log.warning("Anthropic complete failed: %s", e)
            return "[]"

    def verify_opponent_subject(self, sentence: str, opponent_name: str, candidate_name: str) -> str:
        prompt = (
            f"In the following political news sentence, who is the ACTOR performing the action?\n\n"
            f"Sentence: \"{sentence}\"\n\n"
            f'Candidate: "{candidate_name}"\n'
            f'Opponent: "{opponent_name}"\n\n'
            "Reply with exactly one word: opponent, candidate, both, or unclear."
        )
        try:
            raw = self._message(prompt, max_tokens=10).strip().lower()
            if raw in ("opponent", "candidate", "both", "unclear"):
                return raw
        except Exception as e:
            log.warning("Anthropic verify_opponent_subject failed: %s", e)
        return "unclear"


# ── Provider factory ──────────────────────────────────────────────────────────

class GroqProvider(OpenAIProvider):
    """Groq — OpenAI-compatible API with free tier. Sign up at console.groq.com."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
            self._model = model
        except ImportError as e:
            raise RuntimeError("openai package not installed. Run: pip install openai") from e


class GeminiProvider(OpenAIProvider):
    """Google Gemini — free tier with 1M tokens/day. Get a key at aistudio.google.com."""

    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            self._model = model
        except ImportError as e:
            raise RuntimeError("openai package not installed. Run: pip install openai") from e


# ── Fallback provider chain ───────────────────────────────────────────────────

class FallbackProvider(BaseLLMProvider):
    """Round-robins across a list of providers, rotating on ProviderRateLimitError.

    Starts each call from the next provider in the rotation so load is spread
    evenly across all keys from the first request — no single key gets hammered
    until it exhausts its per-minute quota before the others are touched.

    When all providers are exhausted it falls back to Mock so the app stays
    functional, but logs a clear warning.
    """

    def __init__(self, providers: list[BaseLLMProvider]):
        import threading
        self._providers = providers
        self._next = 0
        self._lock = threading.Lock()
        # Tracks which provider indices are known-exhausted this session.
        # Cleared after _EXHAUSTED_TTL_SECONDS so daily limits can recover.
        self._exhausted: set[int] = set()
        self._exhausted_at: dict[int, float] = {}

    _EXHAUSTED_TTL_SECONDS = 3600  # forget exhausted status after 1 hour

    def _start_index(self) -> int:
        with self._lock:
            idx = self._next % len(self._providers)
            self._next += 1
            return idx

    def _is_exhausted(self, idx: int) -> bool:
        import time
        if idx not in self._exhausted:
            return False
        if time.time() - self._exhausted_at.get(idx, 0) > self._EXHAUSTED_TTL_SECONDS:
            self._exhausted.discard(idx)
            self._exhausted_at.pop(idx, None)
            return False
        return True

    def _call(self, method: str, *args, **kwargs):
        import time
        n = len(self._providers)
        start = self._start_index()
        for i in range(n):
            idx = (start + i) % n
            if self._is_exhausted(idx):
                continue
            p = self._providers[idx]
            try:
                return getattr(p, method)(*args, **kwargs)
            except ProviderRateLimitError as e:
                log.warning(
                    "FallbackProvider: %s[%d] rate-limited — trying next provider",
                    type(p).__name__, idx,
                )
                self._exhausted.add(idx)
                self._exhausted_at[idx] = time.time()
                continue
        # All known-good providers failed — reset exhausted set and try once more
        # (limits may have recovered)
        if self._exhausted:
            log.info("FallbackProvider: all providers exhausted, resetting for retry")
            self._exhausted.clear()
            self._exhausted_at.clear()
            for i in range(n):
                idx = (start + i) % n
                p = self._providers[idx]
                try:
                    return getattr(p, method)(*args, **kwargs)
                except ProviderRateLimitError:
                    continue
        log.warning("FallbackProvider: all providers rate-limited for %s — using mock", method)
        return getattr(MockLLMProvider(), method)(*args, **kwargs)

    def complete(self, prompt: str) -> str:
        return self._call("complete", prompt)

    def summarize(self, text: str, max_words: int = 80) -> str:
        return self._call("summarize", text, max_words)

    def classify_urgency(self, text: str) -> str:
        return self._call("classify_urgency", text)

    def extract_issues(self, text: str) -> list[str]:
        return self._call("extract_issues", text)

    def detect_opponent_activity(self, text: str, opponent_name: str) -> dict:
        return self._call("detect_opponent_activity", text, opponent_name)

    def generate_talking_points(self, issue: str, tone: str, context: str = "",
                                campaign_profile: Optional[dict] = None,
                                sources: Optional[list[dict]] = None,
                                opponent_activities: Optional[list[dict]] = None) -> dict:
        return self._call("generate_talking_points", issue, tone, context,
                          campaign_profile, sources, opponent_activities)

    def generate_risk_warning(self, text: str, credibility_note: str) -> Optional[str]:
        return self._call("generate_risk_warning", text, credibility_note)

    def verify_opponent_subject(self, sentence: str, opponent_name: str, candidate_name: str) -> str:
        return self._call("verify_opponent_subject", sentence, opponent_name, candidate_name)


_MOCK_FALLBACK_BANNER = (
    "================================================================\n"
    "  LLM FALLBACK TO MOCK PROVIDER — AI SCORING IS DISABLED\n"
    "  Articles will be marked irrelevant and the UI will look empty.\n"
    "  Reason: %s\n"
    "  Fix: set LLM_PROVIDER=groq and GROQ_API_KEY in backend/.env\n"
    "================================================================"
)


def _fallback_to_mock(reason: str) -> "MockLLMProvider":
    log.warning(_MOCK_FALLBACK_BANNER, reason)
    return MockLLMProvider()


def get_provider_status() -> dict:
    """Return which LLM provider is active and whether it's the mock fallback."""
    provider = get_provider()
    is_mock = isinstance(provider, MockLLMProvider)
    configured = os.environ.get("LLM_PROVIDER", "").lower().strip() or "unset"
    return {
        "configured_provider": configured,
        "active_provider": "mock" if is_mock else configured,
        "is_mock": is_mock,
    }


_provider_singleton: "BaseLLMProvider | None" = None


def get_provider() -> BaseLLMProvider:
    """Return the module-level provider singleton, building it once on first call.

    Singleton keeps FallbackProvider's exhausted-key state alive across requests,
    so once a Groq key hits its daily limit it is skipped for all future calls
    (for up to 1 hour) without retrying it each time.

    Priority order when LLM_PROVIDER=groq (the default):
      1. GROQ_API_KEY (primary)
      2. GROQ_API_KEY_2, GROQ_API_KEY_3, … (additional Groq accounts — each has its own daily quota)
      3. GEMINI_API_KEY (if set) — 1M tokens/day free via Google AI Studio
      4. ANTHROPIC_API_KEY (if set) — kicks in when all Groq keys are exhausted
      5. OPENAI_API_KEY (if set)
      6. Mock (always last — keeps the app functional with no AI scoring)

    Each provider is only tried when the previous one returns a 429 rate-limit error.
    Other errors (network, bad response) are handled inside each provider and return
    safe defaults without triggering the fallback.
    """
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton

    provider_name = os.environ.get("LLM_PROVIDER", "").lower().strip()

    if not provider_name:
        return _fallback_to_mock("LLM_PROVIDER env var is not set")

    if provider_name == "mock":
        return MockLLMProvider()

    providers: list[BaseLLMProvider] = []

    # ── Groq keys (primary + any extras) ──────────────────────────────────────
    if provider_name == "groq":
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        # Collect GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3, … (up to 5 extras)
        groq_keys: list[str] = []
        primary = os.environ.get("GROQ_API_KEY", "").strip()
        if primary:
            groq_keys.append(primary)
        for n in range(2, 7):
            extra = os.environ.get(f"GROQ_API_KEY_{n}", "").strip()
            if extra:
                groq_keys.append(extra)

        if not groq_keys:
            return _fallback_to_mock("LLM_PROVIDER=groq but no GROQ_API_KEY set")

        for key in groq_keys:
            try:
                providers.append(GroqProvider(api_key=key, model=model))
            except RuntimeError as e:
                log.warning("Groq provider init failed: %s", e)

    # ── OpenAI ─────────────────────────────────────────────────────────────────
    elif provider_name == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return _fallback_to_mock("LLM_PROVIDER=openai but OPENAI_API_KEY not set")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        try:
            providers.append(OpenAIProvider(api_key=api_key, model=model))
        except RuntimeError as e:
            return _fallback_to_mock(f"OpenAI provider unavailable: {e}")

    # ── Anthropic ──────────────────────────────────────────────────────────────
    elif provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return _fallback_to_mock("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY not set")
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        try:
            providers.append(AnthropicProvider(api_key=api_key, model=model))
        except RuntimeError as e:
            return _fallback_to_mock(f"Anthropic provider unavailable: {e}")

    else:
        return _fallback_to_mock(f"Unknown LLM_PROVIDER value: {provider_name!r}")

    # ── Append cross-provider fallbacks (always, when primary is Groq) ─────────
    if provider_name == "groq":
        # Google Gemini: 1M tokens/day free — add whenever GEMINI_API_KEY is set
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key:
            gemini_model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
            try:
                providers.append(GeminiProvider(api_key=gemini_key, model=gemini_model))
                log.info("LLM fallback chain includes Gemini (%s)", gemini_model)
            except RuntimeError as e:
                log.warning("Gemini provider init failed: %s", e)

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key:
            anthropic_model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            try:
                providers.append(AnthropicProvider(api_key=anthropic_key, model=anthropic_model))
                log.info("LLM fallback chain: Groq × %d key(s) → Anthropic", len([p for p in providers if isinstance(p, GroqProvider)]))
            except RuntimeError:
                pass

        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key and not anthropic_key:
            openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            try:
                providers.append(OpenAIProvider(api_key=openai_key, model=openai_model))
            except RuntimeError:
                pass

    if not providers:
        _provider_singleton = _fallback_to_mock("no providers could be initialized")
        return _provider_singleton

    result = providers[0] if len(providers) == 1 else FallbackProvider(providers)
    _provider_singleton = result
    return result
