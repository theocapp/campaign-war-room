import { AlertTriangle, Printer } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { MorningBriefing as BriefingData, OwnerType } from '@/api/types'

function ownerColor(t?: OwnerType) {
  if (t === 'candidate') return '#4f8ef7'
  if (t === 'opponent') return '#f05050'
  return '#7d8fa8'
}

function formatBriefingDate() {
  const d = new Date()
  return d.toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  }).toUpperCase()
}

export function MorningBriefing() {
  const [data, setData] = useState<BriefingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.morningBriefing()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '28px 32px', minHeight: '100vh' }}>
      {/* Header — classified document style */}
      <div style={{
        borderBottom: '1px solid #262626',
        padding: '4px 0 16px',
        marginBottom: 28,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{
              fontFamily: "'Barlow Condensed', sans-serif",
              fontSize: 34,
              fontWeight: 900,
              letterSpacing: '0.06em',
              color: '#dce7f3',
              lineHeight: 1,
            }}>
              BRIEFING
            </div>
            <div style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: 12,
              color: '#7d8fa8',
              marginTop: 6,
              letterSpacing: '0.08em',
            }}>
              {formatBriefingDate()}
            </div>
            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#3d4f63', marginTop: 2 }}>
              DISTRICT PA-08 · COGNETTI FOR CONGRESS
            </div>
          </div>
          <button
            onClick={() => window.print()}
            className="btn btn-ghost"
            style={{ fontSize: 12, marginTop: 4 }}
          >
            <Printer size={13} />
            Print
          </button>
        </div>
      </div>

      {loading && (
        <div style={{ padding: '40px 0' }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} style={{ marginBottom: 20 }}>
              <div className="skeleton" style={{ height: 16, width: '40%', marginBottom: 10 }} />
              <div className="skeleton" style={{ height: 100, width: '100%' }} />
            </div>
          ))}
        </div>
      )}

      {error && (
        <div style={{
          padding: 20,
          background: '#171717',
          border: '1px solid #262626',
          borderRadius: 12,
          color: '#f05050',
          fontFamily: "'IBM Plex Mono', monospace",
          fontSize: 12,
        }}>
          FAILED TO LOAD BRIEFING: {error}
          <br />
          <span style={{ color: '#7d8fa8', fontSize: 11, marginTop: 6, display: 'block' }}>
            Ensure the backend is running at localhost:8000
          </span>
        </div>
      )}

      {data && !loading && (
        <div>
          {/* Race situation memo */}
          {data.race_situation_memo && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#3d4f63',
                textTransform: 'uppercase',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                <span style={{ flex: 1, height: 1, background: '#262626', display: 'block' }} />
                Race Situation
                <span style={{ flex: 2, height: 1, background: '#262626', display: 'block' }} />
              </div>
              <div style={{
                background: '#171717',
                border: '1px solid #262626',
                borderRadius: 12,
                padding: '18px 20px',
              }}>
                <p style={{
                  margin: 0,
                  fontSize: 15,
                  lineHeight: 1.65,
                  color: '#dce7f3',
                  fontStyle: 'italic',
                }}>
                  {data.race_situation_memo}
                </p>
              </div>
            </section>
          )}

          {/* Narrative pulse */}
          {data.narrative_pulse && data.narrative_pulse.length > 0 && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#3d4f63',
                textTransform: 'uppercase',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                <span style={{ flex: 1, height: 1, background: '#262626', display: 'block' }} />
                Narrative Pulse
                <span style={{ flex: 2, height: 1, background: '#262626', display: 'block' }} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
                {data.narrative_pulse.map((item, i) => (
                  <div key={i} className="card" style={{ padding: '14px 16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                      <span className={`badge-${item.owner_type ?? 'media'}`} style={{
                        fontFamily: "'IBM Plex Mono', monospace",
                        fontSize: 9,
                        letterSpacing: '0.1em',
                        padding: '2px 6px',
                        borderRadius: 2,
                      }}>
                        {(item.owner_type ?? 'media').toUpperCase()}
                      </span>
                    </div>
                    <div style={{
                      fontFamily: "'Barlow Condensed', sans-serif",
                      fontSize: 14,
                      fontWeight: 700,
                      color: '#dce7f3',
                      marginBottom: 6,
                      lineHeight: 1.2,
                    }}>
                      {item.frame_name ?? item.short_label ?? item.name}
                    </div>
                    {(item.mention_count_24h ?? item.this_week) !== undefined && (
                      <div style={{
                        fontFamily: "'IBM Plex Mono', monospace",
                        fontSize: 11,
                        color: ownerColor(item.owner_type),
                      }}>
                        {item.mention_count_24h ?? item.this_week} MENTIONS / 7D
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Needs response */}
          {data.needs_response && data.needs_response.length > 0 && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#f05050',
                textTransform: 'uppercase',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                <AlertTriangle size={13} />
                Needs Response
                <span style={{ flex: 1, height: 1, background: 'rgba(201,28,28,0.2)', display: 'block', marginLeft: 4 }} />
              </div>
              {data.needs_response.map((item, i) => (
                <div key={i} style={{
                  padding: '14px 18px',
                  background: '#171717',
                  border: '1px solid #262626',
                  borderRadius: 12,
                  marginBottom: 10,
                }}>
                  <div style={{ fontSize: 14, color: '#dce7f3', fontWeight: 500, marginBottom: 6 }}>
                    {item.title}
                  </div>
                  <div style={{ fontSize: 12, color: '#7d8fa8' }}>
                    {item.source_name}
                    {item.published_at && (
                      <span style={{ marginLeft: 8, color: '#3d4f63', fontFamily: "'IBM Plex Mono', monospace", fontSize: 10 }}>
                        · {new Date(item.published_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                      </span>
                    )}
                  </div>
                  {item.summary && (
                    <div style={{ fontSize: 13, color: '#7d8fa8', marginTop: 8, lineHeight: 1.5 }}>
                      {item.summary}
                    </div>
                  )}
                </div>
              ))}
            </section>
          )}

          {/* New developments */}
          {data.new_developments && data.new_developments.length > 0 && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#3d4f63',
                textTransform: 'uppercase',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                <span style={{ flex: 1, height: 1, background: '#262626', display: 'block' }} />
                New Developments
                <span style={{ flex: 2, height: 1, background: '#262626', display: 'block' }} />
              </div>
              {data.new_developments.map((item, i) => (
                <div key={i} className="card" style={{ padding: '14px 18px', marginBottom: 8 }}>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <div style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: 22,
                      fontWeight: 600,
                      color: '#ffbf00',
                      lineHeight: 1,
                      minWidth: 40,
                    }}>
                      {i + 1}.
                    </div>
                    <div>
                      <div style={{ fontSize: 14, color: '#dce7f3', fontWeight: 500, marginBottom: 6 }}>
                        {item.title}
                      </div>
                      {item.why_it_matters && (
                        <div style={{ fontSize: 13, color: '#7d8fa8', lineHeight: 1.5 }}>
                          {item.why_it_matters}
                        </div>
                      )}
                      <div style={{ marginTop: 6, fontSize: 11, color: '#3d4f63', fontFamily: "'IBM Plex Mono', monospace" }}>
                        {item.source_count && `${item.source_count} SOURCES`}
                        {item.issue && <span style={{ marginLeft: 8 }}>· {item.issue.toUpperCase()}</span>}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </section>
          )}

          {/* Risk warnings */}
          {data.risk_warnings && data.risk_warnings.length > 0 && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#c47800',
                textTransform: 'uppercase',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                <AlertTriangle size={13} />
                Risk Warnings
              </div>
              {data.risk_warnings.map((w, i) => (
                <div key={i} style={{
                  padding: '12px 16px',
                  background: '#171717',
                  border: '1px solid #262626',
                  borderRadius: 12,
                  fontSize: 13,
                  color: '#dce7f3',
                  marginBottom: 8,
                }}>
                  {w}
                </div>
              ))}
            </section>
          )}

          {/* Suggested actions */}
          {data.suggested_actions && data.suggested_actions.length > 0 && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.12em',
                color: '#3d4f63',
                textTransform: 'uppercase',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                <span style={{ flex: 1, height: 1, background: '#262626', display: 'block' }} />
                Suggested Actions
                <span style={{ flex: 2, height: 1, background: '#262626', display: 'block' }} />
              </div>
              {data.suggested_actions.map((action, i) => (
                <div key={i} style={{
                  display: 'flex',
                  gap: 12,
                  padding: '12px 16px',
                  background: '#171717',
                  border: '1px solid #262626',
                  borderRadius: 12,
                  marginBottom: 8,
                }}>
                  <span style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: 11,
                    color: '#ffbf00',
                    fontWeight: 600,
                    flexShrink: 0,
                  }}>
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <span style={{ fontSize: 13, color: '#dce7f3' }}>{action}</span>
                </div>
              ))}
            </section>
          )}

          {/* Footer */}
          <div style={{
            borderTop: '1px solid #262626',
            paddingTop: 16,
            marginTop: 8,
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 10,
            color: '#3d4f63',
            letterSpacing: '0.08em',
            display: 'flex',
            justifyContent: 'space-between',
          }}>
            <span>COGNETTI FOR CONGRESS // PA-08</span>
            <span>CAMPAIGN INTERNAL — DO NOT DISTRIBUTE</span>
          </div>
        </div>
      )}
    </div>
  )
}
