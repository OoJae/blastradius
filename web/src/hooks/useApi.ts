import { useCallback, useEffect, useRef, useState } from 'react'
import type { Failure, Hydra, Result } from '../lib/api'
import { useTraces } from './useTraces'

type State<T> = {
  data: T | null
  failure: Failure | null
  loading: boolean
  elapsed: number
  hydra: Hydra | null
}

/** Runs a request, records its trace, and exposes a live elapsed counter so a
 *  slow depth reads as progress rather than a stall. */
export function useApi<T>(endpoint: string) {
  const { record } = useTraces()
  const [state, setState] = useState<State<T>>({
    data: null, failure: null, loading: false, elapsed: 0, hydra: null,
  })
  const timer = useRef<number | null>(null)

  const stop = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current)
      timer.current = null
    }
  }, [])

  useEffect(() => stop, [stop])

  const run = useCallback(
    async (fn: () => Promise<Result<T>>, opts?: { keepData?: boolean }) => {
      const started = performance.now()
      setState((s) => ({
        data: opts?.keepData ? s.data : null,
        failure: null, loading: true, elapsed: 0, hydra: s.hydra,
      }))
      stop()
      timer.current = window.setInterval(
        () => setState((s) => ({ ...s, elapsed: performance.now() - started })), 100,
      )

      const result = await fn()
      stop()
      record(endpoint, result.hydra)

      setState({
        data: result.ok ? result.data : null,
        failure: result.ok ? null : result.failure,
        loading: false,
        elapsed: performance.now() - started,
        hydra: result.hydra,
      })
      return result
    },
    [endpoint, record, stop],
  )

  return { ...state, run }
}
