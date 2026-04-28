import { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'
import type { SourceItem, SourceItemDetail } from '../api/types'
import SourceCard from '../components/SourceCard'
import UrgencyBadge from '../components/UrgencyBadge'

const SOURCE_TYPES = ['all', 'news', 'opponent_statement', 'public_record', 'canvassing', 'campaign_note', 'social']
const URGENCIES = ['all', 'high', 'medium', 'low']

const TYPE_COLORS: Record<string, string> = {
  news: '#93c5fd', public_record: '#86efac', opponent_statement: '#fca5a5',
  canvassing: '#c4b5fd', campaign_note: '#fdba74', social: '#67e8f9',
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

// ── Source Detail Drawer ──────────────────────────────────────────────────────

function SourceDrawer({ sourceId, onClose }: { sourceId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<SourceItemDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getSource(sourceId)
      .then(setDetail)
      .finally(() => setLoading(false))
  }, [sourceId])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const typeColor = detail ? (TYPE_COLORS[detail.source_type] ?? '#8892a4') : '#8892a4'

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
          zIndex: 40, backdropFilter: 'blur(2px)',
        }}
      />
      {/* Drawer */}
      <div style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, width: 520,
        background: 'var(--surface-1)', borderLeft: '1px solid var(--border)',
        zIndex: 50, overflowY: 'auto', display: 'flex', flexDirection: 'column',
      }}>
        {/* Header */}
        <div style={{
          padding: '1rem 1.25rem',
          borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          position: 'sticky', top: 0, background: 'var(--surface-1)', zIndex: 1,
        }}>
          <div className="label">Source Detail</div>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: 'var(--text-muted)',
              cursor: 'pointer', fontSize: '1.1rem', padding: '0.25rem 0.5rem',
            }}
          >✕</button>
        </div>

        {loading && <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>}

        {!loading && detail && (
          <div style={{ padding: '1.25rem', flex: 1 }}>
            {/* Title */}
            <div style={{ marginBottom: '1rem' }}>
              {detail.source_url ? (
                <a
                  href={detail.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--text-primary)', fontWeight: 700, fontSize: '0.95rem', lineHeight: 1.4, textDecoration: 'underline', textDecorationColor: 'rgba(255,255,255,0.2)' }}
                >
                  {detail.title}
                </a>
              ) : (
                <span style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--text-primary)', lineHeight: 1.4 }}>
                  {detail.title}
                </span>
              )}
            </div>

            {/* Meta row */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: '1rem' }}>
              <span style={{ fontSize: '0.65rem', color: typeColor, fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {detail.source_type.replace(/_/g, ' ')}
              </span>
              {detail.source_name && (
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{detail.source_name}</span>
              )}
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 'auto', fontFamily: 'JetBrains Mono' }}>
                {fmtDate(detail.published_at)}
              </span>
              <UrgencyBadge urgency={detail.urgency} size="sm" />
            </div>

            {/* Summary */}
            {detail.summary && (
              <div className="card" style={{ marginBottom: '1rem', borderLeft: '3px solid rgba(59,130,246,0.4)' }}>
                <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: '#93c5fd', letterSpacing: '0.06em', marginBottom: 6 }}>SUMMARY</div>
                <p style={{ margin: 0, fontSize: '0.83rem', color: 'var(--text-primary)', lineHeight: 1.6 }}>{detail.summary}</p>
              </div>
            )}

            {/* Credibility note */}
            {detail.credibility_note && (
              <div className="risk-banner" style={{ marginBottom: '1rem' }}>
                <div style={{ fontSize: '0.65rem', fontWeight: 700, color: '#f87171', marginBottom: 4, fontFamily: 'JetBrains Mono', letterSpacing: '0.06em' }}>
                  ⚠ INTELLIGENCE NOTE
                </div>
                <p style={{ margin: 0, fontSize: '0.8rem', color: '#fca5a5', lineHeight: 1.5 }}>{detail.credibility_note}</p>
              </div>
            )}

            {/* Raw text */}
            {detail.raw_text && (
              <div className="card" style={{ marginBottom: '1rem' }}>
                <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: 'var(--text-muted)', letterSpacing: '0.06em', marginBottom: 6 }}>
                  FULL TEXT
                </div>
                <p style={{
                  margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)',
                  lineHeight: 1.6, whiteSpace: 'pre-wrap', maxHeight: 320, overflowY: 'auto',
                }}>
                  {detail.raw_text}
                </p>
              </div>
            )}

            {/* Source URL */}
            {detail.source_url && (
              <div style={{ marginBottom: '1rem' }}>
                <div className="label" style={{ marginBottom: 4 }}>Source URL</div>
                <a
                  href={detail.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ fontSize: '0.75rem', color: 'var(--accent)', wordBreak: 'break-all' }}
                >
                  {detail.source_url}
                </a>
              </div>
            )}

            {/* Timestamps */}
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', lineHeight: 1.8 }}>
              <div>Published: {fmtDate(detail.published_at)}</div>
              <div>Added: {fmtDate(detail.created_at)}</div>
            </div>
          </div>
        )}
      </div>
    </>
  )
}

