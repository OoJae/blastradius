import { useState } from 'react'
import { api, type LockfileVerdict, type Failure, type Stats } from '../../lib/api'
import { compact, isoUTC } from '../../lib/format'
import { useTraces } from '../../hooks/useTraces'
import { DropZone } from './DropZone'
import { VerdictTable } from './VerdictTable'
import { NotComputed } from '../state/NotComputed'

const TONE: Record<string, string> = {
  EXPOSED: 'text-verdict-exposed',
  UNKNOWN: 'text-verdict-unknown',
  AT_RISK: 'text-verdict-atrisk',
  CLEAN: 'text-verdict-clean',
}

const BAR: Record<string, string> = {
  EXPOSED: 'bg-verdict-exposed',
  AT_RISK: 'bg-verdict-atrisk',
  UNKNOWN: 'bg-verdict-unknown',
  CLEAN: 'bg-verdict-clean',
}

export function LockfilePanel({ stats }: { stats: Stats | null }) {
  const { record } = useTraces()
  const window_ = stats?.advisory.window ?? null
  // Default to the middle of the window: the interesting demo is an install
  // that happened while the artifact was live.
  const midpoint = window_ ? Math.floor((window_[0] + window_[1]) / 2) : null

  const [installedAt, setInstalledAt] = useState<number | null>(midpoint)
  const [result, setResult] = useState<LockfileVerdict | null>(null)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [busy, setBusy] = useState(false)
  const [label, setLabel] = useState<string>('')
  const [body, setBody] = useState<string>('')

  const check = async (text: string, name: string, at: number | null) => {
    setBusy(true); setFailure(null); setLabel(name); setBody(text)
    const response = await api.lockfile(text, at ?? undefined)
    record('/api/lockfile', response.hydra)
    if (response.ok) setResult(response.data)
    else { setResult(null); setFailure(response.failure) }
    setBusy(false)
  }

  const retime = (at: number | null) => {
    setInstalledAt(at)
    if (body) check(body, label, at)
  }

  const chips = window_
    ? [
        ['during the window', midpoint!],
        ['a day before', window_[0] - 86400],
        ['a day after', window_[1] + 86400],
      ] as const
    : []

  return (
    <div className="grid gap-8 lg:grid-cols-[420px_1fr]">
      <div>
        <DropZone onFile={(text, name) => check(text, name, installedAt)} busy={busy} />

        {window_ && (
          <div className="mt-5">
            <p className="text-xs text-chalk-dim">when did your CI run <code>npm ci</code>?</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {chips.map(([text, at]) => (
                <button
                  key={text}
                  onClick={() => retime(at)}
                  className={`rounded border px-2 py-1 text-xs ${
                    installedAt === at
                      ? 'border-ember text-ember'
                      : 'border-ink-600 text-chalk-dim hover:border-chalk-faint'
                  }`}
                >
                  {text}
                </button>
              ))}
            </div>
            {installedAt && (
              <p className="mt-2 font-mono text-[10px] text-chalk-faint">
                {isoUTC(installedAt)} · the advisory's window is {isoUTC(window_[0])} →{' '}
                {isoUTC(window_[1])}
              </p>
            )}
            <p className="mt-2 text-[11px] leading-relaxed text-chalk-faint">
              A lockfile carries no timestamp, so the install time is supplied rather than
              inferred. Without one, "was it live when you installed it" cannot be asked.
            </p>
          </div>
        )}
      </div>

      <div>
        {failure && <NotComputed failure={failure} onRetry={() => check(body, label, installedAt)} />}

        {!failure && !result && (
          <p className="text-sm text-chalk-dim">
            Every resolved artifact is checked against the advisory, and anything it names is
            checked against the window it was live in — by the graph, on the edge.
          </p>
        )}

        {result && (
          <>
            <p className={`text-[56px] font-semibold leading-none ${TONE[result.verdict]}`}>
              {result.verdict}
            </p>
            <p className="mt-2 text-chalk-dim">{result.why}</p>
            {!result.determinate && (
              <p className="mt-1 text-[11px] text-verdict-unknown">
                not determinate — some entries could not be proved safe
              </p>
            )}

            <div className="mt-4 flex h-2 overflow-hidden rounded">
              {(['EXPOSED', 'AT_RISK', 'UNKNOWN', 'CLEAN'] as const).map((v) => {
                const n = result.counts[v] ?? 0
                if (!n) return null
                return (
                  <div
                    key={v}
                    className={BAR[v]}
                    style={{ width: `${(n / result.coverage.entries) * 100}%` }}
                    title={`${v}: ${n}`}
                  />
                )
              })}
            </div>
            <p className="mt-2 font-mono text-[11px] text-chalk-dim">
              {(['EXPOSED', 'AT_RISK', 'UNKNOWN', 'CLEAN'] as const)
                .filter((v) => result.counts[v])
                .map((v) => `${v} ${compact(result.counts[v])}`)
                .join('  ·  ')}
            </p>

            <div className="mt-4 rounded border border-ink-600 bg-ink-800 p-3">
              <p className="font-mono text-[11px] text-chalk-dim">
                {compact(result.coverage.entries)} entries ·{' '}
                {compact(result.coverage.packages_in_graph)} packages in the graph ·{' '}
                {compact(result.coverage.versions_in_graph)} with version-level records ·{' '}
                the advisory names {compact(result.coverage.advisory_artifacts)} artifacts
              </p>
              <p className="mt-1.5 text-[11px] leading-relaxed text-chalk-faint">
                {result.coverage.note}
              </p>
            </div>

            <VerdictTable entries={result.entries} installedAt={installedAt} />
          </>
        )}
      </div>
    </div>
  )
}
