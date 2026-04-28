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
    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
      +{result.added_count} added · {result.skipped_count} skipped
      {result.error_count > 0 && <span style={{ color: '#f87171' }}> · {result.error_count} errors</span>}
    </span>
  )
}

const CATEGORY_COLORS: Record<string, string> = {
  'Local News': 'rgba(59,130,246,0.2)',
  'Government Records': 'rgba(167,139,250,0.2)',
  'Opponent Monitoring': 'rgba(239,68,68,0.15)',
  'Community': 'rgba(34,197,94,0.15)',
  'Social Media': 'rgba(236,72,153,0.15)',
}

function TemplatesPanel({
  templates,
  onUseTemplate,
}: {
  templates: SourceTemplate[]
  onUseTemplate: (t: SourceTemplate) => void
}) {
  const [open, setOpen] = useState(false)
  const categories = Array.from(new Set(templates.map(t => t.category)))

  return (
    <div className="card" style={{ marginBottom: '1.5rem' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          background: 'none', border: 'none', cursor: 'pointer', padding: 0,
          color: 'var(--text-primary)',
        }}
      >
        <div className="label" style={{ marginBottom: 0 }}>Source Templates</div>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{open ? '▲ Hide' : '▼ Show'} ({templates.length} templates)</span>
      </button>

      {open && (
        <div style={{ marginTop: 14 }}>
          <p style={{ margin: '0 0 14px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Common feed types for local campaigns. Click "Use" to pre-fill the add form.
          </p>
          {categories.map(cat => (
            <div key={cat} style={{ marginBottom: 16 }}>
              <div style={{
                fontSize: '0.65rem', fontFamily: 'JetBrains Mono', letterSpacing: '0.06em',
                color: 'var(--text-muted)', marginBottom: 8,
                padding: '2px 8px', background: CATEGORY_COLORS[cat] ?? 'var(--surface-2)',
                display: 'inline-block', borderRadius: 3,
              }}>
                {cat.toUpperCase()}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 8 }}>
                {templates.filter(t => t.category === cat).map(t => (
                  <div key={t.id} className="card" style={{ padding: '0.75rem', background: 'var(--surface-1)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                      <div style={{ fontWeight: 600, fontSize: '0.8rem' }}>{t.name}</div>
                      <button
                        className="btn-ghost"
                        style={{ fontSize: '0.65rem', padding: '0.2rem 0.5rem', flexShrink: 0, marginLeft: 8 }}
                        onClick={() => onUseTemplate(t)}
                      >
                        Use
                      </button>
                    </div>
                    <p style={{ margin: '0 0 6px', fontSize: '0.72rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                      {t.description}
                    </p>
                    {t.url_pattern && (
                      <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: 'var(--accent)', wordBreak: 'break-all', marginBottom: 4 }}>
                        {t.url_pattern}
                      </div>
                    )}
                    {t.setup_note && (
                      <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                        {t.setup_note}
                      </div>
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
  const [feeds, setFeeds] = useState<RssFeed[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [templates, setTemplates] = useState<SourceTemplate[]>([])
  const [packs, setPacks] = useState<SourcePack[]>([])
  const [applyResults, setApplyResults] = useState<Record<number, SourcePackApplyResult>>({})
  const [applyLoading, setApplyLoading] = useState<number | null>(null)
  const [reminders, setReminders] = useState<ManualSourceReminder[]>([])
  const [newReminderName, setNewReminderName] = useState('')
  const [newReminderUrl, setNewReminderUrl] = useState('')
  const [newReminderNote, setNewReminderNote] = useState('')
  const [addingReminder, setAddingReminder] = useState(false)
  const [remindersOpen, setRemindersOpen] = useState(false)

  const [newName, setNewName] = useState('')
  const [newUrl, setNewUrl] = useState('')
  const [newType, setNewType] = useState('news')
  const [addError, setAddError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const [ingestResults, setIngestResults] = useState<Record<number, RssFeedIngestResult>>({})
  const [ingestLoading, setIngestLoading] = useState<number | 'all' | null>(null)
  const [ingestAllSummary, setIngestAllSummary] = useState<string | null>(null)

  const load = useCallback(() => {
    api.getRssFeeds()
      .then(setFeeds)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
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
    } catch { /* silent */ } finally {
      setApplyLoading(null)
    }
  }

  async function addReminder() {
    if (!newReminderName.trim()) return
    setAddingReminder(true)
    try {
      const r = await api.createSourceReminder({
        name: newReminderName.trim(),
        url: newReminderUrl.trim() || undefined,
        setup_note: newReminderNote.trim() || undefined,
      })
      setReminders(prev => [...prev, r])
      setNewReminderName('')
      setNewReminderUrl('')
      setNewReminderNote('')
    } catch { /* silent */ } finally {
      setAddingReminder(false)
    }
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
    if (!newName.trim() || !newUrl.trim()) {
      setAddError('Name and URL are required.')
      return
    }
    setAdding(true)
    setAddError(null)
    try {
      const feed = await api.createRssFeed({ name: newName.trim(), url: newUrl.trim(), source_type: newType })
      setFeeds(prev => [feed, ...prev])
      setNewName('')
      setNewUrl('')
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : 'Failed to add feed')
    } finally {
      setAdding(false)
    }
  }

  async function toggleActive(feed: RssFeed) {
    try {
      const updated = await api.updateRssFeed(feed.id, { active: !feed.active })
      setFeeds(prev => prev.map(f => f.id === feed.id ? updated : f))
    } catch { /* silent */ }
  }

  async function deleteFeed(id: number) {
    if (!confirm('Delete this feed? Source items already ingested will not be removed.')) return
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
      setFeeds(prev => prev.map(f =>
        f.id === id ? { ...f, last_fetched_at: new Date().toISOString() } : f
      ))
    } catch { /* silent */ } finally {
      setIngestLoading(null)
    }
  }

  async function ingestAll() {
    setIngestLoading('all')
    setIngestAllSummary(null)
    try {
      const r = await api.ingestAllFeeds() as { feeds_processed: number; results: { added_count: number; skipped_count: number }[] }
      const total = r.results.reduce((s, x) => s + x.added_count, 0)
      const skipped = r.results.reduce((s, x) => s + x.skipped_count, 0)
      setIngestAllSummary(`${r.feeds_processed} feeds · +${total} added · ${skipped} skipped`)
      load()
    } catch { /* silent */ } finally {
      setIngestLoading(null)
    }
  }

  function useTemplate(t: SourceTemplate) {
    setNewName(t.name)
    setNewUrl(t.url_pattern ?? '')
    setNewType(t.source_type)
  }

  if (loading) return <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>
  if (error) return <div style={{ padding: '2rem', color: '#f87171' }}>Error: {error}</div>

  return (
    <div style={{ padding: '1.5rem', maxWidth: 860 }}>
      <div className="label" style={{ marginBottom: 4 }}>Sources</div>
      <h1 style={{ margin: '0 0 0.25rem', fontSize: '1.2rem', fontWeight: 700 }}>RSS Feeds</h1>
      <p style={{ margin: '0 0 1.5rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        Configure persistent feeds. Ingest one or all at once — duplicates are skipped automatically.
      </p>

      {/* Source packs */}
      {packs.length > 0 && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div className="label" style={{ marginBottom: 8 }}>Starter Packs</div>
          <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Apply a starter pack to pre-configure feeds and source reminders for your race type.
            Placeholder items become reminders you configure manually.
          </p>
          {packs.map(pack => (
            <div key={pack.id} className="card" style={{ marginBottom: 8, padding: '0.75rem 1rem', background: 'var(--surface-1)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: 2 }}>{pack.name}</div>
                  {pack.description && (
                    <p style={{ margin: '0 0 4px', fontSize: '0.73rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                      {pack.description}
                    </p>
                  )}
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    {pack.items.length} items · {pack.race_level} · {pack.geography?.replace('_', ' ')}
                  </div>
                  {applyResults[pack.id] && (
                    <div style={{ marginTop: 6, fontSize: '0.7rem', color: '#34d399' }}>
                      ✓ Applied: {applyResults[pack.id].feeds_created} feed(s), {applyResults[pack.id].reminders_created} reminder(s) created
                      {applyResults[pack.id].skipped_duplicate_feeds > 0 && `, ${applyResults[pack.id].skipped_duplicate_feeds} skipped`}
                    </div>
                  )}
                </div>
                <button
                  className="btn-primary"
                  style={{ fontSize: '0.72rem', padding: '0.3rem 0.85rem', flexShrink: 0, marginLeft: 12 }}
                  onClick={() => applyPack(pack.id)}
                  disabled={applyLoading === pack.id}
                >
                  {applyLoading === pack.id ? '…' : 'Apply'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Source templates */}
      {templates.length > 0 && (
        <TemplatesPanel templates={templates} onUseTemplate={useTemplate} />
      )}

      {/* Add feed form */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="label" style={{ marginBottom: 10 }}>Add Feed</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: 10, marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 4 }}>Name</div>
            <input
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="e.g. Lakeview Tribune"
            />
          </div>
          <div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 4 }}>Feed URL</div>
            <input
              value={newUrl}
              onChange={e => setNewUrl(e.target.value)}
              placeholder="https://example.com/feed.rss"
              onKeyDown={e => e.key === 'Enter' && addFeed()}
            />
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
          <button className="btn-primary" onClick={addFeed} disabled={adding}>
            {adding ? 'Adding…' : 'Add Feed'}
          </button>
          {addError && <span style={{ fontSize: '0.75rem', color: '#f87171' }}>{addError}</span>}
        </div>
      </div>

      {/* Ingest all */}
      {feeds.length > 0 && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: '1.5rem' }}>
          <button
            className="btn-primary"
            onClick={ingestAll}
            disabled={ingestLoading === 'all'}
            style={{ fontSize: '0.8rem' }}
          >
            {ingestLoading === 'all' ? 'Ingesting…' : `Ingest All Active (${feeds.filter(f => f.active).length})`}
          </button>
          {ingestAllSummary && (
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
              {ingestAllSummary}
            </span>
          )}
        </div>
      )}

      {/* Feed list */}
      {feeds.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
          <div style={{ fontSize: '0.85rem' }}>No feeds configured yet. Add one above.</div>
        </div>
      )}

      {feeds.map(feed => (
        <div key={feed.id} className="card" style={{
          marginBottom: 10,
          opacity: feed.active ? 1 : 0.55,
          borderLeft: `3px solid ${feed.active ? 'rgba(59,130,246,0.4)' : 'var(--border)'}`,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>{feed.name}</span>
                <span style={{
                  fontSize: '0.6rem', padding: '1px 6px', borderRadius: 3,
                  background: feed.active ? 'rgba(34,197,94,0.15)' : 'var(--surface-2)',
                  color: feed.active ? '#86efac' : 'var(--text-muted)',
                  fontFamily: 'JetBrains Mono',
                }}>
                  {feed.active ? 'ACTIVE' : 'PAUSED'}
                </span>
                <span className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>{feed.source_type}</span>
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: 4 }}>
                {feed.url}
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                Last fetched: {fmtDate(feed.last_fetched_at)}
              </div>
              {ingestResults[feed.id] && (
                <div style={{ marginTop: 4 }}>
                  <IngestResult result={ingestResults[feed.id]} />
                </div>
              )}
            </div>

            <div style={{ display: 'flex', gap: 6, marginLeft: 12, flexShrink: 0 }}>
              <button
                className="btn-primary"
                style={{ fontSize: '0.72rem', padding: '0.3rem 0.75rem' }}
                onClick={() => ingestOne(feed.id)}
                disabled={ingestLoading === feed.id}
              >
                {ingestLoading === feed.id ? '…' : 'Ingest'}
              </button>
              <button
                className="btn-ghost"
                style={{ fontSize: '0.72rem', padding: '0.3rem 0.75rem' }}
                onClick={() => toggleActive(feed)}
              >
                {feed.active ? 'Pause' : 'Resume'}
              </button>
              <button
                style={{
                  fontSize: '0.72rem', padding: '0.3rem 0.75rem', borderRadius: 5,
                  background: 'transparent', border: '1px solid rgba(239,68,68,0.3)',
                  color: '#f87171', cursor: 'pointer',
                }}
                onClick={() => deleteFeed(feed.id)}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      ))}

      {/* Source Reminders */}
      <div style={{ marginTop: '2rem' }}>
        <button
          onClick={() => setRemindersOpen(o => !o)}
          style={{
            width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            background: 'none', border: 'none', cursor: 'pointer', padding: '0.5rem 0',
            borderTop: '1px solid var(--border)', marginBottom: 0,
            color: 'var(--text-primary)',
          }}
        >
          <div style={{ fontSize: '0.85rem', fontWeight: 600 }}>
            Source Reminders
            {reminders.filter(r => r.active).length > 0 && (
              <span style={{ marginLeft: 8, fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                {reminders.filter(r => r.active).length} active
              </span>
            )}
          </div>
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            {remindersOpen ? '▲ Hide' : '▼ Show'} — non-RSS sources to check manually
          </span>
        </button>

        {remindersOpen && (
          <div style={{ marginTop: 12 }}>
            <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              Track pages that aren't RSS-compatible but need regular checking (FEC filings, opponent social media, ballot pages, etc.).
            </p>

            {/* Add reminder form */}
            <div className="card" style={{ marginBottom: 12 }}>
              <div className="label" style={{ marginBottom: 8 }}>Add Reminder</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
                <input
                  value={newReminderName}
                  onChange={e => setNewReminderName(e.target.value)}
                  placeholder="e.g. Check FEC filings page"
                />
                <input
                  value={newReminderUrl}
                  onChange={e => setNewReminderUrl(e.target.value)}
                  placeholder="URL (optional)"
                />
              </div>
              <input
                value={newReminderNote}
                onChange={e => setNewReminderNote(e.target.value)}
                placeholder="Setup note (optional)"
                style={{ marginBottom: 8 }}
              />
              <button className="btn-primary" style={{ fontSize: '0.75rem' }} onClick={addReminder} disabled={addingReminder}>
                {addingReminder ? 'Adding…' : 'Add Reminder'}
              </button>
            </div>

            {reminders.length === 0 && (
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '0.5rem 0' }}>
                No reminders yet. Apply a starter pack or add one above.
              </div>
            )}

            {reminders.map(r => {
              const staleDays = r.last_checked_at
                ? Math.floor((Date.now() - new Date(r.last_checked_at).getTime()) / 86400000)
                : null
              const isStale = staleDays === null || staleDays > 7
              return (
                <div key={r.id} className="card" style={{
                  marginBottom: 8, padding: '0.65rem 1rem',
                  opacity: r.active ? 1 : 0.5,
                  borderLeft: `3px solid ${isStale ? 'rgba(251,191,36,0.5)' : 'rgba(34,197,94,0.4)'}`,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 2 }}>{r.name}</div>
                      {r.url && (
                        <a href={r.url} target="_blank" rel="noopener noreferrer"
                          style={{ fontSize: '0.68rem', color: 'var(--accent)', fontFamily: 'JetBrains Mono', display: 'block', marginBottom: 2 }}>
                          {r.url.length > 60 ? r.url.slice(0, 60) + '…' : r.url} ↗
                        </a>
                      )}
                      {r.setup_note && (
                        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.4, marginBottom: 4 }}>
                          {r.setup_note}
                        </div>
                      )}
                      <div style={{ fontSize: '0.65rem', color: isStale ? '#fbbf24' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {r.last_checked_at
                          ? `Last checked ${staleDays}d ago`
                          : 'Never checked'}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginLeft: 12, flexShrink: 0 }}>
                      <button
                        className="btn-primary"
                        style={{ fontSize: '0.68rem', padding: '0.25rem 0.6rem' }}
                        onClick={() => markChecked(r.id)}
                      >
                        Mark Checked
                      </button>
                      <button
                        style={{
                          fontSize: '0.68rem', padding: '0.25rem 0.6rem', borderRadius: 4,
                          background: 'transparent', border: '1px solid rgba(239,68,68,0.3)',
                          color: '#f87171', cursor: 'pointer',
                        }}
                        onClick={() => deleteReminder(r.id)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
