/** @type {import('tailwindcss').Config} */
// Tokens copied verbatim from the approved navigation prototype (v3): the design
// is normative, so the colours, fonts and radius live here rather than being
// re-derived. Recurring arbitrary text sizes are promoted to `fontSize` tokens.
export default {
  content: ['./index.html', './src/**/*.{svelte,js}'],
  theme: {
    extend: {
      colors: {
        bg: '#0E1013', panel: '#14171B', card: '#191D22', card2: '#1F242A', sunk: '#0A0C0E',
        line: { DEFAULT: '#252B32', strong: '#333B44' },
        ink: { DEFAULT: '#E4E7EA', 2: '#959DA6', 3: '#626A73' },
        accent: { DEFAULT: '#3FA0B5', hi: '#59BCD1', bg: '#12262C', line: '#1E3D46', on: '#06171C' },
        heat: {
          90: '#96813A', 97: '#C4802F', 99: '#EC9F3E',
          bg90: '#1E1B12', bg97: '#241A10', bg99: '#2A1E0E',
          ln90: '#2E2916', ln97: '#3A2814', ln99: '#452F13',
        },
        ok: { DEFAULT: '#57A06E', bg: '#132118', line: '#1D3325' },
        err: { DEFAULT: '#D0524E', bg: '#251314', line: '#3A2022' },
        viol: { DEFAULT: '#8C93D6', bg: '#1A1D2B', line: '#282D42' },
      },
      fontFamily: {
        sans: ['Manrope', 'system-ui', 'sans-serif'],
        mono: ['DM Mono', 'ui-monospace', 'monospace'],
      },
      borderRadius: { card: '10px' },
      // The integer sizes the prototype repeats as text-[..px] across rows,
      // labels and numbers — promoted so a change is made once. The half-pixel
      // sizes (12.5/13.5/…) stay as arbitrary values to keep class names dot-free.
      fontSize: {
        10: '10px', 11: '11px', 12: '12px', 13: '13px', 14: '14px',
      },
    },
  },
  plugins: [],
}
