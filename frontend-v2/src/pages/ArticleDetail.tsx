import { ArrowLeft, ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '@/api/client'
import type { ArticleDetail as ArticleDetailType } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { formatArticleDateLong } from '@/lib/formatDate'

const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  accent: 'var(--accent)',
  red: 'var(--red)', green: 'var(--green)',
}

// Friendly labels for known fields in the structured_extraction blob.
// Anything not in this map falls through to title-cased key.
const STRUCTURED_LABELS: Record<string, string> = {
  one_sentence: 'AI one-sentence summary',
  framing: 'Framing',
  sentiment: 'Sentiment',
  relevance_score: 'Relevance score',
  relevant: 'Marked relevant',
  opponent_attacks: 'Opponent attacks',
  reason: 'AI rationale',
  claims: 'Claims',
  evidence: 'Evidence',
  notes: 'Notes',
  themes: 'Themes',
  topics: 'Topics',
}

// Fields we already display elsewhere on the page — skip rendering them
// again inside the structured-extraction section to avoid duplication.
const STRUCTURED_SKIP = new Set([
  'one_sentence',      // duplicates the Summary section
  'relevance_score',   // duplicates the Relevance chip
  'sentiment',         // duplicates the Sentiment chip
  'relevant',          // implied by score >= threshold
  'framing',           // duplicates the new Framing chip + section
])

