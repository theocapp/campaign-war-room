import { AlertTriangle, ChevronDown, ChevronRight, Printer } from 'lucide-react'
import { Fragment, useEffect, useMemo, useState } from 'react'
import { api } from '@/api/client'
import type {
  BriefingClaim,
  BriefingEntity,
  GroundedMemo,
  MorningBriefing as BriefingData,
  OwnerType,
} from '@/api/types'
import { quadrantColor as _qc } from '@/lib/quadrantColor'
import { InfoTooltip } from '@/components/InfoTooltip'
import { formatArticleDate } from '@/lib/formatDate'

function isGroundedMemo(m: unknown): m is GroundedMemo {
  return !!m && typeof m === 'object' && 'text' in (m as object) && 'citations' in (m as object)
}

function readVersionFromUrl(): number {
  // Default is v=2 (grounded memo with verbatim claim_record citations).
  // v=1 (legacy paraphrase) remains accessible via ?v=1 for fallback if the
  // grounded path returns nothing — e.g. quiet news week with no labeled
  // claims to cite. See briefing_summary.get_or_generate_grounded.
  if (typeof window === 'undefined') return 2
  const v = new URLSearchParams(window.location.search).get('v')
  const n = v ? parseInt(v, 10) : 2
  return Number.isFinite(n) && n > 0 ? n : 2
}

function ownerColor(t?: OwnerType) {
  if (t === 'candidate') return '#4f8ef7'
  if (t === 'opponent') return '#f05050'
  return 'var(--text-2)'
}

// V13.21 — quadrant color (owner × subject).
function frameColor(f: { owner_type?: OwnerType; subject_type?: OwnerType }): string {
  if (f.subject_type) return _qc(f.owner_type ?? null, f.subject_type ?? null)
  return ownerColor(f.owner_type)
}

function formatBriefingDate() {
  const d = new Date()
  return d.toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  })
}

