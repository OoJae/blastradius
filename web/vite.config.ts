import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Same origin in development and in production: the API serves web/dist, and
// the dev server proxies /api to it. That means no CORS handling anywhere.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/samples': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
})
