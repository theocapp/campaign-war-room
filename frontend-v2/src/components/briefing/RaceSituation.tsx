import { ChevronDown, ChevronRight } from 'lucide-react'
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import type { BriefingClaim, GroundedMemo } from '@/api/types'
import { formatArticleDate } from '@/lib/formatDate'

/**
 * Auto-fits a one-line heading to its container width.
 *
 * Approach: a hidden span (positioned offscreen, inheriting the
 * heading's font family/weight) acts as a measurer. On container
 * resize, binary-search for the largest font size in [minPx, maxPx]
 * whose measured text width fits in containerWidth × fill. Sub-pixel
 * font-rendering differences are handled because the measurement is
 * taken at the actual candidate font size, not extrapolated from a
 * reference.
 *
 * Why binary search rather than iterative shrink/grow:
 *   - Every container width maps to exactly one font size,
 *     deterministically — no deadband, no oscillation.
 *   - No stickiness on container changes (e.g. sidebar collapse
 *     transition); each ResizeObserver fire computes the right size
 *     from scratch.
 *   - The measurer is attached once and reused; each search costs
 *     ~10 cheap style+bounding-rect reads, only on resize.
 *
 * Below minPx the headline keeps the minimum size and the live h2's
 * overflow:hidden + ellipsis truncates the text.
 */
function useAutoFitFontSize(
  ref: React.RefObject<HTMLElement>,
  text: string | null | undefined,
  { minPx, maxPx, fill = 1.0 }: { minPx: number; maxPx: number; fill?: number },
): number {
  const [fontSize, setFontSize] = useState(maxPx)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || !text) return
    const container = el.parentElement
    if (!container) return

    // Hidden offscreen span — inherits font props from the live h2 so
    // measurements at any candidate font size match the live render.
    const measurer = document.createElement('span')
    const cs = getComputedStyle(el)
    measurer.style.cssText =
      `position: absolute; left: -9999px; top: -9999px;` +
      `visibility: hidden; white-space: nowrap;` +
      `font-weight: ${cs.fontWeight};` +
      `font-family: ${cs.fontFamily};` +
      `letter-spacing: ${cs.letterSpacing};`
    measurer.textContent = text
    document.body.appendChild(measurer)

    const widthAt = (size: number): number => {
      measurer.style.fontSize = `${size}px`
      return measurer.getBoundingClientRect().width
    }

    const adjust = () => {
      const containerWidth = container.clientWidth
      if (!containerWidth) return
      // 6px buffer absorbs small measurement discrepancies between
      // the offscreen measurer and the in-flow h2 (different DOM
      // context can cause a 1-5px difference in computed width at
      // the same font size). Without this the headline can overflow
      // by a few pixels and get ellipsis-truncated.
      const targetWidth = Math.max(0, containerWidth * fill - 6)

      // Early-exit: if the largest allowed size fits, use it.
      if (widthAt(maxPx) <= targetWidth) {
        const rounded = maxPx
        setFontSize(prev => Math.abs(prev - rounded) > 0.05 ? rounded : prev)
        return
      }
      // Early-exit: if even the smallest size overflows, clamp at min
      // (h2's overflow:hidden + ellipsis handles the truncation).
      if (widthAt(minPx) > targetWidth) {
        setFontSize(prev => Math.abs(prev - minPx) > 0.05 ? minPx : prev)
        return
      }

      // Binary search [minPx, maxPx] for the largest size that fits.
      let lo = minPx
      let hi = maxPx
      for (let i = 0; i < 20; i++) {  // 20 iters → 0.001px precision
        const mid = (lo + hi) / 2
        if (widthAt(mid) <= targetWidth) lo = mid
        else hi = mid
        if (hi - lo < 0.05) break
      }
      const rounded = Math.round(lo * 10) / 10
      setFontSize(prev => Math.abs(prev - rounded) > 0.05 ? rounded : prev)
    }

    adjust()
    const ro = new ResizeObserver(adjust)
    ro.observe(container)
    return () => {
      ro.disconnect()
      if (measurer.parentNode) measurer.parentNode.removeChild(measurer)
    }
  }, [ref, text, minPx, maxPx, fill])
  return fontSize
}

