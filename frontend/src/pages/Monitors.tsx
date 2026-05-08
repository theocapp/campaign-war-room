import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { SourceMonitor, GenerateMonitorsResult, MonitorIngestResult } from '../api/types'

const TYPE_TABS = ['all', 'search_query', 'manual', 'webpage', 'rss']

function fmtDate(s: string | null) {
  if (!s) return 'Never'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function terms(v: string[] | null | undefined) {
  return (v || []).filter(Boolean).join(', ') || '—'
}

export default function Monitors() {
  const [monitors, setMonitors]         = useState<SourceMonitor[]>([])
  const [filter, setFilter]             = useState('all')
  const [loading, setLoading]           = useState(true)
  const [suggestions, setSuggestions]   = useState<GenerateMonitorsResult | null>(null)
  const [msg, setMsg]                   = useState<string | null>(null)
  const [ingestResults, setIngestResults] = useState<MonitorIngestResult[]>([])
  const [editing, setEditing]           = useState<number | null>(null)
  const [draft, setDraft]               = useState<Partial<SourceMonitor>>({})
  const [acting, setActing]             = useState<number | 'preview' | 'apply' | 'all' | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getMonitors(filter).then(setMonitors).finally(() => setLoading(false))
  }, [filter])

  useEffect(() => { load() }, [load])

  async function preview() {
    setActing('preview')
    setMsg(null)
    try {
      const result = await api.generateMonitors({ apply: false })
      setSuggestions(result)
    } finally { setActing(null) }
  }

  async function apply(replace_existing = false) {
    setActing('apply')
    setMsg(null)
    try {
      const result = await api.generateMonitors({ apply: true, replace_existing })
      setSuggestions(result)
      setMsg(`Created ${result.created_count} monitors · skipped ${result.skipped_duplicates} duplicates.`)
      load()
    } finally { setActing(null) }
  }

  async function saveEdit(id: number) {
    await api.updateMonitor(id, {
      name: draft.name, query: draft.query || null, url: draft.url || null,
      category: draft.category || null, active: draft.active, relevance_hint: draft.relevance_hint || null,
    })
    setEditing(null)
    setDraft({})
    load()
  }

  async function remove(id: number) {
    if (!confirm('Delete this monitor?')) return
    await api.deleteMonitor(id)
    load()
  }

  async function markChecked(id: number) {
    await api.markMonitorChecked(id)
    load()
  }

  async function ingest(id: number) {
    setActing(id)
    try {
      const result = await api.ingestMonitor(id)
      setIngestResults([result])
      setMsg(`${result.monitor_name}: +${result.added_count} added · ${result.skipped_count} skipped · ${result.failed_count} failed.`)
      load()
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : 'Ingestion failed.')
    } finally { setActing(null) }
  }

  async function ingestAllSearch() {
    setActing('all')
    try {
      const result = await api.ingestSearchMonitors()
      setIngestResults(result.results)
      setMsg(`${result.monitor_count} monitors checked · +${result.added_count} added · ${result.skipped_count} skipped · ${result.failed_count} failed.`)
      load()
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : 'Search ingestion failed.')
    } finally { setActing(null) }
  }

  return (
    <div className="page-wide">
      {/* Header */}
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <div className="label" style={{ marginBottom: 5 }}>Intelligence Collection</div>
          <h1 className="page-title">Monitors</h1>
          <p className="page-subtitle">Campaign-specific searches, checks, and feeds generated from your race profile.</p>
        </div>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => setAdvancedOpen(o => !o)}
          style={{ flexShrink: 0 }}
        >
          {advancedOpen ? 'Hide Advanced' : 'Advanced'}
        </button>
      </div>

      {/* Status message */}
      {msg && (
        <div className="info-banner" style={{ marginBottom: '1rem', fontSize: '0.78rem' }}>{msg}</div>
      )}

      {/* Last ingest results */}
      {ingestResults.length > 0 && (
        <div className="card" style={{ marginBottom: '1rem' }}>
          <div className="section-title" style={{ marginBottom: 8 }}>Last Ingestion</div>
          {ingestResults.map(r => (
            <div key={r.monitor_id} style={{ marginBottom: 8, fontSize: '0.78rem', lineHeight: 1.5 }}>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
                {r.monitor_name}: <span style={{ color: 'var(--ok-light)' }}>+{r.added_count}</span> added · {r.skipped_count} skipped · <span style={{ color: r.failed_count > 0 ? 'var(--opponent)' : 'var(--text-muted)' }}>{r.failed_count} failed</span>
              </div>
              {r.provider && <div style={{ color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: '0.68rem' }}>Provider: {r.provider}</div>}
              {r.message && <div style={{ color: 'var(--text-muted)', fontSize: '0.73rem' }}>{r.message}</div>}
              {r.results.slice(0, 5).map((item, idx) => (
                <div key={idx} style={{ color: item.status === 'failed' ? 'var(--opponent-light)' : 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: '0.64rem' }}>
                  {item.status.toUpperCase()} · {item.title || item.url || 'Untitled'}
                  {item.relevance_label && ` · ${item.relevance_label} ${item.relevance_score}`}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* AI suggestions */}
      {suggestions && suggestions.suggestions.length > 0 && (
        <div className="card" style={{ marginBottom: '1rem' }}>
          <div style={{ marginBottom: '0.75rem' }}>
            <div className="section-title" style={{ margin: 0 }}>Suggestions ({suggestions.suggestions.length})</div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8 }}>
            {suggestions.suggestions.slice(0, 12).map((s, i) => (
              <div key={`${s.name}-${i}`} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '0.65rem' }}>
                <div style={{ fontSize: '0.79rem', fontWeight: 600, marginBottom: 3 }}>{s.name}</div>
                <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'uppercase', marginBottom: 4 }}>
                  {s.monitor_type} · {s.category || 'general'}
                </div>
                <div style={{ fontSize: '0.73rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                  {s.query || s.url || s.relevance_hint}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filter tabs */}
      <div className="pill-tabs" style={{ marginBottom: '0.75rem' }}>
        {TYPE_TABS.map(t => (
          <button key={t} className={`pill-tab${filter === t ? ' active' : ''}`} onClick={() => setFilter(t)}>
            {t === 'all' ? 'All' : t.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      {loading && <div className="loading-text" style={{ textAlign: 'left', padding: '1rem 0' }}>Loading…</div>}

      {!loading && monitors.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">◻</div>
          <div className="empty-state-title">No monitors yet</div>
          <div className="empty-state-body">Monitors are generated automatically when you save your campaign profile. Use Advanced to apply or refresh them manually.</div>
        </div>
      )}

      {/* Advanced controls */}
      {advancedOpen && (
        <div className="card" style={{ marginBottom: '1rem' }}>
          <div className="section-title" style={{ marginBottom: '0.75rem' }}>Advanced</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn btn-ghost btn-sm" onClick={preview} disabled={acting === 'preview'}>
              {acting === 'preview' ? '…' : 'Preview Suggestions'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={ingestAllSearch} disabled={acting === 'all'}>
              {acting === 'all' ? '…' : 'Ingest Search'}
            </button>
            <button className="btn btn-primary btn-sm" onClick={() => apply(false)} disabled={acting === 'apply'}>
              {acting === 'apply' ? '…' : 'Apply Suggestions'}
            </button>
            <button className="btn btn-danger btn-sm" onClick={() => apply(true)} disabled={acting === 'apply'}>
              {acting === 'apply' ? '…' : 'Replace Existing'}
            </button>
          </div>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Monitors are generated and ingested automatically on campaign save. Use these controls to preview suggestions, re-run ingestion, or replace all monitors from scratch.
          </p>
        </div>
      )}

      {/* Monitor cards */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {monitors.map(m => (
          <div key={m.id} className="card">
            {editing === m.id ? (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <input value={draft.name ?? ''} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} placeholder="Name" />
                <input value={draft.category ?? ''} onChange={e => setDraft(d => ({ ...d, category: e.target.value }))} placeholder="Category" />
                <input value={draft.query ?? ''} onChange={e => setDraft(d => ({ ...d, query: e.target.value }))} placeholder="Query" />
                <input value={draft.url ?? ''} onChange={e => setDraft(d => ({ ...d, url: e.target.value }))} placeholder="URL" />
                <textarea
                  style={{ gridColumn: '1 / -1', resize: 'vertical' }}
                  value={draft.relevance_hint ?? ''}
                  onChange={e => setDraft(d => ({ ...d, relevance_hint: e.target.value }))}
                  placeholder="Relevance hint"
                />
                <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8 }}>
                  <button className="btn btn-primary btn-sm" onClick={() => saveEdit(m.id)}>Save</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>Cancel</button>
                </div>
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 8 }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '0.88rem', color: 'var(--text-primary)', marginBottom: 3 }}>{m.name}</div>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        {m.monitor_type.replace(/_/g, ' ')}
                      </span>
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>· {m.category || 'general'}</span>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: m.active ? 'var(--ok-light)' : 'var(--text-muted)', flexShrink: 0 }} />
                      <span style={{ fontSize: '0.62rem', color: m.active ? 'var(--ok-light)' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {m.active ? 'active' : 'paused'}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {(m.monitor_type === 'rss' || m.monitor_type === 'search_query') && (
                      <button className="btn btn-primary btn-sm" onClick={() => ingest(m.id)} disabled={acting === m.id}>
                        {acting === m.id ? '…' : m.monitor_type === 'rss' ? 'Ingest RSS' : 'Ingest Search'}
                      </button>
                    )}
                    {m.monitor_type !== 'rss' && (
                      <button className="btn btn-ghost btn-sm" onClick={() => markChecked(m.id)}>Mark Checked</button>
                    )}
                    <button className="btn btn-ghost btn-sm" onClick={() => { setEditing(m.id); setDraft(m) }}>Edit</button>
                    <button className="btn btn-danger btn-sm" onClick={() => remove(m.id)}>Delete</button>
                  </div>
                </div>

                <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  {m.query && <div><strong>Query:</strong> {m.query}</div>}
                  {m.url && (
                    <div><strong>URL:</strong>{' '}
                      <a href={m.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-light)' }}>{m.url}</a>
                    </div>
                  )}
                  {m.relevance_hint && <div style={{ color: 'var(--text-muted)', fontSize: '0.74rem' }}>{m.relevance_hint}</div>}
                  <div style={{ marginTop: 4, fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    Required: {terms(m.required_terms)} · Excluded: {terms(m.excluded_terms)}
                  </div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    Last checked: {fmtDate(m.last_checked_at)}
                  </div>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
