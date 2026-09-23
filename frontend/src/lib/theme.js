import { useSyncExternalStore } from 'react'

// Day/night theme. The stored choice ('light' | 'dark') wins; with none
// stored, the OS preference decides and is followed live. index.html applies
// the same rule before first paint so there is no flash of the wrong theme.
const KEY = 'autolead_theme'
const media = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: light)') : null
const listeners = new Set()

function stored() {
  try { return localStorage.getItem(KEY) } catch { return null }
}

export function currentTheme() {
  const s = stored()
  if (s === 'light' || s === 'dark') return s
  return media?.matches ? 'light' : 'dark'
}

function apply(theme, animate) {
  const root = document.documentElement
  if (animate) {
    root.classList.add('theme-switching')
    window.setTimeout(() => root.classList.remove('theme-switching'), 260)
  }
  root.setAttribute('data-theme', theme)
  document.querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', theme === 'light' ? '#F6F5F1' : '#121211')
  listeners.forEach((l) => l())
}

export function setTheme(theme) {
  try { localStorage.setItem(KEY, theme) } catch { /* private mode: still applies for this visit */ }
  apply(theme, true)
}

media?.addEventListener('change', () => { if (!stored()) apply(currentTheme(), true) })

function subscribe(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, currentTheme)
  return { theme, setTheme, toggle: () => setTheme(theme === 'light' ? 'dark' : 'light') }
}

// Chart colours for SVG attributes that can't read CSS variables. One accent
// (primary) for the headline series; neutrals for everything secondary.
export function chartColors(theme) {
  return theme === 'light'
    ? { grid: '#EAE8E2', axis: '#78746B', legend: '#5E5A52', track: '#EAE8E2',
        primary: '#2F6A51', secondary: '#A9A59B' }
    : { grid: '#242321', axis: '#77746B', legend: '#97948B', track: '#2A2926',
        primary: '#5C9E7F', secondary: '#58564F' }
}
