import { compact } from '../../lib/format'
import type { LiveFacts } from '../lib/incident'
import { ADVISORY, WINDOW } from '../lib/incident'

/** What the tool does, numbered because these genuinely are a sequence: it is
 *  the order an incident responder asks them in, starting at the moment the
 *  advisory lands. */
const STEPS = [
  {
    n: '01',
    q: 'What is the blast radius?',
    a: 'A reverse-dependency closure, anchored on the compromised package and walked over a materialised projection. It paints in stages — one hop, then two, then three — so the screen is alive while the deeper traversal runs.',
    metric: (f: LiveFacts) =>
      f.rings.length === 3
        ? `${compact(f.rings[0].total)} → ${compact(f.rings[1].total)} → ${compact(f.rings[2].total)} packages, by depth`
        : null,
  },
  {
    n: '02',
    q: 'Was I installing while it was live?',
    a: 'Drop the lockfile your CI resolved. Every artifact is checked against the advisory, and for anything it names, the graph evaluates the window as a predicate on the edge itself — the database answers, not the application.',
    metric: () => `${WINDOW.fromISO.slice(11, 19)} → ${WINDOW.untilISO.slice(11, 19)} UTC, derived from registry timestamps`,
  },
  {
    n: '03',
    q: 'What do I do about it?',
    a: 'Every exposed entry carries its own next step: the earliest clean release published after the window, read out of the graph rather than guessed, and the instruction to rotate every credential on the machine that installed it.',
    metric: () => 'npm install @tanstack/react-router@1.170.0 --ignore-scripts',
    mono: true,
  },
  {
    n: '04',
    q: 'Where can the attacker go next?',
    a: 'The worm published with stolen maintainer credentials, not through dependencies. One traversal out to the maintainers and back maps every package those credentials still reach.',
    metric: (f: LiveFacts) => {
      const h = f.forecast?.hindsight
      const r = f.forecast?.reach
      return h && r ? `${h.flagged} of ${h.fell_later} later victims flagged from the first artifact alone · ${r.packages} candidates remain` : null
    },
  },
] as const

export function Instrument({ facts }: { facts: LiveFacts }) {
  return (
    <section className="relative z-10 border-t border-[color:var(--ash)] bg-[color:var(--void)] px-[var(--gutter)] pl-[calc(var(--rail)+var(--gutter))] py-[14vh]">
      <p
        data-reveal
        className="font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint"
      >
        the instrument
      </p>
      <h2
        data-reveal
        data-reveal-delay="1"
        className="mt-5 max-w-[18ch] font-display text-[length:var(--step-section)] font-normal"
        style={{ lineHeight: 1.02, letterSpacing: 'var(--track-display)' }}
      >
        Four questions, in the order you ask them.
      </h2>

      <ol className="mt-16 grid gap-x-16 gap-y-14 lg:grid-cols-2">
        {STEPS.map((s, i) => {
          const metric = s.metric(facts)
          return (
            <li key={s.n} data-reveal data-reveal-delay={String(i + 2)} className="border-t border-[color:var(--ash)] pt-6">
              <p className="font-mono text-[length:var(--step-label)] text-ember">{s.n}</p>
              <h3 className="mt-3 font-display text-[clamp(1.5rem,2.4vw,2.25rem)] font-normal leading-tight">
                {s.q}
              </h3>
              <p className="mt-3 max-w-[42ch] text-[length:var(--step-body)] text-chalk-dim" style={{ lineHeight: 'var(--lh-body)' }}>
                {s.a}
              </p>
              {metric && (
                <p
                  className={`mt-5 break-words font-mono text-[length:var(--step-small)] ${
                    'mono' in s && s.mono ? 'rounded bg-[color:var(--ink)] px-3 py-2 text-chalk' : 'text-chalk-faint'
                  }`}
                >
                  {metric}
                </p>
              )}
            </li>
          )
        })}
      </ol>

      <p data-reveal className="mt-16 max-w-[var(--measure)] text-[length:var(--step-body)] text-chalk-dim" style={{ lineHeight: 'var(--lh-body)' }}>
        Every one of those answers is a query. The inspector shows you the exact
        statement that produced each number, how many milliseconds it took, and
        whether it ran just now or came from cache — because a claim that a graph
        answered the question is only worth something if you can read the
        question.
      </p>
    </section>
  )
}

export function Proof({ facts }: { facts: LiveFacts }) {
  const g = facts.stats?.graph
  const cells = [
    { v: g?.package?.value, label: 'packages in the graph' },
    { v: g?.version?.value, label: 'versions' },
    { v: g?.maintainer?.value, label: 'maintainers' },
    { v: ADVISORY.artifacts, label: 'malicious artifacts' },
  ]

  return (
    <section className="relative z-10 border-t border-[color:var(--ash)] px-[var(--gutter)] pl-[calc(var(--rail)+var(--gutter))] py-[14vh]">
      <p data-reveal className="font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint">
        measured, not estimated
      </p>
      <h2
        data-reveal
        data-reveal-delay="1"
        className="mt-5 max-w-[20ch] font-display text-[length:var(--step-section)] font-normal"
        style={{ lineHeight: 1.02, letterSpacing: 'var(--track-display)' }}
      >
        Checked against an oracle that does not use the graph.
      </h2>
      <p data-reveal data-reveal-delay="2" className="mt-6 max-w-[var(--measure)] text-[length:var(--step-body)] text-chalk-dim" style={{ lineHeight: 'var(--lh-body)' }}>
        Every closure the database returns is recomputed in plain Python from the
        same edge file and compared exactly. Three depths, zero missing, zero
        extra. Where the evaluation finds nothing, it reports nothing — the
        recall of the worm's later victims through dependency edges is zero, and
        that is a fact about how the attack spread rather than a number worth
        massaging.
      </p>

      <dl className="mt-14 grid grid-cols-2 gap-x-10 gap-y-10 lg:grid-cols-4">
        {cells.map((c, i) => (
          <div key={c.label} data-reveal data-reveal-delay={String(i + 3)}>
            <dd className="tnum font-mono text-[clamp(1.75rem,3.6vw,3rem)] leading-none text-chalk">
              {c.v == null ? '—' : compact(c.v)}
            </dd>
            <dt className="mt-3 font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint">
              {c.label}
            </dt>
          </div>
        ))}
      </dl>
    </section>
  )
}
