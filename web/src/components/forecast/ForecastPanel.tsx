import { useEffect, useState } from 'react'
import { api, type Forecast } from '../../lib/api'
import { compact, isoUTC } from '../../lib/format'
import { useTraces } from '../../hooks/useTraces'
import { Skeleton } from '../state/Skeleton'
import { NotComputed } from '../state/NotComputed'
import type { Failure } from '../../lib/api'

/** Where the stolen credentials can still publish.
 *
 *  The panel tells the story in the order the evidence supports it: first the
 *  hindsight check (seeded with one package, did the pivot flag what actually
 *  fell next?), then the candidates that remain, then -- plainly -- what this
 *  cannot predict. */
export function ForecastPanel() {
  const { record } = useTraces()
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [failure, setFailure] = useState<Failure | null>(null)

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const load = async () => {
      const result = await api.forecast()
      if (!alive) return
      if (!result.ok) { setFailure(result.failure); return }
      record('/api/forecast', result.hydra)
      setForecast(result.data)
      // Still warming at boot: ask again rather than showing a skeleton forever.
      if (result.data.warming) timer = window.setTimeout(load, 3000)
    }
    load()
    return () => { alive = false; if (timer) window.clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (failure) return <NotComputed failure={failure} onRetry={() => window.location.reload()} />
  if (!forecast || forecast.warming) {
    return (
      <div>
        <Skeleton className="h-24 w-full" />
        <p className="mt-3 text-xs text-chalk-faint">
          the forecast is computed once at startup — one radius per reachable package
        </p>
      </div>
    )
  }

  const { seeds, maintainers = [], hindsight, candidates = [], reach, boundary } = forecast
  const maxRadius = Math.max(...candidates.map((c) => c.radius_total ?? 0), 1)

  return (
    <div>
      {/* The checkable claim first: this is why the pivot deserves attention. */}
      {hindsight && seeds && (
        <div className="rounded border border-ink-600 bg-ink-800 p-4">
          <p className="text-xs text-chalk-faint">
            rewind to {isoUTC(seeds.first_artifact.published_at)} — only{' '}
            <span className="font-mono text-chalk-dim">
              {seeds.first_artifact.name}@{seeds.first_artifact.version}
            </span>{' '}
            is known compromised
          </p>
          <p className="mt-2 text-2xl font-semibold">
            <span className="tnum text-ember">{hindsight.flagged} of {hindsight.fell_later}</span>{' '}
            <span className="text-chalk">packages that fell in the following minutes were already
            one maintainer hop away</span>
          </p>
          <p className="mt-1.5 text-[11px] text-chalk-dim">
            {hindsight.missed.length === 0
              ? 'none were missed. '
              : `missed: ${hindsight.missed.join(', ')}. `}
            {hindsight.note}
          </p>
        </div>
      )}

      {/* Then what those same edges say is still reachable now. */}
      {reach && (
        <div className="mt-6 flex flex-wrap items-baseline gap-x-10 gap-y-4">
          <div>
            <p className="tnum text-[44px] font-semibold leading-none text-ember">{reach.packages}</p>
            <p className="mt-1 text-xs text-chalk-faint">packages the stolen tokens can still publish to</p>
          </div>
          <div>
            <p className="tnum text-[44px] font-semibold leading-none">{compact(reach.union_radius)}</p>
            <p className="mt-1 text-xs text-chalk-faint">combined blast radius, {reach.depth} hops</p>
          </div>
          <div>
            <p className="tnum text-[44px] font-semibold leading-none">{compact(reach.net_new)}</p>
            <p className="mt-1 text-xs text-chalk-faint">not yet exposed by the incident itself</p>
          </div>
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-chalk-faint">publishers whose credentials must be presumed stolen:</span>
        {maintainers.map((m) => (
          <span key={m.username} className="rounded border border-ink-600 px-2 py-1 font-mono text-chalk-dim">
            {m.username} <span className="text-chalk-faint">· {m.packages_owned} packages</span>
          </span>
        ))}
      </div>

      <ul className="mt-6 rounded border border-ink-600 bg-ink-800">
        {candidates.map((c) => (
          <li key={c.name} className="border-t border-ink-700 px-3 py-2.5 first:border-t-0">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-mono text-xs text-chalk">
                {c.is_popular && <span className="text-ember">• </span>}
                {c.name}
              </span>
              <span className="tnum shrink-0 font-mono text-[11px] text-chalk-dim">
                {c.radius_total === null ? 'radius refused' : `${compact(c.radius_total)} dependents · ${c.radius_depth} hops`}
              </span>
            </div>
            {c.radius_total !== null && (
              <div className="mt-1.5 h-1 rounded bg-ink-900">
                <div
                  className="h-1 rounded bg-ember/60"
                  style={{ width: `${Math.max((c.radius_total / maxRadius) * 100, 1)}%` }}
                />
              </div>
            )}
          </li>
        ))}
      </ul>

      {/* The boundary, stated rather than hidden. */}
      {boundary?.available && (
        <p className="mt-6 max-w-3xl text-[11px] leading-relaxed text-chalk-faint">
          What this cannot do: of the {boundary.victims} packages the broader campaign went on to
          compromise, <span className="text-chalk-dim">{boundary.flagged}</span> are reachable
          through these edges — {boundary.note}
        </p>
      )}
    </div>
  )
}
