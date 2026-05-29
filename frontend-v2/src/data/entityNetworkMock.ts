/**
 * Hand-crafted entity-network mock data for the Entity Network page.
 *
 * This is a UX SKETCH — it represents what a real entity-extraction pipeline
 * would surface from your 2,367 race-relevant articles. Entities/relationships
 * are based on real PA-08 race context; values like mention counts and
 * confidence are realistic-looking placeholders.
 *
 * Once the real pipeline lands, this file is deleted; the page swaps to a
 * /api/entity-network endpoint with the same shape.
 */

export type EntityType =
  | 'person'
  | 'organization'
  | 'bill'
  | 'event'
  | 'location'

export type Affiliation = 'D' | 'R' | 'I' | null

export type RelationType =
  | 'endorses'
  | 'attacks'
  | 'criticizes'
  | 'voted_for'
  | 'voted_against'
  | 'co_sponsored'
  | 'represents'
  | 'mayor_of'
  | 'predecessor_of'
  | 'attended'
  | 'leads'
  | 'member_of'
  | 'opposes_policy_of'
  | 'allies_with'
  | 'donated_to'

export interface Entity {
  id: string
  name: string
  type: EntityType
  description: string
  affiliation: Affiliation
  mention_count: number
  recent_article_titles: string[]   // sample articles for the side panel
  first_seen: string  // ISO date — for timeline mini-chart
  last_seen: string
  // Type-specific metadata. For events this includes event_date, event_location,
  // event_type, date_observations (cross-document reconciliation),
  // date_disagreement (true when articles disagree on the date). For persons
  // it includes role, state. Surfaced by /api/entity-network as `metadata`.
  metadata?: {
    event_date?: string | null
    event_location?: string | null
    event_type?: string | null
    date_observations?: Array<{ date: string }>
    date_disagreement?: boolean
    role?: string
    state?: string
    [key: string]: unknown
  }
}

export interface Relation {
  id: string
  source: string  // entity id
  target: string  // entity id
  type: RelationType
  weight: number  // # of articles supporting this relation
  sample_quote: string
  first_seen: string
  last_seen: string
  // Temporal validity — set on role-type predicates (represents, member_of,
  // predecessor_of). Most extracted event-type predicates leave these null.
  valid_from?: string | null
  valid_to?: string | null
  is_expired?: boolean
  // Claim-layer reference (added v14.6) — lets the UI open the claim
  // inspector by clicking an edge.
  claim_id?: number | null
  claim_status?: 'active' | 'contested' | 'retracted'
}

// v15.0 — quote-anchored claim record. Replaces the triple-shaped Claim
// for new extractions. Each record is a verbatim text span from an
// article, tagged with the canonical entities that appear in it.
// Surfaced in the EntityNetwork side panel as the "Claims" section.
export type ClaimLabel =
  | 'statement'
  | 'attack'
  | 'defense'
  | 'endorsement'
  | 'policy_position'
  | 'vote'
  | 'announcement'
  | 'commitment'

export interface ClaimRecord {
  id: number
  article_id: number
  evidence_span: string
  evidence_start_char: number | null
  evidence_end_char: number | null
  evidence_hash: string
  label: ClaimLabel | null
  confidence: 'high' | 'medium' | 'low'
  extractor_version: string | null
  created_at: string | null
  entities: Array<{
    id: string             // canonical_id
    name: string
    type: EntityType
    affiliation: Affiliation
  }>
  article: {
    id: number
    title: string | null
    source_url: string | null
    source_name: string | null
    published_at: string | null
  } | null
}

export interface ClaimRecordsResponse {
  entity?: {
    id: string
    name: string
    type: EntityType
    affiliation: Affiliation
  }
  count: number
  records: ClaimRecord[]
}

