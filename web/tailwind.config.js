/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // One accent, used only for the thing under attack and for live data.
        ember: { DEFAULT: '#FF6B35', dim: '#B54A24', glow: '#FF8F5E' },
        ink: { 900: '#0B0C0E', 800: '#121417', 700: '#1A1D21', 600: '#24282E' },
        chalk: { DEFAULT: '#E8EAED', dim: '#9BA1A9', faint: '#5F666E' },
        verdict: {
          exposed: '#FF4D4D',
          unknown: '#8B93A1',
          atrisk: '#F0A202',
          clean: '#3DD68C',
        },
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
