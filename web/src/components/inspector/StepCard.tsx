import type { Step } from '../../lib/api'
import { ago, compact, ms } from '../../lib/format'
import { CypherBlock } from './CypherBlock'
import { QUERY_NOTES } from '../../lib/queryNotes'
import { useState } from 'react'

export function StepCard({ step }: { step: Step }) {
  const [showWhy, setShowWhy] = useState(false)
  const note = QUERY_NOTES[step.name]
  const failed = Boolean(step.error)

  return (
    <div
      className={`rounded border ${
        failed ? 'border-verdict-exposed/60' : 'border-ink-600'
      } bg-ink-800 p-3`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-xs text-chalk">{step.name}</span>
        <span className="flex items-baseline gap-2 text-[11px]">
          {failed ? (
            <span className="rounded bg-verdict-exposed px-1.5 py-0.5 font-medium text-ink-900">
              FAILED
            </span>
          ) : step.cached ? (
            <span className="rounded border border-chalk-faint px-1.5 py-0.5 text-chalk-dim">
              CACHED
            </span>
          ) : (
            <span className="rounded bg-ember px-1.5 py-0.5 font-medium text-ink-900">LIVE</span>
          )}
          <span className="tnum text-chalk-dim">{ms(step.ms)}</span>
        </span>
      </div>

      <div className="mt-1 flex items-baseline gap-3 text-[11px] text-chalk-faint">
        <span className="tnum">{compact(step.rows)} rows</span>
        {step.cached && step.computed_at && <span>computed {ago(step.computed_at)}</span>}
      </div>

      {failed && <p className="mt-2 font-mono text-[11px] text-verdict-exposed">{step.error}</p>}

      <div className="mt-2">
        <CypherBlock cypher={step.cypher} />
        <p className="mt-1 text-[10px] text-chalk-faint">
          as sent — explanatory comments are stripped before dispatch, because HydraDB dispatches a
          path procedure only when the trimmed statement begins with CALL
        </p>
      </div>

      {Object.keys(step.params).length > 0 && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px]">
          {Object.entries(step.params).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-chalk-faint">{k}</dt>
              <dd className="truncate text-chalk-dim">{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}

      {note && (
        <div className="mt-2">
          <button
            onClick={() => setShowWhy((s) => !s)}
            className="text-[11px] text-chalk-faint underline-offset-2 hover:text-ember hover:underline"
          >
            {showWhy ? '▾' : '▸'} why this shape
          </button>
          {showWhy && <p className="mt-1 text-[11px] leading-relaxed text-chalk-dim">{note}</p>}
        </div>
      )}
    </div>
  )
}