export function MorningBriefing() {
  const [data, setData] = useState<BriefingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const version = useMemo(readVersionFromUrl, [])

  useEffect(() => {
    api.morningBriefing(version)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [version])

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '28px 32px', minHeight: '100vh' }}>
      {/* Header — clean working dashboard, not a classified document. */}
      <div style={{
        borderBottom: '1px solid var(--bg-3)',
        padding: '4px 0 16px',
        marginBottom: 28,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{
              fontSize: 24,
              fontWeight: 600,
              color: 'var(--text-1)',
              lineHeight: 1.1,
            }}>
              Daily Briefing
            </div>
            <div style={{
              fontSize: 13,
              color: 'var(--text-2)',
              marginTop: 6,
            }}>
              {formatBriefingDate()}
            </div>
            <div style={{
              fontSize: 12,
              color: 'var(--text-3)',
              marginTop: 2,
            }}>
              Cognetti for Congress · PA-08
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
          background: 'var(--bg-2)',
          border: '1px solid var(--bg-3)',
          borderRadius: 12,
          color: '#f05050',
          fontSize: 12,
        }}>
          FAILED TO LOAD BRIEFING: {error}
          <br />
          <span style={{ color: 'var(--text-2)', fontSize: 11, marginTop: 6, display: 'block' }}>
            Ensure the backend is running at localhost:8000
          </span>
        </div>
      )}

      {data && !loading && (() => {
        // Needs-Response section as a reusable JSX value. Position differs
        // between v1 (below Narrative Pulse) and v2 (above — promoted).
        // v2 also renders a positive empty state ("all clear") when the
        // strict filter produces no items — honest signal that the system
        // is working and there's nothing urgent, not a render bug.
        const hasNeedsResponse = data.needs_response && data.needs_response.length > 0
        const needsResponseSection = hasNeedsResponse ? (
          <section style={{ marginBottom: 32 }}>
            <div style={{
              fontSize: 14,
              fontWeight: 600,
              color: '#f05050',
              marginBottom: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}>
              <AlertTriangle size={13} />
              Needs Response
              <InfoTooltip
                text={'Articles flagged by the AI as potentially damaging or requiring a same-day response — usually opponent attacks or unfavorable framing of you. Review these first and decide whether to push back.'}
                color="var(--red)"
              />
              <span style={{ flex: 1, height: 1, background: 'rgba(201,28,28,0.2)', display: 'block', marginLeft: 4 }} />
            </div>
            {data.needs_response.map((item, i) => (
              <div key={i} style={{
                padding: '14px 18px',
                background: 'var(--bg-2)',
                // v2 makes the urgency more visible — red border so it's
                // unmissable when scanning the page top-to-bottom.
                border: version === 2 ? '1px solid rgba(240, 80, 80, 0.5)' : '1px solid var(--bg-3)',
                borderLeft: version === 2 ? '3px solid #f05050' : '1px solid var(--bg-3)',
                borderRadius: 12,
                marginBottom: 10,
              }}>
                <div style={{ fontSize: 14, color: 'var(--text-1)', fontWeight: 500, marginBottom: 6 }}>
                  {item.title}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
                  {item.source_name}
                  {item.published_at && (
                    <span style={{ marginLeft: 8, color: 'var(--text-3)', fontSize: 10 }}>
                      · {formatArticleDate(item.published_at)}
                    </span>
                  )}
                </div>
                {item.summary && (
                  <div style={{ fontSize: 13, color: 'var(--text-2)', marginTop: 8, lineHeight: 1.5 }}>
                    {item.summary}
                  </div>
                )}
              </div>
            ))}
          </section>
        ) : null

        return (
        <div>
          {/* Race situation memo — string in v1, grounded object in v2. */}
          {data.race_memo && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontSize: 14,
                fontWeight: 600,
                color: 'var(--text-2)',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                Race Situation
                {version === 1 && (
                  // v1 is now the alternate (?v=1). Surface a small chip so
                  // anyone on the legacy path knows they're not on the default.
                  <span style={{
                    fontSize: 10, padding: '2px 6px',
                    background: 'rgba(115,115,115,0.18)', color: 'var(--text-3)',
                    borderRadius: 3, letterSpacing: '0.08em',
                  }}>
                    LEGACY · v1
                  </span>
                )}
                <InfoTooltip
                  text={
                    version === 2
                      ? 'AI-written briefing memo grounded in verbatim quotes from your articles. Citations link to the source article. Expand "Sources used" below to see every quote the model had access to.'
                      : 'Legacy v1 briefing memo: AI paraphrase from article summaries, no inline citations. ?v=2 (the default) shows the grounded version.'
                  }
                  color="var(--text-2)"
                />
                <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
              </div>
              <div style={{
                background: 'var(--bg-2)',
                border: '1px solid var(--bg-3)',
                borderRadius: 12,
                padding: '18px 20px',
              }}>
                {isGroundedMemo(data.race_memo) ? (
                  <GroundedMemoView memo={data.race_memo} />
                ) : (
                  <p style={{
                    margin: 0,
                    fontSize: 15,
                    lineHeight: 1.65,
                    color: 'var(--text-1)',
                    fontStyle: 'italic',
                  }}>
                    {data.race_memo as string}
                  </p>
                )}
              </div>
              {isGroundedMemo(data.race_memo) && data.race_memo.sources_used.length > 0 && (
                <SourcesUsedDisclosure
                  sources={data.race_memo.sources_used}
                  cited={new Set(data.race_memo.citations.map(c => c.claim_id))}
                />
              )}
            </section>
          )}

          {/* v2 — Overnight Changes: candidate-specific labeled claims from
              the last 48h. Hidden when empty (which is normal in quiet
              windows; race-specific labeled-quote density is ~1-2/day). */}
          {version === 2 && data.overnight_changes && data.overnight_changes.length > 0 && (
            <OvernightChangesCard claims={data.overnight_changes} />
          )}

          {/* v2 — Needs Response promoted here (was below Narrative Pulse in v1).
              Operational items go before browse/scan material. Renders a
              positive "all clear" empty state when nothing urgent. */}
          {version === 2 && (needsResponseSection || (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontSize: 14, fontWeight: 600,
                color: 'var(--text-2)',
                marginBottom: 12, display: 'flex',
                alignItems: 'center', gap: 8,
              }}>
                Needs Response
                <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
              </div>
              <div style={{
                padding: '12px 18px',
                background: 'var(--bg-2)',
                border: '1px dashed var(--bg-3)',
                borderRadius: 12,
                fontSize: 13, color: 'var(--text-3)',
                fontStyle: 'italic',
              }}>
                No items requiring immediate response in the last 48 hours.
              </div>
            </section>
          ))}

          {/* v2 — Activity this week (top race-allowlist entities). */}
          {version === 2 && data.top_entities && data.top_entities.length > 0 && (
            <TopEntitiesCard entities={data.top_entities} />
          )}

          {/* Narrative pulse — only show frames with non-zero weekly activity,
              capped to the top 8 movers. Previously rendered all 20+ active
              frames, which made the briefing feel overwhelming and buried the
              actual signal. Tail frames (those further down the list with 1-2
              mentions) are available on the Narratives page. */}
          {(() => {
            const allActive = (data.narrative_pulse || []).filter(i => (i.this_week ?? 0) > 0)
            const active = allActive.slice(0, 8)
            if (active.length === 0) return null
            const moreCount = Math.max(0, allActive.length - active.length)
            return (
              <section style={{ marginBottom: 32 }}>
                <div style={{
                  fontSize: 14,
                  fontWeight: 600,
                  color: 'var(--text-2)',
                  marginBottom: 12,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}>
                  Narrative Pulse — top {active.length}{moreCount > 0 ? ` of ${allActive.length} active` : ' active this week'}
                  <InfoTooltip
                    text={'Each card is one of your tracked narratives that got coverage this week. The arrow shows whether the story grew (↑) or shrank (↓) compared to last week, and by how many articles.'}
                    color="var(--text-2)"
                  />
                  <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
                  {active.map((item, i) => {
                    const delta = (item.this_week ?? 0) - (item.last_week ?? 0)
                    const arrow = delta > 0 ? '↑' : delta < 0 ? '↓' : '→'
                    return (
                      <div key={i} className="card" style={{ padding: '14px 16px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                          <span className={`badge-${item.owner_type ?? 'media'}`} style={{
                            fontSize: 9,
                            letterSpacing: '0.1em',
                            padding: '2px 6px',
                            borderRadius: 2,
                          }}>
                            {(item.owner_type ?? 'media').toUpperCase()}
                          </span>
                        </div>
                        <div style={{
                          fontSize: 14,
                          fontWeight: 700,
                          color: 'var(--text-1)',
                          marginBottom: 6,
                          lineHeight: 1.2,
                        }}>
                          {item.name}
                        </div>
                        <div style={{
                          fontSize: 11,
                          color: frameColor(item),
                        }}>
                          {item.this_week} this wk · {arrow} {Math.abs(delta)} vs last
                        </div>
                      </div>
                    )
                  })}
                </div>
              </section>
            )
          })()}

          {/* v1 position for Needs Response — below Narrative Pulse.
              In v2, this section is promoted to right after Overnight
              Changes (rendered above). */}
          {version !== 2 && needsResponseSection}

          {/* Most recent race-relevant articles. Hidden in v2 — the
              Articles page already shows the raw feed, and a synthesis
              briefing shouldn't end on a generic chronological list. */}
          {version !== 2 && data.new_articles && data.new_articles.length > 0 && (
            <section style={{ marginBottom: 32 }}>
              <div style={{
                fontSize: 14,
                fontWeight: 600,
                color: 'var(--text-2)',
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}>
                Most Recent Articles
                <InfoTooltip
                  text={'The freshest race-relevant articles the system has pulled in, regardless of which narrative they belong to. Good for spotting brand-new stories that haven\'t been categorized yet.'}
                  color="var(--text-2)"
                />
                <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
              </div>
              {data.new_articles.map((item, i) => (
                <div key={i} className="card" style={{ padding: '14px 18px', marginBottom: 8 }}>
                  <div style={{ display: 'flex', gap: 12 }}>
                    <div style={{
                      fontSize: 22,
                      fontWeight: 600,
                      color: '#ffbf00',
                      lineHeight: 1,
                      minWidth: 40,
                    }}>
                      {i + 1}.
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 14, color: 'var(--text-1)', fontWeight: 500, marginBottom: 6 }}>
                        {item.source_url ? (
                          <a href={item.source_url} target="_blank" rel="noreferrer"
                             style={{ color: 'var(--text-1)', textDecoration: 'none' }}>
                            {item.title}
                          </a>
                        ) : item.title}
                      </div>
                      {item.summary && (
                        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5, marginBottom: 6 }}>
                          {item.summary}
                        </div>
                      )}
                      <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                        {item.source_name}
                        {item.published_at && (
                          <span style={{ marginLeft: 8 }}>
                            · {formatArticleDate(item.published_at)}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </section>
          )}

          {/* Risk Warnings and Suggested Actions sections were here previously,
              reading from data.risk_warnings and data.suggested_actions. The
              backend has never produced these fields, so they always
              silently returned null. Removed rather than keeping dead UI —
              add them back when the backend actually generates them (likely
              part of the AI-staffer expansion of the briefing). */}

          {/* Footer — small generation timestamp, no theater. */}
          {data.generated_at && (
            <div style={{
              borderTop: '1px solid var(--bg-3)',
              paddingTop: 12,
              marginTop: 16,
              fontSize: 11,
              color: 'var(--text-3)',
            }}>
              Generated {formatArticleDate(data.generated_at)}
            </div>
          )}
        </div>
        )
      })()}
    </div>
  )
}


