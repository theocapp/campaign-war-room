interface Props {
  urgency: string
  size?: 'sm' | 'md'
}

const COLORS: Record<string, string> = {
  high:   'var(--opponent)',
  urgent: 'var(--opponent)',
  medium: 'var(--warning)',
  low:    'var(--ok-light)',
}

export default function UrgencyBadge({ urgency, size = 'md' }: Props) {
  const color = COLORS[urgency] ?? 'var(--text-muted)'
  const dotSize = size === 'sm' ? 6 : 7
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <span style={{ width: dotSize, height: dotSize, borderRadius: '50%', background: color, flexShrink: 0, display: 'inline-block' }} />
      <span style={{ fontSize: size === 'sm' ? '0.64rem' : '0.7rem', color, fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {urgency}
      </span>
    </span>
  )
}
