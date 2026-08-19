import { useEffect, useRef, useState } from 'react'
import Lenis from 'lenis'
import { createField, webglAvailable } from './three/field'
import type { Field } from './three/field'
import { Hero } from './sections/Hero'
import { Detonation } from './sections/Detonation'
import { Instrument, Proof } from './sections/Instrument'
import { Footer } from './sections/Footer'
import { Rail } from './sections/Rail'
import { useReveal } from './lib/useReveal'
import { ADVISORY, BURSTS, WINDOW, loadFacts, EMPTY, utcClock } from './lib/incident'
import type { LiveFacts } from './lib/incident'

// The detonation is pinned for this many viewport heights. The quiet gets the
// largest share on purpose -- it is 336 of the window's 340 seconds -- but it
// is compressed hard, because proportional would be forty screens of nothing.
const ACTS = [
  { phase: 'armed', vh: 1.0 },
  { phase: 'burst-1', vh: 1.4 },
  { phase: 'quiet', vh: 1.6 },
  { phase: 'burst-2', vh: 1.2 },
] as const

type Phase = (typeof ACTS)[number]['phase']
const TOTAL_VH = ACTS.reduce((n, a) => n + a.vh, 0)

export function Landing() {
  const page = useRef<HTMLDivElement>(null)
  const scene = useRef<HTMLDivElement>(null)
  const field = useRef<Field | null>(null)

  const [facts, setFacts] = useState<LiveFacts>(EMPTY)
  const [phase, setPhase] = useState<Phase>('armed')
  const [clock, setClock] = useState<number>(WINDOW.from)
  const [progress, setProgress] = useState(0)

  useReveal(page, [facts.ready])

  // A cold deployment spends minutes building its graph, so asking once and
  // giving up leaves a judge staring at em-dashes with no explanation. Poll
  // until the numbers exist, and say so in the meantime.
  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const ask = async () => {
      const next = await loadFacts()
      if (!alive) return
      setFacts(next)
      if (!next.ready) timer = window.setTimeout(ask, 4000)
    }
    ask()
    return () => { alive = false; if (timer) window.clearTimeout(timer) }
  }, [])

  // The scene is built once the real counts arrive, so the number of points on
  // screen is the number of packages the traversal returned.
  useEffect(() => {
    if (!scene.current || !facts.rings.length || field.current) return
    if (!webglAvailable()) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let f: Field
    try {
      f = createField(facts.rings, { reducedMotion: reduced })
      f.mount(scene.current)
      f.start()
    } catch (error) {
      // A renderer failure must never take the page with it. Without this the
      // throw escapes the effect, React unmounts, and the submitted URL is a
      // blank screen for anyone whose browser cannot do WebGL.
      console.warn('blast field unavailable, serving the page without it', error)
      return
    }
    field.current = f
    return () => { f.dispose(); field.current = null }
  }, [facts.rings])

  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const lenis = reduced ? null : new Lenis({ lerp: 0.1 })
    let raf = 0
    if (lenis) {
      const tick = (t: number) => { lenis.raf(t); raf = requestAnimationFrame(tick) }
      raf = requestAnimationFrame(tick)
    }

    const onScroll = () => {
      const el = document.getElementById('detonation')
      if (!el) return
      const rect = el.getBoundingClientRect()
      const span = el.offsetHeight - window.innerHeight
      const t = span <= 0 ? 0 : Math.min(Math.max(-rect.top / span, 0), 1)
      setProgress(t)

      // Which act, and how far into it.
      let acc = 0
      let current: Phase = 'armed'
      let local = 0
      for (const act of ACTS) {
        const share = act.vh / TOTAL_VH
        if (t <= acc + share || act === ACTS[ACTS.length - 1]) {
          current = act.phase
          local = share === 0 ? 0 : (t - acc) / share
          break
        }
        acc += share
      }
      setPhase(current)

      // Incident time. Each act maps to the seconds it actually occupied, so
      // the readout is the real clock rather than a linear ramp.
      const bounds: Record<Phase, [number, number]> = {
        armed: [0, 0],
        'burst-1': [0, BURSTS[0].seconds],
        quiet: [BURSTS[0].seconds, BURSTS[1].from - WINDOW.from],
        'burst-2': [BURSTS[1].from - WINDOW.from, WINDOW.spanSeconds],
      }
      const [a, b] = bounds[current]
      setClock(WINDOW.from + Math.round(a + (b - a) * Math.min(Math.max(local, 0), 1)))

      // The wave front. It travels through the three shells during burst one
      // and then stays put -- which is the whole point.
      const ignite = current === 'armed' ? 0 : current === 'burst-1' ? local * 3.2 : 3.2
      field.current?.setIgnite(ignite)
      field.current?.setSpin(t * 0.9)
    }

    onScroll()
    const target = lenis ?? window
    if (lenis) lenis.on('scroll', onScroll)
    else window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)

    return () => {
      cancelAnimationFrame(raf)
      lenis?.destroy()
      if (!lenis) window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      void target
    }
  }, [facts.rings.length])

  // The exposed count is the graph's, read off the measured per-second curve
  // during the burst and held flat afterwards -- never interpolated.
  const curve = facts.incident?.exposure_curve ?? null
  const settled = facts.incident?.saturation?.exposed ?? curve?.at(-1)?.exposed ?? null
  const exposed =
    phase === 'armed' ? 0
    : phase === 'burst-1' && curve
      ? curve[Math.min(Math.floor((clock - WINDOW.from) / 1), curve.length - 1)]?.exposed ?? settled
      : settled
  const compromised =
    phase === 'armed' ? 0
    : phase === 'burst-1' && curve
      ? curve[Math.min(Math.floor((clock - WINDOW.from) / 1), curve.length - 1)]?.packages_compromised ?? ADVISORY.packages
      : ADVISORY.packages

  return (
    <div ref={page} className="grain relative">
      <a
        href="#instrument"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[70] focus:rounded focus:bg-ember focus:px-4 focus:py-2 focus:text-[color:var(--void)]"
      >
        Skip the incident replay
      </a>

      <Rail clock={utcClock(clock)} progress={progress} phase={phase} />

      <header className="fixed inset-x-0 top-0 z-50 flex items-center justify-between px-[var(--gutter)] py-5 mix-blend-difference">
        <a href="/" className="flex items-center gap-2.5" aria-label="BlastRadius home">
          <img src="/logo.svg" alt="" width="22" height="22" />
          <span className="font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk">
            BlastRadius
          </span>
        </a>
        <a
          href="/app/"
          className="font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk underline decoration-transparent underline-offset-4 transition-colors duration-[var(--dur-micro)] hover:decoration-current"
        >
          Open the instrument →
        </a>
      </header>

      <div ref={scene} aria-hidden className="pointer-events-none fixed inset-0 z-0" />
      {/* The field is bright enough to fight the type it sits behind, so the
          left column keeps its own ground. The gradient is the composition:
          the detonation reads as happening beside the sentence, not under it. */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-[1] hidden lg:block"
        style={{
          background:
            'linear-gradient(100deg, var(--void) 0%, var(--void) 34%, rgba(8,9,10,0.82) 46%, rgba(8,9,10,0.35) 58%, rgba(8,9,10,0) 70%)',
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-[1] lg:hidden"
        style={{ background: 'linear-gradient(180deg, rgba(8,9,10,0.86) 0%, rgba(8,9,10,0.5) 45%, rgba(8,9,10,0.86) 100%)' }}
      />

      <main>
        <Hero />

        <section
          id="detonation"
          style={{ height: `${TOTAL_VH * 100}svh` }}
          aria-label={`The ${WINDOW.spanSeconds}-second live window, replayed`}
        >
          {/* z-10 on the sticky wrapper itself, not just its children: a
              sticky element creates a stacking context, so a z-index set
              inside it cannot lift the type above the scrim. */}
          <div className="sticky top-0 z-10">
            <Detonation facts={facts} phase={phase} exposed={exposed} compromised={compromised} />
          </div>
        </section>

        <div id="instrument">
          <Instrument facts={facts} />
          <Proof facts={facts} />
        </div>
      </main>

      <Footer />
    </div>
  )
}
