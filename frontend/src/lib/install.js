import { useSyncExternalStore } from 'react'

// Installable-app support. Chrome / Edge / Android fire `beforeinstallprompt`
// (possibly before React mounts, so this module is imported first in
// main.jsx); iPhone/iPad Safari never do — there the user adds the app from
// the Share menu, so we show instructions instead.
let deferredPrompt = null
const listeners = new Set()
const emit = () => listeners.forEach((l) => l())

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferredPrompt = e; emit() })
  window.addEventListener('appinstalled', () => { deferredPrompt = null; emit() })
}

export function isStandalone() {
  return window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true
}

export function isIOS() {
  const ua = window.navigator.userAgent
  return /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1)
}

function snapshot() {
  if (isStandalone()) return 'installed'
  if (deferredPrompt) return 'prompt'
  if (isIOS()) return 'ios'
  return 'unavailable'
}

export function useInstall() {
  const state = useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l) }, snapshot)
  async function install() {
    if (!deferredPrompt) return false
    deferredPrompt.prompt()
    const { outcome } = await deferredPrompt.userChoice
    deferredPrompt = null
    emit()
    return outcome === 'accepted'
  }
  return { state, install }
}

// Service worker: production builds on a secure origin only (HTTPS, or
// localhost on this computer). Never for the desktop file:// build.
export function registerServiceWorker() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
  if (!window.isSecureContext || window.location.protocol === 'file:') return
  // Not on window "load": that also waits for third-party files (the web
  // font), which on some networks never arrive — the app would then never
  // become installable. Register shortly after the page itself is ready.
  const register = () => setTimeout(() => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* app still works without it */ })
  }, 1000)
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', register, { once: true })
  else register()
}
