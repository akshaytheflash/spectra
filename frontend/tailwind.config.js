/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        base: '#070b14',
        panel: '#0c1322',
        raised: '#111a2c',
        line: '#1c2940',
        ink: '#e6edf7',
        muted: '#8a9bb5',
        accent: '#22d3ee',
        danger: '#f43f5e',
        warn: '#f59e0b',
        ok: '#34d399',
        violet: '#a78bfa',
        pink: '#f472b6',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(34,211,238,0.25), 0 0 32px rgba(34,211,238,0.08)',
      },
    },
  },
  plugins: [],
};