export const entities: Entity[] = [
  // ── Candidate / Opponent ──────────────────────────────────────────────
  {
    id: 'cognetti',
    name: 'Paige Cognetti',
    type: 'person',
    description: 'Democratic candidate, PA-08. Mayor of Scranton.',
    affiliation: 'D',
    mention_count: 1248,
    first_seen: '2026-01-08',
    last_seen: '2026-05-25',
    recent_article_titles: [
      "Cognetti accuses Bresnahan of 'public corruption' over stock trades",
      'Scranton mayor Cognetti announces congressional bid',
      'EMILYs List endorses Paige Cognetti for PA-08',
    ],
  },
  {
    id: 'bresnahan',
    name: 'Rob Bresnahan',
    type: 'person',
    description: 'Republican incumbent, PA-08.',
    affiliation: 'R',
    mention_count: 1087,
    first_seen: '2026-01-08',
    last_seen: '2026-05-25',
    recent_article_titles: [
      "Bresnahan puts more guardrails on stock trades after scrutiny",
      'Bresnahan welcomes JD Vance to Scranton',
      'Unearthed audio contradicts Bresnahan stock trading claims',
    ],
  },

  // ── National political figures ────────────────────────────────────────
  {
    id: 'trump',
    name: 'Donald Trump',
    type: 'person',
    description: 'Former / 47th President (R). Key partisan figure shaping national framing.',
    affiliation: 'R',
    mention_count: 894,
    first_seen: '2026-01-08',
    last_seen: '2026-05-25',
    recent_article_titles: [
      "Trump's speech on inflation turns to grievances about immigrants",
      'Trump support cracking among GOP voters on key issue',
      'Trump tax cuts ignored Medicare, Vance says in Luzerne County',
    ],
  },
  {
    id: 'shapiro',
    name: 'Josh Shapiro',
    type: 'person',
    description: 'Governor of Pennsylvania (D).',
    affiliation: 'D',
    mention_count: 412,
    first_seen: '2026-01-12',
    last_seen: '2026-05-22',
    recent_article_titles: [
      'Shapiro headlines fundraiser for Cognetti in Wilkes-Barre',
      "Governor's office criticizes federal Medicaid cuts",
    ],
  },
  {
    id: 'vance',
    name: 'JD Vance',
    type: 'person',
    description: 'Vice President (R).',
    affiliation: 'R',
    mention_count: 287,
    first_seen: '2026-02-04',
    last_seen: '2026-05-23',
    recent_article_titles: [
      'Vance touts Trump tax cuts in Luzerne County visit',
      'Vance rallies for Bresnahan re-election',
    ],
  },
  {
    id: 'cartwright',
    name: 'Matt Cartwright',
    type: 'person',
    description: 'Former PA-08 representative (D). Lost seat to Bresnahan in 2024.',
    affiliation: 'D',
    mention_count: 156,
    first_seen: '2026-01-15',
    last_seen: '2026-05-10',
    recent_article_titles: [
      'Cartwright endorses Cognetti, sees parallels to his own campaign',
      'Former Rep. Cartwright joins Cognetti at union hall',
    ],
  },
  {
    id: 'johnson',
    name: 'Mike Johnson',
    type: 'person',
    description: 'Speaker of the House (R).',
    affiliation: 'R',
    mention_count: 198,
    first_seen: '2026-01-09',
    last_seen: '2026-05-20',
    recent_article_titles: [
      "Republican 'extortionists' have usurped Mike Johnson's power",
      'Johnson defends Bresnahan vote on union bill',
    ],
  },
  {
    id: 'jeffries',
    name: 'Hakeem Jeffries',
    type: 'person',
    description: 'House Minority Leader (D).',
    affiliation: 'D',
    mention_count: 134,
    first_seen: '2026-01-18',
    last_seen: '2026-05-19',
    recent_article_titles: [
      'Jeffries: GOP defectors prove healthcare cuts are unpopular',
      'Black Caucus amplifies calls against gerrymandering',
    ],
  },

  // ── Organizations ─────────────────────────────────────────────────────
  {
    id: 'nrcc',
    name: 'NRCC',
    type: 'organization',
    description: 'National Republican Congressional Committee. Republican House campaign arm.',
    affiliation: 'R',
    mention_count: 318,
    first_seen: '2026-01-10',
    last_seen: '2026-05-25',
    recent_article_titles: [
      "Even the NYT knows how corrupt Cognetti is — NRCC release",
      'NRCC launches ad targeting Cognetti on Scranton crime',
    ],
  },
  {
    id: 'dccc',
    name: 'DCCC',
    type: 'organization',
    description: 'Democratic Congressional Campaign Committee.',
    affiliation: 'D',
    mention_count: 287,
    first_seen: '2026-01-11',
    last_seen: '2026-05-24',
    recent_article_titles: [
      'DCCC names PA-08 a Top 10 Red-to-Blue opportunity',
      'Iowans crushed under Miller-Meeks healthcare cuts — DCCC',
    ],
  },
  {
    id: 'emilys-list',
    name: "EMILY's List",
    type: 'organization',
    description: 'Pro-choice Democratic women candidate PAC.',
    affiliation: 'D',
    mention_count: 92,
    first_seen: '2026-01-08',
    last_seen: '2026-05-15',
    recent_article_titles: [
      "EMILY's List endorses Paige Cognetti for PA-08",
    ],
  },
  {
    id: 'club-for-growth',
    name: 'Club for Growth',
    type: 'organization',
    description: 'Conservative anti-tax advocacy PAC.',
    affiliation: 'R',
    mention_count: 74,
    first_seen: '2026-02-20',
    last_seen: '2026-05-18',
    recent_article_titles: [
      'Club for Growth scores Bresnahan A+ on tax votes',
    ],
  },
  {
    id: 'aflcio',
    name: 'AFL-CIO',
    type: 'organization',
    description: 'Labor federation.',
    affiliation: 'D',
    mention_count: 68,
    first_seen: '2026-03-14',
    last_seen: '2026-05-22',
    recent_article_titles: [
      'NEPA AFL-CIO chapter backs Cognetti over Bresnahan',
      'Union members deliver petition to Bresnahan office',
    ],
  },
  {
    id: 'freedom-caucus',
    name: 'House Freedom Caucus',
    type: 'organization',
    description: 'Conservative House Republican caucus.',
    affiliation: 'R',
    mention_count: 62,
    first_seen: '2026-02-01',
    last_seen: '2026-05-17',
    recent_article_titles: [
      "Republican 'extortionists' have usurped Mike Johnson's power",
    ],
  },

  // ── Bills / policy issues ─────────────────────────────────────────────
  {
    id: 'aca-subsidies',
    name: 'ACA Subsidy Extension',
    type: 'bill',
    description: 'Bipartisan bill to extend Affordable Care Act premium subsidies.',
    affiliation: null,
    mention_count: 187,
    first_seen: '2026-02-15',
    last_seen: '2026-05-24',
    recent_article_titles: [
      'House passes ACA subsidy extension with GOP help',
      'Congress leaves town without ACA deal — premium hikes coming',
    ],
  },
  {
    id: 'stock-act',
    name: 'Stock Trading Ban (STOCK Act reform)',
    type: 'bill',
    description: 'Bipartisan effort to ban Congressional stock trading.',
    affiliation: null,
    mention_count: 142,
    first_seen: '2026-01-20',
    last_seen: '2026-05-23',
    recent_article_titles: [
      'Cognetti signs discharge petition for stock trading ban',
      'Bresnahan reform pledge under scrutiny',
    ],
  },
  {
    id: 'union-bill',
    name: 'Union Crackdown Reversal',
    type: 'bill',
    description: 'Bill reversing Trump-era labor restrictions. 13 House Republicans crossed over.',
    affiliation: null,
    mention_count: 89,
    first_seen: '2026-03-08',
    last_seen: '2026-05-22',
    recent_article_titles: [
      "13 House Republicans join Democrats to advance bill reversing Trump's union crackdown",
    ],
  },
  {
    id: 'medicaid-cuts',
    name: 'Federal Medicaid Cuts',
    type: 'bill',
    description: 'Proposed reductions to Medicaid funding tied to budget reconciliation.',
    affiliation: null,
    mention_count: 156,
    first_seen: '2026-02-01',
    last_seen: '2026-05-24',
    recent_article_titles: [
      'Protesters at Bresnahan office over Medicaid cuts',
      'Rural towns hit hardest by Medicaid scaleback',
    ],
  },

  // ── Events ────────────────────────────────────────────────────────────
  {
    id: 'cognetti-launch',
    name: 'Cognetti Campaign Launch',
    type: 'event',
    description: 'April 2025 launch event in Scranton.',
    affiliation: 'D',
    mention_count: 47,
    first_seen: '2026-04-09',
    last_seen: '2026-04-21',
    recent_article_titles: [
      "'Game on!' in PA-08: Cognetti, Bresnahan race off to a fast start",
    ],
  },
  {
    id: 'vance-luzerne',
    name: 'Vance Luzerne County Visit',
    type: 'event',
    description: 'VP Vance event with Bresnahan promoting Trump tax cuts.',
    affiliation: 'R',
    mention_count: 28,
    first_seen: '2026-04-15',
    last_seen: '2026-04-23',
    recent_article_titles: [
      'In Luzerne County, VP JD Vance touts Trump tax cuts, but ignores Medicare',
    ],
  },

  // ── Locations ─────────────────────────────────────────────────────────
  {
    id: 'scranton',
    name: 'Scranton',
    type: 'location',
    description: 'Largest city in PA-08. Cognetti is mayor.',
    affiliation: null,
    mention_count: 521,
    first_seen: '2026-01-08',
    last_seen: '2026-05-25',
    recent_article_titles: [
      'Stark numbers: Scranton mayor discusses uptick in violence',
      'Scranton crime spike dogs Dem mayor as she challenges GOP rep',
    ],
  },
  {
    id: 'wilkes-barre',
    name: 'Wilkes-Barre',
    type: 'location',
    description: 'Second-largest city in PA-08.',
    affiliation: null,
    mention_count: 234,
    first_seen: '2026-01-12',
    last_seen: '2026-05-22',
    recent_article_titles: [
      'Shapiro headlines fundraiser for Cognetti in Wilkes-Barre',
    ],
  },
  {
    id: 'luzerne',
    name: 'Luzerne County',
    type: 'location',
    description: 'PA-08 county. Contains Wilkes-Barre.',
    affiliation: null,
    mention_count: 167,
    first_seen: '2026-01-15',
    last_seen: '2026-05-23',
    recent_article_titles: [],
  },
]

