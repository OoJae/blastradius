/** What the page knows before it asks, and what it must ask for.
 *
 * The split matters. The INCIDENT is history: the window, the artifact count,
 * the two bursts and the advisory ids happened once and will never change, so
 * they are constants here, each traceable to data/incident/*.json.
 *
 * The GRAPH numbers are not history -- they depend on which slice this
 * deployment loaded, and the hosted slice is a third the size of the local one.
 * Hardcoding those would make the page lie on one deployment or the other, so
 * every one of them is fetched live and rendered only once it arrives.
 */

import { call } from '../../lib/api'
import type { Incident, Stats, Radius, Forecast } from '../../lib/api'

// data/incident/live_window.json, derived from npm registry publish times for
// all 84 malicious versions -- not from the advisory's prose, which says 19:26:00.
export const WINDOW = {
  from: 1778527239,
  fromISO: '2026-05-11T19:20:39Z',
  until: 1778527579,
  untilISO: '2026-05-11T19:26:19Z',
  spanSeconds: 340,
} as const

// data/incident/advisory.json
export const ADVISORY = {
  cve: 'CVE-2026-45321',
  ghsa: 'GHSA-g7cv-rxg3-hmpx',
  artifacts: 84,
  packages: 42,
  summary:
    'Malware in @tanstack/* packages exfiltrates cloud credentials, GitHub tokens, and SSH keys',
} as const

// /api/incident `waves`, which groups the 84 publishes by their real timestamps.
export const BURSTS = [
  { index: 1, from: 1778527239, to: 1778527243, seconds: 4, versions: 42 },
  { index: 2, from: 1778527574, to: 1778527578, seconds: 4, versions: 42 },
] as const

export const GAP_SECONDS = BURSTS[1].from - BURSTS[0].from // 335
export const FIRST_ARTIFACT = '@tanstack/solid-router-devtools@1.166.16'

export type LiveFacts = {
  ready: boolean
  stats: Stats | null
  incident: Incident | null
  rings: { depth: number; total: number }[]
  forecast: Forecast | null
}

export const EMPTY: LiveFacts = { ready: false, stats: null, incident: null, rings: [], forecast: null }

/** Ask the service for everything the page needs, tolerating a cold start.
 *
 *  The instrument's own boot can take a while on a fresh deployment, so this
 *  never blocks the page: the story renders immediately and the numbers arrive
 *  when they arrive. Nothing is invented in the meantime. */
export async function loadFacts(): Promise<LiveFacts> {
  const health = await call<{ ready: boolean }>('/api/health')
  if (!health.ok || !(health.data as any).ready) return EMPTY

  const [stats, incident, forecast] = await Promise.all([
    call<Stats>('/api/stats'),
    call<Incident>('/api/incident'),
    call<Forecast>('/api/forecast'),
  ])

  const rings: { depth: number; total: number }[] = []
  for (const depth of [1, 2, 3]) {
    const r = await call<Radius>(
      `/api/blast-radius?pkg=${encodeURIComponent('@tanstack/react-router')}&depth=${depth}&limit=1`,
    )
    if (r.ok) rings.push({ depth, total: r.data.total })
  }

  return {
    ready: true,
    stats: stats.ok ? stats.data : null,
    incident: incident.ok ? incident.data : null,
    rings,
    forecast: forecast.ok ? forecast.data : null,
  }
}

/** The clock the whole page runs on: a 0..1 scroll position becomes a real
 *  UTC second inside the window, so the readout is never decorative. */
export function scrollToIncidentTime(t: number): number {
  return WINDOW.from + Math.round(t * WINDOW.spanSeconds)
}

export function utcClock(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(11, 19)
}
