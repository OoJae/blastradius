import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { Hydra } from '../lib/api'

// Every request's query trace, kept so the inspector can show what the database
// was asked and what it answered. This is the exhibit for the whole project:
// the claim is that a graph produced these numbers, and the claim is only worth
// something if the statements travel with them.

export type TraceEntry = { id: number; endpoint: string; at: number; hydra: Hydra }

type Store = {
  entries: TraceEntry[]
  latest: TraceEntry | null
  record: (endpoint: string, hydra: Hydra | null) => void
  clear: () => void
  open: boolean
  setOpen: (open: boolean) => void
}

const TraceContext = createContext<Store | null>(null)
let nextId = 1

export function TraceProvider({ children }: { children: React.ReactNode }) {
  const [entries, setEntries] = useState<TraceEntry[]>([])
  const [open, setOpen] = useState(false)

  const record = useCallback((endpoint: string, hydra: Hydra | null) => {
    if (!hydra) return
    setEntries((prev) => [{ id: nextId++, endpoint, at: Date.now(), hydra }, ...prev].slice(0, 25))
  }, [])

  const value = useMemo<Store>(
    () => ({ entries, latest: entries[0] ?? null, record, clear: () => setEntries([]), open, setOpen }),
    [entries, record, open],
  )
  return <TraceContext.Provider value={value}>{children}</TraceContext.Provider>
}

export function useTraces(): Store {
  const store = useContext(TraceContext)
  if (!store) throw new Error('useTraces must be used inside a TraceProvider')
  return store
}
