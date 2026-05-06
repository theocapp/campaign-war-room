import { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'
import type { RssFeed, RssFeedIngestResult, SourceTemplate, SourcePack, SourcePackApplyResult, ManualSourceReminder } from '../api/types'

function fmtDate(s: string | null) {
  if (!s) return 'Never'
  const d = new Date(s)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
}

function IngestResult({ result }: { result: RssFeedIngestResult }) {
  return (
    <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
      <span style={{ color: 'var(--ok-light)' }}>+{result.added_count}</span> added · {result.skipped_count} skipped
      {result.error_count > 0 && <span style={{ color: 'var(--opponent)' }}> · {result.error_count} errors</span>}
    </span>
  )
}

function TemplatesPanel({ templates, onUseTemplate }: { templates: SourceTemplate[]; onUseTemplate: (t: SourceTemplate) => void }) {
  const [open, setOpen] = useState(false)
  const categories = Array.from(new Set(templates.map(t => t.category)))

  return (
    <div className="card" style={{ marginBottom: '1.25rem' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--text-primary)', fontFamily: 'inherit' }}
      >
        <div className="label" style={{ marginBottom: 0 }}>Source Templates</div>
        <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
          {open ? '▲ Hide' : '▼ Show'} ({templates.length})
        </span>
      </button>

      {open && (
        <div style={{ marginTop: '1rem' }}>
          <p style={{ margin: '0 0 1rem', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Common feed types for local campaigns. Click Use to pre-fill the add form.
          </p>
          {categories.map(cat => (
            <div key={cat} style={{ marginBottom: '1rem' }}>
              <div style={{ fontSize: '0.62rem', fontFamily: 'JetBrains Mono', letterSpacing: '0.07em', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 8 }}>
                {cat}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 }}>
                {templates.filter(t => t.category === cat).map(t => (
                  <div key={t.id} className="card" style={{ padding: '0.65rem', background: 'var(--surface-1)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                      <div style={{ fontWeight: 600, fontSize: '0.79rem' }}>{t.name}</div>
                      <button className="btn btn-ghost btn-xs" onClick={() => onUseTemplate(t)} style={{ flexShrink: 0, marginLeft: 8 }}>Use</button>
                    </div>
                    <p style={{ margin: '0 0 5px', fontSize: '0.71rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{t.description}</p>
                    {t.url_pattern && (
                      <div style={{ fontSize: '0.62rem', fontFamily: 'JetBrains Mono', color: 'var(--accent-light)', wordBreak: 'break-all' }}>
                        {t.url_pattern}
                      </div>
                    )}
                    {t.setup_note && (
                      <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.4 }}>{t.setup_note}</div>
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

export default function RssFeeds() {
  const [feeds, setFeeds]                   = useState<RssFeed[]>([])
  const [loading, setLoading]               = useState(true)
  const [error, setError]                   = useState<string | null>(null)
  const [templates, setTemplates]           = useState<SourceTemplate[]>([])
  const [packs, setPacks]                   = useState<SourcePack[]>([])
  const [applyResults, setApplyResults]     = useState<Record<number, SourcePackApplyResult>>({})
  const [applyLoading, setApplyLoading]     = useState<number | null>(null)
  const [reminders, setReminders]           = useState<ManualSourceReminder[]>([])
  const [newReminderName, setNewReminderName] = useState('')
  const [newReminderUrl, setNewReminderUrl]   = useState('')
  const [newReminderNote, setNewReminderNote] = useState('')
  const [addingReminder, setAddingReminder]  = useState(false)
  const [remindersOpen, setRemindersOpen]    = useState(false)
  const [newName, setNewName]               = useState('')
  const [newUrl, setNewUrl]                 = useState('')
  const [newType, setNewType]               = useState('news')
  const [addError, setAddError]             = useState<string | null>(null)
  const [adding, setAdding]                 = useState(false)
  const [ingestResults, setIngestResults]   = useState<Record<number, RssFeedIngestResult>>({})
  const [ingestLoading, setIngestLoading]   = useState<number | 'all' | null>(null)
  const [ingestAllSummary, setIngestAllSummary] = useState<string | null>(null)

  const load = useCallback(() => {
    api.getRssFeeds().then(setFeeds).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    api.getSourceTemplates().then(setTemplates).catch(() => {})
    api.getSourcePacks().then(setPacks).catch(() => {})
    api.getSourceReminders().then(setReminders).catch(() => {})
  }, [load])

  async function applyPack(id: number) {
    setApplyLoading(id)
    try {
      const r = await api.applySourcePack(id)
      setApplyResults(prev => ({ ...prev, [id]: r }))
      load()
      api.getSourceReminders().then(setReminders).catch(() => {})
    } catch { /* silent */ } finally { setApplyLoading(null) }
  }

  async function addReminder() {
    if (!newReminderName.trim()) return
    setAddingReminder(true)
    try {
      const r = await api.createSourceReminder({ name: newReminderName.trim(), url: newReminderUrl.trim() || undefined, setup_note: newReminderNote.trim() || undefined })
      setReminders(prev => [...prev, r])
      setNewReminderName(''); setNewReminderUrl(''); setNewReminderNote('')
    } catch { /* silent */ } finally { setAddingReminder(false) }
  }

  async function markChecked(id: number) {
    try {
      const r = await api.markReminderChecked(id)
      setReminders(prev => prev.map(x => x.id === id ? r : x))
    } catch { /* silent */ }
  }

  async function deleteReminder(id: number) {
    try {
      await api.deleteSourceReminder(id)
      setReminders(prev => prev.filter(x => x.id !== id))
    } catch { /* silent */ }
  }

  async function addFeed() {
    if (!newName.trim() || !newUrl.trim()) { setAddError('Name and URL are required.'); return }
    setAdding(true); setAddError(null)
    try {
      const feed = await api.createRssFeed({ name: newName.trim(), url: newUrl.trim(), source_type: newType })
      setFeeds(prev => [feed, ...prev])
      setNewName(''); setNewUrl('')
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : 'Failed to add feed')
    } finally { setAdding(false) }
  }

  async function toggleActive(feed: RssFeed) {
    try {
      const updated = await api.updateRssFeed(feed.id, { active: !feed.active })
      setFeeds(prev => prev.map(f => f.id === feed.id ? updated : f))
    } catch { /* silent */ }
  }

  async function deleteFeed(id: number) {
    if (!confirm('Delete this feed? Ingested sources will not be removed.')) return
    try {
      await api.deleteRssFeed(id)
      setFeeds(prev => prev.filter(f => f.id !== id))
    } catch { /* silent */ }
  }

  async function ingestOne(id: number) {
    setIngestLoading(id)
    try {
      const result = await api.ingestRssFeed(id)
      setIngestResults(prev => ({ ...prev, [id]: result }))
      setFeeds(prev => prev.map(f => f.id === id ? { ...f, last_fetched_at: new Date().toISOString() } : f))
    } catch { /* silent */ } finally { setIngestLoading(null) }
  }

  async function ingestAll() {
    setIngestLoading('all'); setIngestAllSummary(null)
    try {
      const r = await api.ingestAllFeeds() as { feeds_processed: number; results: { added_count: number; skipped_count: number }[] }
      const total = r.results.reduce((s, x) => s + x.added_count, 0)
      const skipped = r.results.reduce((s, x) => s + x.skipped_count, 0)
      setIngestAllSummary(`${r.feeds_processed} feeds · +${total} added · ${skipped} skipped`)
      load()
    } catch { /* silent */ } finally { setIngestLoading(null) }
  }

  function useTemplate(t: SourceTemplate) {
    setNewName(t.name)
    setNewUrl(t.url_pattern ?? '')
    setNewType(t.source_type)
  }

  if (loading) return <div className="loading-text">Loading…</div>
  if (error)   return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  return (
    <div className="page">
      {/* Header */}
      <div className="page-header">
        <div className="label" style={{ marginBottom: 5 }}>Sources</div>
        <h1 className="page-title">RSS Feeds</h1>
        <p className="page-subtitle">Configure persistent feeds. Duplicates are skipped automatically.</p>
      </div>

      {/* Starter packs */}
      {packs.length > 0 && (
        <div className="card" style={{ marginBottom: '1.25rem' }}>
          <div className="label" style={{ marginBottom: 6 }}>Starter Packs</div>
          <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Pre-configure feeds and reminders for your race type. Placeholder items become reminders you set up manually.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {packs.map(pack => (
              <div key={pack.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: '0.75rem', background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: 2 }}>{pack.name}</div>
                  {pack.description && <p style={{ margin: '0 0 4px', fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>{pack.description}</p>}
                  <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    {pack.items.length} items · {pack.race_level} · {pack.geography?.replace('_', ' ')}
                  </div>
                  {applyResults[pack.id] && (
                    <div style={{ marginTop: 5, fontSize: '0.7rem', color: 'var(--ok-light)' }}>
                      ✓ {applyResults[pack.id].feeds_created} feed(s), {applyResults[pack.id].reminders_created} reminder(s) created
                      {applyResults[pack.id].skipped_duplicate_feeds > 0 && `, ${applyResults[pack.id].skipped_duplicate_feeds} skipped`}
                    </div>
                  )}
                </div>
                <button className="btn btn-primary btn-sm" style={{ marginLeft: 12, flexShrink: 0 }} onClick={() => applyPack(pack.id)} disabled={applyLoading === pack.id}>
                  {applyLoading === pack.id ? '…' : 'Apply'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Templates */}
      {templates.length > 0 && <TemplatesPanel templates={templates} onUseTemplate={useTemplate} />}

      {/* Add feed form */}
      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <div className="label" style={{ marginBottom: 10 }}>Add Feed</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: 10, marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 4 }}>Name</div>
            <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="e.g. Lakeview Tribune" />
          </div>
          <div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 4 }}>Feed URL</div>
            <input value={newUrl} onChange={e => setNewUrl(e.target.value)} placeholder="https://example.com/feed.rss" onKeyDown={e => e.key === 'Enter' && addFeed()} />
          </div>
          <div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 4 }}>Type</div>
            <select value={newType} onChange={e => setNewType(e.target.value)}>
              <option value="news">News</option>
              <option value="opponent_statement">Opponent</option>
              <option value="public_record">Public Record</option>
              <option value="social">Social</option>
            </select>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="btn btn-primary btn-sm" onClick={addFeed} disabled={adding}>{adding ? 'Adding…' : 'Add Feed'}</button>
          {addError && <span style={{ fontSize: '0.75rem', color: 'var(--opponent)' }}>{addError}</span>}
        </div>
      </div>

      {/* Ingest all */}
      {feeds.length > 0 && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: '1.25rem' }}>
          <button className="btn btn-primary btn-sm" onClick={ingestAll} disabled={ingestLoading === 'all'}>
            {ingestLoading === 'all' ? 'Ingesting…' : `Ingest All Active (${feeds.filter(f => f.active).length})`}
          </button>
          {ingestAllSummary && (
            <span style={{ fontSize: '0.73rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>{ingestAllSummary}</span>
          )}
        </div>
      )}

      {/* Feed list */}
      {feeds.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">◻</div>
          <div className="empty-state-title">No feeds yet</div>
          <div className="empty-state-body">Add a feed above or apply a starter pack.</div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: '2rem' }}>
        {feeds.map(feed => (
          <div key={feed.id} className="card" style={{
            opacity: feed.active ? 1 : 0.55,
            borderLeft: `3px solid ${feed.active ? 'var(--accent-border)' : 'var(--border)'}`,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, fontSize: '0.87rem', color: 'var(--text-primary)' }}>{feed.name}</span>
                  <span style={{
                    fontSize: '0.58rem', padding: '1px 7px', borderRadius: 99,
                    background: feed.active ? 'rgba(52,211,153,0.12)' : 'var(--surface-2)',
                    color: feed.active ? '#86efac' : 'var(--text-muted)',
                    fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.05em',
                  }}>
                    {feed.active ? 'Active' : 'Paused'}
                  </span>
                  <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{feed.source_type}</span>
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: 3, wordBreak: 'break-all' }}>
                  {feed.url}
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                  Last fetched: {fmtDate(feed.last_fetched_at)}
                </div>
                {ingestResults[feed.id] && (
                  <div style={{ marginTop: 4 }}>
                    <IngestResult result={ingestResults[feed.id]} />
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', gap: 6, marginLeft: 12, flexShrink: 0 }}>
                <button className="btn btn-primary btn-sm" onClick={() => ingestOne(feed.id)} disabled={ingestLoading === feed.id}>
                  {ingestLoading === feed.id ? '…' : 'Ingest'}
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => toggleActive(feed)}>
                  {feed.active ? 'Pause' : 'Resume'}
                </button>
                <button className="btn btn-danger btn-sm" onClick={() => deleteFeed(feed.id)}>Delete</button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Source Reminders */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1.5rem' }}>
        <button
          onClick={() => setRemindersOpen(o => !o)}
          style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'none', border: 'none', cursor: 'pointer', padding: '0.25rem 0', color: 'var(--text-primary)', fontFamily: 'inherit', marginBottom: '0.25rem' }}
        >
          <span style={{ fontSize: '0.87rem', fontWeight: 600 }}>
            Source Reminders
            {reminders.filter(r => r.active).length > 0 && (
              <span style={{ marginLeft: 8, fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                {reminders.filter(r => r.active).length} active
              </span>
            )}
          </span>
          <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
            {remindersOpen ? '▲ Hide' : '▼ Show'} — non-RSS sources to check manually
          </span>
        </button>

        {remindersOpen && (
          <div style={{ marginTop: '0.75rem' }}>
            <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              Track pages that aren't RSS-compatible but need regular checking (FEC filings, opponent social media, ballot pages, etc.).
            </p>

            {/* Add reminder form */}
            <div className="card" style={{ marginBottom: '0.75rem' }}>
              <div className="label" style={{ marginBottom: 8 }}>Add Reminder</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
                <input value={newReminderName} onChange={e => setNewReminderName(e.target.value)} placeholder="e.g. Check FEC filings page" />
                <input value={newReminderUrl} onChange={e => setNewReminderUrl(e.target.value)} placeholder="URL (optional)" />
              </div>
              <input value={newReminderNote} onChange={e => setNewReminderNote(e.target.value)} placeholder="Setup note (optional)" style={{ marginBottom: 8 }} />
              <button className="btn btn-primary btn-sm" onClick={addReminder} disabled={addingReminder}>
                {addingReminder ? 'Adding…' : 'Add Reminder'}
              </button>
            </div>

            {reminders.length === 0 && (
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '0.5rem 0' }}>No reminders yet.</div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {reminders.map(r => {
                const staleDays = r.last_checked_at
                  ? Math.floor((Date.now() - new Date(r.last_checked_at).getTime()) / 86400000)
                  : null
                const isStale = staleDays === null || staleDays > 7
                return (
                  <div key={r.id} className="card" style={{
                    opacity: r.active ? 1 : 0.5,
                    borderLeft: `3px solid ${isStale ? 'var(--warning-border)' : 'var(--ok-border)'}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 3 }}>{r.name}</div>
                        {r.url && (
                          <a href={r.url} target="_blank" rel="noopener noreferrer"
                            style={{ fontSize: '0.67rem', color: 'var(--accent-light)', fontFamily: 'JetBrains Mono', display: 'block', marginBottom: 2 }}>
                            {r.url.length > 60 ? r.url.slice(0, 60) + '…' : r.url} ↗
                          </a>
                        )}
                        {r.setup_note && (
                          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.4, marginBottom: 4 }}>{r.setup_note}</div>
                        )}
                        <div style={{ fontSize: '0.62rem', color: isStale ? '#fbbf24' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                          {r.last_checked_at ? `Last checked ${staleDays}d ago` : 'Never checked'}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 6, marginLeft: 12, flexShrink: 0 }}>
                        <button className="btn btn-primary btn-sm" onClick={() => markChecked(r.id)}>Mark Checked</button>
                        <button className="btn btn-danger btn-sm" onClick={() => deleteReminder(r.id)}>Remove</button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
