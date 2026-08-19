import type { Failure } from '../../lib/api'
import { useTraces } from '../../hooks/useTraces'

const TITLES: Record<string, string> = {
  admission_control: 'HydraDB refused this query',
  timeout: 'HydraDB did not finish in time',
  deadline: 'The request passed our own deadline',
  unavailable: 'The database is unreachable',
  rejected: 'This statement was rejected',
  http: 'The request failed',
}

const NOTES: Record<string, string> = {
  admission_control:
    'This is a real, non-configurable limit rather than a crash — retrying will hit it again.',
  rejected:
    'That is our bug rather than the database’s; `just parse-check` exists to make this unreachable.',
  deadline: 'The query may still be running on the server.',
}

export function NotComputed({ failure, onRetry }: { failure: Failure; onRetry?: () => void }) {
  const { setOpen } = useTraces()
  return (
    <div className="rounded border border-verdict-exposed/50 bg-ink-800 p-4">
      <h3 className="text-sm font-medium text-verdict-exposed">
        {TITLES[failure.kind] ?? 'Not computed'}
      </h3>
      <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] text-chalk-dim">
        {failure.message}
      </pre>
      {NOTES[failure.kind] && (
        <p className="mt-2 text-xs text-chalk-faint">{NOTES[failure.kind]}</p>
      )}
      <div className="mt-3 flex gap-3 text-xs">
        {failure.retryable && onRetry && (
          <button onClick={onRetry} className="rounded bg-ember px-2 py-1 text-ink-900">
            retry
          </button>
        )}
        <button onClick={() => setOpen(true)} className="text-chalk-dim hover:text-ember">
          show the statement that failed →
        </button>
      </div>
    </div>
  )
}