function prettyKey(k: string): string {
  if (STRUCTURED_LABELS[k]) return STRUCTURED_LABELS[k]
  // Snake_case → Title Case
  return k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function relevanceColor(label?: string): string {
  switch (label) {
    case 'critical': return C.red
    case 'high': return '#fb923c'
    case 'medium': return '#fbbf24'
    case 'low': return C.text2
    default: return C.text3
  }
}

function sentimentColor(s?: string): string {
  switch (s) {
    case 'positive': return C.green
    case 'negative': return C.red
    case 'mixed': return '#fbbf24'
    default: return C.text2
  }
}

function prettifyPerspective(p?: string | null): string {
  switch (p) {
    case 'pro_candidate': return 'Pro-candidate'
    case 'pro_opponent': return 'Pro-opponent'
    case 'neutral': return 'Neutral'
    default: return p || '—'
  }
}

function perspectiveColor(p?: string | null): string {
  switch (p) {
    case 'pro_candidate': return 'var(--candidate)'
    case 'pro_opponent': return 'var(--opponent)'
    default: return C.text2
  }
}

function prettifyFraming(f?: string | null): string {
  switch (f) {
    case 'helps_candidate': return 'Helps candidate'
    case 'hurts_candidate': return 'Hurts candidate'
    case 'opponent_news': return 'Pro-opponent news'
    case 'background': return 'Background'
    case 'irrelevant': return 'Not relevant'
    default: return f || '—'
  }
}

function framingColor(f?: string | null): string {
  switch (f) {
    case 'helps_candidate': return 'var(--candidate)'
    case 'hurts_candidate': return C.red
    case 'opponent_news': return 'var(--opponent)'
    default: return C.text2
  }
}

// ─────────────────────────────────────────────────────────────────────────────

export function ArticleDetail() {
  const { id } = useParams<{ id: string }>()
  const articleId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const { user } = useAuth()
  const isAdmin = !!user?.isAdmin
  // React Router gives the initial route a key of 'default'. Any later
  // in-app navigation gets a unique key, so a non-'default' key means the
  // user arrived here from another page in this session — going back via
  // browser history will land them where they came from. Direct hits
  // (bookmark, pasted URL, fresh tab) keep the old fallback to /articles.
  const hasInAppHistory = location.key !== 'default'
  const backLabel = hasInAppHistory ? 'Back' : 'Back to articles'

  function handleBack() {
    if (hasInAppHistory) navigate(-1)
    else navigate('/articles')
  }

  const backButtonStyle: React.CSSProperties = {
    background: 'transparent', border: 'none', padding: 0,
    font: 'inherit',
    color: C.text2, fontSize: 13, cursor: 'pointer',
    display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 16,
    textAlign: 'left',
  }

  const [data, setData] = useState<ArticleDetailType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!articleId) return
    let cancelled = false
    setLoading(true); setError(null)
    api.articleDetail(articleId)
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) setError(e.message || String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [articleId])

  if (loading) {
    return (
      <div style={{ padding: '24px 28px', maxWidth: 900, margin: '0 auto' }}>
        <div className="skeleton" style={{ height: 20, width: 140, marginBottom: 18 }} />
        <div className="skeleton" style={{ height: 32, marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 100, marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 200, marginBottom: 12 }} />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div style={{ padding: '24px 28px', maxWidth: 900, margin: '0 auto' }}>
        <button onClick={handleBack} style={backButtonStyle}>
          <ArrowLeft size={14} /> {backLabel}
        </button>
        <div style={{ padding: 40, textAlign: 'center', color: C.text3 }}>
          {error ? `Failed to load article: ${error}` : 'Article not found.'}
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: '20px 28px 40px', maxWidth: 900, margin: '0 auto' }}>
      <button onClick={handleBack} style={backButtonStyle}>
        <ArrowLeft size={14} /> {backLabel}
      </button>

      {/* ── Header ── */}
      <header style={{ marginBottom: 22 }}>
        <h1 style={{
          fontSize: 26, fontWeight: 800, color: C.text1,
          margin: 0, lineHeight: 1.25,
        }}>
          {data.title}
        </h1>
        <div style={{
          marginTop: 10, fontSize: 13, color: C.text3,
          display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center',
        }}>
          {data.source_name && <span style={{ color: C.text2, fontWeight: 600 }}>{data.source_name}</span>}
          {data.source_author && <><span>·</span><span>by {data.source_author}</span></>}
          {data.published_at && <><span>·</span><span>{formatArticleDateLong(data.published_at)}</span></>}
          {data.source_type && <><span>·</span><span style={{ textTransform: 'uppercase', letterSpacing: '0.05em' }}>{data.source_type}</span></>}
        </div>
        {data.source_url && (
          <a
            href={data.source_url} target="_blank" rel="noreferrer"
            style={{
              marginTop: 12, display: 'inline-flex', alignItems: 'center', gap: 6,
              fontSize: 13, color: C.accent, textDecoration: 'none', fontWeight: 500,
            }}
          >
            Read original at source <ExternalLink size={12} />
          </a>
        )}
      </header>

      {/* ── Scoring chips ── */}
      <ChipRow>
        {isAdmin && data.race_relevance_label && (
          <Chip label="Relevance" value={data.race_relevance_label.toUpperCase()} color={relevanceColor(data.race_relevance_label)} />
        )}
        {data.framing && data.framing !== 'irrelevant' && (
          <Chip label="Framing" value={prettifyFraming(data.framing)} color={framingColor(data.framing)} />
        )}
        {data.sentiment && <Chip label="Sentiment" value={data.sentiment} color={sentimentColor(data.sentiment)} />}
        {data.actionability_label && <Chip label="Action" value={data.actionability_label} />}
        {data.urgency && <Chip label="Urgency" value={data.urgency} color={data.urgency === 'high' ? C.red : undefined} />}
        {data.priority_score != null && <Chip label="Priority" value={String(data.priority_score)} />}
        {data.geo_relevance && data.geo_relevance !== 'none' && <Chip label="Geo" value={data.geo_relevance} />}
      </ChipRow>

      {/* ── Summary ── */}
      {data.summary && (
        <Section title="Summary">
          <p style={{ margin: 0, fontSize: 15, lineHeight: 1.65, color: C.text1 }}>
            {data.summary}
          </p>
        </Section>
      )}

      {/* ── Full article text — shown by default. Scrollable if long. ── */}
      {data.raw_text && (
        <Section title={`Full article text · ${data.raw_text.length.toLocaleString()} chars`}>
          <div style={{
            padding: 16, background: C.bg2,
            border: `1px solid ${C.border}`, borderRadius: 8,
            fontSize: 14, lineHeight: 1.7, color: C.text1,
            maxHeight: 480, overflowY: 'auto',
            whiteSpace: 'pre-wrap',
          }}>
            {data.raw_text}
          </div>
        </Section>
      )}

      {/* ── Why it's relevant ── */}
      {data.relevance_reasons.length > 0 && (
        <Section title="Why it's relevant">
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14, lineHeight: 1.6, color: C.text2 }}>
            {data.relevance_reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </Section>
      )}

      {/* ── What's mentioned ── */}
      {(data.candidate_mentioned || data.opponent_mentioned || data.district_mentioned || data.priority_issue_mentioned) && (
        <Section title="What's mentioned">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {data.candidate_mentioned && <MiniTag>Candidate</MiniTag>}
            {data.opponent_mentioned && <MiniTag>Opponent</MiniTag>}
            {data.district_mentioned && <MiniTag>District</MiniTag>}
            {data.priority_issue_mentioned && <MiniTag>Priority issue</MiniTag>}
          </div>
        </Section>
      )}

      {/* ── Framing analysis ──
          The AI's per-article framing judgment (helps_candidate /
          hurts_candidate / opponent_news / background) is the precise
          headline signal. The lower-bucket perspective field is shown as
          a secondary detail since it powers landscape dot color but
          conflates "anti-candidate" with "pro-opponent". */}
      {(data.framing || data.perspective) && (
        <Section title="Framing analysis">
          {data.framing && (
            <KV k="Framing">
              <span style={{ color: framingColor(data.framing), fontWeight: 600 }}>
                {prettifyFraming(data.framing)}
              </span>
            </KV>
          )}
          {data.perspective && (
            <KV k="Perspective">
              <span style={{ color: perspectiveColor(data.perspective), fontWeight: 600 }}>
                {prettifyPerspective(data.perspective)}
              </span>
              <span style={{ marginLeft: 8, color: C.text3, fontSize: 11 }}>
                (3-bucket landscape signal)
              </span>
            </KV>
          )}
          {data.perspective_method && <KV k="Perspective method">{data.perspective_method}</KV>}
          {data.perspective_confidence && <KV k="Perspective confidence">{data.perspective_confidence}</KV>}
          {data.perspective_reason && <KV k="Perspective reasoning">{data.perspective_reason}</KV>}
        </Section>
      )}

      {/* ── Full AI analysis — pretty-rendered (no JSON dump). ── */}
      {data.structured_extraction && Object.keys(data.structured_extraction).length > 0 && (
        <Section title="Full AI analysis">
          <StructuredRenderer obj={data.structured_extraction} />
        </Section>
      )}

      {/* ── Opponent activity (claims/attacks/promises) ── */}
      {data.opponent_activities.length > 0 && (
        <Section title={`Opponent activity extracted (${data.opponent_activities.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.opponent_activities.map(oa => (
              <div key={oa.id} style={{
                padding: 12, background: C.bg2,
                border: `1px solid ${C.border}`, borderRadius: 6,
              }}>
                {oa.opponent_name && (
                  <div style={{ fontSize: 11, color: C.text3, marginBottom: 6, letterSpacing: '0.05em' }}>
                    {oa.opponent_name.toUpperCase()}
                  </div>
                )}
                {oa.attack && <Quote label="ATTACK" labelColor={C.red} text={oa.attack} />}
                {oa.claim && <Quote label="CLAIM" labelColor={C.text2} text={oa.claim} />}
                {oa.promise && <Quote label="PROMISE" labelColor="#f0a020" text={oa.promise} />}
                {oa.contradiction_note && (
                  <div style={{ marginTop: 8, fontSize: 12, color: '#f0a020', fontStyle: 'italic' }}>
                    ⚠ Contradicts: {oa.contradiction_note}
                  </div>
                )}
                {oa.repeated_theme && (
                  <div style={{ marginTop: 6, fontSize: 11, color: C.text3, fontFamily: 'monospace' }}>
                    THEME: {oa.repeated_theme}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ── Related issues ── */}
      {data.issue_mentions.length > 0 && (
        <Section title={`Related issues (${data.issue_mentions.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.issue_mentions.map(im => (
              <div key={im.issue_id} style={{
                padding: 10, background: C.bg2,
                border: `1px solid ${C.border}`, borderRadius: 6,
              }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ fontSize: 14, fontWeight: 600, color: C.text1 }}>
                    {im.name || `Issue #${im.issue_id}`}
                  </span>
                  {im.link_strength != null && (
                    <span style={{ fontSize: 11, color: C.text3 }}>
                      strength {im.link_strength}
                    </span>
                  )}
                </div>
                {im.summary && (
                  <div style={{ fontSize: 13, color: C.text2, marginTop: 4 }}>{im.summary}</div>
                )}
                {im.link_reasons.length > 0 && (
                  <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: 12, color: C.text3 }}>
                    {im.link_reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ── GDELT data ── */}
      {(data.gdelt_themes.length > 0 || data.gdelt_tone) && (
        <Section title="GDELT data">
          {data.gdelt_themes.length > 0 && (
            <div style={{ marginBottom: data.gdelt_tone ? 14 : 0 }}>
              <div style={{ fontSize: 11, color: C.text3, marginBottom: 6 }}>THEMES</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {data.gdelt_themes.map((t, i) => <MiniTag key={i}>{t}</MiniTag>)}
              </div>
            </div>
          )}
          {data.gdelt_tone && (
            <div>
              <div style={{ fontSize: 11, color: C.text3, marginBottom: 6 }}>TONE METRICS</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
                {Object.entries(data.gdelt_tone).filter(([, v]) => v != null).map(([k, v]) => (
                  <KV key={k} k={prettyKey(k)}>{typeof v === 'number' ? v.toFixed(1) : String(v)}</KV>
                ))}
              </div>
            </div>
          )}
        </Section>
      )}

      {/* ── Quality & credibility ── */}
      <Section title="Quality & credibility">
        {data.extraction_quality_label && <KV k="Extraction quality">{`${data.extraction_quality_label}${data.extraction_quality_score != null ? ` · ${data.extraction_quality_score}` : ''}`}</KV>}
        {data.extraction_quality_reasons.length > 0 && (
          <ul style={{ margin: '4px 0 8px 130px', paddingLeft: 16, fontSize: 12, color: C.text3 }}>
            {data.extraction_quality_reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        )}
        {data.source_credibility && <KV k="Source credibility">{data.source_credibility}</KV>}
        {data.credibility_score != null && <KV k="Credibility score">{data.credibility_score}</KV>}
        {data.credibility_note && <KV k="Credibility note">{data.credibility_note}</KV>}
        {data.source_owner_type && data.source_owner_type !== 'unclear' && (
          <KV k="Source owner">{`${data.source_owner_type} (${data.source_owner_confidence || 'unknown'} confidence)`}</KV>
        )}
        {data.publisher_domain && <KV k="Publisher domain">{data.publisher_domain}</KV>}
      </Section>

      {/* ── Footer: lifecycle / IDs ── */}
      <div style={{
        marginTop: 28, paddingTop: 14,
        borderTop: `1px solid ${C.border}`,
        fontSize: 11, color: C.text3,
        display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6,
      }}>
        <div>Article ID · {data.id}</div>
        <div>Status · {data.reviewed ? 'Reviewed' : 'Pending'}{data.dismissed ? ' · Dismissed' : ''}{data.archived_as_irrelevant ? ' · Archived' : ''}</div>
        {data.ingested_at && <div>Ingested · {formatArticleDateLong(data.ingested_at)}</div>}
        {data.content_category && data.content_category !== 'irrelevant' && <div>Category · {data.content_category}</div>}
      </div>
    </div>
  )
}

// ───── Structured-extraction renderer ─────

/**
 * Pretty-render the LLM's structured extraction blob. Known keys get
 * friendly labels (see STRUCTURED_LABELS); unknown keys get auto-prettified
 * from snake_case. Skip fields that are already shown elsewhere on the page
 * (one_sentence, relevance_score, sentiment).
 */
function StructuredRenderer({ obj }: { obj: Record<string, unknown> }) {
  const entries = Object.entries(obj).filter(([k, v]) => !STRUCTURED_SKIP.has(k) && !isEmpty(v))
  if (entries.length === 0) {
    return (
      <div style={{ fontSize: 13, color: C.text3, fontStyle: 'italic' }}>
        Nothing new beyond what's already shown above.
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {entries.map(([k, v]) => (
        <StructuredField key={k} label={prettyKey(k)} value={v} />
      ))}
    </div>
  )
}

function StructuredField({ label, value }: { label: string; value: unknown }) {
  // Boolean → Yes/No
  if (typeof value === 'boolean') {
    return <KV k={label}><span style={{ color: value ? C.green : C.text2 }}>{value ? 'Yes' : 'No'}</span></KV>
  }
  // Primitive (string/number) → simple key/value
  if (typeof value === 'string' || typeof value === 'number') {
    return <KV k={label}>{String(value)}</KV>
  }
  // Array
  if (Array.isArray(value)) {
    if (value.length === 0) return <KV k={label}><span style={{ color: C.text3 }}>None</span></KV>
    // Array of primitives → comma-separated tags
    if (value.every(v => typeof v === 'string' || typeof v === 'number')) {
      return (
        <KV k={label}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {value.map((v, i) => <MiniTag key={i}>{String(v)}</MiniTag>)}
          </div>
        </KV>
      )
    }
    // Array of objects → render each as a small card
    return (
      <div style={{ padding: '8px 0' }}>
        <div style={{ fontSize: 12, color: C.text3, marginBottom: 6, textTransform: 'capitalize' }}>{label}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {value.map((v, i) => (
            <div key={i} style={{
              padding: 10, background: C.bg2,
              border: `1px solid ${C.border}`, borderRadius: 6,
            }}>
              {typeof v === 'object' && v !== null ? (
                <StructuredRenderer obj={v as Record<string, unknown>} />
              ) : (
                String(v)
              )}
            </div>
          ))}
        </div>
      </div>
    )
  }
  // Nested object → recurse
  if (typeof value === 'object' && value !== null) {
    return (
      <div style={{ padding: '8px 0' }}>
        <div style={{ fontSize: 12, color: C.text3, marginBottom: 6, textTransform: 'capitalize' }}>{label}</div>
        <div style={{
          padding: 10, background: C.bg2,
          border: `1px solid ${C.border}`, borderRadius: 6,
        }}>
          <StructuredRenderer obj={value as Record<string, unknown>} />
        </div>
      </div>
    )
  }
  // Fallback
  return <KV k={label}>{String(value)}</KV>
}

function isEmpty(v: unknown): boolean {
  if (v == null) return true
  if (typeof v === 'string' && v.trim() === '') return true
  if (Array.isArray(v) && v.length === 0) return true
  if (typeof v === 'object' && Object.keys(v as object).length === 0) return true
  return false
}

// ───── Subcomponents ─────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: 24 }}>
      <h3 style={{
        margin: 0, marginBottom: 10,
        fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
        textTransform: 'uppercase', color: C.text3,
      }}>
        {title}
      </h3>
      <div>{children}</div>
    </section>
  )
}

function ChipRow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: 6,
      marginTop: 18,
    }}>
      {children}
    </div>
  )
}

