import { Plus, Radio, RefreshCw, Search, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { MonitorType, SourceMonitor } from '@/api/types'
import { formatArticleDate } from '@/lib/formatDate'

const TYPE_LABELS: Record<MonitorType, string> = {
  rss: 'RSS FEED',
  search_query: 'SEARCH',
  webpage: 'WEBPAGE',
  manual: 'MANUAL',
}

const TYPE_COLORS: Record<MonitorType, string> = {
  rss: '#4f8ef7',
  search_query: '#f0a020',
  webpage: '#2db866',
  manual: 'var(--text-2)',
}

function formatAgo(iso: string): string {
  // Compact relative-time formatter for "last checked" labels. Returns
  // "2m", "4h", "3d" etc. Empty string for invalid / missing input.
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  if (isNaN(ms) || ms < 0) return ''
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function AddMonitorModal({ onClose, onCreated }: { onClose: () => void; onCreated: (m: SourceMonitor) => void }) {
  const [name, setName] = useState('')
  const [type, setType] = useState<MonitorType>('rss')
  const [url, setUrl] = useState('')
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      const monitor = await api.createMonitor({
        name: name.trim(),
        monitor_type: type,
        url: url.trim() || undefined,
        query: query.trim() || undefined,
        active: true,
      })
      onCreated(monitor)
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create monitor')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-1)', letterSpacing: '0.06em' }}>
            ADD MONITOR
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-2)' }}><X size={18} /></button>
        </div>
        <form onSubmit={submit}>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>MONITOR NAME *</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. WNEP News RSS" required />
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>TYPE</label>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {(Object.entries(TYPE_LABELS) as [MonitorType, string][]).map(([t, label]) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setType(t)}
                  style={{
                    fontSize: 9,
                    letterSpacing: '0.1em',
                    padding: '5px 10px',
                    borderRadius: 2,
                    cursor: 'pointer',
                    border: `1px solid ${type === t ? TYPE_COLORS[t] : 'var(--bg-3)'}44`,
                    color: type === t ? TYPE_COLORS[t] : 'var(--text-2)',
                    background: type === t ? `${TYPE_COLORS[t]}11` : 'transparent',
                    outline: type === t ? `1px solid ${TYPE_COLORS[t]}` : 'none',
                    outlineOffset: 1,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {(type === 'rss' || type === 'webpage') && (
            <div style={{ marginBottom: 14 }}>
              <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>URL</label>
              <input className="input" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://..." />
            </div>
          )}
          {type === 'search_query' && (
            <div style={{ marginBottom: 14 }}>
              <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>SEARCH QUERY</label>
              <input className="input" value={query} onChange={e => setQuery(e.target.value)} placeholder='e.g. "Paige Cognetti" PA-08' />
            </div>
          )}
          {error && <div style={{ color: '#f05050', fontSize: 12, marginBottom: 12 }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" onClick={onClose} className="btn btn-ghost">Cancel</button>
            <button type="submit" disabled={saving} className="btn btn-primary">{saving ? 'Adding...' : 'Add Monitor'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export function Monitors() {
  const [monitors, setMonitors] = useState<SourceMonitor[]>([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [toggling, setToggling] = useState<Set<number>>(new Set())
  const [crawling, setCrawling] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [discoverResult, setDiscoverResult] = useState<{
    converted: number
    failed: number
    skipped_cooldown: number
  } | null>(null)

  useEffect(() => {
    api.monitors().then(setMonitors).catch(() => {}).finally(() => setLoading(false))
  }, [])

  async function toggleActive(monitor: SourceMonitor) {
    setToggling(t => new Set([...t, monitor.id]))
    try {
      const updated = await api.updateMonitor(monitor.id, { active: !monitor.active })
      setMonitors(prev => prev.map(m => m.id === monitor.id ? updated : m))
    } catch { /* silently fail */ } finally {
      setToggling(t => { const n = new Set(t); n.delete(monitor.id); return n })
    }
  }

  async function deleteMonitor(id: number) {
    if (!confirm('Delete this monitor?')) return
    try {
      await api.deleteMonitor(id)
      setMonitors(prev => prev.filter(m => m.id !== id))
    } catch { /* silently fail */ }
  }

  async function triggerCrawl() {
    setCrawling(true)
    try {
      await api.triggerCrawl()
    } catch { /* silently fail */ } finally {
      setCrawling(false)
    }
  }

  async function discoverUrls() {
    setDiscovering(true)
    setDiscoverResult(null)
    try {
      const res = await api.discoverMonitorUrls()
      setDiscoverResult({
        converted: res.converted,
        failed: res.failed,
        skipped_cooldown: res.skipped_cooldown,
      })
      // If anything converted, refetch the monitors list so the UI reflects
      // the manual → webpage type flip + new URLs without a page reload.
      if (res.converted > 0) {
        api.monitors().then(setMonitors).catch(() => {})
      }
    } catch { /* silently fail */ } finally {
      setDiscovering(false)
    }
  }

  const byType: Record<string, SourceMonitor[]> = {}
  for (const m of monitors) {
    if (!byType[m.monitor_type]) byType[m.monitor_type] = []
    byType[m.monitor_type].push(m)
  }

  return (
    <div style={{ minHeight: '100vh' }}>
      {/* Action bar */}
      <div style={{
        padding: '14px 28px',
        borderBottom: '1px solid var(--bg-3)',
        background: 'var(--bg-1)',
        backdropFilter: 'blur(8px)',
        position: 'sticky',
        top: 0,
        zIndex: 10,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'flex-end',
      }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={discoverUrls}
            disabled={discovering}
            className="btn btn-ghost"
            title="Try to find URLs for any manual website monitors and convert them to webpage monitors."
          >
            <Search size={13} style={discovering ? { animation: 'spin 1s linear infinite' } : {}} />
            {discovering ? 'Discovering...' : 'Discover URLs'}
          </button>
          <button
            onClick={triggerCrawl}
            disabled={crawling}
            className="btn btn-ghost"
          >
            <RefreshCw size={13} style={crawling ? { animation: 'spin 1s linear infinite' } : {}} />
            {crawling ? 'Crawling...' : 'Run Crawl'}
          </button>
          <button onClick={() => setShowAdd(true)} className="btn btn-primary">
            <Plus size={13} />
            Add Monitor
          </button>
        </div>
      </div>

      {/* Discover URLs result banner — auto-dismisses on close click */}
      {discoverResult && (
        <div style={{
          margin: '12px 28px 0',
          padding: '10px 14px',
          background: 'var(--bg-2)',
          border: `1px solid ${discoverResult.converted > 0 ? '#2db866' : 'var(--bg-3)'}`,
          borderLeft: `3px solid ${discoverResult.converted > 0 ? '#2db866' : '#f0a020'}`,
          borderRadius: 3,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: 12,
          color: 'var(--text-1)',
        }}>
          <div>
            <span style={{ fontWeight: 600 }}>Discovery complete.</span>{' '}
            Converted <strong style={{ color: '#2db866' }}>{discoverResult.converted}</strong> ·{' '}
            Failed <strong style={{ color: '#f05050' }}>{discoverResult.failed}</strong> ·{' '}
            Skipped (cooldown) <strong style={{ color: 'var(--text-3)' }}>{discoverResult.skipped_cooldown}</strong>
            {discoverResult.failed > 0 && discoverResult.converted === 0 && (
              <span style={{ marginLeft: 8, color: 'var(--text-3)' }}>
                — check that a search provider (e.g. Tavily) is configured.
              </span>
            )}
          </div>
          <button
            onClick={() => setDiscoverResult(null)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: 2 }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      <div style={{ padding: '20px 28px', maxWidth: 960, margin: '0 auto' }}>
        {loading && Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 60, borderRadius: 4, marginBottom: 8 }} />
        ))}

        {!loading && monitors.length === 0 && (
          <div style={{ textAlign: 'center', padding: '80px 0', color: 'var(--text-3)' }}>
            <Radio size={48} style={{ margin: '0 auto 16px', opacity: 0.3 }} />
            <div style={{ fontSize: 20, marginBottom: 8 }}>No monitors configured</div>
            <div style={{ fontSize: 13 }}>Add RSS feeds, search queries, or webpage monitors.</div>
          </div>
        )}

        {/* Stats row */}
        {monitors.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 20 }}>
            {(Object.entries(TYPE_LABELS) as [MonitorType, string][]).map(([type, label]) => {
              const count = (byType[type] ?? []).length
              return (
                <div key={type} style={{
                  padding: '12px 14px',
                  background: 'var(--bg-2)',
                  border: '1px solid var(--bg-3)',
                  borderRadius: 3,
                }}>
                  <div style={{
                    fontSize: 22,
                    fontWeight: 600,
                    color: TYPE_COLORS[type],
                    lineHeight: 1,
                  }}>
                    {count}
                  </div>
                  <div className="section-label" style={{ marginTop: 3 }}>{label}</div>
                </div>
              )
            })}
          </div>
        )}

        {/* Table */}
        {monitors.length > 0 && (
          <div style={{
            background: 'var(--bg-2)',
            border: '1px solid var(--bg-3)',
            borderRadius: 4,
            overflow: 'hidden',
          }}>
            {/* Table header */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: '1fr 120px 180px 100px 80px',
              gap: 0,
              padding: '10px 16px',
              borderBottom: '1px solid var(--bg-3)',
            }}>
              {['NAME', 'TYPE', 'URL / QUERY', 'ADDED', 'STATUS'].map(h => (
                <div key={h} className="section-label">{h}</div>
              ))}
            </div>

            {monitors.map((monitor, idx) => (
              <div
                key={monitor.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 120px 180px 100px 80px',
                  gap: 0,
                  padding: '12px 16px',
                  borderBottom: idx < monitors.length - 1 ? '1px solid var(--bg-3)' : 'none',
                  alignItems: 'center',
                  opacity: monitor.active ? 1 : 0.5,
                  transition: 'opacity 0.15s',
                }}
              >
                {/* Name + last-checked subtitle */}
                <div>
                  <div style={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 500, marginBottom: 2 }}>
                    {monitor.name}
                  </div>
                  <div style={{
                    fontSize: 9,
                    color: monitor.last_checked_at ? '#5d6f88' : 'var(--text-3)',
                    letterSpacing: '0.04em',
                  }}>
                    {monitor.last_checked_at
                      ? `last checked ${formatAgo(monitor.last_checked_at)}`
                      : 'never checked'}
                  </div>
                </div>

                {/* Type */}
                <div>
                  <span style={{
                    fontSize: 9,
                    color: TYPE_COLORS[monitor.monitor_type],
                    background: `${TYPE_COLORS[monitor.monitor_type]}11`,
                    border: `1px solid ${TYPE_COLORS[monitor.monitor_type]}33`,
                    padding: '2px 6px',
                    borderRadius: 2,
                    letterSpacing: '0.08em',
                  }}>
                    {TYPE_LABELS[monitor.monitor_type]}
                  </span>
                </div>

                {/* URL/Query */}
                <div style={{
                  fontSize: 10,
                  color: 'var(--text-3)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {monitor.url ?? monitor.query ?? '—'}
                </div>

                {/* Added */}
                <div style={{
                  fontSize: 10,
                  color: 'var(--text-3)',
                }}>
                  {formatArticleDate(monitor.created_at)}
                </div>

                {/* Status + actions */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    onClick={() => toggleActive(monitor)}
                    disabled={toggling.has(monitor.id)}
                    title={monitor.active ? 'Deactivate' : 'Activate'}
                    style={{
                      width: 32,
                      height: 18,
                      borderRadius: 9,
                      background: monitor.active ? '#1d6ae5' : 'var(--bg-3)',
                      border: 'none',
                      cursor: 'pointer',
                      position: 'relative',
                      transition: 'background 0.2s',
                      flexShrink: 0,
                    }}
                  >
                    <span style={{
                      position: 'absolute',
                      top: 2,
                      left: monitor.active ? 16 : 2,
                      width: 14,
                      height: 14,
                      borderRadius: '50%',
                      background: '#fff',
                      transition: 'left 0.2s',
                    }} />
                  </button>
                  <button
                    onClick={() => deleteMonitor(monitor.id)}
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      color: 'var(--text-3)',
                      padding: 3,
                      borderRadius: 3,
                    }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showAdd && (
        <AddMonitorModal
          onClose={() => setShowAdd(false)}
          onCreated={m => setMonitors(prev => [...prev, m])}
        />
      )}
    </div>
  )
}