export const relations: Relation[] = [
  // Candidate ↔ Opponent direct
  { id: 'r-1', source: 'cognetti', target: 'bresnahan', type: 'attacks',
    weight: 47, sample_quote: "'public corruption' — Cognetti on Bresnahan stock trades",
    first_seen: '2026-01-12', last_seen: '2026-05-24' },
  { id: 'r-2', source: 'bresnahan', target: 'cognetti', type: 'criticizes',
    weight: 23, sample_quote: "Bresnahan's campaign called Cognetti a 'failed mayor'",
    first_seen: '2026-01-18', last_seen: '2026-05-20' },

  // Endorsements / support of Cognetti
  { id: 'r-3', source: 'emilys-list', target: 'cognetti', type: 'endorses',
    weight: 12, sample_quote: "'A clear-eyed leader Pennsylvania needs' — EMILY's List endorsement",
    first_seen: '2026-01-08', last_seen: '2026-01-08' },
  { id: 'r-4', source: 'shapiro', target: 'cognetti', type: 'endorses',
    weight: 8, sample_quote: 'Governor Shapiro: Cognetti will fight for working families',
    first_seen: '2026-02-14', last_seen: '2026-04-12' },
  { id: 'r-5', source: 'dccc', target: 'cognetti', type: 'endorses',
    weight: 18, sample_quote: 'DCCC adds Cognetti to Red-to-Blue program',
    first_seen: '2026-01-25', last_seen: '2026-05-15' },
  { id: 'r-6', source: 'aflcio', target: 'cognetti', type: 'endorses',
    weight: 9, sample_quote: 'NEPA AFL-CIO unanimously backs Cognetti',
    first_seen: '2026-03-14', last_seen: '2026-03-14' },
  { id: 'r-7', source: 'cartwright', target: 'cognetti', type: 'endorses',
    weight: 5, sample_quote: 'Cartwright: she\'ll win back what we lost',
    first_seen: '2026-02-08', last_seen: '2026-02-08' },

  // Attacks on Cognetti
  { id: 'r-8', source: 'nrcc', target: 'cognetti', type: 'attacks',
    weight: 34, sample_quote: 'NRCC ad: Cognetti let crime spike in Scranton',
    first_seen: '2026-01-22', last_seen: '2026-05-24' },

  // Endorsements / support of Bresnahan
  { id: 'r-9', source: 'club-for-growth', target: 'bresnahan', type: 'endorses',
    weight: 6, sample_quote: 'Club for Growth A+ rating',
    first_seen: '2026-02-20', last_seen: '2026-02-20' },
  { id: 'r-10', source: 'trump', target: 'bresnahan', type: 'allies_with',
    weight: 27, sample_quote: 'Trump endorsement: Bresnahan a "champion"',
    first_seen: '2026-01-15', last_seen: '2026-05-10' },
  { id: 'r-11', source: 'vance', target: 'bresnahan', type: 'allies_with',
    weight: 14, sample_quote: 'Vance rallies for Bresnahan in Luzerne County',
    first_seen: '2026-04-15', last_seen: '2026-04-23' },

  // Attacks on Bresnahan
  { id: 'r-12', source: 'dccc', target: 'bresnahan', type: 'attacks',
    weight: 22, sample_quote: 'DCCC: Bresnahan voted to cut his own taxes',
    first_seen: '2026-02-01', last_seen: '2026-05-22' },
  { id: 'r-13', source: 'aflcio', target: 'bresnahan', type: 'criticizes',
    weight: 7, sample_quote: 'Union members deliver petition to Bresnahan office',
    first_seen: '2026-04-10', last_seen: '2026-04-30' },
  { id: 'r-14', source: 'jeffries', target: 'bresnahan', type: 'criticizes',
    weight: 5, sample_quote: 'Jeffries: Bresnahan failed his constituents on ACA',
    first_seen: '2026-03-20', last_seen: '2026-05-19' },

  // Geography / role
  { id: 'r-15', source: 'cognetti', target: 'scranton', type: 'mayor_of',
    weight: 245, sample_quote: 'Scranton Mayor Paige Cognetti',
    first_seen: '2026-01-08', last_seen: '2026-05-25' },
  { id: 'r-16', source: 'bresnahan', target: 'luzerne', type: 'represents',
    weight: 112, sample_quote: 'Rep. Bresnahan, who represents Luzerne County',
    first_seen: '2026-01-09', last_seen: '2026-05-24' },
  { id: 'r-17', source: 'cartwright', target: 'bresnahan', type: 'predecessor_of',
    weight: 24, sample_quote: 'Bresnahan unseated Cartwright in 2024',
    first_seen: '2026-01-09', last_seen: '2026-04-12' },

  // Bills — voting positions
  { id: 'r-18', source: 'bresnahan', target: 'aca-subsidies', type: 'voted_against',
    weight: 18, sample_quote: 'Bresnahan voted no on ACA subsidy extension',
    first_seen: '2026-02-18', last_seen: '2026-05-23' },
  { id: 'r-19', source: 'bresnahan', target: 'medicaid-cuts', type: 'voted_for',
    weight: 23, sample_quote: 'Bresnahan voted in favor of Medicaid reductions',
    first_seen: '2026-02-15', last_seen: '2026-05-24' },
  { id: 'r-20', source: 'bresnahan', target: 'union-bill', type: 'voted_for',
    weight: 8, sample_quote: 'Bresnahan was one of 13 Republicans crossing over',
    first_seen: '2026-03-08', last_seen: '2026-03-12' },
  { id: 'r-21', source: 'cognetti', target: 'stock-act', type: 'co_sponsored',
    weight: 12, sample_quote: 'Cognetti pushes stock-trading ban from campaign trail',
    first_seen: '2026-02-01', last_seen: '2026-05-15' },
  { id: 'r-22', source: 'cognetti', target: 'aca-subsidies', type: 'voted_for',
    weight: 9, sample_quote: 'Cognetti backs ACA subsidy extension',
    first_seen: '2026-02-18', last_seen: '2026-05-23' },
  { id: 'r-23', source: 'cognetti', target: 'medicaid-cuts', type: 'voted_against',
    weight: 14, sample_quote: 'Cognetti opposes federal Medicaid cuts',
    first_seen: '2026-02-15', last_seen: '2026-05-24' },

  // Org/leader membership
  { id: 'r-24', source: 'johnson', target: 'freedom-caucus', type: 'opposes_policy_of',
    weight: 11, sample_quote: 'Freedom Caucus blocked Johnson on spending',
    first_seen: '2026-02-04', last_seen: '2026-05-17' },
  { id: 'r-25', source: 'bresnahan', target: 'freedom-caucus', type: 'member_of',
    weight: 4, sample_quote: 'Bresnahan signed onto Freedom Caucus letter',
    first_seen: '2026-03-12', last_seen: '2026-03-12' },

  // Events
  { id: 'r-26', source: 'vance', target: 'vance-luzerne', type: 'attended',
    weight: 12, sample_quote: 'VP Vance headlined the event',
    first_seen: '2026-04-15', last_seen: '2026-04-15' },
  { id: 'r-27', source: 'bresnahan', target: 'vance-luzerne', type: 'attended',
    weight: 11, sample_quote: 'Bresnahan introduced VP Vance',
    first_seen: '2026-04-15', last_seen: '2026-04-15' },
  { id: 'r-28', source: 'cognetti', target: 'cognetti-launch', type: 'attended',
    weight: 14, sample_quote: 'Cognetti announced bid at IBEW hall',
    first_seen: '2026-04-09', last_seen: '2026-04-09' },
  { id: 'r-29', source: 'shapiro', target: 'wilkes-barre', type: 'attended',
    weight: 5, sample_quote: 'Governor Shapiro fundraiser at Wilkes-Barre',
    first_seen: '2026-02-14', last_seen: '2026-02-14' },

  // National framing
  { id: 'r-30', source: 'trump', target: 'medicaid-cuts', type: 'allies_with',
    weight: 18, sample_quote: 'Trump administration backed reductions',
    first_seen: '2026-01-20', last_seen: '2026-05-22' },
  { id: 'r-31', source: 'trump', target: 'johnson', type: 'criticizes',
    weight: 9, sample_quote: 'Trump dings Johnson on spending vote',
    first_seen: '2026-03-15', last_seen: '2026-05-10' },
]

