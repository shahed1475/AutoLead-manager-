// Finishes the client sign-in build (vite.portal.config.js): the page becomes
// dist-portal/signin/index.html, and files only the dashboard needs are
// dropped (the service worker, offline page and app manifest belong to the
// client's workspace, which serves them at the site root).
//
// Run after:  npx vite build --config vite.portal.config.js
import { existsSync, renameSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const out = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist-portal', 'signin')
if (!existsSync(join(out, 'portal.html'))) throw new Error('portal-dist: run the portal build first')
renameSync(join(out, 'portal.html'), join(out, 'index.html'))
for (const f of ['sw.js', 'offline.html', 'manifest.webmanifest']) rmSync(join(out, f), { force: true })
console.log('portal-dist: sign-in pages ready in dist-portal/signin/')
