import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Same origin in development and in production: the API serves web/dist, and
// the dev server proxies /api to it. That means no CORS handling anywhere.
export default defineConfig({
  plugins: [react()],
  // Two entry points, because the landing page and the instrument are two
  // different products with two different weights. `app/index.html` builds to
  // dist/app/index.html, which matters: the API serves dist through Starlette's
  // StaticFiles(html=True), and that is not an SPA fallback -- it only serves
  // index.html for a real *directory*. A client-side route would 404 on
  // refresh; a directory does not. It also splits three.js into the landing
  // chunk alone, so the instrument stays as light as it is today.
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        landing: new URL('./index.html', import.meta.url).pathname,
        app: new URL('./app/index.html', import.meta.url).pathname,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/samples': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
})