// ── Main Sources page ─────────────────────────────────────────────────────────

export default function Sources() {
  const [sources, setSources] = useState<SourceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState('all')
  const [urgencyFilter, setUrgencyFilter] = useState('all')
  const [showAdd, setShowAdd] = useState(false)
  const [activeDrawerId, setActiveDrawerId] = useState<number | null>(null)

  const [form, setForm] = useState({ title: '', raw_text: '', source_name: '', source_type: 'campaign_note', source_url: '' })
  const [addMsg, setAddMsg] = useState<string | null>(null)
  const [rssUrl, setRssUrl] = useState('')
  const [rssLabel, setRssLabel] = useState('')
  const [urlInput, setUrlInput] = useState('')
  const [rssMsg, setRssMsg] = useState<string | null>(null)
  const [urlMsg, setUrlMsg] = useState<string | null>(null)
  const [addLoading, setAddLoading] = useState(false)

  const load = useCallback(() => {
    const params: Record<string, string> = {}
    if (typeFilter !== 'all') params.source_type = typeFilter
    if (urgencyFilter !== 'all') params.urgency = urgencyFilter
    setLoading(true)
    setError(null)
    api.getSources(params)
      .then(d => { setSources(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [typeFilter, urgencyFilter])

  useEffect(() => { load() }, [load])

  async function addText() {
    if (!form.title.trim() || !form.raw_text.trim()) return
    setAddLoading(true)
    setAddMsg(null)
    try {
      await api.addTextSource(form)
      setAddMsg('✓ Source added and analyzed')
      setForm({ title: '', raw_text: '', source_name: '', source_type: 'campaign_note', source_url: '' })
      load()
    } catch (e: unknown) {
      setAddMsg(e instanceof Error ? `Error: ${e.message}` : 'Error adding source')
    } finally {
      setAddLoading(false)
    }
  }

  async function addRss() {
    if (!rssUrl.trim()) return
    setAddLoading(true)
    setRssMsg(null)
    try {
      const items = await api.addRssOnce(rssUrl, rssLabel || undefined)
      setRssMsg(`✓ Imported ${items.length} items from feed`)
      setRssUrl('')
      setRssLabel('')
      load()
    } catch (e: unknown) {
      setRssMsg(e instanceof Error ? `Error: ${e.message}` : 'Error parsing RSS feed')
    } finally {
      setAddLoading(false)
    }
  }

  async function addUrl() {
    if (!urlInput.trim()) return
    setAddLoading(true)
    setUrlMsg(null)
    try {
      await api.addUrlSource(urlInput)
      setUrlMsg('✓ URL fetched and analyzed')
      setUrlInput('')
      load()
    } catch (e: unknown) {
      setUrlMsg(e instanceof Error ? `Error: ${e.message}` : 'Could not fetch URL')
    } finally {
      setAddLoading(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', background: 'var(--surface-2)', border: '1px solid var(--border)',
    borderRadius: 6, padding: '0.45rem 0.7rem', color: 'var(--text-primary)',
    fontSize: '0.825rem', boxSizing: 'border-box',
  }

  return (
    <div style={{ padding: '1.5rem', maxWidth: 1000 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem' }}>
        <div>
          <div className="label" style={{ marginBottom: 4 }}>Intelligence Sources</div>
          <h1 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>Source Library</h1>
        </div>
        <button className="btn-ghost" onClick={() => setShowAdd(!showAdd)}>
          {showAdd ? '✕ Close' : '+ Add Source'}
        </button>
      </div>

      {showAdd && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1.5rem' }}>
            {/* Text paste */}
            <div>
              <div className="section-title">Paste Text / Transcript</div>
              <div style={{ marginBottom: 8 }}>
                <div className="label" style={{ marginBottom: 3 }}>Title *</div>
                <input style={inputStyle} value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} placeholder="Descriptive title" />
              </div>
              <div style={{ marginBottom: 8 }}>
                <div className="label" style={{ marginBottom: 3 }}>Source Name</div>
                <input style={inputStyle} value={form.source_name} onChange={e => setForm(f => ({ ...f, source_name: e.target.value }))} placeholder="e.g. Lakeview Tribune" />
              </div>
              <div style={{ marginBottom: 8 }}>
                <div className="label" style={{ marginBottom: 3 }}>Type</div>
                <select style={inputStyle} value={form.source_type} onChange={e => setForm(f => ({ ...f, source_type: e.target.value }))}>
                  <option value="campaign_note">Campaign Note</option>
                  <option value="news">News</option>
                  <option value="opponent_statement">Opponent Statement</option>
                  <option value="public_record">Public Record</option>
                  <option value="canvassing">Canvassing</option>
                  <option value="social">Social Media</option>
                </select>
              </div>
              <div style={{ marginBottom: 8 }}>
                <div className="label" style={{ marginBottom: 3 }}>Source URL (optional)</div>
                <input style={inputStyle} value={form.source_url} onChange={e => setForm(f => ({ ...f, source_url: e.target.value }))} placeholder="https://…" />
              </div>
              <div style={{ marginBottom: 10 }}>
                <div className="label" style={{ marginBottom: 3 }}>Text / Transcript *</div>
                <textarea style={{ ...inputStyle, resize: 'vertical' } as React.CSSProperties} value={form.raw_text} onChange={e => setForm(f => ({ ...f, raw_text: e.target.value }))} rows={5} placeholder="Paste full text here…" />
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="btn-primary" onClick={addText} disabled={addLoading || !form.title.trim() || !form.raw_text.trim()}>
                  {addLoading ? 'Adding…' : 'Add & Analyze'}
                </button>
                {addMsg && <span style={{ fontSize: '0.75rem', color: addMsg.startsWith('✓') ? '#34d399' : '#f87171' }}>{addMsg}</span>}
              </div>
            </div>

            {/* RSS feed */}
            <div>
              <div className="section-title">RSS Feed</div>
              <div style={{ marginBottom: 8 }}>
                <div className="label" style={{ marginBottom: 3 }}>Feed URL</div>
                <input style={inputStyle} value={rssUrl} onChange={e => setRssUrl(e.target.value)} placeholder="https://example.com/feed.xml" />
              </div>
              <div style={{ marginBottom: 10 }}>
                <div className="label" style={{ marginBottom: 3 }}>Label (optional)</div>
                <input style={inputStyle} value={rssLabel} onChange={e => setRssLabel(e.target.value)} placeholder="e.g. Lakeview Tribune" />
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="btn-primary" onClick={addRss} disabled={addLoading || !rssUrl.trim()}>
                  {addLoading ? 'Fetching…' : 'Import Feed'}
                </button>
                {rssMsg && <span style={{ fontSize: '0.75rem', color: rssMsg.startsWith('✓') ? '#34d399' : '#f87171' }}>{rssMsg}</span>}
              </div>
            </div>

            {/* URL */}
            <div>
              <div className="section-title">Fetch URL</div>
              <div style={{ marginBottom: 10 }}>
                <div className="label" style={{ marginBottom: 3 }}>Article / Page URL</div>
                <input style={inputStyle} value={urlInput} onChange={e => setUrlInput(e.target.value)} placeholder="https://article.com/…" />
              </div>
              <p style={{ margin: '0 0 10px', fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                Fetches the page, extracts content, and analyzes it for issues and urgency.
              </p>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button className="btn-primary" onClick={addUrl} disabled={addLoading || !urlInput.trim()}>
                  {addLoading ? 'Fetching…' : 'Fetch & Analyze'}
                </button>
                {urlMsg && <span style={{ fontSize: '0.75rem', color: urlMsg.startsWith('✓') ? '#34d399' : '#f87171' }}>{urlMsg}</span>}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: '1.5rem', marginBottom: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {SOURCE_TYPES.map(t => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              style={{
                padding: '0.3rem 0.7rem', borderRadius: 5, fontSize: '0.72rem',
                background: typeFilter === t ? 'rgba(59,130,246,0.2)' : 'var(--surface-1)',
                border: `1px solid ${typeFilter === t ? 'rgba(59,130,246,0.4)' : 'var(--border)'}`,
                color: typeFilter === t ? '#93c5fd' : 'var(--text-muted)',
                cursor: 'pointer',
              }}
            >
              {t === 'all' ? 'All Types' : t.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {URGENCIES.map(u => (
            <button
              key={u}
              onClick={() => setUrgencyFilter(u)}
              style={{
                padding: '0.3rem 0.7rem', borderRadius: 5, fontSize: '0.72rem',
                background: urgencyFilter === u ? 'rgba(245,158,11,0.15)' : 'var(--surface-1)',
                border: `1px solid ${urgencyFilter === u ? 'rgba(245,158,11,0.3)' : 'var(--border)'}`,
                color: urgencyFilter === u ? '#fbbf24' : 'var(--text-muted)',
                cursor: 'pointer',
              }}
            >
              {u === 'all' ? 'All Urgency' : u}
            </button>
          ))}
        </div>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 'auto', fontFamily: 'JetBrains Mono' }}>
          {sources.length} items
        </span>
      </div>

      {/* Source list */}
      {loading && <div style={{ color: 'var(--text-muted)', padding: '2rem 0' }}>Loading…</div>}
      {!loading && error && <div style={{ color: '#f87171', padding: '1rem 0' }}>Error: {error}</div>}
      {!loading && !error && sources.length === 0 && (
        <div style={{
          textAlign: 'center', padding: '3rem 1rem',
          color: 'var(--text-muted)', border: '1px dashed var(--border)', borderRadius: 8,
        }}>
          <div style={{ fontSize: '1.5rem', marginBottom: 8, opacity: 0.4 }}>📰</div>
          <div style={{ fontWeight: 500, marginBottom: 6 }}>No sources match this filter</div>
          <div style={{ fontSize: '0.8rem' }}>
            {typeFilter !== 'all' || urgencyFilter !== 'all'
              ? 'Try clearing filters, or add a new source above.'
              : 'Add your first source using the "+ Add Source" button above.'}
          </div>
        </div>
      )}
      {!loading && !error && sources.map(s => (
        <div key={s.id} onClick={() => setActiveDrawerId(s.id)} style={{ cursor: 'pointer' }}>
          <SourceCard source={s} />
        </div>
      ))}

      {/* Detail drawer */}
      {activeDrawerId !== null && (
        <SourceDrawer
          sourceId={activeDrawerId}
          onClose={() => setActiveDrawerId(null)}
        />
      )}
    </div>
  )
}
