interface Props {
  urgency: string
  size?: 'sm' | 'md'
}

const LABELS: Record<string, string> = {
  high: 'HIGH',
  medium: 'MED',
  low: 'LOW',
  urgent: 'URGENT',
}

export default function UrgencyBadge({ urgency, size = 'md' }: Props) {
  const cls = `badge badge-${urgency}`
  return (
    <span className={cls} style={size === 'sm' ? { fontSize: '0.6rem', padding: '1px 6px' } : undefined}>
      {LABELS[urgency] ?? urgency.toUpperCase()}
    </span>
  )
}
