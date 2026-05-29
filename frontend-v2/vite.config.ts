import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [tailwindcss(), react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5174,
    // Allow tunnel hostnames so friends accessing the app via the public
    // tunnel URL aren't blocked by Vite's anti-DNS-rebinding gate. Covers
    // both ngrok's current domain patterns and Cloudflare's quick-tunnel
    // domain; localhost stays accessible too.
    allowedHosts: [
      '.ngrok-free.app',
      '.ngrok-free.dev',
      '.trycloudflare.com',
      'theosintel.com',
      'www.theosintel.com',
      'localhost',
    ],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // Forward the browser-visible Host header so the backend can tell
        // direct-localhost access (which bypasses the access-code gate)
        // apart from tunnel access. With changeOrigin:true the proxy
        // rewrites Host to localhost:8000, so without this the backend
        // can't see where the request started.
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.headers.host) {
              proxyReq.setHeader('X-Forwarded-Host', req.headers.host)
            }
          })
        },
      },
    },
  },
})
