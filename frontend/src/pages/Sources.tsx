import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ManualCaptureResult, SourceItem, SourceItemDetail } from '../api/types'
import SourceCard, { sourceDate } from '../components/SourceCard'

const SOURCE_TYPES = ['all', 'news', 'opponent_statement', 'public_record', 'canvassing', 'campaign_note', 'social']
const SOURCE_FILTERS = [
  { id: 'relevant',      label: 'Relevant' },
  { id: 'review_queue',  label: 'Queue' },
  { id: 'archived',      label: 'Archived' },
  { id: 'all',           label: 'All' },
]
const URGENCIES = ['all', 'high', 'medium', 'low']
const CAPTURE_TYPES = ['pasted_text', 'flyer', 'endorsement', 'debate_notes', 'newsletter', 'social_post', 'forum_notes', 'press_release', 'other']

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function extractionColors(label: string) {
  if (label === 'poor')  return { color: 'var(--opponent-light)',  border: 'var(--opponent-border)',  bg: 'var(--opponent-bg)' }
  if (label === 'mixed') return { color: 'var(--warning-light)',   border: 'var(--warning-border)',   bg: 'var(--warning-bg)' }
  return                        { color: 'var(--ok-light)',        border: 'var(--ok-border)',         bg: 'var(--ok-bg)' }
}

// ── Source Detail Drawer ───────────────────────────────────────────────────────

