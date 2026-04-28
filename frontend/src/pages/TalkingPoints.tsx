import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Issue, SourceItemDetail, TalkingPointResponse, GeneratedTalkingPoint } from '../api/types'

const TONE_OPTIONS = [
  { value: 'calm', label: 'Calm', desc: 'Measured, factual' },
  { value: 'aggressive', label: 'Aggressive', desc: 'Direct contrast' },
  { value: 'policy-focused', label: 'Policy', desc: 'Detail-oriented' },
  { value: 'debate', label: 'Debate', desc: 'Sharp contrast' },
  { value: 'social', label: 'Social', desc: 'Short & punchy' },
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

  // Pre-fill from query params (from Review Queue "Generate TP" links)
  useEffect(() => {
    const issueId = searchParams.get('issue_id')
    const customText = searchParams.get('custom_issue_text')
    const toneParam = searchParams.get('tone')
    const sourceId = searchParams.get('source_id')

    if (issueId) setSelectedIssueId(Number(issueId))
    if (customText) { setCustomIssue(customText); setSelectedIssueId('') }
    if (toneParam) setTone(toneParam)
    if (sourceId) {
      api.getSource(Number(sourceId)).then(setSourceContext).catch(() => {})
    }
  }, [searchParams])

  async function generate() {
    setLoading(true)
    setError(null)
    try {
      const body: { issue_id?: number; custom_issue_text?: string; tone: string; output_format: string } = {
        tone,
        output_format: 'all',
      }
      if (selectedIssueId !== '') {
        body.issue_id = selectedIssueId as number
      } else if (customIssue.trim()) {
        body.custom_issue_text = customIssue.trim()
      } else {
        setError('Select an issue or enter a custom topic.')
        setLoading(false)
        return
      }
      const r = await api.generateTalkingPoints(body)
      setResult(r)
      setActiveTab('short')
      api.getTalkingPointsHistory().then(setHistory).catch(() => {})
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  const tabContent: Record<string, string | undefined> = result ? {
    short: result.short_answer,
    long: result.long_answer,
    debate: result.debate_answer,
    social: result.social_post,
  } : {}

  function copy() {
    navigator.clipboard.writeText(tabContent[activeTab] ?? '')
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div style={{ padding: '1.5rem', maxWidth: 900 }}>
      <div className="label" style={{ marginBottom: 4 }}>AI-Assisted</div>
      <h1 style={{ margin: '0 0 0.25rem', fontSize: '1.2rem', fontWeight: 700 }}>Talking Points Generator</h1>
      <p style={{ margin: '0 0 1.5rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        Evidence-grounded messaging. Sources cited. Risk warnings included.
      </p>

      {/* Source context panel (when arriving from Review Queue) */}
      {sourceContext && (
        <div className="card" style={{ marginBottom: '1rem', borderLeft: '3px solid rgba(59,130,246,0.4)' }}>
          <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: 'var(--accent)', letterSpacing: '0.06em', marginBottom: 6 }}>
            SOURCE CONTEXT
          </div>
          <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: 4 }}>{sourceContext.title}</div>
          {sourceContext.summary && (
            <p style={{ margin: '0 0 6px', fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              {sourceContext.summary}
            </p>
          )}
          {sourceContext.related_issues.length > 0 && (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {sourceContext.related_issues.map(i => (
                <span key={i.id} className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>{i.name}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Form */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
          <div>
            <div className="label" style={{ marginBottom: 6 }}>Issue</div>
            <select
              value={selectedIssueId}
              onChange={e => { setSelectedIssueId(e.target.value === '' ? '' : Number(e.target.value)); setCustomIssue('') }}
            >
              <option value="">— Select tracked issue —</option>
              {issues.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
            <div style={{ textAlign: 'center', margin: '8px 0', fontSize: '0.7rem', color: 'var(--text-muted)' }}>or</div>
            <input
              value={customIssue}
              onChange={e => { setCustomIssue(e.target.value); setSelectedIssueId('') }}
              placeholder="Enter a custom topic…"
            />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 6 }}>Tone</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {TONE_OPTIONS.map(t => (
                <button
                  key={t.value}
                  onClick={() => setTone(t.value)}
                  title={t.desc}
                  style={{
                    padding: '0.35rem 0.75rem', borderRadius: 5,
                    background: tone === t.value ? 'rgba(59,130,246,0.2)' : 'var(--surface-2)',
                    border: `1px solid ${tone === t.value ? 'rgba(59,130,246,0.5)' : 'var(--border)'}`,
                    color: tone === t.value ? '#93c5fd' : 'var(--text-secondary)',
                    fontSize: '0.75rem', cursor: 'pointer',
                    fontWeight: tone === t.value ? 600 : 400,
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              {TONE_OPTIONS.find(t => t.value === tone)?.desc}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="btn-primary" onClick={generate} disabled={loading}>
            {loading ? 'Generating…' : 'Generate Talking Points'}
          </button>
          {error && <span style={{ fontSize: '0.78rem', color: '#f87171' }}>{error}</span>}
        </div>
      </div>

      {/* Ethics notice */}
      <div style={{ marginBottom: '1.5rem', padding: '0.6rem 1rem', borderRadius: 6, background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.15)', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
        All outputs are evidence-grounded and tied to cited sources. Claims labeled as weak where evidence is insufficient.
        Do not fabricate statistics. Do not misrepresent opponents.
      </div>

      {/* Results */}
      {result && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>
              {result.issue} <span style={{ color: 'var(--text-secondary)', fontWeight: 400, fontSize: '0.85rem' }}>· {tone}</span>
            </h2>
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: '1rem', borderBottom: '1px solid var(--border)' }}>
            {(['short', 'long', 'debate', 'social'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  padding: '0.4rem 1rem', borderRadius: '5px 5px 0 0',
                  background: activeTab === tab ? 'var(--surface-2)' : 'transparent',
                  border: `1px solid ${activeTab === tab ? 'var(--border)' : 'transparent'}`,
                  borderBottom: activeTab === tab ? '1px solid var(--surface-2)' : '1px solid transparent',
                  marginBottom: activeTab === tab ? -1 : 0,
                  color: activeTab === tab ? 'var(--text-primary)' : 'var(--text-muted)',
                  fontSize: '0.78rem', cursor: 'pointer', fontWeight: activeTab === tab ? 600 : 400,
                }}
              >
                {tab === 'short' ? 'Door' : tab === 'long' ? 'Interview' : tab === 'debate' ? 'Debate' : 'Social'}
              </button>
            ))}
          </div>

          <div className="card" style={{ marginBottom: '1rem' }}>
            <p style={{ margin: 0, fontSize: '0.88rem', lineHeight: 1.7, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
              {tabContent[activeTab]}
            </p>
            <button
              className="btn-ghost"
              style={{ marginTop: 10, fontSize: '0.72rem' }}
              onClick={copy}
            >
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>

          {result.risk_warning && (
            <div className="risk-banner" style={{ marginBottom: '1rem' }}>
              <div style={{ fontSize: '0.65rem', fontWeight: 700, color: '#f87171', marginBottom: 4, fontFamily: 'JetBrains Mono', letterSpacing: '0.06em' }}>
                ⚠ RISK & MESSAGING WARNINGS
              </div>
              <p style={{ margin: 0, fontSize: '0.8rem', color: '#fca5a5', lineHeight: 1.6 }}>{result.risk_warning}</p>
            </div>
          )}

          <div className="card" style={{ borderLeft: '3px solid rgba(16,185,129,0.4)', marginBottom: '1rem' }}>
            <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: '#34d399', letterSpacing: '0.06em', marginBottom: 6 }}>
              EVIDENCE & SOURCES
            </div>
            <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>{result.evidence_notes}</p>
          </div>

          {result.source_titles_used.length > 0 && (
            <div className="card">
              <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: 'var(--text-muted)', letterSpacing: '0.06em', marginBottom: 8 }}>
                SOURCES USED ({result.source_titles_used.length})
              </div>
              {result.source_titles_used.map((title, i) => {
                const url = result.source_urls_used[i]
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginTop: 2, flexShrink: 0 }}>
                      {i + 1}.
                    </span>
                    {url ? (
                      <a href={url} target="_blank" rel="noopener noreferrer"
                        style={{ fontSize: '0.78rem', color: 'var(--accent)', lineHeight: 1.4 }}>
                        {title}
                      </a>
                    ) : (
                      <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{title}</span>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {/* History */}
      {history.length > 0 && (
        <div style={{ marginTop: '2rem' }}>
          <div className="section-title">Recent History</div>
          {history.slice(0, 8).map(tp => (
            <div
              key={tp.id}
              className="card card-hover"
              style={{ marginBottom: 8, cursor: 'pointer' }}
              onClick={() => {
                setResult({
                  issue: tp.issue_name,
                  short_answer: tp.short_answer,
                  long_answer: tp.long_answer,
                  debate_answer: tp.debate_answer,
                  social_post: tp.social_post,
                  risk_warning: tp.risk_warning,
                  evidence_notes: tp.evidence_notes,
                  source_titles_used: tp.source_titles_used,
                  source_urls_used: tp.source_urls_used,
                })
                setActiveTab('short')
                window.scrollTo({ top: 0, behavior: 'smooth' })
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <span style={{ fontWeight: 600, fontSize: '0.82rem' }}>{tp.issue_name}</span>
                  <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginLeft: 8 }}>{tp.tone}</span>
                </div>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                  {new Date(tp.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </span>
              </div>
              <p style={{ margin: '4px 0 0', fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                {tp.short_answer.slice(0, 120)}{tp.short_answer.length > 120 ? '…' : ''}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
