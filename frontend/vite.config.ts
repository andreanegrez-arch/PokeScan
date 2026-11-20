import react from '@vitejs/plugin-react'
import { defineConfig, type ProxyOptions } from 'vite'

// The app calls the API at /api, and Vite forwards it to the backend on this
// computer. So a phone on the same Wi-Fi only needs to reach Vite
// (`npm run dev:lan`): the backend stays on localhost and needs no CORS.
const api: Record<string, ProxyOptions> = {
  '/api': {
    target: 'http://localhost:8000',
    rewrite: (path) => path.replace(/^\/api/, ''),
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { proxy: api },
  preview: { proxy: api },
})
