/** @type {import('tailwindcss').Config} */
// Editorial "warm minimalism" system (Anthropic aesthetic).
// Cream book-cloth canvas, warm ink text, a single crail-orange accent,
// hairline borders instead of shadows. Serif (Fraunces) headlines over a
// humanist sans (Geist) body.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Foundation
        ink: '#141413', // primary text, dark surfaces (warm near-black)
        'book-cloth': '#faf9f5', // canonical background — replaces white
        paper: '#f5f4ee', // card / section surface on book-cloth
        'cloud-light': '#e8e6dc', // dividers, hairlines, muted fills
        'cloud-mid': '#b0aea5', // secondary UI, tertiary text
        'cloud-dark': '#6b6a63', // secondary text, captions

        // Accents — use one dominant per surface, sparingly
        crail: { DEFAULT: '#d97757', deep: '#c66848' }, // Anthropic orange — the one energy colour
        'slate-blue': '#6a9bcc', // info / editorial links
        olive: '#788c5d', // success / "all good" status
        clay: '#bd8b6a', // soft illustration tint

        // Legacy alias so stray border-hairline usages keep working
        hairline: '#e8e6dc',
      },
      fontFamily: {
        serif: ['Fraunces', '"Tiempos Text"', 'Georgia', 'serif'],
        sans: ['Geist', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"Geist Mono"', '"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        caption: ['0.8125rem', { lineHeight: '1.4', letterSpacing: '0.04em' }],
        small: ['0.875rem', { lineHeight: '1.5' }],
        body: ['1rem', { lineHeight: '1.6' }],
        'body-lg': ['1.125rem', { lineHeight: '1.6' }],
        'body-xl': ['1.25rem', { lineHeight: '1.55' }],
        h4: ['1.25rem', { lineHeight: '1.4', fontWeight: '500' }],
        h3: ['1.5rem', { lineHeight: '1.3', fontWeight: '500' }],
        h2: ['2rem', { lineHeight: '1.2', letterSpacing: '-0.01em', fontWeight: '400' }],
        h1: ['3rem', { lineHeight: '1.1', letterSpacing: '-0.015em', fontWeight: '400' }],
        display: ['clamp(2.5rem, 4vw + 1rem, 4.5rem)', { lineHeight: '1.05', letterSpacing: '-0.02em', fontWeight: '400' }],
      },
      letterSpacing: {
        display: '-0.02em',
        tight: '-0.015em',
        headline: '-0.01em',
        caps: '0.04em',
      },
      maxWidth: {
        container: '1240px',
        narrow: '720px',
        prose: '680px',
      },
      borderRadius: {
        sm: '4px',
        md: '8px',
        lg: '12px',
        card: '8px', // editorial cards are 8px, not 14px
      },
      boxShadow: {
        // Used only for genuine elevation (overlays). Borders carry the rest.
        sm: '0 1px 2px rgba(20, 20, 19, 0.06)',
        md: '0 4px 12px rgba(20, 20, 19, 0.08)',
      },
    },
  },
  plugins: [],
};
