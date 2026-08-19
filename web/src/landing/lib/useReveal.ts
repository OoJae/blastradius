import { useEffect } from 'react'

/** One reveal, used everywhere, so the page has a single motion vocabulary.
 *
 *  Lines rise out of a mask once, near the viewport, and never again. Only
 *  transform and opacity are touched. Under prefers-reduced-motion the whole
 *  thing collapses to "already visible" rather than a slower animation. */
export function useReveal(root: React.RefObject<HTMLElement | null>, deps: unknown[] = []) {
  useEffect(() => {
    const el = root.current
    if (!el) return
    const targets = Array.from(el.querySelectorAll<HTMLElement>('[data-reveal]'))
    if (!targets.length) return

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      targets.forEach((t) => t.classList.add('is-revealed'))
      return
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          const t = entry.target as HTMLElement
          const delay = Number(t.dataset.revealDelay ?? 0)
          t.style.transitionDelay = `${delay * 0.07}s`
          t.classList.add('is-revealed')
          io.unobserve(t)
        })
      },
      { rootMargin: '0px 0px -15% 0px', threshold: 0.01 },
    )
    targets.forEach((t) => io.observe(t))
    return () => io.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
