import { compact } from '../../lib/format'
import { ADVISORY, BURSTS, GAP_SECONDS, WINDOW, utcClock } from '../lib/incident'
import type { LiveFacts } from '../lib/incident'

/** The six minutes, as scroll.
 *
 *  Three acts, and the middle one is the point. The first burst saturates the
 *  blast radius in three seconds; then nothing happens for 336 seconds; then a
 *  second burst adds 42 more artifacts and not one newly exposed package.
 *  The dead zone is deliberately dull, because the finding is that it was.
 */
export function Detonation({
  facts, phase, exposed, compromised,
}: {
  facts: LiveFacts
  phase: 'armed' | 'burst-1' | 'quiet' | 'burst-2'
  exposed: number | null
  compromised: number
}) {
  const curve = facts.incident?.exposure_curve ?? null
  const saturation = facts.incident?.saturation ?? null

  return (
    <div className="relative z-10 flex h-[100svh] flex-col justify-center px-[var(--gutter)] pl-[calc(var(--rail)+var(--gutter))]">
      <div className="pointer-events-none max-w-[var(--measure)]">
        {phase === 'armed' && (
          <Act
            eyebrow="the field, at rest"
            title={<>Every package that <span className="italic">depends</span> on one of the 42.</>}
            body="Nothing has happened yet. The seed is one package; the shells around it are everything that would pull it in, one hop, two hops, three."
          />
        )}

        {phase === 'burst-1' && (
          <Act
            eyebrow={`burst one · ${BURSTS[0].seconds} seconds · ${BURSTS[0].versions} versions`}
            title={<>The blast radius is complete in <span className="italic">three seconds.</span></>}
            body="Forty-two packages, each with one malicious version, published by an automated run. The wave reaches its full extent almost immediately."
          />
        )}

        {phase === 'quiet' && (
          <Act
            eyebrow="the quiet · 336 seconds"
            title={<>Then nothing happens<br />for five and a half minutes.</>}
            body="This is not a pause in the page. It is the shape of the attack. The advisory describes a six-minute window, which sounds like something spreading — but the registry timestamps say the radius stopped growing at second three and stayed exactly where it was."
          />
        )}

        {phase === 'burst-2' && (
          <Act
            eyebrow={`burst two · ${BURSTS[1].seconds} seconds · ${BURSTS[1].versions} versions`}
            title={<>The second burst adds <span className="italic">nothing.</span></>}
            body={`Another 42 malicious versions land at ${utcClock(BURSTS[1].from)} UTC. Every one of them lands on a package that was already exposed, so the count does not move.`}
          />
        )}
      </div>

      {/* The readout. Fixed position, so the numbers are the one thing that
          moves while the type changes underneath them. */}
      <div className="pointer-events-none mt-12 flex flex-wrap items-end gap-x-14 gap-y-6">
        <Readout
          label="packages exposed"
          value={exposed === null ? null : compact(exposed)}
          hot={phase === 'burst-1'}
          note={
            saturation?.offset_seconds !== undefined
              ? `saturated ${saturation.offset_seconds}s in`
              : 'from HydraDB'
          }
        />
        <Readout
          label="packages compromised"
          value={String(compromised)}
          hot={phase === 'burst-1' || phase === 'burst-2'}
          note={`of ${ADVISORY.packages}`}
        />
        <Readout
          label="malicious versions live"
          value={String(phase === 'armed' ? 0 : phase === 'burst-2' ? ADVISORY.artifacts : ADVISORY.artifacts / 2)}
          hot={false}
          note={`${ADVISORY.artifacts} in total`}
        />
      </div>

      {curve && phase !== 'armed' && (
        <p className="pointer-events-none mt-8 max-w-[var(--measure)] font-mono text-[length:var(--step-label)] leading-relaxed text-chalk-faint">
          {curve.map((p) => `+${p.offset}s ${compact(p.exposed)}`).join('   ')}
          <span className="block pt-1 text-chalk-faint/70">
            exposure per second, measured — the last two values are identical
          </span>
        </p>
      )}
    </div>
  )
}

function Act({ eyebrow, title, body }: { eyebrow: string; title: React.ReactNode; body: string }) {
  return (
    <div key={eyebrow} className="animate-[fade_0.6s_var(--ease-out)]">
      <p className="font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-ember">
        {eyebrow}
      </p>
      <h2
        className="mt-4 font-display text-[length:var(--step-section)] font-normal text-chalk"
        style={{ lineHeight: 1.02, letterSpacing: 'var(--track-display)' }}
      >
        {title}
      </h2>
      <p className="mt-5 text-[length:var(--step-body)] text-chalk-dim" style={{ lineHeight: 'var(--lh-body)' }}>
        {body}
      </p>
    </div>
  )
}

function Readout({ label, value, hot, note }: { label: string; value: string | null; hot: boolean; note: string }) {
  return (
    <div>
      <p
        className={`tnum font-mono text-[clamp(2rem,5vw,4rem)] leading-none transition-colors duration-500 ${
          hot ? 'text-ember' : 'text-chalk'
        }`}
      >
        {value ?? '—'}
      </p>
      <p className="mt-2 font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint">
        {label}
      </p>
      <p className="font-mono text-[length:var(--step-label)] text-chalk-faint/70">{note}</p>
    </div>
  )
}

export const WINDOW_SECONDS = WINDOW.spanSeconds
export const QUIET_SECONDS = GAP_SECONDS - BURSTS[0].seconds
