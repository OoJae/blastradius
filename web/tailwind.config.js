/** @type {import('tailwindcss').Config} */
// Colours resolve through the custom properties in src/styles/tokens.css, so
// the landing page and the instrument cannot drift apart: change a token and
// both move together. The literal hexes live in exactly one file now.
export default {
  content: ['./index.html', './app/index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // One accent, used only for the thing under attack and for live data.
        ember: {
          DEFAULT: 'var(--ember)',
          dim: 'var(--ember-dim)',
          glow: 'var(--ember-glow)',
        },
        ink: {
          900: 'var(--void)',
          800: 'var(--ink)',
          700: 'var(--ink-raised)',
          600: 'var(--ash)',
        },
        chalk: {
          DEFAULT: 'var(--chalk)',
          dim: 'var(--chalk-dim)',
          faint: 'var(--chalk-faint)',
        },
        verdict: {
          exposed: 'var(--verdict-exposed)',
          unknown: 'var(--verdict-unknown)',
          atrisk: 'var(--verdict-atrisk)',
          clean: 'var(--verdict-clean)',
        },
      },
      fontFamily: {
        display: ['var(--font-display)'],
        sans: ['var(--font-body)'],
        mono: ['var(--font-mono)'],
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
        inout: 'var(--ease-inout)',
      },
    },
  },
  plugins: [],
}
