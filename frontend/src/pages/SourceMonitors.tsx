import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { SourceMonitor, Outlet } from '../api/types'

// ── colour tokens (matches rest of app) ──────────────────────────────────────
const C = {
  bg:       'var(--bg, #0f172a)',
  surface:  'var(--surface-1, #1e293b)',
  surface2: 'var(--surface-2, #273548)',
  border:   'var(--border, #1e293b)',
  text:     'var(--text, #f1f5f9)',
  textSec:  'var(--text-secondary, #94a3b8)',
  textMuted:'var(--text-muted, #64748b)',
  accent:   '#7c3aed',
  green:    '#22c55e',
  red:      '#ef4444',
  amber:    '#f59e0b',
}

function timeAgo(iso: string | null) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const TYPE_LABELS: Record<string, string> = {
  rss: 'RSS',
  search_query: 'Search',
  webpage: 'Webpage',
  manual: 'Manual',
}

const AUTHORITY_COLORS: Record<number, string> = {
  9: '#22c55e', 8: '#22c55e', 7: '#84cc16',
  6: '#facc15', 5: '#facc15', 4: '#fb923c',
  3: '#ef4444', 2: '#ef4444', 1: '#ef4444',
}

// ── shared row / cell styles ──────────────────────────────────────────────────
const th: React.CSSProperties = {
  textAlign: 'left', padding: '8px 12px', fontSize: 11,
  fontWeight: 600, color: C.textMuted, textTransform: 'uppercase',
  letterSpacing: '0.06em', borderBottom: `1px solid ${C.border}`,
  whiteSpace: 'nowrap',
}
const td: React.CSSProperties = {
  padding: '9px 12px', fontSize: 13, color: C.text,
  borderBottom: `1px solid ${C.border}`,
  verticalAlign: 'middle',
}

// ── badge component ───────────────────────────────────────────────────────────
function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '1px 7px', borderRadius: 99,
      fontSize: 11, fontWeight: 600,
      background: color + '22', color,
      border: `1px solid ${color}44`,
    }}>{label}</span>
  )
}

// ── toggle ────────────────────────────────────────────────────────────────────
function Toggle({ active, onChange }: { active: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!active)}
      style={{
        width: 36, height: 20, borderRadius: 99, border: 'none',
        background: active ? C.accent : C.surface2,
        position: 'relative', cursor: 'pointer', transition: 'background 0.15s',
        flexShrink: 0,
      }}
    >
      <span style={{
        position: 'absolute', top: 3,
        left: active ? 18 : 3,
        width: 14, height: 14, borderRadius: '50%',
        background: '#fff',
        transition: 'left 0.15s',
      }} />
    </button>
  )
}

