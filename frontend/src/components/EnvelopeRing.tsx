import { centsToEuros, categoryLabel } from './formatters'

export function EnvelopeRing({
  used,
  cap,
  category,
}: {
  used: number
  cap: number
  category: string
}) {
  const pct = cap > 0 ? Math.min(used / cap, 1.5) : 0
  const r = 36
  const c = 2 * Math.PI * r
  const tone =
    pct > 1
      ? 'text-rose-500'
      : pct >= 0.8
      ? 'text-amber-500'
      : 'text-emerald-500'
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="96" height="96" viewBox="0 0 96 96" className={tone}>
        <circle
          cx="48"
          cy="48"
          r={r}
          fill="none"
          stroke="currentColor"
          className="opacity-15"
          strokeWidth="10"
        />
        <circle
          cx="48"
          cy="48"
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - Math.min(pct, 1))}
          transform="rotate(-90 48 48)"
          style={{ transition: 'stroke-dashoffset 400ms ease-out' }}
        />
        <text
          x="48"
          y="54"
          textAnchor="middle"
          className="fill-current text-base font-semibold"
        >
          {Math.round(pct * 100)}%
        </text>
      </svg>
      <div className="text-sm font-medium text-zinc-700">
        {categoryLabel[category] ?? category}
      </div>
      <div className="text-xs text-zinc-500 tabular-nums">
        {centsToEuros(used)} / {centsToEuros(cap)}
      </div>
    </div>
  )
}
