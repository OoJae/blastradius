import { ADVISORY } from '../lib/incident'

export function Footer() {
  return (
    <footer className="relative z-10 border-t border-[color:var(--ash)] px-[var(--gutter)] pl-[calc(var(--rail)+var(--gutter))] py-[12vh]">
      <h2
        data-reveal
        className="max-w-[16ch] font-display text-[length:var(--step-section)] font-normal"
        style={{ lineHeight: 1.02, letterSpacing: 'var(--track-display)' }}
      >
        Find out what you installed.
      </h2>

      <div data-reveal data-reveal-delay="1" className="mt-9 flex flex-wrap items-center gap-4">
        <a
          href="/app/"
          className="group inline-flex items-center gap-3 rounded-full bg-ember px-7 py-3.5 text-[length:var(--step-small)] font-medium text-[color:var(--void)] transition-transform duration-[var(--dur-micro)] ease-out hover:scale-[1.02] active:scale-[0.99]"
        >
          Open the instrument
          <span aria-hidden className="transition-transform duration-[var(--dur-micro)] ease-out group-hover:translate-x-1">→</span>
        </a>
        <a
          href="https://github.com/OoJae/blastradius"
          className="rounded-full border border-[color:var(--ash)] px-7 py-3.5 text-[length:var(--step-small)] text-chalk-dim transition-colors duration-[var(--dur-micro)] hover:border-chalk-faint hover:text-chalk"
        >
          Read the source
        </a>
      </div>

      <div className="mt-16 flex flex-wrap justify-between gap-6 border-t border-[color:var(--ash)] pt-6 font-mono text-[length:var(--step-label)] text-chalk-faint">
        <p>{ADVISORY.ghsa} · {ADVISORY.cve}</p>
        <p>Built on HydraDB for Hack Hydra 2026</p>
      </div>
    </footer>
  )
}
