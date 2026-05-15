import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CampaignProfile, CampaignInitializeResult, CampaignInitializeStep, RaceDirectory, RaceImportResult } from '../api/types'

const PRIORITY_OPTIONS = [
  'Housing & Affordability', 'Public Safety', 'Education & Schools',
  'Infrastructure', 'Economy & Jobs', 'Environment', 'Transportation',
  'Healthcare', 'Taxes & Budget', 'Downtown Development',
  'Immigration', 'Corruption & Ethics', 'Local Government',
]

function parseList(value: string): string[] {
  return value.split(/[\n,]/).map(v => v.trim()).filter(Boolean)
}

function formatRaceDate(value: string | null) {
  if (!value) return 'Date TBD'
  return new Date(value).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function labelValue(value: string | null | undefined) {
  return value?.trim() || 'Not set'
}

export default function CampaignSetup() {
  const [profile, setProfile] = useState<CampaignProfile | null>(null)
  const [form, setForm] = useState({
    candidate_name: '', party: '', office: '', district: '', location: '', race: '',
    race_level: '', election_type: '', district_number: '', election_date: '',
    campaign_message: '', sparse_race_mode: false,
  })
  const [priorities, setPriorities]                 = useState<string[]>([])
  const [relevanceKeywords, setRelevanceKeywords]   = useState('')
  const [excludedKeywords, setExcludedKeywords]     = useState('')
  const [geographyKeywords, setGeographyKeywords]   = useState('')
  const [neighborhoodKeywords, setNeighborhoodKeywords] = useState('')
  const [customPriority, setCustomPriority]         = useState('')
  const [dateInferred, setDateInferred]             = useState(false)
  const [loading, setLoading]                       = useState(true)
  const [advancedOpen, setAdvancedOpen]             = useState(false)

  // Campaign initialization
  const INIT_STEP_LABELS = [
    'Validating campaign profile…',
    'Generating monitors…',
    'Ingesting latest coverage…',
    'Refreshing narrative tracking…',
  ]
  const [initializing, setInitializing]   = useState(false)
  const [initStep, setInitStep]           = useState(0)
  const [initResult, setInitResult]       = useState<CampaignInitializeResult | null>(null)
  const [initError, setInitError]         = useState<string | null>(null)
  const [saving, setSaving]               = useState(false)
  const [saved, setSaved]                 = useState(false)
  const [error, setError]                 = useState<string | null>(null)

  // Race directory
  const [setupMode, setSetupMode]                   = useState<'directory' | 'custom'>('directory')
  const [raceQuery, setRaceQuery]                   = useState('')
  const [races, setRaces]                           = useState<RaceDirectory[]>([])
  const [racesLoading, setRacesLoading]             = useState(false)
  const [raceError, setRaceError]                   = useState<string | null>(null)
  const [selectedRace, setSelectedRace]             = useState<RaceDirectory | null>(null)
  const [selectedCandidateId, setSelectedCandidateId] = useState<number | null>(null)
  const [selectingRace, setSelectingRace]           = useState(false)
  const [raceSelectMessage, setRaceSelectMessage]   = useState<string | null>(null)

  // Reset workspace
  const [resetOpen, setResetOpen]                   = useState(false)
  const [resetConfirm, setResetConfirm]             = useState('')
  const [resetName, setResetName]                   = useState('')
  const [resetOffice, setResetOffice]               = useState('')
  const [resetDistrict, setResetDistrict]           = useState('')
  const [resetParty, setResetParty]                 = useState('')
  const [resetPreserveFeeds, setResetPreserveFeeds] = useState(false)
  const [resetting, setResetting]                   = useState(false)
  const [resetResult, setResetResult]               = useState<string | null>(null)
  const [resetError, setResetError]                 = useState<string | null>(null)

  // CSV import
  const csvRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting]       = useState(false)
  const [importResult, setImportResult] = useState<RaceImportResult | null>(null)
  const [importError, setImportError]   = useState<string | null>(null)
  const initTickerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isMountedRef = useRef(true)

  function applyProfile(p: CampaignProfile) {
    setProfile(p)
    setForm({
      candidate_name: p.candidate_name || '',
      party: p.party || '',
      office: p.office || '',
      district: p.district || '',
      location: p.location || '',
      race: p.race || '',
      race_level: p.race_level || '',
      election_type: p.election_type || '',
      district_number: p.district_number || '',
      election_date: p.election_date ? p.election_date.split('T')[0] : '',
      campaign_message: p.campaign_message || '',
      sparse_race_mode: p.sparse_race_mode || false,
    })
    setDateInferred(!!p.election_date_inferred)
    setPriorities(p.key_priorities || [])
    setRelevanceKeywords((p.relevance_keywords || []).join('\n'))
    setExcludedKeywords((p.excluded_keywords || []).join('\n'))
    setGeographyKeywords((p.geography_keywords || []).join('\n'))
    setNeighborhoodKeywords((p.neighborhood_keywords || []).join('\n'))
  }

  useEffect(() => {
    api.getCampaign().then(applyProfile).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    return () => {
      isMountedRef.current = false
      if (initTickerRef.current) {
        clearInterval(initTickerRef.current)
        initTickerRef.current = null
      }
    }
  }, [])

  useEffect(() => { loadRaces('') }, [])

  async function loadRaces(query = raceQuery) {
    setRacesLoading(true); setRaceError(null)
    try {
      const results = await api.getRaces({ q: query.trim() || undefined, limit: 25 })
      setRaces(results)
      if (!selectedRace && results.length > 0) chooseRace(results[0])
    } catch (e: unknown) {
      setRaceError(e instanceof Error ? e.message : 'Could not load races')
    } finally { setRacesLoading(false) }
  }

  async function inspectRace(race: RaceDirectory) {
    setRaceError(null); setRaceSelectMessage(null)
    try {
      const detail = await api.getRace(race.id)
      chooseRace(detail)
    } catch (e: unknown) {
      setRaceError(e instanceof Error ? e.message : 'Could not load race')
    }
  }

  function chooseRace(race: RaceDirectory) {
    setSelectedRace(race)
    const preferred = race.candidates.find(c => c.role === 'candidate')
    setSelectedCandidateId(preferred?.id || race.candidates[0]?.id || null)
  }

  async function selectDirectoryRace() {
    if (!selectedRace) return
    setSelectingRace(true); setRaceError(null); setRaceSelectMessage(null)
    setInitResult(null); setInitError(null)
    try {
      const result = await api.selectRace(selectedRace.id, { candidate_id: selectedCandidateId || undefined })
      applyProfile(result.campaign)
      setSelectedRace(result.race)
      setSelectedCandidateId(
        result.race.candidates.find(c => c.candidate_name === result.selected_candidate_name)?.id || null
      )
      setRaceSelectMessage(`${result.message} ${result.opponents_created} opponent(s) added, ${result.opponents_updated} updated.`)
      if (result.init_result) {
        setInitResult(result.init_result)
      }
    } catch (e: unknown) {
      setRaceError(e instanceof Error ? e.message : 'Race selection failed')
    } finally { setSelectingRace(false) }
  }

  function field(key: keyof typeof form) {
    return (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      setForm(f => ({ ...f, [key]: e.target.value }))
  }

  function togglePriority(p: string) {
    setPriorities(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])
  }

  function addCustomPriority() {
    const v = customPriority.trim()
    if (v && !priorities.includes(v)) { setPriorities(prev => [...prev, v]); setCustomPriority('') }
  }

  async function resetWorkspace() {
    if (resetConfirm !== 'RESET WORKSPACE') { setResetError('Type exactly: RESET WORKSPACE'); return }
    if (!resetName.trim() || !resetOffice.trim()) { setResetError('Candidate name and office are required.'); return }
    setResetting(true); setResetError(null)
    try {
      const r = await api.resetWorkspace({
        confirm: resetConfirm, candidate_name: resetName.trim(), office: resetOffice.trim(),
        district: resetDistrict.trim() || undefined, party: resetParty.trim() || undefined,
        preserve_feeds: resetPreserveFeeds,
      })
      setResetResult(
        `Reset for ${r.candidate_name}. Cleared: ${r.cleared_sources} sources, ${r.cleared_issues} issues, ` +
        `${r.cleared_opponents} opponents, ${r.cleared_talking_points} talking points` +
        (r.preserved_feeds > 0 ? `, preserved ${r.preserved_feeds} feeds.` : `.`)
      )
      setResetConfirm(''); setResetOpen(false)
      api.getCampaign().then(applyProfile).catch(() => {})
    } catch (e: unknown) {
      setResetError(e instanceof Error ? e.message : 'Reset failed')
    } finally { setResetting(false) }
  }

  async function importCSV(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true); setImportResult(null); setImportError(null)
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

  function buildCampaignPayload() {
    return {
      ...form,
      election_date: form.election_date ? new Date(form.election_date).toISOString() : undefined,
      key_priorities: priorities.length > 0 ? priorities : undefined,
      relevance_keywords: parseList(relevanceKeywords),
      excluded_keywords: parseList(excludedKeywords),
      geography_keywords: parseList(geographyKeywords),
      neighborhood_keywords: parseList(neighborhoodKeywords),
    } as Parameters<typeof api.updateCampaign>[0]
  }

  async function save() {
    if (!form.candidate_name.trim()) { setError('Candidate name is required.'); return }
    setSaving(true); setError(null); setSaved(false)
    try {
      await api.updateCampaign(buildCampaignPayload())
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally { setSaving(false) }
  }

  async function initializeCampaign() {
    if (!form.candidate_name.trim()) {
      setInitError('Candidate name is required before initializing.')
      return
    }
    setInitializing(true)
    setInitResult(null)
    setInitError(null)
    setInitStep(0)

    // Auto-save profile before initializing so the backend sees latest values
    try {
      await api.updateCampaign(buildCampaignPayload())
    } catch (e: unknown) {
      setInitError(e instanceof Error ? e.message : 'Save failed before initialization')
      setInitializing(false)
      return
    }

    let stepIndex = 0
    initTickerRef.current = setInterval(() => {
      stepIndex = Math.min(stepIndex + 1, INIT_STEP_LABELS.length - 1)
      setInitStep(stepIndex)
    }, 1800)

    try {
      const result = await api.initializeCampaign()
      if (!isMountedRef.current) return
      setInitResult(result)
    } catch (e: unknown) {
      if (!isMountedRef.current) return
      setInitError(e instanceof Error ? e.message : 'Initialization failed')
    } finally {
      if (initTickerRef.current) {
        clearInterval(initTickerRef.current)
        initTickerRef.current = null
      }
      if (!isMountedRef.current) return
      setInitializing(false)
      setInitStep(0)
    }
  }

  if (loading) return <div className="loading-text">Loading…</div>
  if (error && !profile) return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  const daysToElection = form.election_date
    ? Math.ceil((new Date(form.election_date).getTime() - Date.now()) / 86400000)
    : null

  return (
    <div className="page">
      {/* Header */}
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <div className="label" style={{ marginBottom: 5 }}>Configuration</div>
          <h1 className="page-title">Campaign Setup</h1>
          <p className="page-subtitle">Set up your campaign to start tracking coverage, monitoring opponents, and generating talking points.</p>
        </div>
        {daysToElection !== null && daysToElection > 0 && (
          <div style={{ textAlign: 'center', padding: '0.6rem 1.1rem', background: 'var(--accent-bg)', border: '1px solid var(--accent-border)', borderRadius: 'var(--radius)' }}>
            <div style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--accent-light)', fontFamily: 'JetBrains Mono', lineHeight: 1 }}>
              {daysToElection}
            </div>
            <div style={{ fontSize: '0.58rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.07em', marginTop: 4 }}>
              Days to Election
            </div>
          </div>
        )}
      </div>

      {/* ── Step 1: Race Setup ─────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', marginBottom: '1rem' }}>
          <div>
            <div className="section-title" style={{ marginBottom: 4 }}>1. Pick Your Race</div>
            <p style={{ margin: 0, fontSize: '0.76rem', color: 'var(--text-muted)', lineHeight: 1.5, maxWidth: 560 }}>
              FEC Directory covers 2025–2026 federal filings (U.S. House &amp; Senate). State and local races use Custom setup.
            </p>
          </div>
          <div style={{ display: 'inline-flex', padding: 3, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface-2)', flexShrink: 0 }}>
            {(['directory', 'custom'] as const).map(mode => (
              <button
                key={mode}
                type="button"
                onClick={() => setSetupMode(mode)}
                style={{
                  border: 'none', borderRadius: 4, padding: '0.35rem 0.75rem', fontSize: '0.74rem',
                  cursor: 'pointer', fontFamily: 'inherit',
                  background: setupMode === mode ? 'var(--surface-3)' : 'transparent',
                  color: setupMode === mode ? 'var(--text-primary)' : 'var(--text-muted)',
                  fontWeight: setupMode === mode ? 600 : 400,
                }}
              >
                {mode === 'directory' ? 'FEC Directory' : 'Custom Race'}
              </button>
            ))}
          </div>
        </div>

        {setupMode === 'directory' ? (
          <>
            <div style={{ display: 'flex', gap: 8, marginBottom: '0.75rem' }}>
              <input
                style={{ flex: 1 }}
                value={raceQuery}
                onChange={e => setRaceQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && loadRaces()}
                placeholder="Search by state, district, office, or candidate name"
              />
              <button className="btn btn-ghost btn-sm" type="button" onClick={() => loadRaces()} disabled={racesLoading}>
                {racesLoading ? '…' : 'Search'}
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(260px, 0.85fr)', gap: 12 }}>
              {/* Race list */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 320, overflow: 'auto', paddingRight: 2 }}>
                {racesLoading && races.length === 0 && (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem', padding: '0.5rem 0' }}>Loading…</div>
                )}
                {!racesLoading && races.length === 0 && (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem', padding: '0.5rem 0' }}>
                    No matching filings found. Try a different query or use Custom Race.
                  </div>
                )}
                {races.map(race => {
                  const active = selectedRace?.id === race.id
                  return (
                    <button
                      key={race.id}
                      type="button"
                      onClick={() => inspectRace(race)}
                      style={{
                        textAlign: 'left', padding: '0.6rem 0.75rem', borderRadius: 'var(--radius-sm)',
                        border: `1px solid ${active ? 'var(--accent-border)' : 'var(--border)'}`,
                        background: active ? 'var(--accent-bg)' : 'var(--surface-2)',
                        color: 'var(--text-primary)', cursor: 'pointer', fontFamily: 'inherit',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 3 }}>
                        <div style={{ fontWeight: 700, fontSize: '0.84rem', lineHeight: 1.2 }}>{race.race_name}</div>
                        <span className="badge badge-ghost" style={{ fontSize: '0.56rem', flexShrink: 0 }}>{race.race_level}</span>
                      </div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                        {race.office_name} · {race.state}{race.district_label ? ` · ${race.district_label}` : ''} · {race.election_type}
                      </div>
                      <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginTop: 2 }}>
                        {formatRaceDate(race.election_date)}
                      </div>
                    </button>
                  )
                })}
              </div>

              {/* Selected race detail */}
              <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', background: 'var(--surface-2)', padding: '0.85rem' }}>
                {selectedRace ? (
                  <>
                    <div style={{ marginBottom: '0.75rem' }}>
                      <div style={{ fontSize: '0.88rem', fontWeight: 700, marginBottom: 4 }}>{selectedRace.race_name}</div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                        {selectedRace.geography_summary || 'No geography summary available.'}
                      </div>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: '0.75rem' }}>
                      {([
                        ['Office', selectedRace.office_name],
                        ['State', selectedRace.state],
                        ['District', selectedRace.district_label || selectedRace.district_number],
                        ['Election', `${selectedRace.election_type} · ${formatRaceDate(selectedRace.election_date)}`],
                      ] as Array<[string, string | null | undefined]>).map(([label, value]) => (
                        <div key={label} style={{ minWidth: 0 }}>
                          <div className="label" style={{ marginBottom: 2 }}>{label}</div>
                          <div style={{ fontSize: '0.76rem', color: 'var(--text-secondary)' }}>{labelValue(value)}</div>
                        </div>
                      ))}
                    </div>

                    <div className="label" style={{ marginBottom: 6 }}>Select your candidate</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: '0.75rem' }}>
                      {selectedRace.candidates.map(candidate => (
                        <label
                          key={candidate.id}
                          style={{
                            display: 'flex', gap: 8, alignItems: 'flex-start',
                            padding: '0.5rem 0.6rem', borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border)',
                            background: selectedCandidateId === candidate.id ? 'rgba(139,92,246,0.08)' : 'var(--surface-1)',
                            cursor: 'pointer',
                          }}
                        >
                          <input
                            type="radio" name="directory_candidate"
                            checked={selectedCandidateId === candidate.id}
                            onChange={() => setSelectedCandidateId(candidate.id)}
                            style={{ width: 13, marginTop: 3 }}
                          />
                          <span style={{ minWidth: 0 }}>
                            <span style={{ display: 'block', fontSize: '0.79rem', fontWeight: 600 }}>
                              {candidate.candidate_name}{candidate.is_incumbent ? ' (incumbent)' : ''}
                            </span>
                            <span style={{ display: 'block', fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                              {labelValue(candidate.party)} · {candidate.role}
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>

                    <button
                      className="btn btn-primary"
                      type="button"
                      onClick={selectDirectoryRace}
                      disabled={!selectedCandidateId || selectingRace}
                      style={{ width: '100%' }}
                    >
                      {selectingRace ? 'Selecting…' : 'Select Race for Workspace'}
                    </button>
                  </>
                ) : (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                    Select a race from the list to inspect candidates.
                  </div>
                )}
              </div>
            </div>

            {raceSelectMessage && (
              <div style={{ marginTop: 10, padding: '0.6rem 0.75rem', borderRadius: 'var(--radius-sm)', background: 'var(--ok-bg)', border: '1px solid var(--ok-border)', color: 'var(--ok-light)', fontSize: '0.76rem' }}>
                ✓ {raceSelectMessage}
                {initResult && (
                  <span style={{ marginLeft: 8, opacity: 0.8 }}>
                    — {initResult.monitors_created} monitor(s) created, {initResult.sources_ingested} source(s) ingested.
                  </span>
                )}
              </div>
            )}
            {raceError && (
              <div style={{ marginTop: 10, padding: '0.6rem 0.75rem', borderRadius: 'var(--radius-sm)', background: 'var(--urgent-bg)', border: '1px solid var(--urgent-border)', color: 'var(--opponent)', fontSize: '0.76rem' }}>
                {raceError}
              </div>
            )}
          </>
        ) : (
          <div style={{ padding: '0.75rem', borderRadius: 'var(--radius-sm)', background: 'var(--surface-2)', border: '1px solid var(--border)', fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Fill in the Candidate Information fields below and click Initialize. Custom setup is for state, local, and municipal races not in the FEC directory.
          </div>
        )}
      </div>

      {/* ── Step 2: Essential Candidate Info ──────────────────────────────── */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="section-title" style={{ marginBottom: '1rem' }}>2. Candidate Information</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>
              Candidate Name <span style={{ color: 'var(--opponent)' }}>*</span>
            </div>
            <input
              data-testid="candidate-name"
              value={form.candidate_name}
              onChange={field('candidate_name')}
              placeholder="e.g. Maria Chen"
            />
            {initError && !form.candidate_name.trim() && (
              <div style={{ fontSize: '0.72rem', color: 'var(--opponent)', marginTop: 4 }}>
                Required
              </div>
            )}
          </div>
          <div>
            <div className="label" style={{ marginBottom: 4 }}>Party</div>
            <input
              data-testid="party"
              value={form.party}
              onChange={field('party')}
              placeholder="e.g. Democrat"
            />
          </div>
        </div>
      </div>

      {/* ── Advanced toggle ────────────────────────────────────────────────── */}
      <button
        data-testid="advanced-toggle"
        type="button"
        onClick={() => setAdvancedOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: 'none', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)', padding: '0.45rem 0.9rem',
          fontSize: '0.76rem', color: 'var(--text-muted)', cursor: 'pointer',
          fontFamily: 'inherit', marginBottom: '1.5rem', width: '100%',
          justifyContent: 'space-between',
        }}
      >
        <span>{advancedOpen ? '▲' : '▼'} Advanced settings — office, message, filters, priorities</span>
        <span style={{ fontSize: '0.68rem', fontFamily: 'JetBrains Mono' }}>
          {advancedOpen ? 'hide' : 'show'}
        </span>
      </button>

      {/* ── Advanced section ───────────────────────────────────────────────── */}
      {advancedOpen && (
        <>
          {/* Additional Candidate Info */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div className="section-title" style={{ marginBottom: '1rem' }}>Additional Candidate Details</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Office Sought</div>
                <input value={form.office} onChange={field('office')} placeholder="e.g. City Council Member" />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>District / Ward</div>
                <input value={form.district} onChange={field('district')} placeholder="e.g. District 7" />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Location / City</div>
                <input value={form.location} onChange={field('location')} placeholder="e.g. Lakeview, CA" />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>
                  Election Date
                  {dateInferred && (
                    <span style={{ marginLeft: 6, fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'none', fontWeight: 400 }}>
                      · inferred
                    </span>
                  )}
                </div>
                <input
                  type="date"
                  value={form.election_date}
                  onChange={e => { setDateInferred(false); field('election_date')(e) }}
                />
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <div className="label" style={{ marginBottom: 4 }}>Race Description</div>
                <input value={form.race} onChange={field('race')} placeholder="e.g. Lakeview City Council, District 7" />
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 4 }}>
                  Shown in the dashboard header. Auto-generated from office + district if blank.
                </div>
              </div>
            </div>
          </div>

          {/* Race Type */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div className="section-title" style={{ marginBottom: '1rem' }}>Race Type</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Race level</div>
                <select value={form.race_level} onChange={field('race_level')}>
                  <option value="">Not set</option>
                  <option value="federal">Federal</option>
                  <option value="state">State</option>
                  <option value="city">City</option>
                  <option value="local">Local</option>
                </select>
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Election type</div>
                <select value={form.election_type} onChange={field('election_type')}>
                  <option value="">Not set</option>
                  <option value="general">General</option>
                  <option value="primary">Primary</option>
                  <option value="special">Special</option>
                </select>
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>District number</div>
                <input value={form.district_number} onChange={field('district_number')} placeholder="e.g. AD 37" />
              </div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <div className="label" style={{ marginBottom: 4 }}>Neighborhood keywords</div>
              <textarea
                value={neighborhoodKeywords}
                onChange={e => setNeighborhoodKeywords(e.target.value)}
                placeholder="e.g. Sunnyside&#10;Woodside&#10;Long Island City"
                rows={2}
                style={{ width: '100%', resize: 'vertical' }}
              />
            </div>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={form.sparse_race_mode}
                onChange={e => setForm(f => ({ ...f, sparse_race_mode: e.target.checked }))}
                style={{ marginTop: 2 }}
              />
              <span>
                <strong style={{ color: 'var(--text-primary)' }}>Sparse race mode</strong> — for local or low-information primaries.
                Generates more manual/social monitors and lets weaker local evidence through for review.
              </span>
            </label>
          </div>

          {/* Campaign Message */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div className="section-title" style={{ marginBottom: '0.5rem' }}>Core Campaign Message</div>
            <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              Used to personalize talking points and suggested actions. 2–4 sentences.
            </p>
            <textarea
              value={form.campaign_message}
              onChange={field('campaign_message')}
              rows={4}
              style={{ width: '100%' }}
              placeholder="e.g. District 7 deserves a council member who shows up. I will fight for housing families can afford, schools our kids deserve, and infrastructure that works."
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 4 }}>
              <div style={{ fontSize: '0.68rem', color: form.campaign_message.length > 400 ? 'var(--opponent)' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                {form.campaign_message.length} chars
              </div>
            </div>
          </div>

          {/* Campaign Filters */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div className="section-title" style={{ marginBottom: '0.5rem' }}>Campaign Filters</div>
            <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              Tune the Race Relevance Engine. One term per line or comma-separated.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Required / relevance terms</div>
                <textarea
                  value={relevanceKeywords}
                  onChange={e => setRelevanceKeywords(e.target.value)}
                  placeholder="e.g. constituent services&#10;campaign finance"
                  rows={5}
                  style={{ width: '100%', resize: 'vertical' }}
                />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Excluded noise terms</div>
                <textarea
                  value={excludedKeywords}
                  onChange={e => setExcludedKeywords(e.target.value)}
                  placeholder="e.g. lottery&#10;recipe&#10;celebrity"
                  rows={5}
                  style={{ width: '100%', resize: 'vertical' }}
                />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Local geography terms</div>
                <textarea
                  value={geographyKeywords}
                  onChange={e => setGeographyKeywords(e.target.value)}
                  placeholder="e.g. Lackawanna County&#10;Scranton&#10;PA-08"
                  rows={5}
                  style={{ width: '100%', resize: 'vertical' }}
                />
              </div>
            </div>
          </div>

          {/* Key Priorities */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div className="section-title" style={{ marginBottom: '0.5rem' }}>Key Priorities</div>
            <p style={{ margin: '0 0 12px', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              Select the issues your campaign is focused on. These guide talking point generation.
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: '1rem' }}>
              {PRIORITY_OPTIONS.map(p => {
                const active = priorities.includes(p)
                return (
                  <button
                    key={p}
                    onClick={() => togglePriority(p)}
                    style={{
                      padding: '0.35rem 0.75rem', borderRadius: 99, cursor: 'pointer',
                      fontSize: '0.74rem', fontFamily: 'inherit',
                      background: active ? 'var(--accent-bg)' : 'var(--surface-2)',
                      border: `1px solid ${active ? 'var(--accent-border)' : 'var(--border)'}`,
                      color: active ? 'var(--accent-light)' : 'var(--text-secondary)',
                      fontWeight: active ? 600 : 400, transition: 'all 0.12s',
                    }}
                  >
                    {active && <span style={{ marginRight: 4 }}>✓</span>}{p}
                  </button>
                )
              })}
            </div>

            {priorities.filter(p => !PRIORITY_OPTIONS.includes(p)).length > 0 && (
              <div style={{ marginBottom: 10, display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {priorities.filter(p => !PRIORITY_OPTIONS.includes(p)).map(p => (
                  <span key={p} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5,
                    padding: '0.3rem 0.65rem', borderRadius: 99,
                    background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.3)',
                    fontSize: '0.74rem', color: '#c4b5fd',
                  }}>
                    {p}
                    <button
                      onClick={() => setPriorities(prev => prev.filter(x => x !== p))}
                      style={{ background: 'none', border: 'none', color: 'var(--opponent)', cursor: 'pointer', padding: 0, fontSize: '0.9rem', lineHeight: 1 }}
                    >×</button>
                  </span>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8 }}>
              <input
                style={{ flex: 1 }}
                value={customPriority}
                onChange={e => setCustomPriority(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addCustomPriority()}
                placeholder="Add a custom priority…"
              />
              <button className="btn btn-ghost btn-sm" onClick={addCustomPriority}>Add</button>
            </div>
          </div>

          {/* Save Profile (in Advanced) */}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: '1.5rem' }}>
            <button className="btn btn-ghost" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save Profile'}
            </button>
            {saved && <span style={{ fontSize: '0.8rem', color: 'var(--ok-light)' }}>✓ Saved</span>}
            {error && <span style={{ fontSize: '0.8rem', color: 'var(--opponent)' }}>{error}</span>}
          </div>

          <div className="info-banner" style={{ marginBottom: '2rem', fontSize: '0.76rem', lineHeight: 1.6 }}>
            <strong style={{ color: 'var(--text-secondary)' }}>How this is used:</strong> Your candidate name, office, district, campaign message, and key priorities are included in every talking point generation prompt. When using a real LLM provider, the AI will reference this context to produce personalized, on-message content.
          </div>

          {/* CSV Import */}
          <div className="card" style={{ marginBottom: '2rem' }}>
            <div className="section-title" style={{ marginBottom: '0.5rem' }}>Import Race Setup from CSV</div>
            <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              Upload a CSV to bulk-create your campaign profile, opponents, RSS feeds, and source reminders.
              Each row needs a <code>type</code> column: <code>campaign</code>, <code>opponent</code>, <code>rss_feed</code>, or <code>reminder</code>.
            </p>
            <div style={{ marginBottom: 10, padding: '0.6rem 0.75rem', background: 'var(--surface-1)', borderRadius: 'var(--radius-sm)', fontFamily: 'JetBrains Mono', fontSize: '0.62rem', color: 'var(--text-muted)', lineHeight: 1.7 }}>
              type,name,url,category,source_type,notes,party,office,district,location,election_date<br />
              campaign,Jane Smith,,,,,Democrat,U.S. Rep,PA-08,Scranton PA,2026-11-03<br />
              opponent,John Doe,,,opponent_statement,,Republican,U.S. Rep,,,<br />
              rss_feed,Local Tribune,https://tribune.com/rss,,news,,,,,<br />
              reminder,Check FEC Page,https://fec.gov,,public_record,Check quarterly,,,,,
            </div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <label className="btn btn-ghost btn-sm" style={{ cursor: 'pointer' }}>
                {importing ? 'Importing…' : 'Choose CSV File'}
                <input ref={csvRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={importCSV} disabled={importing} />
              </label>
              {importError && <span style={{ fontSize: '0.75rem', color: 'var(--opponent)' }}>{importError}</span>}
            </div>
            {importResult && (
              <div style={{ marginTop: 10, padding: '0.6rem 0.75rem', borderRadius: 'var(--radius-sm)', background: 'var(--ok-bg)', border: '1px solid var(--ok-border)', fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                <strong style={{ color: 'var(--ok-light)' }}>✓ Import complete:</strong>{' '}
                {importResult.campaign_updated && 'Campaign profile updated. '}
                {importResult.opponents_created > 0 && `${importResult.opponents_created} opponent(s) added. `}
                {importResult.feeds_created > 0 && `${importResult.feeds_created} RSS feed(s) added. `}
                {importResult.reminders_created > 0 && `${importResult.reminders_created} reminder(s) added. `}
                {importResult.skipped > 0 && `${importResult.skipped} row(s) skipped (duplicates). `}
                {importResult.errors.length > 0 && (
                  <div style={{ marginTop: 4, color: 'var(--opponent)' }}>
                    {importResult.errors.map((e, i) => <div key={i}>{e}</div>)}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Reset Workspace */}
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1.5rem', marginBottom: '1.5rem' }}>
            <button
              className="btn btn-ghost btn-sm"
              style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}
              onClick={() => { setResetOpen(o => !o); setResetError(null); setResetResult(null) }}
            >
              {resetOpen ? '▲' : '▼'} Workspace Reset
            </button>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 4 }}>
              Clears all demo/test data and starts fresh with a new campaign profile.
            </div>

            {resetResult && (
              <div style={{ marginTop: 8, padding: '0.5rem 0.75rem', borderRadius: 'var(--radius-sm)', background: 'var(--ok-bg)', border: '1px solid var(--ok-border)', fontSize: '0.75rem', color: 'var(--ok-light)' }}>
                ✓ {resetResult}
              </div>
            )}

            {resetOpen && (
              <div className="card" style={{ marginTop: 10, borderColor: 'var(--urgent-border)' }}>
                <div className="risk-banner" style={{ marginBottom: 10 }}>
                  <span style={{ fontSize: '0.62rem', fontFamily: 'JetBrains Mono', color: 'var(--opponent)', letterSpacing: '0.07em' }}>
                    ⚠ DESTRUCTIVE — CANNOT BE UNDONE
                  </span>
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
                  Type{' '}
                  <span style={{ fontFamily: 'JetBrains Mono', color: 'var(--opponent)', fontSize: '0.8rem' }}>RESET WORKSPACE</span>{' '}
                  to confirm
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
                      padding: '0.4rem 1rem', borderRadius: 'var(--radius-sm)', fontSize: '0.78rem',
                      background: 'var(--urgent-bg)', border: '1px solid var(--urgent-border)',
                      color: 'var(--opponent)', cursor: resetting ? 'not-allowed' : 'pointer',
                      opacity: resetting ? 0.6 : 1, fontFamily: 'inherit',
                    }}
                    disabled={resetting}
                    onClick={resetWorkspace}
                  >
                    {resetting ? 'Resetting…' : 'Reset Workspace'}
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setResetOpen(false)}>Cancel</button>
                  {resetError && <span style={{ fontSize: '0.75rem', color: 'var(--opponent)' }}>{resetError}</span>}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Step 3: Initialize Campaign ───────────────────────────────────── */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div className="section-title" style={{ marginBottom: 4 }}>3. Initialize Campaign</div>
            <p style={{ margin: 0, fontSize: '0.76rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              For custom races: saves your profile, generates monitors, ingests coverage, and starts narrative tracking. FEC races run this automatically on selection.
            </p>
          </div>
          <button
            data-testid="initialize-btn"
            className="btn btn-primary"
            onClick={initializeCampaign}
            disabled={initializing}
            style={{ flexShrink: 0, minWidth: 180 }}
          >
            {initializing
              ? `Step ${initStep + 1}/4 · ${INIT_STEP_LABELS[initStep]}`
              : '⚡ Initialize Campaign'}
          </button>
        </div>

        {initError && (
          <div data-testid="init-error" style={{ marginTop: '0.75rem', fontSize: '0.78rem', color: 'var(--opponent)' }}>
            {initError}
          </div>
        )}

        {initResult && (
          <div data-testid="init-result" style={{ marginTop: '1rem' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: '0.75rem' }}>
              {initResult.steps.map((s: CampaignInitializeStep) => (
                <div key={s.step} style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: '0.78rem' }}>
                  <span style={{
                    fontFamily: 'JetBrains Mono', fontSize: '0.65rem', fontWeight: 700,
                    color: s.status === 'ok' ? 'var(--ok-light)' : s.status === 'error' ? 'var(--opponent)' : 'var(--text-muted)',
                  }}>
                    {s.status === 'ok' ? '✓' : s.status === 'error' ? '✗' : '–'}
                  </span>
                  <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>{s.label}</span>
                  <span style={{ color: 'var(--text-muted)' }}>{s.detail}</span>
                </div>
              ))}
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--ok-light)', fontWeight: 500 }}>
              {initResult.message}
            </div>
          </div>
        )}
      </div>

      <RescorePanel />
    </div>
  )
}

function RescorePanel() {
  type RescoreStatus = { running: boolean; total: number; processed: number; updated: number; errors: number; current_title: string | null; started_at: string | null; finished_at: string | null }
  const [status, setStatus] = React.useState<RescoreStatus | null>(null)
  const [starting, setStarting] = React.useState(false)
  const [msg, setMsg] = React.useState<string | null>(null)

  React.useEffect(() => {
    api.getRescoreStatus().then(setStatus).catch(() => {})
    const id = setInterval(() => {
      api.getRescoreStatus().then(setStatus).catch(() => {})
    }, 3000)
    return () => clearInterval(id)
  }, [])

  async function start() {
    setStarting(true)
    setMsg(null)
    try {
      const r = await api.startRescore()
      if (r.started) {
        setMsg(`Rescoring ${r.total} articles in the background (~${r.estimated_minutes} min). Progress updates every few seconds.`)
      } else {
        setMsg(r.reason || 'Could not start.')
      }
    } catch (e: any) {
      setMsg(e.message)
    } finally {
      setStarting(false)
    }
  }

  const pct = status && status.total > 0 ? Math.round((status.processed / status.total) * 100) : 0

  return (
    <div className="card" style={{ marginBottom: '1.5rem' }}>
      <div className="section-title" style={{ marginBottom: 4 }}>Rescore Existing Articles</div>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 12 }}>
        Run the AI relevance pipeline on all existing articles to replace old keyword-based scores.
        Takes ~70 minutes in the background — you can close this page and come back.
      </div>

      {status?.running && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: 4 }}>
            <span>{status.processed} / {status.total} articles ({pct}%)</span>
            <span>{status.updated} updated · {status.errors} errors</span>
          </div>
          <div style={{ height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${pct}%`, background: '#3b82f6', transition: 'width 0.5s', borderRadius: 3 }} />
          </div>
          {status.current_title && (
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 4, fontStyle: 'italic' }}>
              {status.current_title}
            </div>
          )}
        </div>
      )}

      {status && !status.running && status.finished_at && (
        <div style={{ fontSize: '0.78rem', color: 'var(--ok-light)', marginBottom: 12 }}>
          ✓ Complete — {status.updated} of {status.total} articles updated.
        </div>
      )}

      {msg && <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: 10 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 8 }}>
        <button
          className="btn btn-primary btn-sm"
          onClick={start}
          disabled={starting || status?.running}
        >
          {status?.running ? `Running… (${pct}%)` : starting ? 'Starting…' : 'Start Rescore'}
        </button>
        {status?.running && (
          <button className="btn btn-ghost btn-sm" onClick={() => api.stopRescore().then(() => setMsg('Stopped.'))}>
            Stop
          </button>
        )}
      </div>
    </div>
  )
}
