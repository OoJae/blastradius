# Motion and interaction

Every value here is a token in `web/src/styles/tokens.css`. Nothing in the
interface should invent its own timing; if a component needs a duration that is
not on this page, the page is wrong and should be changed deliberately.

## The two curves

| Token | Value | Used for |
|---|---|---|
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | everything that arrives: reveals, hovers, the hero, scene changes |
| `--ease-inout` | `cubic-bezier(0.65, 0, 0.35, 1)` | state toggles only — a tab switching, a drawer opening |

Linear is reserved for ambient loops (the field's idle breathing). It is never
used for anything a person triggers.

## Durations

| Token | Value | Used for |
|---|---|---|
| `--dur-micro` | 240 ms | hover, press, focus, colour changes |
| `--dur-reveal` | 800 ms | an element entering the viewport |
| `--dur-scene` | 1200 ms | the hero, and transitions between the incident's acts |
| `--stagger` | 70 ms | delay between siblings in a reveal group |

Stagger is applied through `data-reveal-delay="n"`, which multiplies `n` by
`--stagger`. Groups are numbered in reading order, so the eye is led down the
page rather than around it.

## The one reveal

There is exactly one entrance animation in the product: an element fades in and
rises `0.6em`, once, when it comes within 15% of the viewport bottom. It is
implemented in `web/src/landing/lib/useReveal.ts` with an `IntersectionObserver`
that unobserves on first fire, so nothing re-animates on scroll-back.

Only `opacity` and `transform` are animated, anywhere. No layout property is
ever transitioned.

## The scroll

The landing page uses Lenis at `lerp: 0.1` as a smooth-scroll backbone. It does
not hijack scrolling: the page never scrolls somewhere the reader did not ask
for, and every section can be reached by keyboard alone. The incident replay is
a `position: sticky` scene inside a tall section, so the scrollbar remains
honest about how much page is left.

Scroll position drives exactly one thing: incident time. Each act maps to the
seconds it actually occupied on 11 May 2026, so the clock in the rail is a
readout rather than a progress bar.

## Buttons

Press is a `scale(0.99)` on `:active` and `scale(1.02)` on `:hover`, both at
`--dur-micro`. The primary button additionally sweeps a lighter fill across
itself on hover — one gesture, 500 ms, `--ease-out` — because it is the same
shape as the blast the page is about.

Arrows translate `4px` on hover at `--dur-micro`. Nothing else moves.

## Reduced motion

`prefers-reduced-motion: reduce` is honoured completely, not partially:

- every duration token collapses to `0.01s`, so reveals are effectively instant
- `useReveal` skips the observer entirely and marks all targets revealed on mount
- Lenis is not constructed; native scrolling is used
- the WebGL field runs **no animation frame loop** — it renders a single frame,
  and repaints once per scroll update so the scene still reflects where the
  reader is

That last point is the subtle one. A field frozen at its initial state would be
worse than the animation the reader opted out of, so `setIgnite` and `setSpin`
repaint on demand when the loop is not running.

## Performance

The point cloud is capped at 9,000 instances regardless of slice size, drawn in
a single `THREE.Points` call with a custom shader. Ignition is a uniform, not
per-point CPU work, so a scroll frame costs one uniform write. Device pixel
ratio is clamped to 2.
