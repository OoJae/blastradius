export const compact = (n: number) => n.toLocaleString('en-US')

export const ms = (v: number) =>
  v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)} s` : `${Math.round(v)} ms`

export const isoUTC = (epoch: number) =>
  new Date(epoch * 1000).toISOString().replace('.000', '').replace('T', ' ').replace('Z', 'Z')

export function ago(epochSeconds: number): string {
  const delta = Date.now() / 1000 - epochSeconds
  if (delta < 60) return `${Math.round(delta)}s ago`
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`
  return `${Math.round(delta / 3600)}h ago`
}

export const duration = (seconds: number) => {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  return `${m}m ${seconds - m * 60}s`
}