function SourceDrawer({ sourceId, onClose }: { sourceId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<SourceItemDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getSource(sourceId).then(setDetail).finally(() => setLoading(false))
  }, [sourceId])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const ext = detail ? extractionColors(detail.extraction_quality_label) : extractionColors('good')

  return (
    <>
      <div onClick={onClose} style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
        zIndex: 40, backdropFilter: 'blur(2px)',
      }} />
      <div style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, width: 540,
        background: 'var(--surface-1)', borderLeft: '1px solid var(--border)',
        zIndex: 50, overflowY: 'auto', display: 'flex', flexDirection: 'column',
        boxShadow: 'var(--shadow-xl)',
      }}>
        {/* Sticky header */}
        <div style={{
          position: 'sticky', top: 0, zIndex: 1,
          background: 'var(--surface-1)', borderBottom: '1px solid var(--border)',
          padding: '1rem 1.25rem',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span className="label">Source Detail</span>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', color: 'var(--text-muted)',
            cursor: 'pointer', fontSize: '1rem', padding: '2px 6px',
            borderRadius: 4, lineHeight: 1,
          }}>✕</button>
        </div>

        {loading && <div className="loading-text">Loading…</div>}

        {!loading && detail && (
          <div style={{ padding: '1.5rem', flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {/* Title */}
            {detail.source_url ? (
              <a href={detail.source_url} target="_blank" rel="noopener noreferrer"
                style={{ fontWeight: 700, fontSize: '1rem', lineHeight: 1.4, color: 'var(--text-primary)', textDecoration: 'underline', textDecorationColor: 'rgba(255,255,255,0.15)' }}>
                {detail.title}
              </a>
            ) : (
              <span style={{ fontWeight: 700, fontSize: '1rem', lineHeight: 1.4, color: 'var(--text-primary)' }}>
                {detail.title}
              </span>
            )}

            {/* Meta chips */}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="badge badge-ghost">{detail.source_type.replace(/_/g, ' ')}</span>
              <span className="badge badge-ghost">{detail.source_owner_type.replace(/_/g, ' ')}</span>
              {detail.source_name && <span style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>{detail.source_name}</span>}
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: '0.65rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
                {(() => { const { date, label } = sourceDate(detail); return label ? `${label}: ${date}` : date })()}
              </span>
            </div>

            {detail.source_url && (
              <a href={detail.source_url} target="_blank" rel="noopener noreferrer"
                className="btn btn-ghost btn-sm"
                style={{ textDecoration: 'none', display: 'inline-flex', width: 'fit-content' }}>
                View original ↗
              </a>
            )}

            {/* Scores */}
            <div className="card" style={{ padding: '0.75rem 1rem' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                {[
                  { label: 'Relevance', value: `${detail.race_relevance_label} · ${detail.race_relevance_score}` },
                  { label: 'Action',    value: `${detail.actionability_label}` },
                  { label: 'Category', value: detail.content_category.replace(/_/g, ' ') },
                  { label: 'Geo',      value: detail.geo_relevance },
                ].map(({ label, value }) => (
                  <div key={label}>
                    <div className="label" style={{ marginBottom: 2 }}>{label}</div>
                    <div style={{ fontSize: '0.78rem', color: 'var(--text-primary)' }}>{value}</div>
                  </div>
                ))}
              </div>
              {detail.relevance_reasons.length > 0 && (
                <ul style={{ margin: '10px 0 0', paddingLeft: '1rem', color: 'var(--text-secondary)', fontSize: '0.77rem', lineHeight: 1.55 }}>
                  {detail.relevance_reasons.map(r => <li key={r}>{r}</li>)}
                </ul>
              )}
            </div>

            {/* Extraction quality */}
            <div className="card" style={{ borderLeft: `3px solid ${ext.border}`, background: ext.bg, padding: '0.75rem 1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <div className="label" style={{ color: ext.color }}>Extraction Quality</div>
                <span className="badge badge-ghost" style={{ color: ext.color, fontSize: '0.6rem' }}>
                  {detail.extraction_quality_label} · {detail.extraction_quality_score}
                </span>
              </div>
              {detail.extraction_quality_label === 'poor' && (
                <p style={{ margin: '0 0 8px', fontSize: '0.78rem', color: 'var(--opponent-light)', lineHeight: 1.5 }}>
                  This page may include sidebar or unrelated teaser text. Verify against the original source.
                </p>
              )}
              {detail.extraction_quality_reasons.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: '1rem', color: 'var(--text-secondary)', fontSize: '0.75rem', lineHeight: 1.5 }}>
                  {detail.extraction_quality_reasons.slice(0, 4).map(r => <li key={r}>{r}</li>)}
                </ul>
              )}
            </div>

            {/* Campaign snapshot */}
            {detail.snapshot && (
              <div className="card" style={{ borderLeft: '3px solid var(--ok-border)', padding: '0.75rem 1rem' }}>
                <div className="label" style={{ color: 'var(--ok-light)', marginBottom: 8 }}>Campaign Snapshot</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                  <span className="badge badge-ghost">{detail.snapshot.action_signal}</span>
                  <span className="badge badge-ghost">{detail.snapshot.evidence_summary} evidence</span>
                </div>
                <div style={{ display: 'grid', gap: 8 }}>
                  <div>
                    <div className="label" style={{ marginBottom: 2 }}>What happened</div>
                    <div style={{ fontSize: '0.82rem', color: 'var(--text-primary)', lineHeight: 1.55 }}>{detail.snapshot.what_happened}</div>
                  </div>
                  <div>
                    <div className="label" style={{ marginBottom: 2 }}>Why it matters</div>
                    <div style={{ fontSize: '0.79rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>{detail.snapshot.why_it_matters}</div>
                  </div>
                  {detail.snapshot.key_claim_or_quote && (
                    <blockquote style={{ margin: 0, padding: '0.5rem 0.75rem', borderLeft: '2px solid var(--opponent-border)', borderRadius: '0 4px 4px 0', background: 'var(--opponent-bg)', fontSize: '0.79rem', color: 'var(--opponent-light)', lineHeight: 1.5, fontStyle: 'italic' }}>
                      "{detail.snapshot.key_claim_or_quote}"
                    </blockquote>
                  )}
                </div>
              </div>
            )}

            {/* Summary */}
            {detail.summary && (
              <div className="card" style={{ borderLeft: '3px solid var(--accent-border)', padding: '0.75rem 1rem' }}>
                <div className="label" style={{ color: 'var(--accent-light)', marginBottom: 6 }}>Summary</div>
                <p style={{ margin: 0, fontSize: '0.83rem', color: 'var(--text-primary)', lineHeight: 1.65 }}>{detail.summary}</p>
              </div>
            )}

            {/* Credibility */}
            {detail.credibility_note && (
              <div className="risk-banner">
                <div className="label" style={{ color: 'var(--opponent)', marginBottom: 5 }}>⚠ Intelligence Note</div>
                <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--opponent-light)', lineHeight: 1.55 }}>{detail.credibility_note}</p>
              </div>
            )}

            {/* Raw text */}
            {detail.raw_text && (
              <details className="card" style={{ padding: '0.75rem 1rem' }}>
                <summary style={{ cursor: 'pointer', fontSize: '0.73rem', fontFamily: 'JetBrains Mono', color: 'var(--text-muted)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                  Show extracted text
                </summary>
                <p style={{ margin: '10px 0 0', fontSize: '0.79rem', color: 'var(--text-secondary)', lineHeight: 1.65, whiteSpace: 'pre-wrap', maxHeight: 280, overflowY: 'auto' }}>
                  {detail.raw_text}
                </p>
              </details>
            )}

            {/* Timestamps */}
            <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', lineHeight: 1.9 }}>
              {detail.published_at
                ? <div>Published: {fmtDate(detail.published_at)}</div>
                : null}
              <div>Collected: {fmtDate(detail.ingested_at ?? detail.created_at)}</div>
            </div>
          </div>
        )}
      </div>
    </>
  )
}

// ── Add Source Panel ───────────────────────────────────────────────────────────

type AddTab = 'capture' | 'paste' | 'rss' | 'url'

function AddSourcePanel({ onAdded }: { onAdded: () => void }) {
  const [tab, setTab] = useState<AddTab>('capture')
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  // Capture form
  const [cap, setCap] = useState({ title: '', raw_text: '', source_name: '', source_type: 'campaign_note', source_url: '', capture_type: 'pasted_text', geography_tags: '', issue_tags: '', notes: '', candidate_related: false, opponent_related: false })
  const [capResult, setCapResult] = useState<ManualCaptureResult | null>(null)

  // Text paste
  const [paste, setPaste] = useState({ title: '', raw_text: '', source_name: '', source_type: 'campaign_note', source_url: '' })

  // RSS / URL
  const [rssUrl, setRssUrl] = useState(''); const [rssLabel, setRssLabel] = useState('')
  const [urlInput, setUrlInput] = useState('')

  function ok(text: string) { setMsg({ text, ok: true }) }
  function err(e: unknown) { setMsg({ text: e instanceof Error ? e.message : 'Error', ok: false }) }

  async function addCapture() {
    if (!cap.title.trim() || !cap.raw_text.trim()) return
    setLoading(true); setMsg(null); setCapResult(null)
    try {
      const r = await api.createManualCapture({ ...cap, geography_tags: cap.geography_tags.split(',').map(s => s.trim()).filter(Boolean), issue_tags: cap.issue_tags.split(',').map(s => s.trim()).filter(Boolean), notes: cap.notes || undefined, source_url: cap.source_url || undefined })
      setCapResult(r); ok('✓ Captured and classified'); onAdded()
      setCap({ title: '', raw_text: '', source_name: '', source_type: 'campaign_note', source_url: '', capture_type: 'pasted_text', geography_tags: '', issue_tags: '', notes: '', candidate_related: false, opponent_related: false })
    } catch (e) { err(e) } finally { setLoading(false) }
  }

  async function addPaste() {
    if (!paste.title.trim() || !paste.raw_text.trim()) return
    setLoading(true); setMsg(null)
    try { await api.addTextSource(paste); ok('✓ Added and analyzed'); onAdded(); setPaste({ title: '', raw_text: '', source_name: '', source_type: 'campaign_note', source_url: '' }) }
    catch (e) { err(e) } finally { setLoading(false) }
  }

  async function addRss() {
    if (!rssUrl.trim()) return
    setLoading(true); setMsg(null)
    try { const items = await api.addRssOnce(rssUrl, rssLabel || undefined); ok(`✓ Imported ${items.length} items`); onAdded(); setRssUrl(''); setRssLabel('') }
    catch (e) { err(e) } finally { setLoading(false) }
  }

  async function addUrl() {
    if (!urlInput.trim()) return
    setLoading(true); setMsg(null)
    try { await api.addUrlSource(urlInput); ok('✓ Fetched and analyzed'); onAdded(); setUrlInput('') }
    catch (e) { err(e) } finally { setLoading(false) }
  }

  const addTabs: { id: AddTab; label: string }[] = [
    { id: 'capture', label: 'Capture Inbox' },
    { id: 'paste',   label: 'Paste Text' },
    { id: 'rss',     label: 'RSS Feed' },
    { id: 'url',     label: 'Fetch URL' },
  ]

  return (
    <div className="card" style={{ marginBottom: '1.5rem', padding: '1.25rem 1.5rem' }}>
      {/* Add-source tabs */}
      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)', marginBottom: '1.25rem' }}>
        {addTabs.map(t => (
          <button
            key={t.id}
            onClick={() => { setTab(t.id); setMsg(null) }}
            style={{
              background: 'none', border: 'none', fontFamily: 'inherit',
              padding: '0.4rem 0.75rem', cursor: 'pointer',
              fontSize: '0.8rem', fontWeight: tab === t.id ? 600 : 400,
              color: tab === t.id ? 'var(--text-primary)' : 'var(--text-muted)',
              borderBottom: tab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
              marginBottom: -1,
              transition: 'color 0.12s',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Capture Inbox */}
      {tab === 'capture' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: '1.25rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div><div className="label" style={{ marginBottom: 3 }}>Title *</div><input value={cap.title} onChange={e => setCap(f => ({ ...f, title: e.target.value }))} placeholder="e.g. Forum notes on housing" /></div>
              <div><div className="label" style={{ marginBottom: 3 }}>Source Name</div><input value={cap.source_name} onChange={e => setCap(f => ({ ...f, source_name: e.target.value }))} placeholder="e.g. Local club, Candidate IG" /></div>
            </div>
            <div><div className="label" style={{ marginBottom: 3 }}>Captured Text *</div><textarea value={cap.raw_text} onChange={e => setCap(f => ({ ...f, raw_text: e.target.value }))} rows={9} placeholder="Paste post, flyer, debate notes, endorsement, transcript…" /></div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div><div className="label" style={{ marginBottom: 3 }}>Capture Type</div><select value={cap.capture_type} onChange={e => setCap(f => ({ ...f, capture_type: e.target.value }))}>{CAPTURE_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}</select></div>
              <div><div className="label" style={{ marginBottom: 3 }}>Source Type</div><select value={cap.source_type} onChange={e => setCap(f => ({ ...f, source_type: e.target.value }))}><option value="campaign_note">Campaign Note</option><option value="opponent_statement">Opponent Statement</option><option value="public_record">Public Record</option><option value="social">Social Media</option><option value="news">News</option></select></div>
            </div>
            <div><div className="label" style={{ marginBottom: 3 }}>URL (optional)</div><input value={cap.source_url} onChange={e => setCap(f => ({ ...f, source_url: e.target.value }))} placeholder="https://…" /></div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div><div className="label" style={{ marginBottom: 3 }}>Geography Tags</div><input value={cap.geography_tags} onChange={e => setCap(f => ({ ...f, geography_tags: e.target.value }))} placeholder="District 7, Jackson Hts" /></div>
              <div><div className="label" style={{ marginBottom: 3 }}>Issue Tags</div><input value={cap.issue_tags} onChange={e => setCap(f => ({ ...f, issue_tags: e.target.value }))} placeholder="housing, transit" /></div>
            </div>
            <div><div className="label" style={{ marginBottom: 3 }}>Notes</div><input value={cap.notes} onChange={e => setCap(f => ({ ...f, notes: e.target.value }))} placeholder="Where from, what to verify" /></div>
            <div style={{ display: 'flex', gap: 12, fontSize: '0.78rem', color: 'var(--text-secondary)', marginTop: 2 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 5 }}><input type="checkbox" checked={cap.candidate_related} onChange={e => setCap(f => ({ ...f, candidate_related: e.target.checked }))} style={{ width: 14, accentColor: 'var(--accent)' }} /> Candidate-related</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 5 }}><input type="checkbox" checked={cap.opponent_related} onChange={e => setCap(f => ({ ...f, opponent_related: e.target.checked }))} style={{ width: 14, accentColor: 'var(--accent)' }} /> Opponent-related</label>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
              <button className="btn btn-primary" onClick={addCapture} disabled={loading || !cap.title.trim() || !cap.raw_text.trim()}>{loading ? 'Classifying…' : 'Capture & Classify'}</button>
              {msg && <span style={{ fontSize: '0.75rem', color: msg.ok ? 'var(--ok-light)' : 'var(--opponent)' }}>{msg.text}</span>}
            </div>
          </div>
        </div>
      )}
      {capResult && tab === 'capture' && (
        <div className="card" style={{ marginTop: 12, background: 'var(--surface-2)' }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
            <span className="badge badge-ghost">{capResult.source_item.archived_as_irrelevant ? 'archived' : 'active'}</span>
            <span className="badge badge-ghost">{capResult.source_item.race_relevance_label} {capResult.source_item.race_relevance_score}</span>
            <span className="badge badge-ghost">{capResult.source_item.actionability_label}</span>
            {capResult.related_issues.map(i => <span key={i.id} className="badge badge-purple">{i.name}</span>)}
          </div>
          <div style={{ fontSize: '0.79rem', color: 'var(--text-secondary)', lineHeight: 1.45 }}>{capResult.message}</div>
        </div>
      )}

      {/* Paste */}
      {tab === 'paste' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div><div className="label" style={{ marginBottom: 3 }}>Title *</div><input value={paste.title} onChange={e => setPaste(f => ({ ...f, title: e.target.value }))} placeholder="Descriptive title" /></div>
            <div><div className="label" style={{ marginBottom: 3 }}>Text / Transcript *</div><textarea value={paste.raw_text} onChange={e => setPaste(f => ({ ...f, raw_text: e.target.value }))} rows={7} placeholder="Paste full text here…" /></div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div><div className="label" style={{ marginBottom: 3 }}>Source Name</div><input value={paste.source_name} onChange={e => setPaste(f => ({ ...f, source_name: e.target.value }))} placeholder="e.g. Tribune" /></div>
            <div><div className="label" style={{ marginBottom: 3 }}>Type</div><select value={paste.source_type} onChange={e => setPaste(f => ({ ...f, source_type: e.target.value }))}><option value="campaign_note">Campaign Note</option><option value="news">News</option><option value="opponent_statement">Opponent Statement</option><option value="public_record">Public Record</option><option value="canvassing">Canvassing</option><option value="social">Social</option></select></div>
            <div><div className="label" style={{ marginBottom: 3 }}>Source URL (optional)</div><input value={paste.source_url} onChange={e => setPaste(f => ({ ...f, source_url: e.target.value }))} placeholder="https://…" /></div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 'auto' }}>
              <button className="btn btn-primary" onClick={addPaste} disabled={loading || !paste.title.trim() || !paste.raw_text.trim()}>{loading ? 'Adding…' : 'Add & Analyze'}</button>
              {msg && <span style={{ fontSize: '0.75rem', color: msg.ok ? 'var(--ok-light)' : 'var(--opponent)' }}>{msg.text}</span>}
            </div>
          </div>
        </div>
      )}

      {/* RSS */}
      {tab === 'rss' && (
        <div style={{ maxWidth: 440, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div><div className="label" style={{ marginBottom: 3 }}>Feed URL</div><input value={rssUrl} onChange={e => setRssUrl(e.target.value)} placeholder="https://example.com/feed.xml" /></div>
          <div><div className="label" style={{ marginBottom: 3 }}>Label (optional)</div><input value={rssLabel} onChange={e => setRssLabel(e.target.value)} placeholder="e.g. Lakeview Tribune" /></div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
            <button className="btn btn-primary" onClick={addRss} disabled={loading || !rssUrl.trim()}>{loading ? 'Fetching…' : 'Import Feed'}</button>
            {msg && <span style={{ fontSize: '0.75rem', color: msg.ok ? 'var(--ok-light)' : 'var(--opponent)' }}>{msg.text}</span>}
          </div>
        </div>
      )}

      {/* URL */}
      {tab === 'url' && (
        <div style={{ maxWidth: 440, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div><div className="label" style={{ marginBottom: 3 }}>Article / Page URL</div><input value={urlInput} onChange={e => setUrlInput(e.target.value)} placeholder="https://article.com/…" /></div>
          <p style={{ margin: 0, fontSize: '0.76rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>Fetches the page, extracts content, and analyzes it for issues, urgency, and relevance.</p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn btn-primary" onClick={addUrl} disabled={loading || !urlInput.trim()}>{loading ? 'Fetching…' : 'Fetch & Analyze'}</button>
            {msg && <span style={{ fontSize: '0.75rem', color: msg.ok ? 'var(--ok-light)' : 'var(--opponent)' }}>{msg.text}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Sources() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [sources, setSources] = useState<SourceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState('all')
  const [urgencyFilter, setUrgencyFilter] = useState('all')
  const [sourceFilter, setSourceFilter] = useState('relevant')
  const [showAdd, setShowAdd] = useState(false)
  const [activeDrawerId, setActiveDrawerId] = useState<number | null>(null)

  const load = useCallback(() => {
    const params: Record<string, string> = {}
    if (typeFilter !== 'all') params.source_type = typeFilter
    if (urgencyFilter !== 'all') params.urgency = urgencyFilter
    params.source_filter = sourceFilter
    setLoading(true); setError(null)
    api.getSources(params)
      .then(d => { setSources(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [typeFilter, urgencyFilter, sourceFilter])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const id = Number(searchParams.get('source_id'))
    if (id > 0) setActiveDrawerId(id)
  }, [searchParams])

  return (
    <div className="page">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.75rem' }}>
        <div>
          <div className="label" style={{ marginBottom: 4 }}>Intelligence Sources</div>
          <h1 className="page-title">Source Library</h1>
        </div>
        <button
          className={showAdd ? 'btn btn-ghost' : 'btn btn-primary'}
          onClick={() => setShowAdd(!showAdd)}
        >
          {showAdd ? '✕ Close' : '+ Add Source'}
        </button>
      </div>

      {showAdd && <AddSourcePanel onAdded={load} />}

      {/* Filters */}
      <div style={{ display: 'flex', gap: '1.25rem', marginBottom: '1.25rem', alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Source filter */}
        <div className="pill-tabs">
          {SOURCE_FILTERS.map(f => (
            <button key={f.id} className={`pill-tab${sourceFilter === f.id ? ' active' : ''}`} onClick={() => setSourceFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>

        {/* Type filter */}
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {SOURCE_TYPES.map(t => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              style={{
                padding: '0.25rem 0.65rem', borderRadius: 99, fontSize: '0.71rem',
                background: typeFilter === t ? 'var(--accent-bg)' : 'transparent',
                border: `1px solid ${typeFilter === t ? 'var(--accent-border)' : 'var(--border)'}`,
                color: typeFilter === t ? 'var(--accent-light)' : 'var(--text-muted)',
                cursor: 'pointer', fontFamily: 'inherit', transition: 'all 0.12s',
              }}
            >
              {t === 'all' ? 'All types' : t.replace(/_/g, ' ')}
            </button>
          ))}
        </div>

        {/* Urgency filter */}
        <div style={{ display: 'flex', gap: 4 }}>
          {URGENCIES.map(u => (
            <button
              key={u}
              onClick={() => setUrgencyFilter(u)}
              style={{
                padding: '0.25rem 0.65rem', borderRadius: 99, fontSize: '0.71rem',
                background: urgencyFilter === u ? 'var(--warning-bg)' : 'transparent',
                border: `1px solid ${urgencyFilter === u ? 'var(--warning-border)' : 'var(--border)'}`,
                color: urgencyFilter === u ? 'var(--warning-light)' : 'var(--text-muted)',
                cursor: 'pointer', fontFamily: 'inherit', transition: 'all 0.12s',
              }}
            >
              {u === 'all' ? 'All urgency' : u}
            </button>
          ))}
        </div>

        <span style={{ marginLeft: 'auto', fontFamily: 'JetBrains Mono', fontSize: '0.67rem', color: 'var(--text-muted)' }}>
          {sources.length} items
        </span>
      </div>

      {/* Source list */}
      {loading && <div className="loading-text" style={{ padding: '2rem 0', textAlign: 'left' }}>Loading…</div>}
      {!loading && error && <div style={{ color: 'var(--opponent)', fontSize: '0.82rem', padding: '1rem 0' }}>Error: {error}</div>}
      {!loading && !error && sources.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">📰</div>
          <div className="empty-state-title">No sources match this filter</div>
          <div className="empty-state-body">
            {typeFilter !== 'all' || urgencyFilter !== 'all'
              ? 'Try clearing the filters.'
              : 'Add your first source using the button above.'}
          </div>
        </div>
      )}
      {!loading && !error && sources.map(s => (
        <div key={s.id} onClick={() => setActiveDrawerId(s.id)} style={{ cursor: 'pointer' }}>
          <SourceCard source={s} />
        </div>
      ))}

      {activeDrawerId !== null && (
        <SourceDrawer
          sourceId={activeDrawerId}
          onClose={() => {
            setActiveDrawerId(null)
            if (searchParams.get('source_id')) setSearchParams({})
          }}
        />
      )}
    </div>
  )
}
