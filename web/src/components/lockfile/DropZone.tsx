import { useRef, useState } from 'react'

/** owner/repo from anything a person might paste: the bare pair, a github.com
 *  URL, a clone URL. Anything else is refused rather than guessed at. */
function parseRepo(input: string): { owner: string; repo: string } | null {
  const cleaned = input.trim().replace(/^https?:\/\/(www\.)?github\.com\//, '').replace(/\.git$/, '')
  const parts = cleaned.split('/').filter(Boolean)
  if (parts.length < 2 || parts[0].includes('.com')) return null
  return { owner: parts[0], repo: parts[1] }
}

const SAMPLES = [
  { file: 'lock-real-exposed.json', label: 'resolved 1.169.5 during the window' },
  { file: 'lock-real-at-risk.json', label: 'on the fixed release' },
  { file: 'lock-real-clean.json', label: 'an unrelated stack' },
]

export function DropZone({
  onFile, busy,
}: { onFile: (body: string, label: string) => void; busy: boolean }) {
  const [over, setOver] = useState(false)
  const [repo, setRepo] = useState('')
  const [fetching, setFetching] = useState(false)
  const [repoError, setRepoError] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)

  // The lockfile comes straight from GitHub to the browser -- raw.githubusercontent
  // sends `access-control-allow-origin: *` -- then to /api/lockfile like any drop.
  const checkRepo = async () => {
    const parsed = parseRepo(repo)
    if (!parsed) { setRepoError('that does not look like owner/repo or a GitHub URL'); return }
    setRepoError(null); setFetching(true)
    try {
      const url = `https://raw.githubusercontent.com/${parsed.owner}/${parsed.repo}/HEAD/package-lock.json`
      const response = await fetch(url)
      if (!response.ok) {
        setRepoError(
          `no package-lock.json at the root of ${parsed.owner}/${parsed.repo} — ` +
          'this reads npm lockfiles; pnpm and yarn lockfiles are different formats',
        )
        return
      }
      onFile(await response.text(), `${parsed.owner}/${parsed.repo}`)
    } catch {
      setRepoError('GitHub did not answer — check the name, or your network')
    } finally {
      setFetching(false)
    }
  }

  const read = async (file: File) => onFile(await file.text(), file.name)

  const sample = async (file: string, label: string) => {
    const response = await fetch(`/samples/${file}`)
    onFile(await response.text(), label)
  }

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault(); setOver(false)
          const file = e.dataTransfer.files[0]
          if (file) read(file)
        }}
        onClick={() => picker.current?.click()}
        className={`cursor-pointer rounded border-2 border-dashed px-6 py-10 text-center transition-colors ${
          over ? 'border-ember bg-ink-700' : 'border-ink-600 bg-ink-800 hover:border-chalk-faint'
        }`}
      >
        <p className="text-sm text-chalk">
          {busy ? 'checking every resolved artifact…' : 'drop a package-lock.json'}
        </p>
        <p className="mt-1 text-xs text-chalk-faint">or click to choose a file</p>
        <input
          ref={picker} type="file" accept=".json,application/json" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) read(f) }}
        />
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); checkRepo() }}
        className="mt-3 flex items-center gap-2"
      >
        <span className="text-xs text-chalk-faint">or a GitHub repo:</span>
        <input
          value={repo}
          onChange={(e) => { setRepo(e.target.value); setRepoError(null) }}
          placeholder="owner/repo"
          spellCheck={false}
          className="w-48 rounded border border-ink-600 bg-ink-900 px-2 py-1 font-mono text-xs text-chalk placeholder:text-chalk-faint focus:border-ember focus:outline-none"
        />
        <button
          type="submit"
          disabled={fetching || !repo.trim()}
          className="rounded border border-ink-600 px-2 py-1 text-xs text-chalk-dim enabled:hover:border-ember enabled:hover:text-ember disabled:opacity-40"
        >
          {fetching ? 'fetching…' : 'check'}
        </button>
      </form>
      {repoError && <p className="mt-1.5 text-[11px] text-verdict-exposed">{repoError}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-chalk-faint">or try a real one:</span>
        {SAMPLES.map((s) => (
          <button
            key={s.file}
            onClick={() => sample(s.file, s.label)}
            className="rounded border border-ink-600 px-2 py-1 text-chalk-dim hover:border-ember hover:text-ember"
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  )
}
