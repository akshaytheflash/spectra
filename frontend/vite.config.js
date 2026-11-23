import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Without VITE_API_URL the dev server proxies /api/* to the local FastAPI
// (API_PORT or 8000), matching the production single-origin layout.
const PROXY = process.env.VITE_PROXY_TARGET || `http://127.0.0.1:${process.env.API_PORT || 8000}`;

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: PROXY,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
        error: (err) => console.warn('[vite proxy]', err?.message || err),
      },
      '/media': { target: PROXY, changeOrigin: true },
    },
  },
});
