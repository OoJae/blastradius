import { ADVISORY, FIRST_ARTIFACT, WINDOW } from '../lib/incident'

/** The thesis. One sentence from the reader's side of the screen, the clock at
 *  the second the first malicious version went live, and a way into the tool
 *  that does not require scrolling to find. */
export function Hero() {
  return (
    <section className="relative flex min-h-[100svh] flex-col justify-end pb-[4vh] pt-[14vh]">
      <div className="relative z-10 px-[var(--gutter)] pl-[calc(var(--rail)+var(--gutter))]">
        <p
          data-reveal
          className="font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint"
        >
          {WINDOW.fromISO.slice(0, 10)} · {ADVISORY.cve}
        </p>

        <h1
          className="mt-4 max-w-[16ch] font-display font-normal text-[length:var(--step-hero)]"
          style={{ lineHeight: 'var(--lh-display)', letterSpacing: 'var(--track-display)' }}
        >
          <span className="mask-line">
            <span data-reveal data-reveal-delay="1" className="block">Your CI ran</span>
          </span>
          <span className="mask-line">
            <span data-reveal data-reveal-delay="2" className="block">
              <span className="font-mono text-[0.82em] text-ember">npm install</span>
            </span>
          </span>
          <span className="mask-line">
            <span data-reveal data-reveal-delay="3" className="block italic">four seconds ago.</span>
          </span>
        </h1>

        <p
          data-reveal
          data-reveal-delay="5"
          className="mt-5 max-w-[46ch] text-[length:var(--step-body)] text-chalk-dim"
          style={{ lineHeight: 1.55 }}
        >
          {ADVISORY.artifacts} malicious versions of {ADVISORY.packages} packages went live at{' '}
          <span className="whitespace-nowrap font-mono text-chalk">{WINDOW.fromISO.slice(11, 19)} UTC</span>.
          The only question that matters is which of your services just installed one.
        </p>

        <div data-reveal data-reveal-delay="7" className="mt-7 flex flex-wrap items-center gap-x-7 gap-y-4">
          <a
            href="/app/"
            className="group relative inline-flex items-center gap-3 overflow-hidden rounded-full bg-ember px-7 py-3.5 text-[length:var(--step-small)] font-medium text-[color:var(--void)] transition-transform duration-[var(--dur-micro)] ease-out hover:scale-[1.02] active:scale-[0.99]"
          >
            {/* The sweep is the blast, once, on hover -- the same gesture the
                page is about, at the scale of a button. */}
            <span
              aria-hidden
              className="absolute inset-0 -translate-x-full bg-[color:var(--ember-glow)] transition-transform duration-500 ease-out group-hover:translate-x-0"
            />
            <span className="relative">Open the instrument</span>
            <span aria-hidden className="relative transition-transform duration-[var(--dur-micro)] ease-out group-hover:translate-x-1">→</span>
          </a>
          <p className="font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint">
            live graph · {ADVISORY.ghsa}
          </p>
        </div>
      </div>

      <div className="relative z-10 mt-[6vh] flex items-end justify-between gap-6 border-t border-[color:var(--ash)] px-[var(--gutter)] pl-[calc(var(--rail)+var(--gutter))] pt-4">
        <p data-reveal data-reveal-delay="8" className="font-mono text-[length:var(--step-label)] text-chalk-faint">
          first artifact <span className="text-chalk-dim">{FIRST_ARTIFACT}</span>
        </p>
        <p data-reveal data-reveal-delay="9" className="shrink-0 font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint">
          scroll to detonate ↓
        </p>
      </div>
    </section>
  )
}
