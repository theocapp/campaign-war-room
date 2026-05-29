/**
 * Entity review queue — human triage of KG quality issues.
 *
 * Surfaces (subject, object) pairs where the entity-extraction layer
 * produced contradictory signals: both support-type and opposition-type
 * relations against the same target. Some are real political nuance
 * (procedurally supports + rhetorically criticizes), others are extraction
 * noise. Human reviewer marks each as approve / reject / skip.
 */
import { AlertTriangle, Check, ChevronRight, SkipForward, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'

type Item = Awaited<ReturnType<typeof api.entityReviewQueue>>['contradictions'][number]
type QueuePayload = Awaited<ReturnType<typeof api.entityReviewQueue>>
type DriftPayload = Awaited<ReturnType<typeof api.extractorDriftSummary>>

function affiliationColor(a: string | null): string {
  if (a === 'D') return 'var(--candidate)'
  if (a === 'R') return 'var(--opponent)'
  return 'var(--text-3)'
}

function predicateColor(p: string): string {
  if (p === 'endorses' || p === 'co_sponsored' || p === 'voted_for' || p === 'member_of') return 'var(--green)'
  if (p === 'criticizes' || p === 'attacks' || p === 'voted_against') return 'var(--red)'
  return 'var(--text-3)'
}

export function EntityReview({ embedded = false }: { embedded?: boolean } = {}) {
  const [data, setData] = useState<QueuePayload | null>(null)
  const [drift, setDrift] = useState<DriftPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingId, setPendingId] = useState<string | null>(null)

  async function load() {
    try {
      setLoading(true)
      const [queue, driftResult] = await Promise.all([
        api.entityReviewQueue(),
        api.extractorDriftSummary().catch(() => null),
      ])
      setData(queue)
      setDrift(driftResult)
      setLoading(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function decide(item: Item, decision: 'approve' | 'reject' | 'skip') {
    setPendingId(item.item_key)
    try {
      await api.entityReviewDecide(item.item_type, item.item_key, decision)
      // Optimistic remove from local list
      setData(d => d ? {
        ...d,
        summary: { ...d.summary, contradictions: d.summary.contradictions - 1, total: d.summary.total - 1 },
        contradictions: d.contradictions.filter(c => c.item_key !== item.item_key),
      } : d)
    } catch (e) {
      console.error('decide failed', e)
    } finally {
      setPendingId(null)
    }
  }

  return (
    <div style={{
      padding: embedded ? 0 : '20px 28px',
      maxWidth: 1100, margin: '0 auto', color: 'var(--text-1)',
    }}>
      {!embedded && (
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Entity Review Queue</h1>
          <p style={{ fontSize: 13, color: 'var(--text-2)', margin: 0 }}>
            Pairs where the graph contains both support- and opposition-type relations against the
            same target. Some are real political nuance; others are extraction errors. Review each
            and decide.
          </p>
        </div>
      )}

      {loading && <div style={{ color: 'var(--text-2)' }}>Loading…</div>}
      {error && <div style={{ color: 'var(--red)' }}>Failed to load: {error}</div>}

      {drift && drift.relations_with_any_stale_evidence > 0 && (
        <div style={{ padding: '12px 14px', background: 'rgba(255, 191, 0, 0.08)',
                      border: '1px solid rgba(255, 191, 0, 0.3)', borderRadius: 8,
                      marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
            <AlertTriangle size={16} color="var(--accent)" style={{ flexShrink: 0, marginTop: 1 }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent)', marginBottom: 4 }}>
                Ontology drift: {drift.relations_with_all_stale_evidence} relations have only stale evidence
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.5 }}>
                Current extractor is <code style={{ background: 'var(--bg-3)', padding: '1px 5px', borderRadius: 3 }}>{drift.current_version}</code>{' '}
                — {drift.current_summary}
              </div>
              {drift.diffs.length > 0 && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ fontSize: 11, color: 'var(--text-2)', cursor: 'pointer' }}>
                    What's different in {drift.current_version}? ({drift.diffs.length} older version{drift.diffs.length > 1 ? 's' : ''} in evidence)
                  </summary>
                  <div style={{ marginTop: 8, padding: '8px 10px', background: 'var(--bg-sidebar)', borderRadius: 6 }}>
                    {drift.diffs.map(d => (
                      <div key={d.from_version} style={{ marginBottom: 8 }}>
                        <div style={{ fontSize: 11, color: 'var(--text-1)', fontWeight: 600, marginBottom: 4 }}>
                          {d.from_version} → {d.to_version}
                        </div>
                        {d.changes.map((c, i) => (
                          <div key={i} style={{ fontSize: 11, color: 'var(--text-2)', lineHeight: 1.5, marginLeft: 12 }}>
                            • {c}
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                </details>
              )}
              <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-3)' }}>
                Run <code style={{ background: 'var(--bg-3)', padding: '1px 5px', borderRadius: 3 }}>
                  .venv/bin/python scripts/entity_drift_reextract.py --apply --limit 200
                </code> to re-extract the highest-impact stale articles.
              </div>
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-3)', display: 'flex', gap: 10 }}>
                {drift.by_version
                  .filter(v => v.evidence_count > 0)
                  .map(v => (
                    <span key={v.version}>
                      <strong style={{ color: v.stale ? 'var(--accent)' : 'var(--green)' }}>{v.version}</strong>: {v.evidence_count} evidence
                    </span>
                  ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {data && (
        <>
          <div style={{ display: 'flex', gap: 16, padding: '10px 14px', background: 'var(--bg-2)',
                        border: '1px solid #2f2f2f', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
            <div><strong>{data.summary.contradictions}</strong> contradictions pending</div>
          </div>

          {data.contradictions.length === 0 && (
            <div style={{ padding: '40px 24px', textAlign: 'center', color: 'var(--text-3)',
                          background: 'var(--bg-sidebar)', border: '1px dashed #2f2f2f', borderRadius: 8 }}>
              No items in the queue — every contradiction has been triaged.
            </div>
          )}

          {data.contradictions.map(item => (
            <div key={item.item_key} style={{
              background: 'var(--bg-2)', border: '1px solid #2f2f2f', borderRadius: 10,
              marginBottom: 16, padding: '16px 18px',
              opacity: pendingId === item.item_key ? 0.5 : 1,
              transition: 'opacity 0.15s',
            }}>
              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%',
                               background: affiliationColor(item.subject.affiliation), flexShrink: 0 }} />
                <div style={{ fontSize: 16, fontWeight: 600 }}>{item.subject.name}</div>
                <ChevronRight size={14} color="var(--text-3)" />
                <div style={{ fontSize: 16, fontWeight: 600 }}>{item.object.name}</div>
                <div style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-3)' }}>
                  balance {item.balance_score}  ·  supports {item.support_weight}  ·  opposes {item.oppose_weight}
                </div>
              </div>

              {/* Stance summary — which dimensions are in conflict, and what
                  the aggregate stance looks like across all relations */}
              {item.aggregate_stance && (
                <div style={{ padding: '8px 10px', background: 'var(--bg-sidebar)', border: '1px solid #2f2f2f', borderRadius: 6, marginBottom: 10, fontSize: 11 }}>
                  <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700 }}>
                      Conflict on:
                    </span>
                    {(item.conflicting_dimensions ?? []).map(d => (
                      <span key={d} style={{
                        padding: '1px 6px', borderRadius: 3,
                        background: 'rgba(255, 191, 0, 0.15)', color: 'var(--accent)',
                        fontWeight: 600, textTransform: 'capitalize',
                      }}>
                        {d}
                      </span>
                    ))}
                    <span style={{ marginLeft: 'auto', color: 'var(--text-3)' }}>
                      Aggregate:
                      <span style={{ color: 'var(--text-1)', marginLeft: 4 }}>
                        proc=<strong>{item.aggregate_stance.procedural}</strong>{' · '}
                        rhet=<strong>{item.aggregate_stance.rhetorical}</strong>{' · '}
                        ideo=<strong>{item.aggregate_stance.ideological}</strong>{' · '}
                        intensity={item.aggregate_stance.intensity}
                      </span>
                    </span>
                  </div>
                </div>
              )}

              {/* Support + oppose columns */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--green)', textTransform: 'uppercase',
                                fontWeight: 700, letterSpacing: '0.06em', marginBottom: 6 }}>
                    Support signals
                  </div>
                  {item.support_relations.map((r, i) => (
                    <div key={`s-${i}`} style={{ fontSize: 12, color: 'var(--text-1)', marginBottom: 4 }}>
                      <span style={{ color: predicateColor(r.predicate), fontWeight: 600 }}>{r.predicate}</span>
                      <span style={{ color: 'var(--text-3)' }}>  weight {r.weight}</span>
                      {r.sample_quote && (
                        <div style={{ fontSize: 11, color: 'var(--text-2)', fontStyle: 'italic', marginTop: 2 }}>
                          &ldquo;{r.sample_quote.slice(0, 160)}&rdquo;
                        </div>
                      )}
                    </div>
                  ))}
                  {item.support_titles.slice(0, 2).map((t, i) => (
                    <div key={`st-${i}`} style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 4 }}>
                      • {t}
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--red)', textTransform: 'uppercase',
                                fontWeight: 700, letterSpacing: '0.06em', marginBottom: 6 }}>
                    Opposition signals
                  </div>
                  {item.oppose_relations.map((r, i) => (
                    <div key={`o-${i}`} style={{ fontSize: 12, color: 'var(--text-1)', marginBottom: 4 }}>
                      <span style={{ color: predicateColor(r.predicate), fontWeight: 600 }}>{r.predicate}</span>
                      <span style={{ color: 'var(--text-3)' }}>  weight {r.weight}</span>
                      {r.sample_quote && (
                        <div style={{ fontSize: 11, color: 'var(--text-2)', fontStyle: 'italic', marginTop: 2 }}>
                          &ldquo;{r.sample_quote.slice(0, 160)}&rdquo;
                        </div>
                      )}
                    </div>
                  ))}
                  {item.oppose_titles.slice(0, 2).map((t, i) => (
                    <div key={`ot-${i}`} style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 4 }}>
                      • {t}
                    </div>
                  ))}
                </div>
              </div>

              {/* Actions */}
              <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
                <button
                  onClick={() => decide(item, 'skip')}
                  disabled={pendingId === item.item_key}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                           borderRadius: 6, border: '1px solid #2f2f2f', background: 'var(--bg-sidebar)',
                           color: 'var(--text-2)', cursor: 'pointer', fontSize: 12 }}
                >
                  <SkipForward size={14} /> Skip
                </button>
                <button
                  onClick={() => decide(item, 'reject')}
                  disabled={pendingId === item.item_key}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                           borderRadius: 6, border: '1px solid #ef4444', background: 'rgba(239,68,68,0.1)',
                           color: 'var(--red)', cursor: 'pointer', fontSize: 12 }}
                >
                  <X size={14} /> Reject (extraction error)
                </button>
                <button
                  onClick={() => decide(item, 'approve')}
                  disabled={pendingId === item.item_key}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px',
                           borderRadius: 6, border: '1px solid #22c55e', background: 'rgba(34,197,94,0.1)',
                           color: 'var(--green)', cursor: 'pointer', fontSize: 12 }}
                >
                  <Check size={14} /> Approve (real nuance)
                </button>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
