import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the React app runs on :5173 and the FastAPI backend on :8000.
// The proxy makes /api/* fetches transparently hit the backend, so the
// frontend code uses the same relative path /api/analyze in dev and prod.
// In prod (after `npm run build`), FastAPI serves both the SPA and the API
// from the same origin, no proxy needed.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
