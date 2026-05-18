import { AreaChart, Area, ResponsiveContainer, Tooltip } from 'recharts'

interface SparklineProps {
  data: { date: string; count: number; weighted_reach?: number }[]
  color?: string
  height?: number
}

export default function Sparkline({ data, color = '#3b82f6', height = 40 }: SparklineProps) {
  if (!data || data.length === 0) return null
  const useReach = data.some(d => d.weighted_reach != null)
  const dataKey = useReach ? 'weighted_reach' : 'count'
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
        <defs>
          <linearGradient id={`sg-${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Tooltip
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 4, fontSize: 11, padding: '4px 8px' }}
          labelStyle={{ color: '#94a3b8' }}
          itemStyle={{ color: '#f1f5f9' }}
          formatter={(v) => [(v as number).toFixed(1), 'reach']}
        />
        <Area
          type="monotone"
          dataKey={dataKey}
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#sg-${color.replace('#', '')})`}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
