/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Claude-style monochrome — warm cream canvas, ink text, dusty-rose accent
        canvas: '#F4EFE6',      // warm off-white, sits below cards
        surface: '#FAF6EE',     // card / sidebar surface, subtle warm
        ink: {
          DEFAULT: '#1B1814',   // near-black warm
          mute: '#5C534A',      // body secondary
          soft: '#9A9088',      // hint text / hairlines on text
        },
        accent: {
          DEFAULT: '#B97A8A',   // logo rose — the only saturated colour we use
          50:  '#F4E5E8',
          100: '#EAD0D6',
          300: '#D6A2AC',
          500: '#B97A8A',
          700: '#8C5566',
          900: '#5E3641',
        },
        cream: '#F2E9DD',       // logo glyph — used for highlights / decorative fills
        hairline: 'rgba(27,24,20,0.08)',
      },
      fontFamily: {
        sans: ['Inter', 'SF Pro Text', 'system-ui', 'sans-serif'],
        serif: ['"Tiempos Headline"', '"Source Serif Pro"', 'Georgia', 'serif'],
      },
      borderRadius: {
        card: '14px',
      },
      boxShadow: {
        card: '0 1px 0 rgba(27,24,20,0.04), 0 6px 18px rgba(27,24,20,0.04)',
        ring: '0 0 0 1px rgba(27,24,20,0.06)',
      },
    },
  },
  plugins: [],
};
