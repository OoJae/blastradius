import { isoUTC } from '../../lib/format'

/** Where an install falls relative to the window an artifact was live in.
 *
 *  Resolving a malicious version and installing it while it was live are two
 *  different facts, and this is the one that is hardest to convey in words. */
export function WindowBar({
  liveFrom,
  liveUntil,
  installedAt,
  placement,
  confirmedByGraph,
}: {
  liveFrom: number
  liveUntil: number
  installedAt: number | null
  placement: 'before' | 'inside' | 'after' | null
  confirmedByGraph: boolean | null
}) {
  const span = Math.max(liveUntil - liveFrom, 1)
  // Clamp the marker into view, and say separately where it actually fell —
  // an install a day earlier should not be drawn as if it were a second early.
  const raw = installedAt !== null ? (installedAt - liveFrom) / span : null
  const pct = raw === null ? null : Math.min(Math.max(raw, 0), 1) * 100

  const sentence = (() => {
    if (installedAt === null) return 'no install time given, so the window cannot be placed'
    const delta = installedAt - liveFrom
    if (placement === 'inside') {
      return `installed ${delta}s after this artifact went live, while it was still live`
    }
    if (placement === 'before') {
      return `installed ${liveFrom - installedAt}s before this artifact was published`
    }
    return `installed ${installedAt - liveUntil}s after this artifact was withdrawn`
  })()

  return (
    <div className="mt-3">
      <div className="relative h-6 rounded bg-ink-900">
        <div
          className={`absolute inset-y-0 rounded ${
            placement === 'inside' ? 'bg-verdict-exposed/30' : 'bg-ink-700'
          }`}
          style={{ left: 0, right: 0 }}
        />
        {pct !== null && (
          <div
            className="absolute inset-y-0 w-0.5 bg-ember"
            style={{ left: `${pct}%` }}
            title={isoUTC(installedAt!)}
          />
        )}
        <span className="absolute left-1.5 top-1/2 -translate-y-1/2 font-mono text-[10px] text-chalk-faint">
          {isoUTC(liveFrom).slice(11)}
        </span>
        <span className="absolute right-1.5 top-1/2 -translate-y-1/2 font-mono text-[10px] text-chalk-faint">
          {isoUTC(liveUntil).slice(11)}
        </span>
      </div>

      <p
        className={`mt-1.5 text-[11px] ${
          placement === 'inside' ? 'text-verdict-exposed' : 'text-chalk-dim'
        }`}
      >
        {sentence}
      </p>

      {confirmedByGraph && (
        <p className="mt-1 text-[10px] text-chalk-faint">
          confirmed by HydraDB as an integer predicate on the <code>AFFECTS</code> edge — the
          statement is in the inspector
        </p>
      )}
    </div>
  )
}