function isGroundedMemo(m: unknown): m is GroundedMemo {
  return !!m && typeof m === 'object' && 'text' in (m as object) && 'citations' in (m as object)
}

interface Props {
  memo: GroundedMemo | string | null | undefined
}

/**
 * The "Race Situation" memo section — the AI's synthesis of the week.
 * v=2 (grounded) renders prose with [Cn] citation markers that link to the
 * cited article. v=1 (legacy string) is still accepted for backward compat
 * but the homepage always requests v=2.
 */
export function RaceSituation({ memo }: Props) {
  if (!memo) return null

  const grounded = isGroundedMemo(memo)

  // V5 — "Linear headline" treatment. Tiny BRIEFING label above a punchy
  // 1-line headline (LLM-generated) that summarizes the take. The body
  // memo runs in smaller muted prose below — the headline is the focus,
  // body provides the evidence. Headline only renders if the backend
  // produced one; falls back to body-only gracefully.
  const headline = grounded ? memo.headline : null
  const h2Ref = useRef<HTMLHeadingElement>(null)
  // Auto-fit so the headline fills the column width — short headlines
  // scale up, long ones scale down. Max 80px is generous enough that
  // typical-length briefing headlines (~85 chars) can fill columns up
  // to ~3300px wide (covers 1920/2560/2880 monitors at sidebar-collapsed
  // state); min 18px floors the shrink so very long headlines truncate
  // with ellipsis instead of becoming illegible. fill=1.0 = no intentional
  // gap on the right. A short headline on a quiet news day may visually
  // blow up close to the cap — that's an accepted edge case; the prompt
  // strongly prefers ~85-char headlines anyway.
  const headlineFontSize = useAutoFitFontSize(h2Ref, headline, { minPx: 18, maxPx: 80, fill: 1.0 })
  return (
    <section style={{ marginBottom: 32 }}>
      <div style={{
        fontSize: 10, fontWeight: 700, color: 'var(--text-3)',
        textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 8,
      }}>
        The read
      </div>
      {headline && (
        // Font size is auto-fit by useAutoFitFontSize above so the
        // headline always fills the line regardless of length. Single
        // line enforced by white-space: nowrap; if the headline is so
        // long the minimum size still overflows, text-overflow: ellipsis
        // truncates instead of wrapping.
        <h2
          ref={h2Ref}
          style={{
            margin: '0 0 14px 0',
            fontSize: headlineFontSize,
            fontWeight: 700,
            color: 'var(--text-1)',
            lineHeight: 1.3,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {headline}
        </h2>
      )}
      {grounded ? (
        <GroundedMemoView
          memo={memo}
          pStyle={{ fontSize: 15, lineHeight: 1.65, color: 'var(--text-2)' }}
        />
      ) : (
        <p style={{
          margin: 0, fontSize: 15, lineHeight: 1.65, color: 'var(--text-2)',
        }}>
          {memo as string}
        </p>
      )}
      {grounded && memo.sources_used.length > 0 && (
        <SourcesUsedDisclosure
          sources={memo.sources_used}
          cited={new Set(memo.citations.map(c => c.claim_id))}
        />
      )}
    </section>
  )
}

// Splits memo text on [C\d+] markers and renders each marker as a superscript
// link to the corresponding article. Citations the model invented (no
// matching claim_id) are stripped server-side, so all markers shown here
// resolve to a real claim.
function GroundedMemoView({ memo, pStyle }: { memo: GroundedMemo; pStyle?: React.CSSProperties }) {
  const claimById: Record<number, BriefingClaim> = {}
  for (const c of memo.sources_used) claimById[c.claim_id] = c
  const markerToClaim: Record<string, BriefingClaim | undefined> = {}
  for (const cit of memo.citations) {
    markerToClaim[cit.marker] = claimById[cit.claim_id]
  }

  const segments: Array<{ type: 'text' | 'cite'; value: string; n?: number }> = []
  const re = /\[C(\d+)\]/g
  let last = 0
  let match: RegExpExecArray | null
  const orderedMarkers: string[] = []
  while ((match = re.exec(memo.text)) !== null) {
    if (match.index > last) {
      // Strip trailing whitespace so the citation sits flush against the
      // preceding word (academic-citation convention: "Act[1]" not "Act [1]").
      const textValue = memo.text.slice(last, match.index).replace(/\s+$/, '')
      if (textValue) segments.push({ type: 'text', value: textValue })
    }
    const marker = 'C' + match[1]
    if (!orderedMarkers.includes(marker)) {
      orderedMarkers.push(marker)
    }
    const n = orderedMarkers.indexOf(marker) + 1
    segments.push({ type: 'cite', value: marker, n })
    last = match.index + match[0].length
  }
  if (last < memo.text.length) {
    segments.push({ type: 'text', value: memo.text.slice(last) })
  }

  return (
    <p style={{
      margin: 0,
      fontSize: 17,
      lineHeight: 1.55,
      color: 'var(--text-1)',
      ...pStyle,
    }}>
      {segments.map((seg, i) => {
        if (seg.type === 'text') return <Fragment key={i}>{seg.value}</Fragment>
        const claim = markerToClaim[seg.value]
        return <CitationLink key={i} n={seg.n!} claim={claim} />
      })}
    </p>
  )
}

/**
 * A single [N] citation marker in the briefing memo. Renders an anchor that
 * opens the source article in a new tab; on hover, an article-preview card
 * (outlet · date · quote · open-article link) appears anchored to the
 * marker. The popover flips above/below based on viewport room and stays
 * open while the cursor is inside it so the link is clickable.
 *
 * Clicking the popover body itself navigates to the in-app article detail
 * page (`/articles/{article_id}`); only the "Open article →" link opens
 * the original source in a new tab.
 *
 * Falls back to a plain `[N]` span when the claim couldn't be resolved
 * (shouldn't happen — invalid markers are stripped server-side — but the
 * defensive path matches the prior render behavior).
 */
function CitationLink({ n, claim }: { n: number; claim: BriefingClaim | undefined }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [placement, setPlacement] = useState<'top' | 'bottom'>('bottom')
  const supRef = useRef<HTMLSpanElement>(null)
  const closeTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    return () => {
      if (closeTimer.current !== undefined) window.clearTimeout(closeTimer.current)
    }
  }, [])

  function handleEnter() {
    if (closeTimer.current !== undefined) {
      window.clearTimeout(closeTimer.current)
      closeTimer.current = undefined
    }
    if (supRef.current) {
      // Conservative popover height for the flip check. Real popover is
      // ~140–220px depending on quote length; 220 covers the upper end.
      const POPOVER_HEIGHT = 220
      const EDGE_MARGIN = 16
      const rect = supRef.current.getBoundingClientRect()
      const roomBelow = window.innerHeight - rect.bottom
      const roomAbove = rect.top
      // Default below; flip up when there isn't room below AND there is above.
      if (roomBelow < POPOVER_HEIGHT + EDGE_MARGIN && roomAbove > roomBelow) {
        setPlacement('top')
      } else {
        setPlacement('bottom')
      }
    }
    setOpen(true)
  }

  function handleLeave() {
    // 150ms grace so the cursor can travel from the marker into the popover.
    closeTimer.current = window.setTimeout(() => setOpen(false), 150)
  }

  if (!claim) {
    return (
      <sup style={{ marginLeft: 1 }}>
        <span style={{ color: 'var(--text-3)' }}>[{n}]</span>
      </sup>
    )
  }

  const href = claim.article_url || undefined
  const popoverStyle: CSSProperties = {
    position: 'absolute',
    left: '50%',
    transform: 'translateX(-50%)',
    ...(placement === 'bottom' ? { top: 'calc(100% + 6px)' } : { bottom: 'calc(100% + 6px)' }),
    width: 360,
    maxWidth: 'min(360px, 90vw)',
    background: 'var(--bg-2)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    padding: '12px 14px',
    boxShadow: 'var(--shadow-elev)',
    zIndex: 1000,
    fontSize: 12,
    fontWeight: 400,
    lineHeight: 1.5,
    letterSpacing: 'normal',
    textTransform: 'none',
    fontFamily: 'Inter, system-ui, sans-serif',
    color: 'var(--text-1)',
    textAlign: 'left',
    cursor: 'pointer',
  }

  function handleCardClick() {
    if (!claim) return
    setOpen(false)
    navigate(`/articles/${claim.article_id}`)
  }

  function handleCardKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleCardClick()
    }
  }

  return (
    <sup ref={supRef} style={{ position: 'relative', marginLeft: 1 }}>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
          onFocus={handleEnter}
          onBlur={handleLeave}
          aria-label={`Citation ${n}: ${claim.outlet}`}
          style={{
            color: 'var(--accent)',
            textDecoration: 'none',
            fontWeight: 700,
            padding: '0 2px',
          }}
        >
          [{n}]
        </a>
      ) : (
        <span
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
          style={{ color: 'var(--text-3)', cursor: 'help', padding: '0 2px' }}
        >
          [{n}]
        </span>
      )}
      {open && (
        <span
          role="link"
          tabIndex={0}
          aria-label={`Open article in detail page: ${claim.outlet}`}
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
          onClick={handleCardClick}
          onKeyDown={handleCardKeyDown}
          style={popoverStyle}
        >
          <span style={{
            display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap',
            fontSize: 11, color: 'var(--text-2)', marginBottom: 8,
          }}>
            <span style={{ fontWeight: 600, color: 'var(--text-1)' }}>{claim.outlet}</span>
            {claim.published_at && (
              <>
                <span style={{ color: 'var(--text-3)' }}>·</span>
                <span>{formatArticleDate(claim.published_at)}</span>
              </>
            )}
            {claim.reliability_score != null && (
              <>
                <span style={{ color: 'var(--text-3)' }}>·</span>
                <span>reliability {claim.reliability_score}</span>
              </>
            )}
          </span>
          <span style={{
            display: 'block',
            fontSize: 13, lineHeight: 1.5, color: 'var(--text-1)',
            fontStyle: 'italic',
            marginBottom: href ? 10 : 0,
            // Keep very long quotes from making the popover scroll the page.
            maxHeight: 200,
            overflow: 'auto',
          }}>
            &ldquo;{claim.quote}&rdquo;
          </span>
          {href && (
            <span style={{ display: 'block', fontSize: 11 }}>
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                // Stop the click from bubbling to the card's onClick — clicking
                // "Open article →" should open the original source in a new
                // tab WITHOUT also navigating the current tab to /articles/N.
                onClick={e => e.stopPropagation()}
                onKeyDown={e => e.stopPropagation()}
                style={{ color: 'var(--accent)', textDecoration: 'none', fontWeight: 600 }}
              >
                Open article →
              </a>
            </span>
          )}
        </span>
      )}
    </sup>
  )
}