// Saved-query showcase — these would become natural-language entry points
// once the real pipeline lands. For the mockup they're just preset filters.
export interface SavedQuery {
  id: string
  label: string
  description: string
  // Which entity ids should be highlighted when run
  highlight_entities: string[]
  // Which relation types should be the only edges visible
  filter_relation_types?: RelationType[]
}

export const savedQueries: SavedQuery[] = [
  {
    id: 'q-endorsers-cognetti',
    label: 'Who endorsed Cognetti?',
    description: 'All orgs and individuals with an endorsing relationship.',
    highlight_entities: ['cognetti', 'emilys-list', 'shapiro', 'dccc', 'aflcio', 'cartwright'],
    filter_relation_types: ['endorses'],
  },
  {
    id: 'q-attackers-bresnahan',
    label: 'Who attacks Bresnahan?',
    description: 'Entities critical of Bresnahan in coverage.',
    highlight_entities: ['bresnahan', 'cognetti', 'dccc', 'aflcio', 'jeffries'],
    filter_relation_types: ['attacks', 'criticizes'],
  },
  {
    id: 'q-bresnahan-votes',
    label: "Bresnahan's voting record",
    description: 'Every bill Bresnahan has voted on.',
    highlight_entities: ['bresnahan', 'aca-subsidies', 'medicaid-cuts', 'union-bill'],
    filter_relation_types: ['voted_for', 'voted_against'],
  },
  {
    id: 'q-trump-network',
    label: "Trump's PA-08 footprint",
    description: 'Trump and everything he touches in this race.',
    highlight_entities: ['trump', 'bresnahan', 'vance', 'johnson', 'medicaid-cuts'],
  },
  {
    id: 'q-nrcc-attacks',
    label: 'NRCC attack vectors',
    description: 'Lines of attack the NRCC is using against Cognetti.',
    highlight_entities: ['nrcc', 'cognetti', 'scranton'],
    filter_relation_types: ['attacks'],
  },
]
