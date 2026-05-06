import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Issue, SourceItemDetail, TalkingPointResponse, GeneratedTalkingPoint } from '../api/types'

const TONE_OPTIONS = [
  { value: 'calm',          label: 'Calm',    desc: 'Measured, factual' },
  { value: 'aggressive',    label: 'Sharp',   desc: 'Direct contrast' },
  { value: 'policy-focused',label: 'Policy',  desc: 'Detail-oriented' },
  { value: 'debate',        label: 'Debate',  desc: 'Structured contrast' },
  { value: 'social',        label: 'Social',  desc: 'Short & punchy' },
]

const TABS = [
  { id: 'short' as const,  label: 'Door Knock' },
  { id: 'long' as const,   label: 'Interview' },
  { id: 'debate' as const, label: 'Debate' },
  { id: 'social' as const, label: 'Social' },
]

export default function TalkingPoints() {
  const [searchParams] = useSearchParams()
  const [issues, setIssues] = useState<Issue[]>([])
  const [selectedIssueId, setSelectedIssueId] = useState<number | ''>('')
  const [customIssue, setCustomIssue] = useState('')
  const [tone, setTone] = useState('calm')
  const [result, setResult] = useState<TalkingPointResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'short' | 'long' | 'debate' | 'social'>('short')
  const [copied, setCopied] = useState(false)
  const [history, setHistory] = useState<GeneratedTalkingPoint[]>([])
  const [sourceContext, setSourceContext] = useState<SourceItemDetail | null>(null)

  useEffect(() => {
    api.getIssues().then(setIssues).catch(() => {})
    api.getTalkingPointsHistory().then(setHistory).catch(() => {})
  }, [])

  useEffect(() => {
    const issueId   = searchParams.get('issue_id')
    const customText = searchParams.get('custom_issue_text')
    const toneParam  = searchParams.get('tone')
    const sourceId   = searchParams.get('source_id')
    if (issueId)    setSelectedIssueId(Number(issueId))
    if (customText) { setCustomIssue(customText); setSelectedIssueId('') }
    if (toneParam)  setTone(toneParam)
    if (sourceId)   api.getSource(Number(sourceId)).then(setSourceContext).catch(() => {})
  }, [searchParams])

  async function generate() {
    if (selectedIssueId === '' && !customIssue.trim()) {
      setError('Select an issue or enter a custom topic.')
      return
    }
    setLoading(true); setError(null)
    try {
      const body: Parameters<typeof api.generateTalkingPoints>[0] = { tone, output_format: 'all' }
      if (selectedIssueId !== '') body.issue_id = selectedIssueId as number
      else body.custom_issue_text = customIssue.trim()
      const r = await api.generateTalkingPoints(body)
      setResult(r); setActiveTab('short')
      api.getTalkingPointsHistory().then(setHistory).catch(() => {})
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally { setLoading(false) }
  }

  const tabContent: Record<string, string | undefined> = result ? {
    short: result.short_answer, long: result.long_answer,
    debate: result.debate_answer, social: result.social_post,
  } : {}

  function copy() {
    navigator.clipboard.writeText(tabContent[activeTab] ?? '')
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }

  function loadHistory(tp: GeneratedTalkingPoint) {
    setResult({
      issue: tp.issue_name, short_answer: tp.short_answer, long_answer: tp.long_answer,
      debate_answer: tp.debate_answer, social_post: tp.social_post,
      risk_warning: tp.risk_warning, evidence_notes: tp.evidence_notes,
      source_titles_used: tp.source_titles_used, source_urls_used: tp.source_urls_used,
    })
    setActiveTab('short')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 0, minHeight: '100vh' }}>
      {/* Main panel */}
      <div style={{ padding: '2rem', borderRight: '1px solid var(--border)', overflowY: 'auto' }}>
        <div className="label" style={{ marginBottom: 5 }}>AI-Assisted</div>
        <h1 className="page-title" style={{ marginBottom: 4 }}>Talking Points</h1>
        <p className="page-subtitle" style={{ marginBottom: '1.75rem' }}>Evidence-grounded messaging. Sources cited. Risk warnings included.</p>

        {/* Source context (from Review Queue) */}
        {sourceContext && (
          <div className="card" style={{ marginBottom: '1.25rem', borderLeft: '3px solid var(--accent-border)' }}>
            <div className="label" style={{ color: 'var(--accent-light)', marginBottom: 6 }}>Source Context</div>
            <div style={{ fontWeight: 600, fontSize: '0.87rem', marginBottom: 4 }}>{sourceContext.title}</div>
            {sourceContext.summary && (
              <p style={{ margin: '0 0 6px', fontSize: '0.79rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                {sourceContext.summary}
              </p>
            )}
            {sourceContext.related_issues.length > 0 && (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                {sourceContext.related_issues.map(i => (
                  <span key={i.id} className="badge badge-purple">{i.name}</span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Form */}
        <div className="card" style={{ marginBottom: '1.25rem' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem', marginBottom: '1.25rem' }}>
            {/* Issue selector */}
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Issue</div>
              <select
                value={selectedIssueId}
                onChange={e => { setSelectedIssueId(e.target.value === '' ? '' : Number(e.target.value)); setCustomIssue('') }}
                style={{ marginBottom: 8 }}
              >
                <option value="">— Tracked issue —</option>
                {issues.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
              </select>
              <div style={{ textAlign: 'center', fontSize: '0.68rem', color: 'var(--text-muted)', margin: '0 0 8px' }}>or</div>
              <input
                value={customIssue}
                onChange={e => { setCustomIssue(e.target.value); setSelectedIssueId('') }}
                placeholder="Custom topic…"
              />
            </div>
            {/* Tone */}
            <div>
              <div className="label" style={{ marginBottom: 8 }}>Tone</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {TONE_OPTIONS.map(t => (
                  <button
                    key={t.value}
                    onClick={() => setTone(t.value)}
                    style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '0.45rem 0.75rem', borderRadius: 'var(--radius-sm)',
                      background: tone === t.value ? 'var(--accent-bg)' : 'var(--surface-2)',
                      border: `1px solid ${tone === t.value ? 'var(--accent-border)' : 'var(--border)'}`,
                      color: tone === t.value ? 'var(--accent-light)' : 'var(--text-secondary)',
                      fontSize: '0.79rem', cursor: 'pointer', fontWeight: tone === t.value ? 600 : 400,
                      fontFamily: 'inherit', transition: 'all 0.12s',
                    }}
                  >
                    <span>{t.label}</span>
                    <span style={{ fontSize: '0.66rem', color: 'var(--text-muted)', fontWeight: 400 }}>{t.desc}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn btn-primary" onClick={generate} disabled={loading}>
              {loading ? 'Generating…' : 'Generate talking points'}
            </button>
            {error && <span style={{ fontSize: '0.78rem', color: 'var(--opponent)' }}>{error}</span>}
          </div>
        </div>

        {/* Ethics notice */}
        <div className="info-banner" style={{ marginBottom: '1.5rem', fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.55 }}>
          All outputs are evidence-grounded and tied to cited sources. Claims are labeled as weak where evidence is insufficient.
          Do not fabricate statistics. Do not misrepresent opponents.
        </div>

        {/* Results */}
        {result && (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '1rem' }}>
              <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700 }}>
                {result.issue}
              </h2>
              <span className="badge badge-ghost">{tone}</span>
            </div>

            {/* Pill tabs */}
            <div className="pill-tabs" style={{ marginBottom: '1rem' }}>
              {TABS.map(tab => (
                <button
                  key={tab.id}
                  className={`pill-tab${activeTab === tab.id ? ' active' : ''}`}
                  onClick={() => setActiveTab(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="card" style={{ marginBottom: '1rem', position: 'relative' }}>
              <p style={{ margin: 0, fontSize: '0.9rem', lineHeight: 1.75, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                {tabContent[activeTab]}
              </p>
              <button
                className="btn btn-ghost btn-sm"
                style={{ marginTop: 12 }}
                onClick={copy}
              >
                {copied ? '✓ Copied' : 'Copy'}
              </button>
            </div>

            {result.risk_warning && (
              <div className="risk-banner" style={{ marginBottom: '1rem' }}>
                <div className="label" style={{ color: 'var(--opponent)', marginBottom: 5 }}>⚠ Risk & Messaging Warnings</div>
                <p style={{ margin: 0, fontSize: '0.81rem', color: 'var(--opponent-light)', lineHeight: 1.6 }}>
                  {result.risk_warning}
                </p>
              </div>
            )}

            <div className="card" style={{ borderLeft: '3px solid var(--ok-border)', marginBottom: '1rem' }}>
              <div className="label" style={{ color: 'var(--ok-light)', marginBottom: 6 }}>Evidence & Sources</div>
              <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                {result.evidence_notes}
              </p>
            </div>

            {result.source_titles_used.length > 0 && (
              <div className="card">
                <div className="label" style={{ marginBottom: 10 }}>Sources Used ({result.source_titles_used.length})</div>
                {result.source_titles_used.map((title, i) => {
                  const url = result.source_urls_used[i]
                  return (
                    <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 7, alignItems: 'flex-start' }}>
                      <span style={{ fontFamily: 'JetBrains Mono', fontSize: '0.62rem', color: 'var(--text-muted)', flexShrink: 0, marginTop: 3 }}>
                        {i + 1}.
                      </span>
                      {url ? (
                        <a href={url} target="_blank" rel="noopener noreferrer"
                          style={{ fontSize: '0.8rem', color: 'var(--accent-light)', lineHeight: 1.45 }}>
                          {title}
                        </a>
                      ) : (
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.45 }}>{title}</span>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}
      </div>

      {/* History sidebar */}
      <div style={{ padding: '1.75rem 1.25rem', overflowY: 'auto', background: 'var(--surface-1)' }}>
        <div className="section-title">History</div>
        {history.length === 0 ? (
          <p style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>No history yet.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {history.slice(0, 12).map(tp => (
              <button
                key={tp.id}
                onClick={() => loadHistory(tp)}
                style={{
                  width: '100%', textAlign: 'left', cursor: 'pointer',
                  background: 'var(--surface-2)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '0.65rem 0.75rem',
                  transition: 'all 0.12s',
                  fontFamily: 'inherit',
                }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-strong)' }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)' }}
              >
                <div style={{ fontWeight: 600, fontSize: '0.79rem', color: 'var(--text-primary)', marginBottom: 2, lineHeight: 1.3 }}>
                  {tp.issue_name}
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: 5 }}>
                  {tp.tone} · {new Date(tp.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </div>
                <div style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', lineHeight: 1.45,
                  overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                  {tp.short_answer}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
