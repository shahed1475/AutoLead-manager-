import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

// The client link's sign-in pages (/signin/): their own small build, served by
// the client link's web server. They never include the dashboard's code.
//   npx vite build --config vite.portal.config.js && node scripts/portal-dist.mjs
export default defineConfig({
  plugins: [react()],
  base: '/signin/',
  build: {
    outDir: 'dist-portal/signin',
    emptyOutDir: true,
    rollupOptions: {
      input: { portal: fileURLToPath(new URL('./portal.html', import.meta.url)) },
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) return 'vendor'
          return undefined
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: { '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true } },
  },
})
