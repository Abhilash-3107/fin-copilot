import process from 'node:process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Node 25 prints a warning whenever its own (experimental) localStorage is
// touched without a storage file, and the test setup touches it before
// installing the in-memory one. The flag only exists on newer Node, so ask.
const NO_WEBSTORAGE = '--no-experimental-webstorage'
const execArgv = process.allowedNodeEnvironmentFlags.has(NO_WEBSTORAGE) ? [NO_WEBSTORAGE] : []

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: false,
    poolOptions: { forks: { execArgv } },
  },
})
