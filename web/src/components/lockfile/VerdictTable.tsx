import { useState } from 'react'
import type { EntryVerdict, Remediation } from '../../lib/api'
import { compact, isoUTC } from '../../lib/format'
import { WindowBar } from './WindowBar'

const TONE: Record<string, string> = {
  EXPOSED: 'text-verdict-exposed',
  UNKNOWN: 'text-verdict-unknown',
  AT_RISK: 'text-verdict-atrisk',
  CLEAN: 'text-verdict-clean',
}

const SIGNAL_ORDER = [
  ['advisory_hit', 'named by an advisory'],
  ['package_in_graph', 'package is in the graph'],
  ['version_in_graph', 'this version is in the graph'],
  ['closure_complete', 'the blast radius computed fully'],
] as const

function Signals({ signals }: { signals: Record<string, boolean> }) {
  return (
    <span className="ml-2 inline-flex gap-1 align-middle">
      {SIGNAL_ORDER.map(([key, title]) => (
        <span
          key={key}
          title={`${title}: ${signals[key] ? 'yes' : 'no'}`}
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            signals[key] ? 'bg-chalk-dim' : 'border border-chalk-faint'
          }`}
        />
      ))}
    </span>
  )
}

function WhatToDoNow({ remediation }: { remediation: Remediation }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    if (!remediation.command) return
    await navigator.clipboard.writeText(remediation.command)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="mt-2 rounded border border-verdict-exposed/30 bg-ink-900 p-2.5">
      <p className="text-[10px] font-medium uppercase tracking-wide text-chalk-faint">
        what to do now
      </p>
      {remediation.command ? (
        <>
          <button
            onClick={copy}
            title="copy"
            className="mt-1.5 block w-full truncate rounded bg-ink-800 px-2 py-1.5 text-left font-mono text-[11px] text-chalk hover:text-ember"
          >
            {copied ? 'copied ✓' : `$ ${remediation.command}`}
          </button>
          <p className="mt-1 text-[10px] text-chalk-faint">
            {remediation.first_clean_version} is {remediation.source}
            {remediation.published_at ? ` — published ${isoUTC(remediation.published_at)}` : ''}
          </p>
        </>
      ) : (
        <p className="mt-1.5 text-[11px] text-chalk-dim">{remediation.note}</p>
      )}
      <p className="mt-1.5 text-[11px] text-verdict-exposed">
        then {remediation.rotate_credentials}
      </p>
    </div>
  )
}

function Row({ entry, installedAt }: { entry: EntryVerdict; installedAt: number | null }) {
  return (
    <li className="border-t border-ink-700 px-3 py-2.5 first:border-t-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-xs text-chalk">
          {entry.name}
          <span className="text-chalk-faint">@{entry.version}</span>
          {entry.occurrences > 1 && (
            <span className="ml-2 text-[10px] text-chalk-faint">×{entry.occurrences}</span>
          )}
          <Signals signals={entry.signals} />
        </span>
        <span className={`text-[11px] font-medium ${TONE[entry.verdict]}`}>{entry.verdict}</span>
      </div>

      <p className="mt-0.5 text-[11px] text-chalk-dim">{entry.reason}</p>

      {entry.advisory && (
        <div className="mt-2 rounded bg-ink-900 p-2.5">
          <p className="font-mono text-[11px] text-chalk-dim">
            {entry.advisory.cve || entry.advisory.advisory}
            {entry.advisory.severity ? ` · CVSS ${entry.advisory.severity}` : ''}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-chalk-faint">
            live {isoUTC(entry.advisory.live_from)} → {isoUTC(entry.advisory.live_until)}
          </p>
          <WindowBar
            liveFrom={entry.advisory.live_from}
            liveUntil={entry.advisory.live_until}
            installedAt={installedAt}
            placement={entry.window}
            confirmedByGraph={entry.in_window_per_graph}
          />
        </div>
      )}

      {entry.remediation && <WhatToDoNow remediation={entry.remediation} />}
    </li>
  )
}

function Group({
  verdict, entries, installedAt, defaultOpen, caption,
}: {
  verdict: string
  entries: EntryVerdict[]
  installedAt: number | null
  defaultOpen: boolean
  caption?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  if (entries.length === 0) return null

  return (
    <section className="mt-4 rounded border border-ink-600 bg-ink-800">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-baseline justify-between px-3 py-2 text-left"
      >
        <span className={`text-xs font-medium ${TONE[verdict]}`}>
          {verdict} · {compact(entries.length)}
        </span>
        <span className="text-[11px] text-chalk-faint">{open ? '▾ hide' : '▸ show'}</span>
      </button>

      {caption && (
        <p className="border-t border-ink-700 px-3 py-2 text-[11px] leading-relaxed text-chalk-dim">
          {caption}
        </p>
      )}

      {open && (
        <ul>
          {entries.slice(0, 200).map((e) => (
            <Row key={`${e.name}@${e.version}`} entry={e} installedAt={installedAt} />
          ))}
          {entries.length > 200 && (
            <li className="border-t border-ink-700 px-3 py-2 text-[11px] text-chalk-faint">
              showing the first 200 of {compact(entries.length)}
            </li>
          )}
        </ul>
      )}
    </section>
  )
}

export function VerdictTable({
  entries, installedAt,
}: { entries: EntryVerdict[]; installedAt: number | null }) {
  // The server already sorts by ladder rank then name, so group sequentially
  // rather than re-sorting -- re-sorting here would silently diverge from the
  // ordering the verdict rules define.
  const by = (v: string) => entries.filter((e) => e.verdict === v)

  return (
    <div>
      <Group verdict="EXPOSED" entries={by('EXPOSED')} installedAt={installedAt} defaultOpen />
      <Group verdict="AT_RISK" entries={by('AT_RISK')} installedAt={installedAt} defaultOpen />
      <Group
        verdict="UNKNOWN"
        entries={by('UNKNOWN')}
        installedAt={installedAt}
        defaultOpen={false}
        caption="This is a scope statement rather than a failure. Version-level records exist only for the packages this advisory names, so for everything else we report absence of evidence rather than evidence of absence."
      />
      <Group verdict="CLEAN" entries={by('CLEAN')} installedAt={installedAt} defaultOpen={false} />
    </div>
  )
}