// ── v2 grounded memo components ─────────────────────────────────────────

// Splits memo text on [C\d+] markers and renders each marker as a superscript
// link to the corresponding article. Citations the model invented (no
// matching claim_id) are stripped server-side, so all markers shown here
// resolve to a real claim.
function GroundedMemoView({ memo }: { memo: GroundedMemo }) {
  const claimById: Record<number, BriefingClaim> = {}
  for (const c of memo.sources_used) claimById[c.claim_id] = c
  const markerToClaim: Record<string, BriefingClaim | undefined> = {}
  for (const cit of memo.citations) {
    markerToClaim[cit.marker] = claimById[cit.claim_id]
  }

  // Split text into segments — alternating text and markers.
  const segments: Array<{ type: 'text' | 'cite'; value: string; n?: number }> = []
  const re = /\[C(\d+)\]/g
  let last = 0
  let match: RegExpExecArray | null
  let citeIdx = 1
  const orderedMarkers: string[] = []
  while ((match = re.exec(memo.text)) !== null) {
    if (match.index > last) {
      segments.push({ type: 'text', value: memo.text.slice(last, match.index) })
    }
    const marker = 'C' + match[1]
    if (!orderedMarkers.includes(marker)) {
      orderedMarkers.push(marker)
    }
    const n = orderedMarkers.indexOf(marker) + 1
    segments.push({ type: 'cite', value: marker, n })
    last = match.index + match[0].length
    citeIdx++
  }
  if (last < memo.text.length) {
    segments.push({ type: 'text', value: memo.text.slice(last) })
  }

  return (
    <p style={{
      margin: 0,
      fontSize: 15,
      lineHeight: 1.65,
      color: 'var(--text-1)',
    }}>
      {segments.map((seg, i) => {
        if (seg.type === 'text') return <Fragment key={i}>{seg.value}</Fragment>
        const claim = markerToClaim[seg.value]
        const href = claim?.article_url || undefined
        return (
          <sup key={i} style={{ marginLeft: 1 }}>
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                title={claim ? `${claim.outlet} — ${claim.quote.slice(0, 140)}…` : seg.value}
                style={{
                  color: 'var(--accent)',
                  textDecoration: 'none',
                  fontWeight: 700,
                  padding: '0 2px',
                }}
              >
                [{seg.n}]
              </a>
            ) : (
              <span style={{ color: 'var(--text-3)' }}>[{seg.n}]</span>
            )}
          </sup>
        )
      })}
    </p>
  )
}

