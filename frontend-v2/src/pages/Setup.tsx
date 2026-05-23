import { CheckCircle, Circle, Loader } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { CampaignConfig, SetupStatus } from '@/api/types'

function CheckItem({ done, label, desc }: { done: boolean; label: string; desc: string }) {
  return (
    <div style={{
      display: 'flex',
      gap: 14,
      padding: '14px 18px',
      background: done ? 'rgba(14,124,58,0.06)' : '#090c17',
      border: `1px solid ${done ? 'rgba(14,124,58,0.2)' : '#1c2a3f'}`,
      borderLeft: `3px solid ${done ? '#0e7c3a' : '#1c2a3f'}`,
      borderRadius: 3,
      marginBottom: 8,
    }}>
      <div style={{ flexShrink: 0, marginTop: 1 }}>
        {done
          ? <CheckCircle size={18} style={{ color: '#2db866' }} />
          : <Circle size={18} style={{ color: '#3d4f63' }} />
        }
      </div>
      <div>
        <div style={{
          fontFamily: "'Barlow Condensed', sans-serif",
          fontSize: 15,
          fontWeight: 700,
          color: done ? '#2db866' : '#dce7f3',
          letterSpacing: '0.02em',
          marginBottom: 2,
        }}>
          {label}
        </div>
        <div style={{ fontSize: 12, color: '#7d8fa8' }}>{desc}</div>
      </div>
    </div>
  )
}

