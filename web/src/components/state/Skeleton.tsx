export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-ink-700 ${className}`} />
}
