import { useRef, useState } from 'react'

const SAMPLES = [
  { file: 'lock-inside-window.json', label: 'installed during the window' },
  { file: 'lock-before-window.json', label: 'installed before' },
  { file: 'lock-after-fix.json', label: 'installed after the fix' },
]

export function DropZone({
  onFile, busy,
}: { onFile: (body: string, label: string) => void; busy: boolean }) {
  const [over, setOver] = useState(false)
  const picker = useRef<HTMLInputElement>(null)

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
