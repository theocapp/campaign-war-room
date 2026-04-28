import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CampaignProfile, RaceImportResult } from '../api/types'

const PRIORITY_OPTIONS = [
  'Housing & Affordability', 'Public Safety', 'Education & Schools',
  'Infrastructure', 'Economy & Jobs', 'Environment', 'Transportation',
  'Healthcare', 'Taxes & Budget', 'Downtown Development',
  'Immigration', 'Corruption & Ethics', 'Local Government',
]

const inputStyle: React.CSSProperties = {
  width: '100%', background: 'var(--surface-2)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '0.5rem 0.75rem', color: 'var(--text-primary)',
  fontSize: '0.875rem', boxSizing: 'border-box',
}
const textareaStyle: React.CSSProperties = { ...inputStyle, resize: 'vertical', minHeight: 80 }

export default function CampaignSetup() {
  const [profile, setProfile] = useState<CampaignProfile | null>(null)
  const [form, setForm] = useState({
    candidate_name: '',
    party: '',
    office: '',
    district: '',
    location: '',
    race: '',
    election_date: '',
    campaign_message: '',
  })
  const [priorities, setPriorities] = useState<string[]>([])
  const [customPriority, setCustomPriority] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset workspace state
  const [resetOpen, setResetOpen] = useState(false)
  const [resetConfirm, setResetConfirm] = useState('')
  const [resetName, setResetName] = useState('')
  const [resetOffice, setResetOffice] = useState('')
  const [resetDistrict, setResetDistrict] = useState('')
  const [resetParty, setResetParty] = useState('')
  const [resetPreserveFeeds, setResetPreserveFeeds] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [resetResult, setResetResult] = useState<string | null>(null)
  const [resetError, setResetError] = useState<string | null>(null)

  // CSV import state
  const csvRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<RaceImportResult | null>(null)
  const [importError, setImportError] = useState<string | null>(null)

  useEffect(() => {
    api.getCampaign()
      .then(p => {
        setProfile(p)
        setForm({
          candidate_name: p.candidate_name || '',
          party: p.party || '',
          office: p.office || '',
          district: p.district || '',
          location: p.location || '',
          race: p.race || '',
          election_date: p.election_date ? p.election_date.split('T')[0] : '',
          campaign_message: p.campaign_message || '',
        })
        setPriorities(p.key_priorities || [])
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  function field(key: keyof typeof form) {
    return (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm(f => ({ ...f, [key]: e.target.value }))
  }

  function togglePriority(p: string) {
    setPriorities(prev =>
      prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]
    )
  }

  function addCustomPriority() {
    const v = customPriority.trim()
    if (v && !priorities.includes(v)) {
      setPriorities(prev => [...prev, v])
      setCustomPriority('')
    }
  }

  async function resetWorkspace() {
    if (resetConfirm !== 'RESET WORKSPACE') {
      setResetError("Type exactly: RESET WORKSPACE")
      return
    }
    if (!resetName.trim() || !resetOffice.trim()) {
      setResetError('Candidate name and office are required.')
      return
    }
    setResetting(true)
    setResetError(null)
    try {
      const r = await api.resetWorkspace({
        confirm: resetConfirm,
        candidate_name: resetName.trim(),
        office: resetOffice.trim(),
        district: resetDistrict.trim() || undefined,
        party: resetParty.trim() || undefined,
        preserve_feeds: resetPreserveFeeds,
      })
      setResetResult(
        `Workspace reset for ${r.candidate_name}. ` +
        `Cleared: ${r.cleared_sources} sources, ${r.cleared_issues} issues, ` +
        `${r.cleared_opponents} opponents, ${r.cleared_talking_points} talking points` +
        (r.preserved_feeds > 0 ? `, preserved ${r.preserved_feeds} feeds.` : `.`)
      )
      setResetConfirm('')
      setResetOpen(false)
      // Reload campaign profile
      api.getCampaign().then(p => {
        setProfile(p)
        setForm({
          candidate_name: p.candidate_name || '',
          party: p.party || '',
          office: p.office || '',
          district: p.district || '',
          location: p.location || '',
          race: p.race || '',
          election_date: p.election_date ? p.election_date.split('T')[0] : '',
          campaign_message: p.campaign_message || '',
        })
        setPriorities(p.key_priorities || [])
      }).catch(() => {})
    } catch (e: unknown) {
      setResetError(e instanceof Error ? e.message : 'Reset failed')
    } finally {
      setResetting(false)
    }
  }

  async function importCSV(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true)
    setImportResult(null)
    setImportError(null)
    try {
      const r = await api.importRaceCSV(file)
      setImportResult(r)
    } catch (err: unknown) {
      setImportError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setImporting(false)
      if (csvRef.current) csvRef.current.value = ''
    }
  }

  async function save() {
    if (!form.candidate_name.trim()) {
      setError('Candidate name is required.')
      return
    }
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await api.updateCampaign({
        ...form,
        election_date: form.election_date ? new Date(form.election_date).toISOString() : undefined,
        key_priorities: priorities.length > 0 ? priorities : undefined,
      } as Parameters<typeof api.updateCampaign>[0])
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>
  if (error && !profile) return <div style={{ padding: '2rem', color: '#f87171' }}>Error: {error}</div>

  const daysToElection = form.election_date
    ? Math.ceil((new Date(form.election_date).getTime() - Date.now()) / 86400000)
    : null

  return (
    <div style={{ padding: '1.5rem', maxWidth: 800 }}>
      <div className="label" style={{ marginBottom: 4 }}>Campaign Configuration</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ margin: '0 0 0.25rem', fontSize: '1.2rem', fontWeight: 700 }}>Campaign Setup</h1>
          <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            Your campaign profile personalizes all AI-generated talking points, actions, and analysis.
          </p>
        </div>
        {daysToElection !== null && daysToElection > 0 && (
          <div style={{ textAlign: 'right', padding: '0.5rem 1rem', background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)', borderRadius: 8 }}>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#93c5fd', fontFamily: 'JetBrains Mono' }}>{daysToElection}</div>
            <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>DAYS TO ELECTION</div>
          </div>
        )}
      </div>

      {/* Candidate info */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="section-title">Candidate Information</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>Candidate Name *</div>
            <input style={inputStyle} value={form.candidate_name} onChange={field('candidate_name')} placeholder="e.g. Maria Chen" />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>Party</div>
            <input style={inputStyle} value={form.party} onChange={field('party')} placeholder="e.g. Democrat" />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>Office Sought</div>
            <input style={inputStyle} value={form.office} onChange={field('office')} placeholder="e.g. City Council Member" />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>District / Ward</div>
            <input style={inputStyle} value={form.district} onChange={field('district')} placeholder="e.g. District 7" />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>Location / City</div>
            <input style={inputStyle} value={form.location} onChange={field('location')} placeholder="e.g. Lakeview, CA" />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>Election Date</div>
            <input style={inputStyle} type="date" value={form.election_date} onChange={field('election_date')} />
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <div className="label" style={{ marginBottom: 4 }}>Race Description</div>
          <input style={inputStyle} value={form.race} onChange={field('race')} placeholder="e.g. Lakeview City Council, District 7" />
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 4 }}>
            Shown on the dashboard header. Auto-generated from office + district if left blank.
          </div>
        </div>
      </div>

      {/* Campaign message */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="section-title">Core Campaign Message</div>
        <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          Your central campaign message — used to personalize talking points and suggested actions.
        </p>
        <textarea
          style={textareaStyle}
          value={form.campaign_message}
          onChange={field('campaign_message')}
          rows={4}
          placeholder="e.g. District 7 deserves a council member who shows up. I will fight for housing families can afford, schools our kids deserve, and infrastructure that works."
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            2-4 sentences. Use in talking points and social posts.
          </div>
          <div style={{ fontSize: '0.7rem', color: form.campaign_message.length > 400 ? '#f87171' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
            {form.campaign_message.length} chars
          </div>
        </div>
      </div>

      {/* Key priorities */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="section-title">Key Priorities</div>
        <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          Select the issues your campaign is focused on. These guide talking point generation.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
          {PRIORITY_OPTIONS.map(p => {
            const active = priorities.includes(p)
            return (
              <button
                key={p}
                onClick={() => togglePriority(p)}
                style={{
                  padding: '0.35rem 0.75rem', borderRadius: 5, cursor: 'pointer',
                  fontSize: '0.75rem',
                  background: active ? 'rgba(59,130,246,0.2)' : 'var(--surface-2)',
                  border: `1px solid ${active ? 'rgba(59,130,246,0.5)' : 'var(--border)'}`,
                  color: active ? '#93c5fd' : 'var(--text-secondary)',
                  fontWeight: active ? 600 : 400,
                }}
              >
                {active ? '✓ ' : ''}{p}
              </button>
            )
          })}
        </div>

        {/* Custom priorities */}
        {priorities.filter(p => !PRIORITY_OPTIONS.includes(p)).length > 0 && (
          <div style={{ marginBottom: 12, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {priorities.filter(p => !PRIORITY_OPTIONS.includes(p)).map(p => (
              <span key={p} style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '0.3rem 0.6rem', borderRadius: 5,
                background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.3)',
                fontSize: '0.75rem', color: '#c4b5fd',
              }}>
                {p}
                <button
                  onClick={() => setPriorities(prev => prev.filter(x => x !== p))}
                  style={{ background: 'none', border: 'none', color: '#f87171', cursor: 'pointer', padding: 0, fontSize: '0.85rem', lineHeight: 1 }}
                >×</button>
              </span>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8 }}>
          <input
            style={{ ...inputStyle, flex: 1 }}
            value={customPriority}
            onChange={e => setCustomPriority(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addCustomPriority()}
            placeholder="Add a custom priority…"
          />
          <button className="btn-ghost" onClick={addCustomPriority}>Add</button>
        </div>
      </div>

      {/* Save */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <button className="btn-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save Campaign Profile'}
        </button>
        {saved && (
          <span style={{ fontSize: '0.8rem', color: '#34d399', display: 'flex', alignItems: 'center', gap: 4 }}>
            ✓ Saved — talking points and actions now use this profile
          </span>
        )}
        {error && <span style={{ fontSize: '0.8rem', color: '#f87171' }}>{error}</span>}
      </div>

      {/* Info box */}
      <div style={{ marginTop: '1.5rem', padding: '0.75rem 1rem', borderRadius: 6, background: 'rgba(59,130,246,0.05)', border: '1px solid rgba(59,130,246,0.12)', fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.6 }}>
        <strong style={{ color: 'var(--text-secondary)' }}>How this is used:</strong> Your candidate name, office, district, campaign message, and key priorities are included in every talking point generation prompt. When using a real LLM provider (OpenAI or Anthropic), the AI will reference this context to produce personalized, on-message content.
      </div>

      {/* CSV Import */}
      <div className="card" style={{ marginTop: '2rem' }}>
        <div className="section-title">Import Race Setup from CSV</div>
        <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
          Upload a CSV to bulk-create your campaign profile, opponents, RSS feeds, and source reminders.
          Each row has a <code>type</code> column: <code>campaign</code>, <code>opponent</code>, <code>rss_feed</code>, or <code>reminder</code>.
        </p>
        <div style={{ marginBottom: 10, padding: '0.6rem 0.75rem', background: 'var(--surface-1)', borderRadius: 4, fontFamily: 'JetBrains Mono', fontSize: '0.65rem', color: 'var(--text-muted)', lineHeight: 1.7 }}>
          type,name,url,category,source_type,notes,party,office,district,location,election_date<br />
          campaign,Jane Smith,,,,,Democrat,U.S. Rep,PA-08,Scranton PA,2026-11-03<br />
          opponent,John Doe,,,opponent_statement,,Republican,U.S. Rep,,,<br />
          rss_feed,Local Tribune,https://tribune.com/rss,,news,,,,,<br />
          reminder,Check FEC Page,https://fec.gov,,public_record,Check quarterly,,,,,
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <label className="btn-ghost" style={{ fontSize: '0.78rem', cursor: 'pointer', padding: '0.4rem 0.9rem' }}>
            {importing ? 'Importing…' : 'Choose CSV File'}
            <input ref={csvRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={importCSV} disabled={importing} />
          </label>
          {importError && <span style={{ fontSize: '0.75rem', color: '#f87171' }}>{importError}</span>}
        </div>
        {importResult && (
          <div style={{ marginTop: 10, padding: '0.6rem 0.75rem', borderRadius: 4, background: 'rgba(34,197,94,0.06)', border: '1px solid rgba(34,197,94,0.2)', fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
            <strong style={{ color: '#34d399' }}>Import complete:</strong>{' '}
            {importResult.campaign_updated && 'Campaign profile updated. '}
            {importResult.opponents_created > 0 && `${importResult.opponents_created} opponent(s) added. `}
            {importResult.feeds_created > 0 && `${importResult.feeds_created} RSS feed(s) added. `}
            {importResult.reminders_created > 0 && `${importResult.reminders_created} reminder(s) added. `}
            {importResult.skipped > 0 && `${importResult.skipped} row(s) skipped (duplicates). `}
            {importResult.errors.length > 0 && (
              <div style={{ marginTop: 4, color: '#f87171' }}>
                {importResult.errors.map((e, i) => <div key={i}>{e}</div>)}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Reset Workspace — kept small and non-prominent */}
      <div style={{ marginTop: '2.5rem', borderTop: '1px solid var(--border)', paddingTop: '1.5rem' }}>
        <button
          className="btn-ghost"
          style={{ fontSize: '0.72rem', color: 'var(--text-muted)', padding: '0.3rem 0.6rem' }}
          onClick={() => { setResetOpen(o => !o); setResetError(null); setResetResult(null) }}
        >
          {resetOpen ? '▲' : '▼'} Workspace Reset
        </button>
        <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 4 }}>
          Clears all demo/test data and starts fresh with a new campaign profile.
        </div>

        {resetResult && (
          <div style={{ marginTop: 8, padding: '0.5rem 0.75rem', borderRadius: 4, background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)', fontSize: '0.75rem', color: '#34d399' }}>
            ✓ {resetResult}
          </div>
        )}

        {resetOpen && (
          <div className="card" style={{ marginTop: 10, borderColor: 'rgba(239,68,68,0.3)' }}>
            <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: '#f87171', letterSpacing: '0.06em', marginBottom: 8 }}>
              ⚠ DESTRUCTIVE ACTION — CANNOT BE UNDONE
            </div>
            <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              This will delete all sources, issues, opponents, canvassing notes, and talking point history.
              Configure the new campaign below, then confirm.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Candidate Name *</div>
                <input value={resetName} onChange={e => setResetName(e.target.value)} placeholder="e.g. Jane Smith" />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Office *</div>
                <input value={resetOffice} onChange={e => setResetOffice(e.target.value)} placeholder="e.g. U.S. Representative" />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>District</div>
                <input value={resetDistrict} onChange={e => setResetDistrict(e.target.value)} placeholder="e.g. PA-08" />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Party</div>
                <input value={resetParty} onChange={e => setResetParty(e.target.value)} placeholder="e.g. Democrat" />
              </div>
            </div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, fontSize: '0.78rem', color: 'var(--text-secondary)', cursor: 'pointer' }}>
              <input type="checkbox" checked={resetPreserveFeeds} onChange={e => setResetPreserveFeeds(e.target.checked)} />
              Preserve configured RSS feeds
            </label>
            <div className="label" style={{ marginBottom: 4 }}>
              Type <span style={{ fontFamily: 'JetBrains Mono', color: '#f87171' }}>RESET WORKSPACE</span> to confirm
            </div>
            <input
              value={resetConfirm}
              onChange={e => setResetConfirm(e.target.value)}
              placeholder="RESET WORKSPACE"
              style={{ marginBottom: 10, fontFamily: 'JetBrains Mono' }}
            />
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button
                style={{
                  padding: '0.4rem 1rem', borderRadius: 5, fontSize: '0.78rem',
                  background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)',
                  color: '#f87171', cursor: resetting ? 'not-allowed' : 'pointer',
                  opacity: resetting ? 0.6 : 1,
                }}
                disabled={resetting}
                onClick={resetWorkspace}
              >
                {resetting ? 'Resetting…' : 'Reset Workspace'}
              </button>
              <button className="btn-ghost" style={{ fontSize: '0.72rem' }} onClick={() => setResetOpen(false)}>Cancel</button>
              {resetError && <span style={{ fontSize: '0.75rem', color: '#f87171' }}>{resetError}</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
