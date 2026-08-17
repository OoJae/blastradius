// Every endpoint returns the same envelope, including when it fails, so there
// is one code path here rather than a success path and a scattering of error
// handling. A 503 is a real answer -- "the database refused, here is the
// statement and how long it ran" -- not an exception.

export type Step = {
  name: string
  cypher: string
  params: Record<string, unknown>
  ms: number
  rows: number
  cached: boolean
  computed_at?: number
  error?: string
}

export type Hydra = {
  cypher: string
  ms: number
  queries: number
  live: number
  cached: number
  steps: Step[]
}

export type Failure = {
  kind: 'admission_control' | 'timeout' | 'unavailable' | 'rejected' | 'deadline' | 'http'
  message: string
  hint: string | null
  retryable: boolean
}

export type Ok<T> = { ok: true; data: T; hydra: Hydra | null; partial?: unknown }
export type Err = { ok: false; failure: Failure; hydra: Hydra | null; partial?: unknown; status: number }
export type Result<T> = Ok<T> | Err

const EMPTY_HYDRA: Hydra = { cypher: '', ms: 0, queries: 0, live: 0, cached: 0, steps: [] }

export async function call<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (error) {
    return {
      ok: false,
      status: 0,
      hydra: null,
      failure: {
        kind: 'unavailable',
        message: error instanceof Error ? error.message : 'the request could not be sent',
        hint: null,
        retryable: true,
      },
    }
  }

  let body: any
  try {
    body = await response.json()
  } catch {
    return {
      ok: false,
      status: response.status,
      hydra: null,
      failure: { kind: 'http', message: `${response.status} with no JSON body`, hint: null, retryable: false },
    }
  }

  const hydra: Hydra | null = body.hydra ? { ...EMPTY_HYDRA, ...body.hydra } : null

  if (body.status === 'ok') return { ok: true, data: body.result as T, hydra }

  // `not_computed` carries the classified reason; the rest are 404/422 shapes
  // that still deserve the same envelope so callers never special-case them.
  if (body.not_computed) {
    return { ok: false, status: response.status, hydra, partial: body.partial, failure: body.not_computed }
  }

  return {
    ok: false,
    status: response.status,
    hydra,
    failure: {
      kind: 'http',
      message: body.message ?? body.status ?? `request failed (${response.status})`,
      hint: null,
      retryable: false,
    },
  }
}

export const api = {
  health: () => call<{ ready: boolean }>('/api/health'),
  stats: () => call<Stats>('/api/stats'),
  suggest: (q: string, live = false) =>
    call<{ matches: Suggestion[]; source: string }>(
      `/api/suggest?q=${encodeURIComponent(q)}&limit=10${live ? '&live=true' : ''}`,
    ),
  radius: (pkg: string, depth: number, fresh = false) =>
    call<Radius>(
      `/api/blast-radius?pkg=${encodeURIComponent(pkg)}&depth=${depth}&limit=1500${fresh ? '&fresh=true' : ''}`,
    ),
  maintainers: (pkg: string) => call<MaintainerOverlap>(`/api/maintainer-overlap/${pkg}`),
  typosquats: (pkg: string) => call<Typosquats>(`/api/typosquats/${pkg}`),
  incident: () => call<Incident>('/api/incident'),
  lockfile: (body: string, installedAt?: number) =>
    call<LockfileVerdict>(
      `/api/lockfile${installedAt ? `?installed_at=${installedAt}` : ''}`,
      { method: 'POST', body, headers: { 'Content-Type': 'application/json' } },
    ),
}

// --- response shapes, mirroring api/main.py -------------------------------

export type Suggestion = { name: string; is_popular: boolean | null }

export type CountCell = { value: number | null; source: string; error?: string }

export type Stats = {
  graph: Record<string, CountCell>
  advisory: {
    key: string | null
    is_the_demonstrated_incident: boolean
    artifacts: number
    packages: number
    window: [number, number] | null
    note: string | null
  }
  suggest: { names: number; memory_bytes: number; source: string; why: string }
  warming: boolean
  loading: string | null
  built_at: number
  boot_queries: Step[]
}

export type Radius = {
  package: string
  depth: number
  total: number
  returned: number
  dependents: { name: string; is_popular: boolean }[]
}

export type MaintainerOverlap = {
  package: string
  maintainers: string[]
  total: number
  packages: { name: string; shared_with: string[] }[]
  note: string
}

export type Typosquats = {
  package: string
  total: number
  neighbours: { name: string; distance: number; is_popular: boolean }[]
  note: string
}

export type Wave = {
  wave: number
  from: number
  from_iso: string
  to: number
  seconds: number
  versions: number
  packages: number
}

export type Incident = {
  advisory: string
  is_the_demonstrated_incident: boolean
  packages: string[]
  artifacts: number
  window: [number, number] | null
  publishes: { name: string; version: string; published_at: number }[]
  exposed: { depth?: number; total?: number; complete?: boolean; warming?: boolean }
  waves: Wave[]
  exposure_curve:
    | { t: number; offset: number; packages_compromised: number; exposed: number }[]
    | null
  saturation: { exposed?: number; offset_seconds?: number; note?: string; warming?: boolean } | null
}

export type EntryVerdict = {
  name: string
  version: string
  verdict: 'EXPOSED' | 'UNKNOWN' | 'AT_RISK' | 'CLEAN'
  reason: string
  occurrences: number
  window: 'before' | 'inside' | 'after' | null
  in_window_per_graph: boolean | null
  advisory: Record<string, any> | null
  signals: Record<string, boolean>
}

export type LockfileVerdict = {
  service: string | null
  lockfile_version: number
  sha256: string
  verdict: string
  determinate: boolean
  why: string
  counts: Record<string, number>
  coverage: Record<string, any>
  entries: EntryVerdict[]
}