function Chip({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'baseline', gap: 5,
      padding: '4px 11px', borderRadius: 999,
      background: C.bg2, border: `1px solid ${C.border}`,
      fontSize: 12,
    }}>
      <span style={{ color: C.text3, fontSize: 10, letterSpacing: '0.05em' }}>{label}</span>
      <span style={{ color: color || C.text1, fontWeight: 600, textTransform: 'capitalize' }}>{value}</span>
    </span>
  )
}

function MiniTag({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px',
      background: C.bg2, border: `1px solid ${C.border}`,
      borderRadius: 4, fontSize: 11, color: C.text2,
    }}>
      {children}
    </span>
  )
}

function KV({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', gap: 12, fontSize: 13, padding: '4px 0',
      flexWrap: 'wrap', alignItems: 'baseline',
    }}>
      <span style={{ color: C.text3, minWidth: 130, textTransform: 'capitalize' }}>{k}</span>
      <span style={{ color: C.text1, flex: 1, minWidth: 200 }}>{children}</span>
    </div>
  )
}

function Quote({ label, labelColor, text }: { label: string; labelColor: string; text: string }) {
  return (
    <div style={{ marginTop: 4 }}>
      <span style={{
        fontSize: 9, fontWeight: 700, letterSpacing: '0.08em',
        color: labelColor, marginRight: 6,
      }}>
        {label}
      </span>
      <span style={{ fontSize: 13, color: C.text1, lineHeight: 1.5 }}>"{text}"</span>
    </div>
  )
}