export function Setup() {
  const [config, setConfig] = useState<CampaignConfig | null>(null)
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  // Form fields
  const [candidateName, setCandidateName] = useState('')
  const [office, setOffice] = useState('')
  const [district, setDistrict] = useState('')
  const [state, setState] = useState('')
  const [electionDate, setElectionDate] = useState('')
  const [campaignMessage, setCampaignMessage] = useState('')
  const [keywords, setKeywords] = useState('')
  const [priorities, setPriorities] = useState('')

  useEffect(() => {
    Promise.allSettled([api.campaign(), api.setupStatus()]).then(([c, s]) => {
      if (c.status === 'fulfilled') {
        const d = c.value
        setConfig(d)
        setCandidateName(d.candidate_name ?? '')
        setOffice(d.office ?? '')
        setDistrict(d.district ?? '')
        setState(d.state ?? '')
        setElectionDate(d.election_date ?? '')
        setCampaignMessage(d.campaign_message ?? '')
        setKeywords((d.keywords ?? []).join(', '))
        setPriorities((d.priorities ?? []).join('\n'))
      }
      if (s.status === 'fulfilled') setStatus(s.value)
    }).finally(() => setLoading(false))
  }, [])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    setSaved(false)
    try {
      const updated = await api.updateCampaign({
        candidate_name: candidateName.trim(),
        office: office.trim() || undefined,
        district: district.trim() || undefined,
        state: state.trim() || undefined,
        election_date: electionDate || undefined,
        campaign_message: campaignMessage.trim() || undefined,
        keywords: keywords.split(',').map(k => k.trim()).filter(Boolean),
        priorities: priorities.split('\n').map(p => p.trim()).filter(Boolean),
      })
      setConfig(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const setupDone = status
    ? Object.values(status).filter(Boolean).length
    : 0
  const setupTotal = 4

  return (
    <div style={{ minHeight: '100vh' }}>
      <div style={{ padding: '24px 28px', maxWidth: 800, margin: '0 auto' }}>
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, color: '#3d4f63', padding: '40px 0' }}>
            <Loader size={20} style={{ animation: 'spin 1s linear infinite' }} />
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12 }}>LOADING CONFIGURATION...</span>
          </div>
        )}

        {!loading && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 28, alignItems: 'start' }}>
            {/* Campaign config form */}
            <div>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 16,
                fontWeight: 700,
                letterSpacing: '0.06em',
                color: '#7d8fa8',
                textTransform: 'uppercase',
                marginBottom: 16,
                paddingBottom: 8,
                borderBottom: '1px solid #1c2a3f',
              }}>
                Campaign Profile
              </div>
              <form onSubmit={save}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                  <div>
                    <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>CANDIDATE NAME *</label>
                    <input
                      className="input"
                      value={candidateName}
                      onChange={e => setCandidateName(e.target.value)}
                      placeholder="Paige Cognetti"
                      required
                    />
                  </div>
                  <div>
                    <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>OFFICE</label>
                    <input
                      className="input"
                      value={office}
                      onChange={e => setOffice(e.target.value)}
                      placeholder="U.S. House of Representatives"
                    />
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginBottom: 14 }}>
                  <div>
                    <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>DISTRICT</label>
                    <input
                      className="input"
                      value={district}
                      onChange={e => setDistrict(e.target.value)}
                      placeholder="PA-08"
                    />
                  </div>
                  <div>
                    <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>STATE</label>
                    <input
                      className="input"
                      value={state}
                      onChange={e => setState(e.target.value)}
                      placeholder="PA"
                      maxLength={2}
                    />
                  </div>
                  <div>
                    <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>ELECTION DATE</label>
                    <input
                      className="input"
                      type="date"
                      value={electionDate}
                      onChange={e => setElectionDate(e.target.value)}
                    />
                  </div>
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>CAMPAIGN MESSAGE</label>
                  <textarea
                    className="input"
                    value={campaignMessage}
                    onChange={e => setCampaignMessage(e.target.value)}
                    placeholder="Core campaign message or contrast with opponent..."
                    rows={3}
                    style={{ resize: 'vertical' }}
                  />
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>
                    TRACKING KEYWORDS
                    <span style={{ color: '#3d4f63', marginLeft: 8, fontWeight: 400 }}>comma-separated</span>
                  </label>
                  <input
                    className="input"
                    value={keywords}
                    onChange={e => setKeywords(e.target.value)}
                    placeholder="Paige Cognetti, PA-08, Lackawanna County, Scranton..."
                  />
                </div>

                <div style={{ marginBottom: 20 }}>
                  <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>
                    CAMPAIGN PRIORITIES
                    <span style={{ color: '#3d4f63', marginLeft: 8, fontWeight: 400 }}>one per line</span>
                  </label>
                  <textarea
                    className="input"
                    value={priorities}
                    onChange={e => setPriorities(e.target.value)}
                    placeholder={"healthcare\neconomy\ninfrastructure\neducation"}
                    rows={4}
                    style={{ resize: 'vertical', fontFamily: "'IBM Plex Mono', monospace", fontSize: 12 }}
                  />
                </div>

                {error && (
                  <div style={{
                    color: '#f05050',
                    fontSize: 12,
                    marginBottom: 12,
                    padding: '8px 12px',
                    background: 'rgba(201,28,28,0.08)',
                    border: '1px solid rgba(201,28,28,0.2)',
                    borderRadius: 3,
                  }}>
                    {error}
                  </div>
                )}

                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <button type="submit" disabled={saving} className="btn btn-primary">
                    {saving ? (
                      <>
                        <Loader size={13} style={{ animation: 'spin 1s linear infinite' }} />
                        Saving...
                      </>
                    ) : 'Save Configuration'}
                  </button>
                  {saved && (
                    <span style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: 11,
                      color: '#2db866',
                      letterSpacing: '0.08em',
                    }}>
                      ✓ SAVED
                    </span>
                  )}
                </div>
              </form>
            </div>

            {/* Setup checklist */}
            <div>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 16,
                fontWeight: 700,
                letterSpacing: '0.06em',
                color: '#7d8fa8',
                textTransform: 'uppercase',
                marginBottom: 16,
                paddingBottom: 8,
                borderBottom: '1px solid #1c2a3f',
              }}>
                Setup Checklist
              </div>

              {/* Progress bar */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span className="section-label">COMPLETION</span>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#7d8fa8' }}>
                    {setupDone}/{setupTotal}
                  </span>
                </div>
                <div style={{ height: 4, background: '#1c2a3f', borderRadius: 2, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%',
                    width: `${(setupDone / setupTotal) * 100}%`,
                    background: setupDone === setupTotal
                      ? 'linear-gradient(90deg, #0e7c3a, #2db866)'
                      : 'linear-gradient(90deg, #1d6ae5, #4f8ef7)',
                    borderRadius: 2,
                    transition: 'width 0.5s ease',
                  }} />
                </div>
              </div>

              {status && (
                <>
                  <CheckItem
                    done={status.campaign_profile}
                    label="Campaign Profile"
                    desc="Set candidate name, office, and election details"
                  />
                  <CheckItem
                    done={status.opponent_added}
                    label="Opponent Added"
                    desc="Add at least one opponent to track"
                  />
                  <CheckItem
                    done={status.source_added}
                    label="Source Added"
                    desc="Configure an RSS feed or monitor"
                  />
                  <CheckItem
                    done={status.narrative_frame_added}
                    label="Narrative Frame"
                    desc="Create at least one narrative frame to track"
                  />
                </>
              )}

              {setupDone === setupTotal && (
                <div style={{
                  marginTop: 16,
                  padding: '12px 16px',
                  background: 'rgba(14,124,58,0.1)',
                  border: '1px solid rgba(14,124,58,0.25)',
                  borderRadius: 3,
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize: 11,
                  color: '#2db866',
                  letterSpacing: '0.08em',
                  textAlign: 'center',
                }}>
                  ✓ WAR ROOM FULLY OPERATIONAL
                </div>
              )}

              {/* Info box */}
              <div style={{
                marginTop: 16,
                padding: '12px 14px',
                background: '#0e1422',
                border: '1px solid #1c2a3f',
                borderRadius: 3,
              }}>
                <div className="section-label" style={{ marginBottom: 6 }}>SYSTEM INFO</div>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: '#3d4f63', lineHeight: 1.6 }}>
                  <div>CANDIDATE: {config?.candidate_name ?? '—'}</div>
                  <div>DISTRICT: {config?.district ?? '—'} ({config?.state ?? '—'})</div>
                  <div>ELECTION: {config?.election_date ?? '—'}</div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
