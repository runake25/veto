import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Python runner serves the built app from /app/, so the base has to match.
// In dev, Vite proxies /api to the runner on 8787 to keep cookies same-origin.
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  build: {
    outDir: '../app',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8787', changeOrigin: false },
    },
  },
})
