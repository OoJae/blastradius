import { compact } from '../../lib/format'

// Radius scales with the square root of the count, so the rings are comparable
// by area rather than exaggerating the outer ones. This draws counts, which is
// exactly what it has -- it is never a stand-in for topology it does not know.
export function RingDiagram({
  rings, seed,
}: { rings: { depth: number; total: number; ms: number }[]; seed: string }) {
  const max = Math.max(...rings.map((r) => r.total), 1)
  const size = 340
  const centre = size / 2
  const maxR = centre - 30

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full">
      {rings.map((ring) => {
        const r = Math.sqrt(ring.total / max) * maxR
        return (
          <g key={ring.depth}>
            <circle
              cx={centre} cy={centre} r={r}
              fill="none" stroke="#FF6B35"
              strokeOpacity={0.15 + 0.2 * (4 - ring.depth)}
              strokeWidth={1} strokeDasharray="3 4"
              style={{ transition: 'r 400ms ease-out' }}
            />
            <text
              x={centre} y={centre - r - 5}
              textAnchor="middle" className="fill-chalk-dim font-mono"
              style={{ fontSize: 10 }}
            >
              {compact(ring.total)} at depth {ring.depth}
            </text>
          </g>
        )
      })}
      <circle cx={centre} cy={centre} r={5} fill="#FF6B35" />
      <text x={centre} y={centre + 20} textAnchor="middle" className="fill-ember font-mono" style={{ fontSize: 10 }}>
        {seed}
      </text>
    </svg>
  )
}
