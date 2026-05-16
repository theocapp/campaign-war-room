interface FilterOption {
  label: string
  value: string
}

interface FilterChipsProps {
  label?: string
  options: FilterOption[]
  value: string
  onChange: (value: string) => void
}

export default function FilterChips({ label, options, value, onChange }: FilterChipsProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      {label && (
        <span style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {label}:
        </span>
      )}
      {options.map(opt => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            style={{
              padding: '3px 10px',
              fontSize: 12,
              borderRadius: 20,
              cursor: 'pointer',
              background: active ? 'var(--text, #f1f5f9)' : 'transparent',
              border: `1px solid ${active ? 'var(--text, #f1f5f9)' : 'var(--border, #334155)'}`,
              color: active ? 'var(--bg, #0f172a)' : 'var(--text-muted, #94a3b8)',
              fontWeight: active ? 700 : 400,
              transition: 'all 0.1s',
            }}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