function SourcesUsedDisclosure({ sources, cited }: { sources: BriefingClaim[]; cited: Set<number> }) {
  const [open, setOpen] = useState(false)
  // Only show sources that were actually cited in the memo. The full
  // "considered" pool was previously shown faded out, but that read as
  // clutter — what the user actually wants is "where did each [N] come
  // from?", not the broader research set.
  const citedSources = sources.filter(s => cited.has(s.claim_id))
  if (citedSources.length === 0) return null
  return (
    <div style={{ marginTop: 4 }}>
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
        Citations ({citedSources.length})
      </button>
      {open && (
        <div style={{
          background: 'var(--bg-2)',
          border: '1px solid var(--bg-3)',
          borderRadius: 8,
          padding: '4px 0',
        }}>
          {citedSources.map(s => {
            return (
              <div
                key={s.claim_id}
                style={{
                  padding: '12px 16px',
                  borderTop: '1px solid var(--bg-3)',
                }}
              >
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  marginBottom: 6, fontSize: 11,
                  color: 'var(--text-2)',
                }}>
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
                </div>
                <div style={{
                  fontSize: 13, lineHeight: 1.5, color: 'var(--text-1)',
                  fontStyle: 'italic', marginBottom: 6,
                }}>
                  &ldquo;{s.quote}&rdquo;
                </div>
                {s.article_url && (
                  <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                    <a
                      href={s.article_url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: 'var(--accent)', textDecoration: 'none' }}
                    >
                      Open article →
                    </a>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
