interface Props {
  trend: string
}

const CONFIG: Record<string, { arrow: string; color: string }> = {
  rising:  { arrow: '↑', color: '#f87171' },
  stable:  { arrow: '→', color: '#8892a4' },
  falling: { arrow: '↓', color: '#34d399' },
}

export default function TrendBadge({ trend }: Props) {
  const cfg = CONFIG[trend] ?? CONFIG.stable
  return (
    <span style={{
      fontFamily: 'JetBrains Mono',
      fontSize: '0.7rem',
      color: cfg.color,
      display: 'inline-flex',
      alignItems: 'center',
      gap: 3,
    }}>
      {cfg.arrow} {trend}
    </span>
  )
}
