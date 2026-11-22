import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const backend = process.env.UI_BACKEND ?? 'http://localhost:8001'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': backend, '/lab': backend } },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'] },
})