function SourcesUsedDisclosure({ sources, cited }: { sources: BriefingClaim[]; cited: Set<number> }) {
  const [open, setOpen] = useState(false)
  const citedCount = sources.filter(s => cited.has(s.claim_id)).length
  return (
    <div style={{ marginTop: 12 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: 'transparent', border: 'none',
          color: 'var(--text-2)', cursor: 'pointer',
          fontSize: 12,
          padding: '6px 0',
        }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Sources used ({citedCount} cited / {sources.length} considered)
      </button>
      {open && (
        <div style={{
          background: 'var(--bg-2)',
          border: '1px solid var(--bg-3)',
          borderRadius: 8,
          padding: '4px 0',
        }}>
          {sources.map(s => {
            const wasCited = cited.has(s.claim_id)
            return (
              <div
                key={s.claim_id}
                style={{
                  padding: '12px 16px',
                  borderTop: '1px solid var(--bg-3)',
                  opacity: wasCited ? 1 : 0.75,
                }}
              >
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  marginBottom: 6, fontSize: 11,
                  color: 'var(--text-2)',
                }}>
                  <span style={{
                    padding: '1px 6px', borderRadius: 3,
                    background: 'rgba(255,191,0,0.12)',
                    color: 'var(--accent)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    fontWeight: 700,
                  }}>
                    {s.label}
                  </span>
                  <span>{s.outlet}</span>
                  {s.reliability_score != null && (
                    <span style={{ color: 'var(--text-3)' }}>
                      · reliability {s.reliability_score}
                    </span>
                  )}
                  {s.published_at && (
                    <span style={{ color: 'var(--text-3)' }}>
                      · {formatArticleDate(s.published_at)}
                    </span>
                  )}
                  {wasCited && (
                    <span style={{
                      marginLeft: 'auto', color: 'var(--accent)',
                      fontSize: 9, fontWeight: 700,
                    }}>
                      ● CITED IN MEMO
                    </span>
                  )}
                </div>
                <div style={{
                  fontSize: 13, lineHeight: 1.5, color: 'var(--text-1)',
                  fontStyle: 'italic', marginBottom: 6,
                }}>
                  &ldquo;{s.quote}&rdquo;
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                  {s.entities.length > 0 && (
                    <span>
                      Entities:{' '}
                      {s.entities.map((e, i) => (
                        <Fragment key={e.id}>
                          {i > 0 && ', '}
                          <span style={{
                            color: e.affiliation === 'D' ? 'var(--candidate)'
                                 : e.affiliation === 'R' ? 'var(--opponent)'
                                 : 'var(--text-2)',
                          }}>{e.name}</span>
                        </Fragment>
                      ))}
                    </span>
                  )}
                  {s.article_url && (
                    <a
                      href={s.article_url}
                      target="_blank"
                      rel="noreferrer"
                      style={{
                        marginLeft: 12, color: 'var(--accent)',
                        textDecoration: 'none',
                      }}
                    >
                      Open article →
                    </a>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// OvernightChangesCard — labeled candidate-specific claims from last 48h.
// Single horizontal-rule list, NOT a card grid — denser and easier to scan
// at briefing-open time than card-style display. Each item shows the label,
// outlet, the verbatim quote, and a link to the source article.
function OvernightChangesCard({ claims }: { claims: BriefingClaim[] }) {
  return (
    <section style={{ marginBottom: 32 }}>
      <div style={{
        fontSize: 14, fontWeight: 600,
        color: 'var(--text-2)',
        marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        What Changed in the Race
        <InfoTooltip
          text="Labeled quotes from the last 48 hours that mention Cognetti or Bresnahan directly. National Trump/Shapiro coverage stays out of this section — the entity gate is the candidates themselves."
          color="var(--text-2)"
        />
        <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
      </div>
      <div style={{
        background: 'var(--bg-2)',
        border: '1px solid var(--bg-3)',
        borderRadius: 12,
        padding: '4px 0',
      }}>
        {claims.map(c => (
          <div key={c.claim_id} style={{
            padding: '12px 18px',
            borderTop: '1px solid var(--bg-3)',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              marginBottom: 6, fontSize: 11,
              color: 'var(--text-2)',
            }}>
              <span style={{
                padding: '1px 6px', borderRadius: 3,
                background: 'rgba(255,191,0,0.12)', color: 'var(--accent)',
                textTransform: 'uppercase', letterSpacing: '0.06em',
                fontWeight: 700,
              }}>
                {c.label}
              </span>
              <span>{c.outlet}</span>
              {c.published_at && (
                <span style={{ color: 'var(--text-3)' }}>
                  · {formatArticleDate(c.published_at)}
                </span>
              )}
            </div>
            <div style={{
              fontSize: 14, lineHeight: 1.5, color: 'var(--text-1)',
              marginBottom: c.article_url ? 4 : 0,
            }}>
              &ldquo;{c.quote}&rdquo;
            </div>
            {c.article_url && (
              <a
                href={c.article_url}
                target="_blank"
                rel="noreferrer"
                style={{
                  fontSize: 11, color: 'var(--accent)',
                  textDecoration: 'none',
                }}
              >
                Open article →
              </a>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function TopEntitiesCard({ entities }: { entities: BriefingEntity[] }) {
  return (
    <section style={{ marginBottom: 32 }}>
      <div style={{
        fontSize: 14, fontWeight: 600,
        color: 'var(--text-2)',
        marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        Activity This Week
        <InfoTooltip
          text="Who's getting coverage this week vs last week. Restricted to your candidate, opponent, and a short allowlist of high-impact race-adjacent figures and bills — so this doesn't drift into trending-names noise."
          color="var(--text-2)"
        />
        <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',  // minmax(0,1fr) lets cards shrink below their content's natural width — fixes the overflow when long sample titles push cards wider than their column
        gap: 10,
      }}>
        {entities.map(e => {
          const delta = e.delta
          const arrow = delta > 0 ? '↑' : delta < 0 ? '↓' : '→'
          const color =
            e.affiliation === 'D' ? 'var(--candidate)'
            : e.affiliation === 'R' ? 'var(--opponent)'
            : 'var(--text-2)'
          return (
            <div key={e.id} className="card" style={{
              padding: '14px 16px',
              minWidth: 0,      // required for grid item to shrink
              overflow: 'hidden',
            }}>
              <div style={{
                display: 'flex', justifyContent: 'space-between',
                alignItems: 'baseline', marginBottom: 8, gap: 6,
              }}>
                <span style={{
                  color, fontSize: 14, fontWeight: 700,
                  overflow: 'hidden', textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap', minWidth: 0,
                }}>
                  {e.name}
                </span>
                <span style={{
                  fontSize: 10, color: 'var(--text-3)',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                  flexShrink: 0,
                }}>
                  {e.type}
                </span>
              </div>
              <div style={{
                display: 'flex', alignItems: 'baseline', gap: 8,
                marginBottom: 8,
              }}>
                <span style={{
                  fontSize: 22, fontWeight: 700, color: 'var(--text-1)',
                }}>
                  {e.mentions_this_week}
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                  this week
                </span>
                <span style={{
                  marginLeft: 'auto', fontSize: 11,
                  color: delta > 0 ? 'var(--green, #22c55e)'
                       : delta < 0 ? 'var(--red, #ef4444)'
                       : 'var(--text-3)',
                  flexShrink: 0,
                }}>
                  {arrow}{Math.abs(delta)} vs {e.mentions_last_week}
                </span>
              </div>
              {e.sample_recent_titles.length > 0 && (
                <ul style={{
                  margin: 0, padding: 0, listStyle: 'none',
                  fontSize: 11, color: 'var(--text-2)', lineHeight: 1.45,
                }}>
                  {e.sample_recent_titles.slice(0, 2).map((t, i) => (
                    <li key={i} style={{
                      overflow: 'hidden', textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap', marginTop: i > 0 ? 4 : 0,
                    }}>
                      · {t}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
