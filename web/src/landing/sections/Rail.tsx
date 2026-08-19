/** The tick rail: the page's time axis, and the only fixed chrome.
 *
 *  It is a seismograph's paper edge. The clock is the real UTC second the
 *  scroll position corresponds to, so the rail is a readout rather than
 *  decoration. */
export function Rail({ clock, progress, phase }: { clock: string; progress: number; phase: string }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-y-0 left-0 z-40 hidden w-[var(--rail)] flex-col justify-between border-r border-[color:var(--ash)] py-5 sm:flex"
    >
      <span className="rotate-180 self-center font-mono text-[length:var(--step-label)] uppercase tracking-[var(--track-label)] text-chalk-faint [writing-mode:vertical-rl]">
        11 May 2026
      </span>

      <div className="relative mx-auto h-[42vh] w-px bg-[color:var(--ash)]">
        <span
          className="absolute -left-[3px] h-[7px] w-[7px] rounded-full bg-ember transition-transform duration-150 ease-out"
          style={{ transform: `translateY(${progress * 42}vh)` }}
        />
      </div>

      <span
        className={`self-center font-mono text-[length:var(--step-label)] tabular-nums [writing-mode:vertical-rl] ${
          phase === 'burst-1' || phase === 'burst-2' ? 'text-ember' : 'text-chalk-dim'
        }`}
      >
        {clock}
      </span>
    </div>
  )
}
