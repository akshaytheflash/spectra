/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        base: 'rgb(var(--c-base) / <alpha-value>)',
        panel: 'rgb(var(--c-panel) / <alpha-value>)',
        raised: 'rgb(var(--c-raised) / <alpha-value>)',
        line: 'rgb(var(--c-line) / <alpha-value>)',
        ink: 'rgb(var(--c-ink) / <alpha-value>)',
        muted: 'rgb(var(--c-muted) / <alpha-value>)',
        accent: 'rgb(var(--c-accent) / <alpha-value>)',
        danger: 'rgb(var(--c-danger) / <alpha-value>)',
        warn: 'rgb(var(--c-warn) / <alpha-value>)',
        ok: 'rgb(var(--c-ok) / <alpha-value>)',
        violet: 'rgb(var(--c-violet) / <alpha-value>)',
        pink: 'rgb(var(--c-pink) / <alpha-value>)',
        brass: 'rgb(var(--c-brass) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['"Archivo Variable"', 'Archivo', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '6px',
        md: '6px',
        lg: '8px',
        xl: '10px',
      },
      ringColor: {
        DEFAULT: 'rgb(var(--c-accent) / 0.35)',
      },
    },
  },
  plugins: [],
};
