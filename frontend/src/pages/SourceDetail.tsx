import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { SourceItemDetail, FrameMention } from '../api/types'

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diff / 3600000)
  if (h < 1) return 'Just now'
  if (h === 1) return '1h ago'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const SENTIMENT_CONFIG: Record<string, { label: string; color: string }> = {
  positive: { label: 'Positive', color: '#22c55e' },
  negative: { label: 'Negative', color: '#ef4444' },
  mixed:    { label: 'Mixed',    color: '#f97316' },
  neutral:  { label: 'Neutral',  color: '#64748b' },
}

const FRAMING_CONFIG: Record<string, { label: string; color: string }> = {
  hurts_candidate: { label: 'Hurts candidate',  color: '#ef4444' },
  opponent_news:   { label: 'Opponent news',    color: '#f97316' },
  helps_candidate: { label: 'Helps candidate',  color: '#22c55e' },
  background:      { label: 'Background',       color: '#64748b' },
  irrelevant:      { label: 'Irrelevant',       color: '#334155' },
}

const ACTION_CONFIG: Record<string, { label: string; color: string }> = {
  respond:  { label: 'Needs response',   color: '#ef4444' },
  review:   { label: 'Worth reviewing',  color: '#f97316' },
  monitor:  { label: 'Monitor',          color: '#64748b' },
  ignore:   { label: 'Low priority',     color: '#334155' },
}

function Chip({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, color,
      border: `1px solid ${color}`,
      borderRadius: 4, padding: '2px 8px',
    }}>
      {label}
    </span>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h3 style={{ margin: '0 0 10px', fontSize: 12, fontWeight: 700, color: 'var(--text-muted, #94a3b8)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        {title}
      </h3>
      {children}
    </div>
  )
}

function FrameMentionRow({ mention }: { mention: FrameMention }) {
  const ownerColor = mention.frame_owner_type === 'candidate' ? '#22c55e'
    : mention.frame_owner_type === 'opponent' ? '#ef4444' : '#64748b'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '8px 12px', background: 'var(--bg-card, #1e293b)',
      border: '1px solid var(--border, #334155)', borderRadius: 6, marginBottom: 6,
    }}>
      <span style={{ fontSize: 11, fontWeight: 700, color: ownerColor, border: `1px solid ${ownerColor}`, borderRadius: 3, padding: '1px 6px', flexShrink: 0 }}>
        {mention.frame_owner_type}
      </span>
      <span style={{ fontSize: 13, color: 'var(--text, #f1f5f9)', flexGrow: 1 }}>
        {mention.frame_name}
      </span>
      <span style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>
        {mention.confidence}% · {mention.matched_by}
      </span>
    </div>
  )
}

export default function SourceDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [item, setItem] = useState<SourceItemDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    api.getSource(Number(id))
      .then(setItem)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <div style={{ padding: 32, color: 'var(--text-muted)' }}>Loading…</div>
  if (error)   return <div style={{ padding: 32, color: '#ef4444' }}>Error: {error}</div>
  if (!item)   return <div style={{ padding: 32, color: 'var(--text-muted)' }}>Not found.</div>

  const sentiment = item.sentiment ? SENTIMENT_CONFIG[item.sentiment] : null
  const framing   = item.actionability_label ? ACTION_CONFIG[item.actionability_label] : null
  const framingDetail = FRAMING_CONFIG[item.content_category] ?? null

  return (
    <div className="page">

      {/* Back nav */}
      <button
        onClick={() => navigate(-1)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-muted, #94a3b8)', fontSize: 13, padding: '0 0 16px',
          display: 'flex', alignItems: 'center', gap: 6,
        }}
      >
        ← Back
      </button>

      {/* Title + meta */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: '0 0 8px', fontSize: 20, fontWeight: 700, color: 'var(--text, #f1f5f9)', lineHeight: 1.3 }}>
          {item.title}
        </h1>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--text-muted, #94a3b8)' }}>
          {item.source_name && <span style={{ fontWeight: 600 }}>{item.source_name}</span>}
          {item.source_author && <span>by {item.source_author}</span>}
          {item.published_at && <span>{timeAgo(item.published_at)}</span>}
          {item.source_url && (
            <a href={item.source_url} target="_blank" rel="noopener noreferrer"
              style={{ color: '#60a5fa', textDecoration: 'none' }}>
              View original ↗
            </a>
          )}
        </div>
      </div>

      {/* Signal row */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 24 }}>
        {sentiment && <Chip label={`Sentiment: ${sentiment.label}`} color={sentiment.color} />}
        {framing   && <Chip label={framing.label}   color={framing.color} />}
        {framingDetail && <Chip label={framingDetail.label} color={framingDetail.color} />}
        <Chip label={`Relevance: ${item.race_relevance_score}`} color={item.race_relevance_score >= 60 ? '#22c55e' : item.race_relevance_score >= 35 ? '#f97316' : '#64748b'} />
        {item.urgency === 'high' && <Chip label="⚠ Urgent" color="#ef4444" />}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 24, alignItems: 'start' }}>

        {/* Main column */}
        <div>
          {item.summary && (
            <Section title="AI Summary">
              <p style={{ margin: 0, fontSize: 14, color: 'var(--text, #f1f5f9)', lineHeight: 1.6 }}>
                {item.summary}
              </p>
            </Section>
          )}

          {(item.relevance_reasons ?? []).length > 0 && (
            <Section title="Why this matters">
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {(item.relevance_reasons ?? []).map((r, i) => (
                  <li key={i} style={{ fontSize: 13, color: 'var(--text-muted, #94a3b8)', marginBottom: 4, lineHeight: 1.5 }}>
                    {r}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {item.raw_text && (
            <Section title="Extracted text">
              <div style={{
                fontSize: 13, color: 'var(--text-muted, #94a3b8)', lineHeight: 1.7,
                maxHeight: 400, overflowY: 'auto',
                padding: '12px 14px', background: 'var(--bg-card, #1e293b)',
                border: '1px solid var(--border, #334155)', borderRadius: 6,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>
                {item.raw_text}
              </div>
            </Section>
          )}
        </div>

        {/* Sidebar */}
        <div>
          {(item.frame_mentions ?? []).length > 0 && (
            <Section title={`Narrative frames (${(item.frame_mentions ?? []).length})`}>
              {(item.frame_mentions ?? []).map(m => (
                <FrameMentionRow key={m.frame_id} mention={m} />
              ))}
            </Section>
          )}

          <Section title="Source signals">
            <div style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 12px' }}>
              <span>Evidence</span>     <span style={{ color: 'var(--text, #f1f5f9)', fontWeight: 600 }}>{item.evidence_score}</span>
              <span>Credibility</span>  <span style={{ color: 'var(--text, #f1f5f9)', fontWeight: 600 }}>{item.credibility_score}</span>
              <span>Priority</span>     <span style={{ color: 'var(--text, #f1f5f9)', fontWeight: 600 }}>{item.priority_score}</span>
              <span>Type</span>         <span style={{ color: 'var(--text, #f1f5f9)', fontWeight: 600 }}>{item.source_type}</span>
              <span>Owner</span>        <span style={{ color: 'var(--text, #f1f5f9)', fontWeight: 600 }}>{item.source_owner_type}</span>
              {item.extraction_quality_label !== 'good' && (
                <>
                  <span>Extraction</span>
                  <span style={{ color: '#f97316', fontWeight: 600 }}>{item.extraction_quality_label}</span>
                </>
              )}
            </div>
          </Section>
        </div>
      </div>
    </div>
  )
}
