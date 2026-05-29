import { Info } from 'lucide-react'
import { useRef, useState } from 'react'
import type { CSSProperties } from 'react'

interface InfoTooltipProps {
  /** Tooltip body. Use `\n` for line breaks. */
  text: string
  /** Icon size in px. Default 12. */
  size?: number
  /** Preferred placement. Will auto-flip to the opposite side if there's
   *  not enough room (e.g. `top` near the top of the viewport flips to
   *  `bottom`). Default 'top'. */
  placement?: 'top' | 'bottom' | 'right' | 'left'
  /** Override icon color. Default text3 (#666). */
  color?: string
  /** Max-width of the tooltip popup in px. Default 280. */
  maxWidth?: number
}

/**
 * Small (i) icon with a hover-tooltip. Auto-flips placement so the popup
 * stays inside the viewport — e.g. a `top`-placement tooltip near the
 * top edge of the page will flip to `bottom`.
 *
 * Pure CSS positioning — no portal — but we measure the icon's bounding
 * rect on hover to decide the effective placement.
 */
export function InfoTooltip({
  text, size = 12, placement = 'top', color = 'var(--text-3)', maxWidth = 280,
}: InfoTooltipProps) {
  const [hovered, setHovered] = useState(false)
  // Effective placement after viewport-edge check. Falls back to the prop
  // until we measure on the first hover.
  const [effectivePlacement, setEffectivePlacement] = useState(placement)
  const iconRef = useRef<HTMLSpanElement>(null)

  // Conservative estimate of the popup's height/width (without measuring
  // the actual rendered tooltip, which would require a two-pass render).
  // ~220px covers most tooltips on this app — long enough that a 3–4 line
  // explanation fits, short enough that we don't waste room.
  const POPUP_ESTIMATED_SIZE = 220
  const EDGE_MARGIN = 16

  function handleEnter() {
    if (iconRef.current) {
      const rect = iconRef.current.getBoundingClientRect()
      const vw = window.innerWidth
      const vh = window.innerHeight
      let p: typeof placement = placement
      // Flip top → bottom if there's no room above
      if (placement === 'top' && rect.top < POPUP_ESTIMATED_SIZE + EDGE_MARGIN) {
        p = 'bottom'
      }
      // Flip bottom → top if there's no room below
      else if (placement === 'bottom' && rect.bottom > vh - POPUP_ESTIMATED_SIZE - EDGE_MARGIN) {
        p = 'top'
      }
      // Flip left → right if there's no room to the left
      else if (placement === 'left' && rect.left < POPUP_ESTIMATED_SIZE + EDGE_MARGIN) {
        p = 'right'
      }
      // Flip right → left if there's no room to the right
      else if (placement === 'right' && rect.right > vw - POPUP_ESTIMATED_SIZE - EDGE_MARGIN) {
        p = 'left'
      }
      setEffectivePlacement(p)
    }
    setHovered(true)
  }

  const popupPos: CSSProperties = (() => {
    switch (effectivePlacement) {
      case 'bottom': return { top: '130%', left: '50%', transform: 'translateX(-50%)' }
      case 'right':  return { left: '130%', top: '50%', transform: 'translateY(-50%)' }
      case 'left':   return { right: '130%', top: '50%', transform: 'translateY(-50%)' }
      case 'top':
      default:       return { bottom: '130%', left: '50%', transform: 'translateX(-50%)' }
    }
  })()

  return (
    <span
      ref={iconRef}
      onMouseEnter={handleEnter}
      onMouseLeave={() => setHovered(false)}
      onClick={e => e.stopPropagation()}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        position: 'relative', cursor: 'help', marginLeft: 4,
        color, lineHeight: 1, verticalAlign: 'middle',
      }}
    >
      <Info size={size} strokeWidth={2} />
      {hovered && (
        <span
          role="tooltip"
          style={{
            position: 'absolute',
            ...popupPos,
            background: 'var(--bg-4)',
            color: 'var(--text-1)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '8px 10px',
            fontSize: 11,
            fontWeight: 400,
            lineHeight: 1.5,
            letterSpacing: 'normal',
            textTransform: 'none',
            whiteSpace: 'pre-line',
            maxWidth,
            width: 'max-content',
            zIndex: 1000,
            boxShadow: 'var(--shadow-elev)',
            pointerEvents: 'none',
            fontFamily: 'Inter, system-ui, sans-serif',
          }}
        >
          {text}
        </span>
      )}
    </span>
  )
}
