import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API stays bound to 127.0.0.1 on the server. In development you reach it
// through an SSH tunnel:
//
//   ssh -L 8090:127.0.0.1:8090 root@10.130.9.243
//
// Everything then goes through this proxy, so the browser only ever talks to
// localhost:5173 - same origin, no CORS in the loop at all.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8090',
        changeOrigin: true,
      },
    },
  },
})