// ── add-monitor modal ─────────────────────────────────────────────────────────
function AddMonitorModal({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (m: SourceMonitor) => void
}) {
  const [name, setName] = useState('')
  const [type, setType] = useState('rss')
  const [query, setQuery] = useState('')
  const [url, setUrl] = useState('')
  const [sourceType, setSourceType] = useState('news')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function save() {
    if (!name.trim()) { setErr('Name is required'); return }
    if (type === 'rss' && !url.trim()) { setErr('URL is required for RSS'); return }
    if (type === 'search_query' && !query.trim()) { setErr('Query is required for Search'); return }
    if (type === 'webpage' && !url.trim()) { setErr('URL is required for Webpage'); return }
    setSaving(true); setErr('')
    try {
      const m = await api.createSourceMonitor({
        name: name.trim(),
        monitor_type: type,
        query: query.trim() || undefined,
        url: url.trim() || undefined,
        source_type: sourceType,
      })
      onCreated(m)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to create monitor')
    } finally { setSaving(false) }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }} onClick={onClose}>
      <div style={{
        background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12,
        padding: 28, width: 440, maxWidth: '95vw',
      }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 20px', color: C.text, fontSize: 16 }}>Add Monitor</h3>

        {[
          { label: 'Name', el: <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Times-Tribune Politics" style={inputStyle} /> },
          { label: 'Type', el: (
            <select value={type} onChange={e => setType(e.target.value)} style={inputStyle}>
              <option value="rss">RSS Feed</option>
              <option value="webpage">Webpage / Crawler</option>
              <option value="search_query">Search Query</option>
              <option value="manual">Manual</option>
            </select>
          )},
          { label: 'Source Type', el: (
            <select value={sourceType} onChange={e => setSourceType(e.target.value)} style={inputStyle}>
              <option value="news">News</option>
              <option value="social">Social</option>
              <option value="public_record">Public Record</option>
              <option value="opponent_statement">Opponent Statement</option>
            </select>
          )},
          ...(type !== 'search_query' ? [{ label: 'URL', el: <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://…" style={inputStyle} /> }] : []),
          ...(type === 'search_query' ? [{ label: 'Search Query', el: <input value={query} onChange={e => setQuery(e.target.value)} placeholder='e.g. "Paige Cognetti" site:thetimes-tribune.com' style={inputStyle} /> }] : []),
        ].map(({ label, el }) => (
          <div key={label} style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: C.textSec, marginBottom: 5 }}>{label}</label>
            {el}
          </div>
        ))}

        {err && <p style={{ color: C.red, fontSize: 12, margin: '0 0 14px' }}>{err}</p>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={btnSecondary}>Cancel</button>
          <button onClick={save} disabled={saving} style={btnPrimary}>
            {saving ? 'Saving…' : 'Add Monitor'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── add-outlet modal ──────────────────────────────────────────────────────────
function AddOutletModal({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (o: Outlet) => void
}) {
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('')
  const [type, setType] = useState('local_news')
  const [state, setState] = useState('PA')
  const [city, setCity] = useState('')
  const [authority, setAuthority] = useState(5)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function save() {
    if (!name.trim()) { setErr('Name is required'); return }
    if (!domain.trim()) { setErr('Domain is required'); return }
    setSaving(true); setErr('')
    try {
      const o = await api.createOutlet({
        name: name.trim(), domain: domain.trim().replace(/^https?:\/\/(www\.)?/, ''),
        outlet_type: type, state: state.trim() || undefined,
        city: city.trim() || undefined, authority_score: authority,
      })
      onCreated(o)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to create outlet')
    } finally { setSaving(false) }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }} onClick={onClose}>
      <div style={{
        background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12,
        padding: 28, width: 440, maxWidth: '95vw',
      }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 20px', color: C.text, fontSize: 16 }}>Add Outlet</h3>

        {[
          { label: 'Name', el: <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Times-Tribune" style={inputStyle} /> },
          { label: 'Domain', el: <input value={domain} onChange={e => setDomain(e.target.value)} placeholder="thetimes-tribune.com" style={inputStyle} /> },
          { label: 'Type', el: (
            <select value={type} onChange={e => setType(e.target.value)} style={inputStyle}>
              <option value="local_news">Local News</option>
              <option value="regional_news">Regional News</option>
              <option value="national">National</option>
              <option value="broadcast">Broadcast</option>
              <option value="blog">Blog</option>
              <option value="social">Social</option>
            </select>
          )},
          { label: 'State', el: <input value={state} onChange={e => setState(e.target.value)} placeholder="PA" maxLength={2} style={{ ...inputStyle, width: 60 }} /> },
          { label: 'City', el: <input value={city} onChange={e => setCity(e.target.value)} placeholder="Scranton" style={inputStyle} /> },
          { label: `Authority Score (${authority}/10)`, el: (
            <input type="range" min={1} max={10} value={authority} onChange={e => setAuthority(Number(e.target.value))} style={{ width: '100%' }} />
          )},
        ].map(({ label, el }) => (
          <div key={label} style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 12, color: C.textSec, marginBottom: 5 }}>{label}</label>
            {el}
          </div>
        ))}

        {err && <p style={{ color: C.red, fontSize: 12, margin: '0 0 14px' }}>{err}</p>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={btnSecondary}>Cancel</button>
          <button onClick={save} disabled={saving} style={btnPrimary}>
            {saving ? 'Saving…' : 'Add Outlet'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── shared styles ─────────────────────────────────────────────────────────────
const inputStyle: React.CSSProperties = {
  width: '100%', background: '#0f172a', border: `1px solid #334155`,
  borderRadius: 6, padding: '7px 10px', color: '#f1f5f9', fontSize: 13,
  outline: 'none', boxSizing: 'border-box',
}
const btnPrimary: React.CSSProperties = {
  background: '#7c3aed', color: '#fff', border: 'none', borderRadius: 6,
  padding: '7px 16px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
}
const btnSecondary: React.CSSProperties = {
  background: 'transparent', color: '#94a3b8', border: '1px solid #334155',
  borderRadius: 6, padding: '7px 16px', fontSize: 13, cursor: 'pointer',
}
const btnDanger: React.CSSProperties = {
  background: 'transparent', color: '#ef4444', border: '1px solid #ef444444',
  borderRadius: 6, padding: '4px 10px', fontSize: 12, cursor: 'pointer',
}

// ── main page ─────────────────────────────────────────────────────────────────
type Tab = 'monitors' | 'outlets'

export default function SourceMonitors() {
  const [tab, setTab] = useState<Tab>('monitors')
  const [monitors, setMonitors] = useState<SourceMonitor[]>([])
  const [outlets, setOutlets] = useState<Outlet[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddMonitor, setShowAddMonitor] = useState(false)
  const [showAddOutlet, setShowAddOutlet] = useState(false)
  const [crawlStatus, setCrawlStatus] = useState<string | null>(null)
  const [redditStatus, setRedditStatus] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.getSourceMonitors(), api.getOutlets()])
      .then(([m, o]) => { setMonitors(m); setOutlets(o) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function toggleMonitor(m: SourceMonitor) {
    try {
      const updated = await api.updateSourceMonitor(m.id, { active: !m.active })
      setMonitors(prev => prev.map(x => x.id === m.id ? updated : x))
    } catch { /* toast handles it */ }
  }

  async function deleteMonitor(id: number) {
    if (!confirm('Delete this monitor?')) return
    try {
      await api.deleteSourceMonitor(id)
      setMonitors(prev => prev.filter(m => m.id !== id))
    } catch { /* toast handles it */ }
  }

  async function toggleOutlet(o: Outlet) {
    try {
      const updated = await api.updateOutlet(o.id, { active: !o.active })
      setOutlets(prev => prev.map(x => x.id === o.id ? updated : x))
    } catch { /* toast handles it */ }
  }

  async function deleteOutlet(id: number) {
    if (!confirm('Delete this outlet?')) return
    try {
      await api.deleteOutlet(id)
      setOutlets(prev => prev.filter(o => o.id !== id))
    } catch { /* toast handles it */ }
  }

  async function runCrawl() {
    setCrawlStatus('Running…')
    try {
      const r = await api.triggerCrawl()
      setCrawlStatus(`Done — ${r.total_added} added, ${r.total_skipped} skipped`)
    } catch { setCrawlStatus('Failed') }
    setTimeout(() => setCrawlStatus(null), 5000)
  }

  async function runReddit() {
    setRedditStatus('Running…')
    try {
      const r = await api.triggerReddit()
      setRedditStatus(`Done — ${r.added} added from ${r.posts_found} posts`)
    } catch { setRedditStatus('Failed') }
    setTimeout(() => setRedditStatus(null), 5000)
  }

  const activeMonitors = monitors.filter(m => m.active).length
  const activeOutlets = outlets.filter(o => o.active).length

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1100 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: C.text }}>Source Monitors</h1>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: C.textMuted }}>
            {activeMonitors} active monitors · {activeOutlets} outlets indexed
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={runCrawl} style={btnSecondary}>
            {crawlStatus ?? 'Run Crawler'}
          </button>
          <button onClick={runReddit} style={btnSecondary}>
            {redditStatus ?? 'Run Reddit'}
          </button>
          {tab === 'monitors'
            ? <button onClick={() => setShowAddMonitor(true)} style={btnPrimary}>+ Add Monitor</button>
            : <button onClick={() => setShowAddOutlet(true)} style={btnPrimary}>+ Add Outlet</button>
          }
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: `1px solid ${C.border}` }}>
        {(['monitors', 'outlets'] as Tab[]).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              padding: '8px 16px', fontSize: 13, fontWeight: tab === t ? 600 : 400,
              color: tab === t ? C.text : C.textMuted,
              borderBottom: `2px solid ${tab === t ? C.accent : 'transparent'}`,
              marginBottom: -1, textTransform: 'capitalize',
            }}
          >
            {t === 'monitors' ? `Monitors (${monitors.length})` : `Outlets (${outlets.length})`}
          </button>
        ))}
      </div>

      {loading && <p style={{ color: C.textMuted, fontSize: 13 }}>Loading…</p>}

      {/* Monitors table */}
      {!loading && tab === 'monitors' && (
        monitors.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 0', color: C.textMuted, fontSize: 14 }}>
            No monitors yet. Add one to start tracking sources automatically.
          </div>
        ) : (
          <div style={{ background: C.surface, borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['Name', 'Type', 'Source', 'Query / URL', 'Last Checked', 'Active', ''].map(h => (
                    <th key={h} style={th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {monitors.map(m => (
                  <tr key={m.id} style={{ opacity: m.active ? 1 : 0.5 }}>
                    <td style={td}>
                      <span style={{ fontWeight: 500 }}>{m.name}</span>
                      {m.category && <span style={{ fontSize: 11, color: C.textMuted, marginLeft: 6 }}>{m.category}</span>}
                    </td>
                    <td style={td}>
                      <Badge
                        label={TYPE_LABELS[m.monitor_type] ?? m.monitor_type}
                        color={m.monitor_type === 'rss' ? '#22c55e' : m.monitor_type === 'webpage' ? '#3b82f6' : m.monitor_type === 'search_query' ? '#a78bfa' : '#64748b'}
                      />
                    </td>
                    <td style={td}>
                      <span style={{ fontSize: 12, color: C.textSec }}>{m.source_type}</span>
                    </td>
                    <td style={{ ...td, maxWidth: 260 }}>
                      <span style={{
                        fontSize: 11, color: C.textMuted, fontFamily: 'monospace',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        display: 'block',
                      }}>
                        {m.query ?? m.url ?? '—'}
                      </span>
                    </td>
                    <td style={td}>
                      <span style={{ fontSize: 12, color: C.textMuted }}>{timeAgo(m.last_checked_at)}</span>
                    </td>
                    <td style={td}>
                      <Toggle active={m.active} onChange={() => toggleMonitor(m)} />
                    </td>
                    <td style={td}>
                      <button onClick={() => deleteMonitor(m.id)} style={btnDanger}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {/* Outlets table */}
      {!loading && tab === 'outlets' && (
        outlets.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 0', color: C.textMuted, fontSize: 14 }}>
            No outlets indexed yet.
          </div>
        ) : (
          <div style={{ background: C.surface, borderRadius: 10, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['Outlet', 'Domain', 'Type', 'Location', 'Authority', 'Active', ''].map(h => (
                    <th key={h} style={th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {outlets.map(o => (
                  <tr key={o.id} style={{ opacity: o.active ? 1 : 0.5 }}>
                    <td style={td}>
                      <span style={{ fontWeight: 500 }}>{o.name}</span>
                    </td>
                    <td style={td}>
                      <span style={{ fontSize: 12, color: C.textMuted, fontFamily: 'monospace' }}>{o.domain}</span>
                    </td>
                    <td style={td}>
                      <Badge
                        label={o.outlet_type.replace('_', ' ')}
                        color={o.outlet_type === 'local_news' ? '#22c55e' : o.outlet_type === 'broadcast' ? '#3b82f6' : o.outlet_type === 'blog' ? '#f59e0b' : '#94a3b8'}
                      />
                    </td>
                    <td style={td}>
                      <span style={{ fontSize: 13, color: C.textSec }}>
                        {[o.city, o.state].filter(Boolean).join(', ') || '—'}
                      </span>
                    </td>
                    <td style={td}>
                      <span style={{
                        fontSize: 13, fontWeight: 700,
                        color: AUTHORITY_COLORS[o.authority_score] ?? C.textMuted,
                      }}>
                        {o.authority_score}/10
                      </span>
                    </td>
                    <td style={td}>
                      <Toggle active={o.active} onChange={() => toggleOutlet(o)} />
                    </td>
                    <td style={td}>
                      <button onClick={() => deleteOutlet(o.id)} style={btnDanger}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {showAddMonitor && (
        <AddMonitorModal
          onClose={() => setShowAddMonitor(false)}
          onCreated={m => { setMonitors(prev => [...prev, m]); setShowAddMonitor(false) }}
        />
      )}
      {showAddOutlet && (
        <AddOutletModal
          onClose={() => setShowAddOutlet(false)}
          onCreated={o => { setOutlets(prev => [...prev, o]); setShowAddOutlet(false) }}
        />
      )}
    </div>
  )
}
