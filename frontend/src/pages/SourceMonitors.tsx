import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { SourceMonitor, Outlet, SourceTemplate, ManualSourceReminder } from '../api/types'

// ── colour tokens ─────────────────────────────────────────────────────────────
const C = {
  bg:       'var(--bg, #0f172a)',
  surface:  'var(--surface-1, #1e293b)',
  surface2: 'var(--surface-2, #273548)',
  border:   'var(--border, #1e293b)',
  text:     'var(--text, #f1f5f9)',
  textSec:  'var(--text-secondary, #94a3b8)',
  textMuted:'var(--text-muted, #64748b)',
  accent:   '#ffbf00',
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

function fmtDate(s: string | null) {
  if (!s) return 'Never'
  const d = new Date(s)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
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

// ── shared styles ─────────────────────────────────────────────────────────────
const inputStyle: React.CSSProperties = {
  width: '100%', background: '#0f172a', border: `1px solid #334155`,
  borderRadius: 6, padding: '7px 10px', color: '#f1f5f9', fontSize: 13,
  outline: 'none', boxSizing: 'border-box',
}
const btnPrimary: React.CSSProperties = {
  background: '#ffbf00', color: '#000', border: 'none', borderRadius: 6,
  padding: '7px 16px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
}
const btnSecondary: React.CSSProperties = {
  background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '7px 16px', fontSize: 13, cursor: 'pointer',
}
const btnDanger: React.CSSProperties = {
  background: 'transparent', color: '#ef4444', border: '1px solid #ef444444',
  borderRadius: 6, padding: '4px 10px', fontSize: 12, cursor: 'pointer',
}
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

// ── badge ─────────────────────────────────────────────────────────────────────
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
interface MonitorInitial { name?: string; url?: string; sourceType?: string }

function AddMonitorModal({ onClose, onCreated, initial }: {
  onClose: () => void
  onCreated: (m: SourceMonitor) => void
  initial?: MonitorInitial
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [type, setType] = useState('rss')
  const [query, setQuery] = useState('')
  const [url, setUrl] = useState(initial?.url ?? '')
  const [sourceType, setSourceType] = useState(initial?.sourceType ?? 'news')
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

// ── templates panel ───────────────────────────────────────────────────────────
function TemplatesPanel({ templates, onUse }: {
  templates: SourceTemplate[]
  onUse: (t: SourceTemplate) => void
}) {
  const [open, setOpen] = useState(false)
  if (templates.length === 0) return null
  const categories = Array.from(new Set(templates.map(t => t.category)))

  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: '14px 16px', marginBottom: 16,
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: C.text, fontFamily: 'inherit' }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>Source Templates</span>
        <span style={{ fontSize: 11, color: C.textMuted }}>{open ? '▲ Hide' : `▼ Show (${templates.length})`}</span>
      </button>

      {open && (
        <div style={{ marginTop: 12 }}>
          <p style={{ margin: '0 0 12px', fontSize: 12, color: C.textMuted, lineHeight: 1.5 }}>
            Common feed types for local campaigns. Click Use to pre-fill the Add Monitor form.
          </p>
          {categories.map(cat => (
            <div key={cat} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontFamily: 'monospace', letterSpacing: '0.07em', color: C.textMuted, textTransform: 'uppercase', marginBottom: 8 }}>
                {cat}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 }}>
                {templates.filter(t => t.category === cat).map(t => (
                  <div key={t.id} style={{
                    padding: '10px 12px', background: C.surface2,
                    border: `1px solid ${C.border}`, borderRadius: 8,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                      <div style={{ fontWeight: 600, fontSize: 12, color: C.text }}>{t.name}</div>
                      <button
                        onClick={() => onUse(t)}
                        style={{ ...btnSecondary, padding: '2px 10px', fontSize: 11, marginLeft: 8, flexShrink: 0 }}
                      >
                        Use
                      </button>
                    </div>
                    <p style={{ margin: '0 0 4px', fontSize: 11, color: C.textSec, lineHeight: 1.4 }}>{t.description}</p>
                    {t.url_pattern && (
                      <div style={{ fontSize: 10, fontFamily: 'monospace', color: '#a78bfa', wordBreak: 'break-all' }}>
                        {t.url_pattern}
                      </div>
                    )}
                    {t.setup_note && (
                      <div style={{ fontSize: 10, color: C.textMuted, marginTop: 4, lineHeight: 1.4 }}>{t.setup_note}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── source reminders ──────────────────────────────────────────────────────────
function RemindersSection({ reminders, onMarkChecked, onDelete, onAdd }: {
  reminders: ManualSourceReminder[]
  onMarkChecked: (id: number) => void
  onDelete: (id: number) => void
  onAdd: (name: string, url: string, note: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [note, setNote] = useState('')
  const [adding, setAdding] = useState(false)
  const activeCount = reminders.filter(r => r.active).length

  async function submit() {
    if (!name.trim()) return
    setAdding(true)
    try { await onAdd(name.trim(), url.trim(), note.trim()) }
    finally { setAdding(false) }
    setName(''); setUrl(''); setNote('')
  }

  return (
    <div style={{ borderTop: `1px solid ${C.border}`, marginTop: 24, paddingTop: 16 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 0', color: C.text, fontFamily: 'inherit', marginBottom: 4 }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
          Source Reminders
          {activeCount > 0 && (
            <span style={{ marginLeft: 8, fontSize: 11, color: C.textMuted, fontFamily: 'monospace' }}>
              {activeCount} active
            </span>
          )}
        </span>
        <span style={{ fontSize: 11, color: C.textMuted }}>
          {open ? '▲ Hide' : '▼ Show'} — non-RSS sources to check manually
        </span>
      </button>

      {open && (
        <div style={{ marginTop: 12 }}>
          <p style={{ margin: '0 0 14px', fontSize: 12, color: C.textMuted, lineHeight: 1.5 }}>
            Track pages that aren't RSS-compatible (FEC filings, opponent social media, ballot pages, etc.).
          </p>

          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: '12px 14px', marginBottom: 12 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: C.textMuted, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>Add Reminder</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Check FEC filings" style={inputStyle} />
              <input value={url} onChange={e => setUrl(e.target.value)} placeholder="URL (optional)" style={inputStyle} />
            </div>
            <input value={note} onChange={e => setNote(e.target.value)} placeholder="Setup note (optional)" style={{ ...inputStyle, marginBottom: 10 }} />
            <button onClick={submit} disabled={adding} style={{ ...btnPrimary, padding: '5px 14px', fontSize: 12 }}>
              {adding ? 'Adding…' : 'Add Reminder'}
            </button>
          </div>

          {reminders.length === 0 && (
            <div style={{ fontSize: 13, color: C.textMuted, padding: '8px 0' }}>No reminders yet.</div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {reminders.map(r => {
              const staleDays = r.last_checked_at
                ? Math.floor((Date.now() - new Date(r.last_checked_at).getTime()) / 86400000)
                : null
              const isStale = staleDays === null || staleDays > 7
              return (
                <div key={r.id} style={{
                  background: C.surface, border: `1px solid ${C.border}`,
                  borderLeft: `3px solid ${isStale ? C.amber : C.green}`,
                  borderRadius: 8, padding: '10px 14px',
                  opacity: r.active ? 1 : 0.5,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 3, color: C.text }}>{r.name}</div>
                      {r.url && (
                        <a href={r.url} target="_blank" rel="noopener noreferrer"
                          style={{ fontSize: 11, color: '#a78bfa', fontFamily: 'monospace', display: 'block', marginBottom: 2 }}>
                          {r.url.length > 60 ? r.url.slice(0, 60) + '…' : r.url} ↗
                        </a>
                      )}
                      {r.setup_note && (
                        <div style={{ fontSize: 12, color: C.textMuted, lineHeight: 1.4, marginBottom: 4 }}>{r.setup_note}</div>
                      )}
                      <div style={{ fontSize: 11, color: isStale ? C.amber : C.textMuted, fontFamily: 'monospace' }}>
                        {r.last_checked_at ? `Last checked ${staleDays}d ago — ${fmtDate(r.last_checked_at)}` : 'Never checked'}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginLeft: 12, flexShrink: 0 }}>
                      <button onClick={() => onMarkChecked(r.id)} style={{ ...btnSecondary, padding: '4px 10px', fontSize: 11 }}>
                        Mark Checked
                      </button>
                      <button onClick={() => onDelete(r.id)} style={btnDanger}>Remove</button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────
type Tab = 'monitors' | 'outlets'

export default function SourceMonitors() {
  const [tab, setTab] = useState<Tab>('monitors')
  const [monitors, setMonitors] = useState<SourceMonitor[]>([])
  const [outlets, setOutlets] = useState<Outlet[]>([])
  const [templates, setTemplates] = useState<SourceTemplate[]>([])
  const [reminders, setReminders] = useState<ManualSourceReminder[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddMonitor, setShowAddMonitor] = useState(false)
  const [addMonitorInitial, setAddMonitorInitial] = useState<MonitorInitial | undefined>()
  const [showAddOutlet, setShowAddOutlet] = useState(false)
  const [lastCrawlAt, setLastCrawlAt] = useState<string | null>(null)
  const [lastRedditAt, setLastRedditAt] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      api.getSourceMonitors(),
      api.getOutlets(),
      api.getIngestStatus(),
      api.getSourceTemplates(),
      api.getSourceReminders(),
    ]).then(([m, o, s, tmpl, rem]) => {
      setMonitors(m)
      setOutlets(o)
      setLastCrawlAt(s.last_crawl_at)
      setLastRedditAt(s.last_reddit_at)
      setTemplates(tmpl)
      setReminders(rem)
    }).catch(() => {}).finally(() => setLoading(false))
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

  function handleMonitorCreated(m: SourceMonitor) {
    setMonitors(prev => [...prev, m])
    setShowAddMonitor(false)
    setAddMonitorInitial(undefined)
    if (m.monitor_type === 'webpage' || m.monitor_type === 'rss') {
      api.triggerCrawl()
        .then(() => api.getIngestStatus())
        .then(s => setLastCrawlAt(s.last_crawl_at))
        .catch(() => {})
    }
  }

  function useTemplate(t: SourceTemplate) {
    setAddMonitorInitial({ name: t.name, url: t.url_pattern ?? '', sourceType: t.source_type })
    setShowAddMonitor(true)
  }

  async function addReminder(name: string, url: string, note: string) {
    const r = await api.createSourceReminder({ name, url: url || undefined, setup_note: note || undefined })
    setReminders(prev => [...prev, r])
  }

  async function markReminderChecked(id: number) {
    try {
      const r = await api.markReminderChecked(id)
      setReminders(prev => prev.map(x => x.id === id ? r : x))
    } catch { /* toast handles it */ }
  }

  async function deleteReminder(id: number) {
    try {
      await api.deleteSourceReminder(id)
      setReminders(prev => prev.filter(x => x.id !== id))
    } catch { /* toast handles it */ }
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
            <span style={{ marginLeft: 12, color: C.textMuted }}>·</span>
            <span style={{ marginLeft: 8, fontSize: 12, color: C.textMuted }}>
              crawler {timeAgo(lastCrawlAt)}
            </span>
            <span style={{ marginLeft: 8, color: C.textMuted }}>·</span>
            <span style={{ marginLeft: 8, fontSize: 12, color: C.textMuted }}>
              reddit {timeAgo(lastRedditAt)}
            </span>
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
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

      {/* Monitors tab */}
      {!loading && tab === 'monitors' && (
        <>
          <TemplatesPanel templates={templates} onUse={useTemplate} />

          {monitors.length === 0 ? (
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
          )}

          <RemindersSection
            reminders={reminders}
            onMarkChecked={markReminderChecked}
            onDelete={deleteReminder}
            onAdd={addReminder}
          />
        </>
      )}

      {/* Outlets tab */}
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
          onClose={() => { setShowAddMonitor(false); setAddMonitorInitial(undefined) }}
          onCreated={handleMonitorCreated}
          initial={addMonitorInitial}
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
