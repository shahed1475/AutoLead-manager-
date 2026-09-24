import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Relative asset paths — required so the built app can be loaded via
  // file:// inside Electron (an absolute '/assets/...' base 404s there).
  base: './',
  build: {
    // The client sign-in pages are a separate build: vite.portal.config.js.
    rollupOptions: {
      output: {
        // Charting code (recharts + its d3 helpers) stays in its own chunk so
        // the client portal, which draws no charts, never downloads it; small
        // shared helpers (clsx, …) go to vendor instead of riding along.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/[\\/]node_modules[\\/](recharts|recharts-scale|react-smooth|d3-[^\\/]+|victory-vendor|internmap|decimal\.js-light)[\\/]/.test(id)) return 'charts'
          return 'vendor'
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes, req) => {
            if (req.url && req.url.includes('/stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },
  },
})
