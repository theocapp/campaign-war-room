interface Props {
  trend: string
}

const CONFIG: Record<string, { arrow: string; color: string }> = {
  rising:    { arrow: '↑', color: 'var(--opponent)' },
  stable:    { arrow: '→', color: 'var(--text-muted)' },
  unchanged: { arrow: '→', color: 'var(--text-muted)' },
  falling:   { arrow: '↓', color: 'var(--ok-light)' },
}

export default function TrendBadge({ trend }: Props) {
  const cfg = CONFIG[trend] ?? CONFIG.stable
  return (
    <span style={{ fontFamily: 'JetBrains Mono', fontSize: '0.7rem', color: cfg.color, display: 'inline-flex', alignItems: 'center', gap: 3 }}>
      {cfg.arrow} {trend}
    </span>
  )
}
