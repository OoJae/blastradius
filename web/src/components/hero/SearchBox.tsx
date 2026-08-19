import { useEffect, useRef, useState } from 'react'
import { api, type Suggestion } from '../../lib/api'

export function SearchBox({
  value, onSearch,
}: { value: string; onSearch: (pkg: string) => void }) {
  const [text, setText] = useState(value)
  const [matches, setMatches] = useState<Suggestion[]>([])
  const [openList, setOpenList] = useState(false)
  const [lookupMs, setLookupMs] = useState<number | null>(null)
  const debounce = useRef<number | null>(null)

  useEffect(() => setText(value), [value])

  const query = (q: string) => {
    setText(q)
    if (debounce.current) window.clearTimeout(debounce.current)
    if (q.length < 2) { setMatches([]); return }
    debounce.current = window.setTimeout(async () => {
      const started = performance.now()
      const result = await api.suggest(q)
      setLookupMs(performance.now() - started)
      if (result.ok) { setMatches(result.data.matches); setOpenList(true) }
    }, 60)
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-3">
        <input
          value={text}
          onChange={(e) => query(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { setOpenList(false); onSearch(text.trim()) }
            if (e.key === 'Escape') setOpenList(false)
          }}
          placeholder="a package name…"
          spellCheck={false}
          className="w-full rounded border border-ink-600 bg-ink-800 px-4 py-3 font-mono text-base text-chalk outline-none placeholder:text-chalk-faint focus:border-ember"
        />
        <button
          onClick={() => { setOpenList(false); onSearch(text.trim()) }}
          className="whitespace-nowrap rounded bg-ember px-4 py-3 text-sm font-medium text-ink-900 hover:bg-ember-glow"
        >
          blast radius
        </button>
      </div>

      {lookupMs !== null && (
        <p className="mt-1 font-mono text-[10px] text-chalk-faint">
          {lookupMs.toFixed(2)} ms · startup index, captured from HydraDB — not a live query
        </p>
      )}

      {openList && matches.length > 0 && (
        <ul className="absolute z-30 mt-1 max-h-72 w-full overflow-y-auto rounded border border-ink-600 bg-ink-800 py-1">
          {matches.map((m) => (
            <li key={m.name}>
              <button
                onClick={() => { setText(m.name); setOpenList(false); onSearch(m.name) }}
                className="flex w-full items-baseline justify-between px-4 py-1.5 text-left font-mono text-sm text-chalk hover:bg-ink-700"
              >
                <span>{m.name}</span>
                {m.is_popular && <span className="text-[10px] text-ember">popular</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
