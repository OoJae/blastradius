import { useEffect, useState } from 'react'
import type { Stats } from '../../lib/api'
import { compact, ms } from '../../lib/format'
import { useTraces } from '../../hooks/useTraces'
import { StepCard } from './StepCard'

type Tab = 'request' | 'startup' | 'refused'

export function InspectorDrawer({ stats }: { stats: Stats | null }) {
  const { latest, open, setOpen } = useTraces()
  const [tab, setTab] = useState<Tab>('request')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
      if (e.key === '\\' && !(e.target instanceof HTMLInputElement)) setOpen(!open)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  const refused = Object.entries(stats?.graph ?? {}).filter(([, c]) => c.source === 'refused')

  return (
    <>
      {open && <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setOpen(false)} />}
      <aside
        className={`fixed right-0 top-0 z-50 flex h-full w-[540px] max-w-full flex-col border-l border-ink-600 bg-ink-800 transition-transform duration-200 ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <header className="border-b border-ink-600 px-4 py-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">HydraDB inspector</h2>
            <button onClick={() => setOpen(false)} className="text-chalk-faint hover:text-chalk">
              esc
            </button>
          </div>
          <nav className="mt-3 flex gap-1 text-xs">
            {(['request', 'startup', 'refused'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded px-2 py-1 ${
                  tab === t ? 'bg-ember text-ink-900' : 'text-chalk-dim hover:text-chalk'
                }`}
              >
                {t === 'request' ? 'This request' : t === 'startup' ? 'Startup' : `Refused${refused.length ? ` (${refused.length})` : ''}`}
              </button>
            ))}
          </nav>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {tab === 'request' && (
            <>
              {!latest && (
                <p className="text-sm text-chalk-dim">
                  Run a search and the statements that answered it appear here.
                </p>
              )}
              {latest && (
                <>
                  <div className="rounded border border-ink-600 bg-ink-900 p-3 text-xs">
                    <p className="font-mono text-chalk">{latest.endpoint}</p>
                    <p className="mt-1 tnum text-chalk-dim">
                      {latest.hydra.queries} queries · {latest.hydra.live} live ·{' '}
                      {latest.hydra.cached} cached · {ms(latest.hydra.ms)}
                    </p>
                    <p className="mt-1 text-[10px] text-chalk-faint">
                      ms is database time on this request, excluding answers served from cache
                    </p>
                  </div>
                  {latest.hydra.steps.map((step, i) => (
                    <StepCard key={i} step={step} />
                  ))}
                </>
              )}
            </>
          )}

          {tab === 'startup' && (
            <>
              <p className="text-xs leading-relaxed text-chalk-dim">
                These ran once, when the service booted. Two answers in this product are served
                from their results: autocomplete and the header counts.
              </p>
              {stats?.suggest && (
                <div className="rounded border border-ink-600 bg-ink-900 p-3 text-xs">
                  <p className="tnum text-chalk">
                    {compact(stats.suggest.names)} names · {(stats.suggest.memory_bytes / 1048576).toFixed(1)} MB
                  </p>
                  <p className="mt-1 text-[11px] leading-relaxed text-chalk-faint">
                    {stats.suggest.why}
                  </p>
                </div>
              )}
              {(stats?.boot_queries ?? []).map((step, i) => (
                <StepCard key={i} step={step} />
              ))}
            </>
          )}

          {tab === 'refused' && (
            <>
              {refused.length === 0 && (
                <p className="text-sm text-chalk-dim">Nothing was refused on this graph.</p>
              )}
              {refused.map(([label, cell]) => (
                <div key={label} className="rounded border border-verdict-exposed/50 bg-ink-900 p-3">
                  <p className="text-xs text-chalk">{label} count</p>
                  <p className="mt-1 text-[11px] text-verdict-exposed">source: refused</p>
                  <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] text-chalk-dim">
                    {cell.error}
                  </pre>
                </div>
              ))}
              {refused.length > 0 && (
                <p className="text-[11px] leading-relaxed text-chalk-faint">
                  We show the refusal rather than quietly substituting the loader's own row count.
                  Anchored traversals over the same edges run in milliseconds — counting{' '}
                  <em>everything</em> is what is impossible here, not traversing.
                </p>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  )
}
