import { useCallback, useEffect, useState } from 'react'
import { api, type Radius, type Stats } from './lib/api'
import { compact, ms } from './lib/format'
import { TraceProvider, useTraces } from './hooks/useTraces'
import { InspectorDrawer } from './components/inspector/InspectorDrawer'
import { SearchBox } from './components/hero/SearchBox'
import { RingDiagram } from './components/radius/RingDiagram'
import { NotComputed } from './components/state/NotComputed'
import { LockfilePanel } from './components/lockfile/LockfilePanel'
import { Skeleton } from './components/state/Skeleton'
import type { Failure } from './lib/api'

const DEPTHS = [1, 2, 3]
const SEED = '@tanstack/react-router'

type Ring = { depth: number; total: number; ms: number }

function Shell() {
  const { latest, setOpen, record } = useTraces()
  const [stats, setStats] = useState<Stats | null>(null)
  const [booting, setBooting] = useState(true)
  const [bootStage, setBootStage] = useState<string>('')
  const [pkg, setPkg] = useState(SEED)
  const [rings, setRings] = useState<Ring[]>([])
  const [radius, setRadius] = useState<Radius | null>(null)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'radius' | 'lockfile'>('radius')

  // Wait for the service to finish reading the graph before asking it anything.
  useEffect(() => {
    let alive = true
    const poll = async () => {
      const health = await api.health()
      if (!alive) return
      if (health.ok && (health.data as any).ready) {
        setBooting(false)
        const s = await api.stats()
        if (s.ok && alive) setStats(s.data)
        return
      }
      const body: any = health.ok ? health.data : {}
      setBootStage(body.stage ?? 'starting')
      window.setTimeout(poll, 800)
    }
    poll()
    return () => { alive = false }
  }, [])

  // Depth 1 paints at ~170 ms, then 2, then 3. The screen is alive immediately
  // and the rings visibly inflate, instead of a blank pause for the full depth.
  const search = useCallback(async (name: string) => {
    if (!name) return
    setPkg(name); setRings([]); setRadius(null); setFailure(null); setBusy(true)
    for (const depth of DEPTHS) {
      const started = performance.now()
      const result = await api.radius(name, depth)
      record(`/api/blast-radius?pkg=${name}&depth=${depth}`, result.hydra)
      if (!result.ok) { setFailure(result.failure); break }
      const elapsed = performance.now() - started
      setRings((prev) => [...prev.filter((r) => r.depth !== depth), { depth, total: result.data.total, ms: elapsed }])
      setRadius(result.data)
    }
    setBusy(false)
  }, [record])

  useEffect(() => { if (!booting) search(SEED) }, [booting, search])

  if (booting) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <div className="max-w-lg text-center">
          <h1 className="font-mono text-sm text-ember">BLASTRADIUS</h1>
          <p className="mt-4 text-lg text-chalk">{bootStage || 'starting'}</p>
          <p className="mt-2 text-sm text-chalk-dim">
            The autocomplete index is built from HydraDB when the service starts. This runs once.
          </p>
        </div>
      </main>
    )
  }

  const deepest = rings.at(-1)

  return (
    <div className="min-h-screen pb-12">
      <header className="sticky top-0 z-20 border-b border-ink-600 bg-ink-900/95 px-6 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between">
          <div className="flex items-baseline gap-6">
            <span className="font-mono text-sm tracking-wide text-ember">BLASTRADIUS</span>
            <nav className="flex gap-1 text-xs">
              {(['radius', 'lockfile'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`rounded px-2 py-1 ${
                    tab === t ? 'bg-ink-700 text-chalk' : 'text-chalk-dim hover:text-chalk'
                  }`}
                >
                  {t === 'radius' ? 'blast radius' : 'my lockfile'}
                </button>
              ))}
            </nav>
          </div>
          <button
            onClick={() => setOpen(true)}
            className="rounded border border-ink-600 px-3 py-1 font-mono text-[11px] text-chalk-dim hover:border-ember hover:text-ember"
          >
            {latest
              ? `${latest.hydra.queries} queries · ${latest.hydra.live} live · ${ms(latest.hydra.ms)}`
              : 'inspector'}
            {' '}⌥
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-6">
        {tab === 'radius' && (
        <section className="pt-12">
          <h1 className="max-w-4xl text-[44px] font-semibold leading-[1.1] tracking-tight">
            <span className="font-mono text-ember">{SEED}</span> was compromised on 11 May 2026.
          </h1>
          <p className="mt-3 max-w-2xl text-chalk-dim">
            Who would have installed it? That is a transitive reverse-dependency closure, and this
            answers it from a graph.
          </p>

          <div className="mt-8 max-w-3xl"><SearchBox value={pkg} onSearch={search} /></div>

          {stats && (
            <dl className="mt-10 grid grid-cols-2 gap-8 sm:grid-cols-4">
              {[
                ['packages', stats.graph.package],
                ['versions', stats.graph.version],
                ['maintainers', stats.graph.maintainer],
                ['advisories', stats.graph.advisory],
              ].map(([label, cell]: any) => (
                <div key={label}>
                  <dd className="tnum text-[40px] font-semibold leading-none">
                    {cell?.value !== null && cell?.value !== undefined ? compact(cell.value) : '—'}
                  </dd>
                  <dt className="mt-1 text-xs text-chalk-faint">
                    {label} · {cell?.source === 'refused' ? 'refused' : 'from HydraDB'}
                  </dt>
                </div>
              ))}
            </dl>
          )}
        </section>
        )}

        {tab === 'lockfile' && (
          <section className="mt-12">
            <h2 className="text-2xl font-semibold">Were you exposed?</h2>
            <p className="mt-2 max-w-2xl text-sm text-chalk-dim">
              Drop the lockfile your CI resolved. Each artifact is checked against the advisory,
              and anything it names is checked against the window it was live in.
            </p>
            <div className="mt-8">
              <LockfilePanel stats={stats} />
            </div>
          </section>
        )}

        {tab === 'radius' && (
        <section className="mt-14 grid gap-8 lg:grid-cols-[380px_1fr]">
          <div className="rounded border border-ink-600 bg-ink-800 p-4">
            {rings.length === 0 && busy && <Skeleton className="h-[340px] w-full" />}
            {rings.length > 0 && <RingDiagram rings={rings} seed={pkg} />}
          </div>

          <div>
            {failure && <NotComputed failure={failure} onRetry={() => search(pkg)} />}

            {!failure && deepest && (
              <>
                <p className="tnum text-[64px] font-semibold leading-none text-ember">
                  {compact(deepest.total)}
                </p>
                <p className="mt-2 text-chalk-dim">
                  packages would have installed a malicious version, within {deepest.depth} hops
                </p>
                <ul className="mt-4 space-y-1 font-mono text-[11px] text-chalk-faint">
                  {rings.map((r) => (
                    <li key={r.depth} className="tnum">
                      depth {r.depth}: {compact(r.total)} · {ms(r.ms)}
                      {busy && r.depth === deepest.depth && ' · deepening…'}
                    </li>
                  ))}
                </ul>

                {radius && (
                  <div className="mt-6">
                    <p className="text-xs text-chalk-faint">
                      showing {compact(radius.returned)} of {compact(radius.total)}
                    </p>
                    <ul className="mt-2 grid max-h-72 grid-cols-2 gap-x-6 overflow-y-auto font-mono text-[11px] text-chalk-dim md:grid-cols-3">
                      {radius.dependents.slice(0, 150).map((d) => (
                        <li key={d.name} className="truncate">
                          {d.is_popular && <span className="text-ember">• </span>}{d.name}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </div>
        </section>
        )}
      </main>

      <InspectorDrawer stats={stats} />
    </div>
  )
}

export default function App() {
  return (
    <TraceProvider>
      <Shell />
    </TraceProvider>
  )
}
