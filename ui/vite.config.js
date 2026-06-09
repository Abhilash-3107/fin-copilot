import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const PROXY_PATHS = [
  '/statements', '/transactions', '/annotations',
  '/groups', '/people', '/embeddings', '/categories', '/health',
]

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      PROXY_PATHS.map(p => [p, { target: 'http://localhost:8000', changeOrigin: true }])
    ),
  },
  build: {
    outDir: 'dist',
  },
})
